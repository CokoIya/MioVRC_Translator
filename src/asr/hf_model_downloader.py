"""HuggingFace model downloader with pause / resume / cancel support.

Download state machine:
  IDLE → DOWNLOADING ⇄ PAUSED → COMPLETED
                    ↘ CANCELLED / ERROR

Speed strategy:
  - Mirror probe: race hf-mirror.com (China CDN) vs huggingface.co; the first
    healthy responder wins for the whole session.
  - Parallel range: large files are split into N segments downloaded in
    parallel via HTTP Range headers. The total throughput on a residential
    link with 4–8 concurrent streams is typically several × that of a single
    stream because the per-connection cap dominates over the link's bandwidth.
  - Connection pool: a single requests.Session reuses TCP/TLS handshakes
    across files and segments via an HTTPAdapter with a larger pool.
  - Streaming chunks: small files and the small remainder use stable 2 MB
    chunks so progress updates stay smooth without misleading adaptive state.

Resume safety:
  Each parallel segment writes to a part file `dest.partN`. On resume the
  downloader picks each part up where it left off. When every segment is
  finished the parts are stitched into `dest` and removed.
"""
from __future__ import annotations

import copy
import logging
import os
import pathlib
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

from src.utils.app_paths import writable_app_dir

logger = logging.getLogger(__name__)

# Streaming chunk size. Keep this fixed; requests.iter_content binds the
# chunk size when the iterator is created.
_CHUNK_SIZE = 2 * 1024 * 1024

# Parallel range download tuning
_PARALLEL_THRESHOLD_BYTES = 32 * 1024 * 1024   # files >= 32 MB use ranges
_PARALLEL_PART_TARGET     = 64 * 1024 * 1024   # ~64 MB per segment
_PARALLEL_MIN_PARTS       = 2
_PARALLEL_MAX_PARTS       = 6                  # cap to avoid mirror throttling

_SPEED_WINDOW_S  = 4.0    # rolling window for speed / ETA
_UI_EMIT_MIN_S   = 0.15   # throttle: emit to UI at most every 150 ms

# Mirror candidates — tried in order; first to respond wins.
_MIRRORS = (
    "https://hf-mirror.com",       # mainland China CDN (ModelScope-backed)
    "https://huggingface.co",      # official
)
_PROBE_TIMEOUT      = 4     # max seconds for any single mirror probe
_PROBE_BYTES        = 768 * 1024   # bytes pulled to measure throughput
_PROBE_LOCALE_BONUS = 1.5   # locale-preferred mirror wins ties up to this ratio
_CONNECT_TIMEOUT    = 15
_READ_TIMEOUT       = 60

# Browser-style UA: a few mirror CDNs (incl. some hf-mirror nodes) reject
# the default python-requests UA with 403.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 MioTranslator/1.3"
    ),
    "Accept-Encoding": "identity",   # model.bin is already compressed
}


def _make_session() -> requests.Session:
    """Session with a pool large enough for parallel-range workers."""
    sess = requests.Session()
    sess.headers.update(_DEFAULT_HEADERS)
    adapter = HTTPAdapter(
        pool_connections=8,
        pool_maxsize=_PARALLEL_MAX_PARTS * 2,
        max_retries=0,
    )
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


class DownloadState(str, Enum):
    IDLE       = "idle"
    DOWNLOADING = "downloading"
    PAUSED     = "paused"
    COMPLETED  = "completed"
    CANCELLED  = "cancelled"
    ERROR      = "error"


@dataclass
class DownloadProgress:
    state:       DownloadState = DownloadState.IDLE
    file_name:   str   = ""
    file_index:  int   = 0
    file_count:  int   = 0
    file_bytes:  int   = 0
    file_total:  int   = 0
    total_bytes: int   = 0
    total_total: int   = 0
    speed_bps:   float = 0.0
    eta_s:       float = 0.0
    error:       str   = ""
    mirror:      str   = ""   # which base URL is being used

    @property
    def overall_fraction(self) -> float:
        return self.total_bytes / self.total_total if self.total_total > 0 else 0.0

    @property
    def speed_mb(self) -> str:
        mb = self.speed_bps / 1_048_576
        return f"{mb:.1f} MB/s" if mb >= 0.1 else f"{self.speed_bps / 1024:.0f} KB/s"

    @property
    def eta_str(self) -> str:
        s = int(self.eta_s)
        if s <= 0:
            return ""
        m, s = divmod(s, 60)
        return f"{m}分{s:02d}秒" if m else f"{s}秒"


# File list for each HF model repo. Ordered so the tiny files come first
# (fast to resume), the large model.bin is last.
_HF_MODEL_FILES: dict[str, list[str]] = {
    "ku-nlp/deberta-v2-large-japanese-char-wwm": [
        "config.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "vocab.txt",
        "model.safetensors",
    ],
    "microsoft/deberta-v3-large": [
        "config.json",
        "tokenizer_config.json",
        "spm.model",
        "pytorch_model.bin",
    ],
    "hfl/chinese-roberta-wwm-ext-large": [
        "config.json",
        "added_tokens.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
        "pytorch_model.bin",
    ],
}


def _model_dir(model_id: str) -> pathlib.Path:
    slug = model_id.replace("/", "--")
    return writable_app_dir() / "runtime_models" / slug


def model_dir(model_id: str) -> pathlib.Path:
    """Return the managed download directory for a Hugging Face model."""
    return _model_dir(model_id)


def _repo_url(base: str, model_id: str, filename: str) -> str:
    return f"{base}/{model_id}/resolve/main/{filename}"


def _normalise_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _mirror_candidates() -> list[str]:
    """Mirror list, with optional user/operator overrides first.

    Mainland users often need a local Hugging Face proxy. Let advanced users
    and packaged builds prepend mirrors without code changes while keeping the
    built-in hf-mirror/huggingface fallback chain intact.
    """
    candidates: list[str] = []
    for env_name in ("MIO_HF_MIRROR_BASES", "HF_ENDPOINT", "HF_HUB_ENDPOINT"):
        raw = os.environ.get(env_name, "")
        for item in raw.replace(";", ",").split(","):
            base = _normalise_base_url(item)
            if base and base not in candidates:
                candidates.append(base)
    for base in _MIRRORS:
        clean = _normalise_base_url(base)
        if clean not in candidates:
            candidates.append(clean)
    return candidates


def _mirror_fallback_order(selected: str) -> list[str]:
    candidates = _mirror_candidates()
    selected = _normalise_base_url(selected)
    ordered = [selected] if selected else []
    preferred = _preferred_mirror_for_locale()
    if preferred:
        preferred = _normalise_base_url(preferred)
        if preferred not in ordered and preferred in candidates:
            ordered.append(preferred)
    for base in candidates:
        if base not in ordered:
            ordered.append(base)
    return ordered


def model_is_complete(model_id: str) -> bool:
    """True if all required files are present (non-zero size)."""
    directory = _model_dir(model_id)
    for filename in _HF_MODEL_FILES.get(model_id, ["model.bin"]):
        path = directory / filename
        if not path.exists() or path.stat().st_size == 0:
            return False
    return True


ProgressCallback = Callable[[DownloadProgress], None]


def _probe_mirror_throughput(base: str, model_id: str, filename: str) -> float | None:
    """Pull a small range from the mirror and return measured B/s.

    HEAD-only probes mislead overseas users: a CN mirror can answer HEAD in
    ~200 ms but cap real downloads at 1 MB/s, while huggingface.co might be
    300 ms HEAD but sustain 30 MB/s. Measuring actual bytes-per-second on a
    short range is a far better predictor of full-download time.
    """
    url = _repo_url(base, model_id, filename)
    headers = dict(_DEFAULT_HEADERS)
    headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    try:
        t0 = time.monotonic()
        resp = requests.get(
            url, headers=headers, stream=True, timeout=_PROBE_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            resp.close()
            return None
        bytes_read = 0
        deadline = t0 + _PROBE_TIMEOUT
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            bytes_read += len(chunk)
            if bytes_read >= _PROBE_BYTES or time.monotonic() >= deadline:
                break
        resp.close()
        elapsed = max(time.monotonic() - t0, 1e-3)
        if bytes_read <= 0:
            return None
        return bytes_read / elapsed
    except Exception:
        return None


def _preferred_mirror_for_locale() -> str | None:
    """Return the mirror users in this locale should prefer, if any."""
    try:
        from src.utils.locale_detect import get_system_language
        lang = get_system_language()
    except Exception:
        return None
    if lang in {"zh", "yue"}:
        return "https://hf-mirror.com"
    # Default to the canonical CDN for everyone else (USA/Europe/JP/KR/...).
    return "https://huggingface.co"


def _select_mirror(model_id: str, first_file: str) -> str:
    """Race mirrors by measured throughput; bias by system locale on ties."""
    candidates = _mirror_candidates()
    results: dict[str, float] = {}     # base_url -> bytes/sec
    lock = threading.Lock()

    def _try(base: str) -> None:
        bps = _probe_mirror_throughput(base, model_id, first_file)
        if bps is not None and bps > 0:
            with lock:
                results[base] = bps
            logger.debug("Mirror probe %s: %.2f MB/s", base, bps / 1_048_576)
        else:
            logger.debug("Mirror probe %s: unreachable / 0 B/s", base)

    threads = [threading.Thread(target=_try, args=(b,), daemon=True) for b in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_PROBE_TIMEOUT + 1)

    if not results:
        preferred = _preferred_mirror_for_locale()
        fallback = (
            _normalise_base_url(preferred)
            if preferred and _normalise_base_url(preferred) in candidates
            else candidates[0]
        )
        logger.warning("All mirror probes failed, defaulting to %s", fallback)
        return fallback

    fastest = max(results, key=results.__getitem__)
    preferred = _preferred_mirror_for_locale()
    chosen = fastest

    # Locale bias: if the locale-preferred mirror is within PROBE_LOCALE_BONUS×
    # of the fastest, pick it instead. Avoids surprises like a Chinese user
    # being routed to huggingface.co when hf-mirror is only marginally slower.
    if preferred and preferred in results and preferred != fastest:
        if results[preferred] * _PROBE_LOCALE_BONUS >= results[fastest]:
            chosen = preferred
            logger.info(
                "Mirror: %s wins on locale bias (%.1f MB/s vs %s %.1f MB/s)",
                preferred, results[preferred] / 1_048_576,
                fastest, results[fastest] / 1_048_576,
            )

    logger.info("Selected mirror: %s (%.1f MB/s probed)",
                chosen, results[chosen] / 1_048_576)
    return chosen


class HFModelDownloader:
    """Thread-safe downloader for a single HuggingFace model."""

    def __init__(self, model_id: str) -> None:
        self._model_id    = model_id
        self._lock        = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()          # not paused initially
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress    = DownloadProgress()
        self._listeners:  list[ProgressCallback] = []
        self._speed_samples: deque[tuple[float, int]] = deque()
        self._last_emit_t: float = 0.0
        self._session: requests.Session | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def add_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            self._listeners = [x for x in self._listeners if x is not cb]

    @property
    def state(self) -> DownloadState:
        with self._lock:
            return self._progress.state

    @property
    def progress(self) -> DownloadProgress:
        with self._lock:
            return copy.copy(self._progress)

    def start(self) -> None:
        with self._lock:
            if self._progress.state in (DownloadState.DOWNLOADING, DownloadState.COMPLETED):
                return
            self._progress = DownloadProgress(state=DownloadState.DOWNLOADING)
            self._cancel_event.clear()
            self._pause_event.set()
            self._last_emit_t = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name="hf-download")
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._progress.state != DownloadState.DOWNLOADING:
                return
            self._progress.state = DownloadState.PAUSED
        self._pause_event.clear()
        self._emit(force=True)

    def resume(self) -> None:
        with self._lock:
            if self._progress.state != DownloadState.PAUSED:
                return
            self._progress.state = DownloadState.DOWNLOADING
        self._pause_event.set()
        self._emit(force=True)

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.set()   # unblock if paused
        with self._lock:
            self._progress.state = DownloadState.CANCELLED
        self._emit(force=True)

    # ── internals ───────────────────────────────────────────────────────────

    def _emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_emit_t) < _UI_EMIT_MIN_S:
                return
            self._last_emit_t = now
            p = copy.copy(self._progress)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(p)
            except Exception:
                logger.debug("Progress listener raised", exc_info=True)

    def _set_state(self, state: DownloadState, error: str = "") -> None:
        with self._lock:
            self._progress.state = state
            if error:
                self._progress.error = error

    def _update_progress(
        self,
        *,
        file_name: str,
        file_index: int,
        file_count: int,
        file_bytes: int,
        file_total: int,
        total_bytes: int,
        total_total: int,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._speed_samples.append((now, total_bytes))
            cutoff = now - _SPEED_WINDOW_S
            while self._speed_samples and self._speed_samples[0][0] < cutoff:
                self._speed_samples.popleft()

            speed_bps = 0.0
            if len(self._speed_samples) >= 2:
                t0, b0 = self._speed_samples[0]
                t1, b1 = self._speed_samples[-1]
                dt = t1 - t0
                if dt > 0:
                    speed_bps = (b1 - b0) / dt

            remaining = total_total - total_bytes
            eta_s = remaining / speed_bps if speed_bps > 0 else 0.0

            self._progress.file_name   = file_name
            self._progress.file_index  = file_index
            self._progress.file_count  = file_count
            self._progress.file_bytes  = file_bytes
            self._progress.file_total  = file_total
            self._progress.total_bytes = total_bytes
            self._progress.total_total = total_total
            self._progress.speed_bps   = speed_bps
            self._progress.eta_s       = eta_s

        self._emit()

    def _run(self) -> None:
        try:
            self._session = _make_session()
            self._download_all()
        except Exception as exc:
            logger.exception("HF model download failed: %s", self._model_id)
            self._set_state(DownloadState.ERROR, str(exc))
            self._emit(force=True)
        finally:
            if self._session:
                self._session.close()
                self._session = None

    def _download_all(self) -> None:
        files = _HF_MODEL_FILES.get(self._model_id, ["model.bin"])
        directory = _model_dir(self._model_id)
        directory.mkdir(parents=True, exist_ok=True)

        # Select fastest mirror before any real download
        base_url = _select_mirror(self._model_id, files[0])
        base_urls = _mirror_fallback_order(base_url)
        with self._lock:
            self._progress.mirror = base_url

        # Pre-calculate total size via HEAD
        file_sizes: list[int] = []
        for filename in files:
            size = self._head_size(base_url, filename)
            if size <= 0:
                for fallback_base in base_urls[1:]:
                    size = self._head_size(fallback_base, filename)
                    if size > 0:
                        break
            file_sizes.append(size)
        total_total = sum(file_sizes)

        total_bytes_done = 0
        for i, (filename, expected_size) in enumerate(zip(files, file_sizes)):
            if self._cancel_event.is_set():
                return

            dest = directory / filename
            existing = dest.stat().st_size if dest.exists() else 0

            if expected_size > 0 and existing == expected_size:
                total_bytes_done += existing
                self._update_progress(
                    file_name=filename, file_index=i, file_count=len(files),
                    file_bytes=existing, file_total=expected_size,
                    total_bytes=total_bytes_done, total_total=total_total,
                )
                continue

            total_bytes_done = self._download_file_with_fallback(
                base_urls=base_urls,
                filename=filename,
                dest=dest,
                file_index=i,
                file_count=len(files),
                expected_size=expected_size,
                total_bytes_so_far=total_bytes_done,
                total_total=total_total,
            )
            if self._cancel_event.is_set():
                return

        if not self._cancel_event.is_set():
            self._set_state(DownloadState.COMPLETED)
            with self._lock:
                self._progress.total_bytes = total_total
                self._progress.total_total = total_total
                self._progress.file_bytes  = file_sizes[-1] if file_sizes else 0
                self._progress.file_total  = file_sizes[-1] if file_sizes else 0
            self._emit(force=True)
            logger.info("Model download complete: %s (via %s)", self._model_id, base_url)

    def _download_file_with_fallback(
        self,
        *,
        base_urls: list[str],
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        last_error: BaseException | None = None
        for base_url in base_urls:
            if self._cancel_event.is_set() and self._is_user_cancelled():
                return total_bytes_so_far
            if not self._pause_event.is_set():
                self._pause_event.wait()
            try:
                with self._lock:
                    self._progress.mirror = base_url
                return self._download_file(
                    base_url=base_url,
                    filename=filename,
                    dest=dest,
                    file_index=file_index,
                    file_count=file_count,
                    expected_size=expected_size,
                    total_bytes_so_far=total_bytes_so_far,
                    total_total=total_total,
                )
            except Exception as exc:
                if self._is_user_cancelled():
                    return total_bytes_so_far
                last_error = exc
                self._cancel_event.clear()
                self._pause_event.set()
                logger.warning(
                    "Download from %s failed for %s; trying next mirror",
                    base_url,
                    filename,
                    exc_info=True,
                )
        if last_error is not None:
            raise last_error
        return total_bytes_so_far

    def _head_size(self, base_url: str, filename: str) -> int:
        url = _repo_url(base_url, self._model_id, filename)
        try:
            assert self._session is not None
            resp = self._session.head(url, timeout=_CONNECT_TIMEOUT, allow_redirects=True)
            return int(resp.headers.get("Content-Length") or 0)
        except Exception:
            return 0

    def _download_file(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        # Use parallel range download for large files when total size known.
        if expected_size >= _PARALLEL_THRESHOLD_BYTES:
            try:
                return self._download_file_parallel(
                    base_url=base_url,
                    filename=filename,
                    dest=dest,
                    file_index=file_index,
                    file_count=file_count,
                    expected_size=expected_size,
                    total_bytes_so_far=total_bytes_so_far,
                    total_total=total_total,
                )
            except _RangeNotSupported:
                logger.info("Server refused Range; falling back to single stream for %s", filename)

        return self._download_file_single(
            base_url=base_url,
            filename=filename,
            dest=dest,
            file_index=file_index,
            file_count=file_count,
            expected_size=expected_size,
            total_bytes_so_far=total_bytes_so_far,
            total_total=total_total,
        )

    def _download_file_single(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        url = _repo_url(base_url, self._model_id, filename)
        resume_from = dest.stat().st_size if dest.exists() else 0
        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            logger.info("Resuming %s from byte %d", filename, resume_from)

        assert self._session is not None
        resp = self._session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        resp.raise_for_status()

        # Server may not honour Range — treat 200 as restart
        if resp.status_code == 200 and resume_from > 0:
            resume_from = 0
            dest.unlink(missing_ok=True)

        file_bytes  = resume_from
        total_bytes = total_bytes_so_far + resume_from
        mode        = "ab" if resume_from > 0 else "wb"

        with dest.open(mode) as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                self._pause_event.wait()

                if self._cancel_event.is_set():
                    return total_bytes

                if chunk:
                    fh.write(chunk)
                    n = len(chunk)
                    file_bytes  += n
                    total_bytes += n
                    self._update_progress(
                        file_name=filename,
                        file_index=file_index,
                        file_count=file_count,
                        file_bytes=file_bytes,
                        file_total=expected_size,
                        total_bytes=total_bytes,
                        total_total=total_total,
                    )

        return total_bytes

    # ── parallel-range path ───────────────────────────────────────────────

    def _plan_segments(self, expected_size: int) -> list[tuple[int, int]]:
        """Split [0, expected_size) into N inclusive byte ranges."""
        n = max(_PARALLEL_MIN_PARTS, min(_PARALLEL_MAX_PARTS,
                                          (expected_size + _PARALLEL_PART_TARGET - 1)
                                          // _PARALLEL_PART_TARGET))
        seg_size = expected_size // n
        ranges: list[tuple[int, int]] = []
        for i in range(n):
            start = i * seg_size
            end = (start + seg_size - 1) if i < n - 1 else (expected_size - 1)
            ranges.append((start, end))
        return ranges

    def _download_file_parallel(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        url = _repo_url(base_url, self._model_id, filename)
        ranges = self._plan_segments(expected_size)
        n_parts = len(ranges)
        logger.info(
            "Parallel download: %s in %d parts (%.0f MB each, ~%.0f MB total)",
            filename, n_parts, expected_size / n_parts / 1_048_576,
            expected_size / 1_048_576,
        )

        # Probe Range support with the first segment via a HEAD/GET so that
        # we can fall back fast if the mirror returns 200 instead of 206.
        if not self._probe_range(url, ranges[0]):
            raise _RangeNotSupported()

        part_paths = [dest.with_suffix(dest.suffix + f".part{i}") for i in range(n_parts)]

        # Resume: existing parts contribute to total_bytes baseline
        baseline = sum(p.stat().st_size if p.exists() else 0 for p in part_paths)
        progress_lock = threading.Lock()
        bytes_done = total_bytes_so_far + baseline
        file_bytes = baseline
        emit_state = {"last_emit": 0.0}

        def _on_chunk(n: int) -> None:
            nonlocal bytes_done, file_bytes
            with progress_lock:
                bytes_done += n
                file_bytes += n
                # Throttle inside the lock so we don't fan out on every chunk
                now = time.monotonic()
                if (now - emit_state["last_emit"]) < _UI_EMIT_MIN_S:
                    return
                emit_state["last_emit"] = now
                snapshot = (file_bytes, bytes_done)
            self._update_progress(
                file_name=filename,
                file_index=file_index,
                file_count=file_count,
                file_bytes=snapshot[0],
                file_total=expected_size,
                total_bytes=snapshot[1],
                total_total=total_total,
            )

        worker_error: list[BaseException] = []
        worker_lock = threading.Lock()

        def _worker(idx: int, span: tuple[int, int]) -> None:
            if self._cancel_event.is_set():
                return
            try:
                self._download_segment(
                    url=url,
                    part_path=part_paths[idx],
                    span=span,
                    on_chunk=_on_chunk,
                )
            except BaseException as exc:   # noqa: BLE001 — propagate to driver
                with worker_lock:
                    worker_error.append(exc)
                # Cancel the rest so we exit fast on a failure
                self._cancel_event.set()
                self._pause_event.set()

        with ThreadPoolExecutor(max_workers=n_parts, thread_name_prefix="hf-range") as pool:
            futs = [pool.submit(_worker, i, span) for i, span in enumerate(ranges)]
            for _ in as_completed(futs):
                pass

        if self._cancel_event.is_set():
            # Surface the underlying error if cancellation was caused by one.
            if worker_error and not self._is_user_cancelled():
                raise worker_error[0]
            return total_bytes_so_far + sum(
                p.stat().st_size if p.exists() else 0 for p in part_paths
            )

        # Stitch parts → final file. Use shutil.copyfileobj for streaming copy.
        import shutil
        missing = [p for p in part_paths if not p.exists()]
        if missing:
            # This should not happen on a clean run, but guard against OS-level
            # file removal (e.g. antivirus, disk full abort).
            raise RuntimeError(
                f"Parallel download incomplete: missing part files: "
                f"{[p.name for p in missing]}"
            )
        with dest.open("wb") as out:
            for part in part_paths:
                with part.open("rb") as src_fh:
                    shutil.copyfileobj(src_fh, out, length=8 * 1024 * 1024)
        for part in part_paths:
            try:
                part.unlink()
            except OSError:
                logger.debug("Could not remove part file %s", part, exc_info=True)

        # Final emit so progress bar lands on 100% for this file
        self._update_progress(
            file_name=filename,
            file_index=file_index,
            file_count=file_count,
            file_bytes=expected_size,
            file_total=expected_size,
            total_bytes=total_bytes_so_far + expected_size,
            total_total=total_total,
        )
        return total_bytes_so_far + expected_size

    def _is_user_cancelled(self) -> bool:
        with self._lock:
            return self._progress.state == DownloadState.CANCELLED

    def _probe_range(self, url: str, span: tuple[int, int]) -> bool:
        """Check whether the server honours a Range request on this URL."""
        assert self._session is not None
        start, end = span
        try:
            resp = self._session.get(
                url,
                headers={"Range": f"bytes={start}-{min(start + 1023, end)}"},
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            try:
                if resp.status_code != 206:
                    return False
                # Drain a tiny bit then close to release the connection.
                next(resp.iter_content(chunk_size=1024), None)
            finally:
                resp.close()
            return True
        except Exception:
            logger.debug("Range probe failed for %s", url, exc_info=True)
            return False

    def _download_segment(
        self,
        *,
        url: str,
        part_path: pathlib.Path,
        span: tuple[int, int],
        on_chunk: Callable[[int], None],
    ) -> None:
        """Download a single Range segment to part_path with resume."""
        start, end = span
        existing = part_path.stat().st_size if part_path.exists() else 0
        if existing >= (end - start + 1):
            return  # already complete
        seg_start = start + existing
        headers = {"Range": f"bytes={seg_start}-{end}"}

        assert self._session is not None
        resp = self._session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        resp.raise_for_status()
        if resp.status_code != 206:
            # Server ignored the Range — abort to fall back to single-stream
            resp.close()
            raise _RangeNotSupported()

        mode = "ab" if existing > 0 else "wb"
        with part_path.open(mode) as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                self._pause_event.wait()
                if self._cancel_event.is_set():
                    return
                if not chunk:
                    continue
                fh.write(chunk)
                on_chunk(len(chunk))


class _RangeNotSupported(Exception):
    """Mirror returned 200 instead of 206 — fall back to single stream."""


# ── Module-level singleton per model_id ─────────────────────────────────────

_downloaders: dict[str, HFModelDownloader] = {}
_downloaders_lock = threading.Lock()


def get_downloader(model_id: str) -> HFModelDownloader:
    """Return (or create) the shared downloader for this model."""
    with _downloaders_lock:
        if model_id not in _downloaders:
            _downloaders[model_id] = HFModelDownloader(model_id)
        return _downloaders[model_id]
