"""High-throughput downloader for bundled Hololive Style-Bert-VITS2 packs."""
from __future__ import annotations

import copy
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import requests

from src.asr.hf_model_downloader import (
    DownloadProgress,
    DownloadState,
    _CHUNK_SIZE,
    _CONNECT_TIMEOUT,
    _DEFAULT_HEADERS,
    _PARALLEL_MAX_PARTS,
    _PARALLEL_MIN_PARTS,
    _PARALLEL_PART_TARGET,
    _PARALLEL_THRESHOLD_BYTES,
    _PROBE_BYTES,
    _PROBE_LOCALE_BONUS,
    _PROBE_TIMEOUT,
    _READ_TIMEOUT,
    _RangeNotSupported,
    _SPEED_WINDOW_S,
    _UI_EMIT_MIN_S,
    _make_session,
    _mirror_candidates,
    _mirror_fallback_order,
    _normalise_base_url,
)
from .style_bert_vits2_models import (
    StyleBertVits2ModelError,
    hololive_model_bundle,
    inspect_style_bert_model_dir,
    style_bert_models_dir,
)

logger = logging.getLogger(__name__)

_HF_REPO = "spaces/Kit-Lemonfoot/Hololive-Style-Bert-VITS2"

ProgressCallback = Callable[[DownloadProgress], None]


def _model_dir(model_path: str) -> Path:
    return style_bert_models_dir() / model_path


def _repo_url(base: str, model_path: str, filename: str) -> str:
    return f"{base}/{_HF_REPO}/resolve/main/model_assets/{model_path}/{filename}"


def hololive_bundle_is_complete(model_path: str) -> bool:
    bundle = hololive_model_bundle(model_path)
    if bundle is None:
        return False
    directory = _model_dir(bundle.model_path)
    if not all(
        (directory / filename).is_file() and (directory / filename).stat().st_size > 0
        for filename in bundle.files
    ):
        return False
    if not _looks_like_numpy_file(directory / "style_vectors.npy"):
        return False
    try:
        inspect_style_bert_model_dir(directory)
    except StyleBertVits2ModelError:
        return False
    return True


def _looks_like_numpy_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(6) == b"\x93NUMPY"
    except OSError:
        return False


def _probe_bundle_throughput(base: str, model_path: str, filename: str) -> float | None:
    url = _repo_url(base, model_path, filename)
    headers = dict(_DEFAULT_HEADERS)
    headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    try:
        t0 = time.monotonic()
        resp = requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=_PROBE_TIMEOUT,
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
        return bytes_read / elapsed if bytes_read > 0 else None
    except Exception:
        return None


def _preferred_base_for_locale() -> str | None:
    try:
        from src.utils.locale_detect import get_system_language

        lang = get_system_language()
    except Exception:
        return None
    return "https://hf-mirror.com" if lang in {"zh", "yue"} else "https://huggingface.co"


def _select_base(model_path: str, first_file: str) -> str:
    bases = _mirror_candidates()
    results: dict[str, float] = {}
    lock = threading.Lock()

    def _try(base: str) -> None:
        bps = _probe_bundle_throughput(base, model_path, first_file)
        if bps is not None and bps > 0:
            with lock:
                results[base] = bps

    threads = [threading.Thread(target=_try, args=(base,), daemon=True) for base in bases]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_PROBE_TIMEOUT + 1)

    if not results:
        preferred = _preferred_base_for_locale()
        fallback = (
            _normalise_base_url(preferred)
            if preferred and _normalise_base_url(preferred) in bases
            else bases[0]
        )
        logger.warning("All Hololive model mirrors failed, defaulting to %s", fallback)
        return fallback

    fastest = max(results, key=results.__getitem__)
    preferred = _preferred_base_for_locale()
    chosen = fastest
    if preferred and preferred in results and preferred != fastest:
        if results[preferred] * _PROBE_LOCALE_BONUS >= results[fastest]:
            chosen = preferred
    logger.info("Selected Hololive model mirror: %s", chosen)
    return chosen


class HololiveStyleBertDownloader:
    """Pause/resume/cancel downloader for one shared Hololive model pack."""

    def __init__(self, model_path: str) -> None:
        self._bundle = hololive_model_bundle(model_path)
        if self._bundle is None:
            raise ValueError(f"Unknown Hololive model pack: {model_path}")
        self._model_path = self._bundle.model_path
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = DownloadProgress()
        self._listeners: list[ProgressCallback] = []
        self._speed_samples: deque[tuple[float, int]] = deque()
        self._last_emit_t = 0.0
        self._session: requests.Session | None = None

    def add_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            self._listeners = [item for item in self._listeners if item is not cb]

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
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"hololive-download-{self._model_path}",
        )
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
        self._pause_event.set()
        with self._lock:
            self._progress.state = DownloadState.CANCELLED
        self._emit(force=True)

    def _emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_emit_t) < _UI_EMIT_MIN_S:
                return
            self._last_emit_t = now
            progress = copy.copy(self._progress)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(progress)
            except Exception:
                logger.debug("Hololive download listener raised", exc_info=True)

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
            self._progress.file_name = file_name
            self._progress.file_index = file_index
            self._progress.file_count = file_count
            self._progress.file_bytes = file_bytes
            self._progress.file_total = file_total
            self._progress.total_bytes = total_bytes
            self._progress.total_total = total_total
            self._progress.speed_bps = speed_bps
            self._progress.eta_s = eta_s
        self._emit()

    def _run(self) -> None:
        try:
            self._session = _make_session()
            self._download_all()
        except Exception as exc:
            logger.exception("Hololive model download failed: %s", self._model_path)
            self._set_state(DownloadState.ERROR, str(exc))
            self._emit(force=True)
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    def _download_all(self) -> None:
        files = self._bundle.files
        directory = _model_dir(self._model_path)
        directory.mkdir(parents=True, exist_ok=True)
        base_url = _select_base(self._model_path, files[0])
        base_urls = _mirror_fallback_order(base_url)
        with self._lock:
            self._progress.mirror = base_url

        file_sizes = []
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

        for index, (filename, expected_size) in enumerate(zip(files, file_sizes)):
            if self._cancel_event.is_set():
                return
            dest = directory / filename
            existing = dest.stat().st_size if dest.exists() else 0
            if expected_size > 0 and existing == expected_size:
                total_bytes_done += existing
                self._update_progress(
                    file_name=filename,
                    file_index=index,
                    file_count=len(files),
                    file_bytes=existing,
                    file_total=expected_size,
                    total_bytes=total_bytes_done,
                    total_total=total_total,
                )
                continue
            total_bytes_done = self._download_file_with_fallback(
                base_urls=base_urls,
                filename=filename,
                dest=dest,
                file_index=index,
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
                self._progress.file_bytes = file_sizes[-1] if file_sizes else 0
                self._progress.file_total = file_sizes[-1] if file_sizes else 0
            self._emit(force=True)

    def _download_file_with_fallback(
        self,
        *,
        base_urls: list[str],
        filename: str,
        dest: Path,
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
                    "Hololive model download from %s failed for %s; trying next mirror",
                    base_url,
                    filename,
                    exc_info=True,
                )
        if last_error is not None:
            raise last_error
        return total_bytes_so_far

    def _is_user_cancelled(self) -> bool:
        with self._lock:
            return self._progress.state == DownloadState.CANCELLED

    def _head_size(self, base_url: str, filename: str) -> int:
        url = _repo_url(base_url, self._model_path, filename)
        try:
            assert self._session is not None
            response = self._session.head(url, timeout=_CONNECT_TIMEOUT, allow_redirects=True)
            return int(response.headers.get("Content-Length") or 0)
        except Exception:
            return 0

    def _download_file(
        self,
        *,
        base_url: str,
        filename: str,
        dest: Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
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
                logger.info("Hololive model host refused Range; using single stream for %s", filename)
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
        dest: Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        url = _repo_url(base_url, self._model_path, filename)
        resume_from = dest.stat().st_size if dest.exists() else 0
        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
        assert self._session is not None
        response = self._session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        response.raise_for_status()
        if response.status_code == 200 and resume_from > 0:
            resume_from = 0
            dest.unlink(missing_ok=True)
        file_bytes = resume_from
        total_bytes = total_bytes_so_far + resume_from
        mode = "ab" if resume_from > 0 else "wb"
        with dest.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                self._pause_event.wait()
                if self._cancel_event.is_set():
                    return total_bytes
                if chunk:
                    handle.write(chunk)
                    chunk_size = len(chunk)
                    file_bytes += chunk_size
                    total_bytes += chunk_size
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

    def _plan_segments(self, expected_size: int) -> list[tuple[int, int]]:
        count = max(
            _PARALLEL_MIN_PARTS,
            min(
                _PARALLEL_MAX_PARTS,
                (expected_size + _PARALLEL_PART_TARGET - 1) // _PARALLEL_PART_TARGET,
            ),
        )
        segment_size = expected_size // count
        ranges: list[tuple[int, int]] = []
        for index in range(count):
            start = index * segment_size
            end = (start + segment_size - 1) if index < count - 1 else expected_size - 1
            ranges.append((start, end))
        return ranges

    def _download_file_parallel(
        self,
        *,
        base_url: str,
        filename: str,
        dest: Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        url = _repo_url(base_url, self._model_path, filename)
        ranges = self._plan_segments(expected_size)
        if not self._probe_range(url, ranges[0]):
            raise _RangeNotSupported()

        part_paths = [dest.with_suffix(dest.suffix + f".part{idx}") for idx in range(len(ranges))]
        part_done = [
            min(part_path.stat().st_size, end - start + 1) if part_path.exists() else 0
            for part_path, (start, end) in zip(part_paths, ranges)
        ]
        initial_done = sum(part_done)
        total_bytes = total_bytes_so_far + initial_done
        self._update_progress(
            file_name=filename,
            file_index=file_index,
            file_count=file_count,
            file_bytes=initial_done,
            file_total=expected_size,
            total_bytes=total_bytes,
            total_total=total_total,
        )
        progress_lock = threading.Lock()

        def _download_part(part_index: int) -> int:
            start, end = ranges[part_index]
            part_path = part_paths[part_index]
            already = part_done[part_index]
            if already >= (end - start + 1):
                return already
            headers = {"Range": f"bytes={start + already}-{end}"}
            assert self._session is not None
            response = self._session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            if response.status_code != 206:
                response.close()
                raise _RangeNotSupported()
            response.raise_for_status()
            mode = "ab" if already else "wb"
            current = already
            with part_path.open(mode) as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        return current
                    if chunk:
                        handle.write(chunk)
                        current += len(chunk)
                        with progress_lock:
                            part_done[part_index] = current
                            merged = sum(part_done)
                            self._update_progress(
                                file_name=filename,
                                file_index=file_index,
                                file_count=file_count,
                                file_bytes=merged,
                                file_total=expected_size,
                                total_bytes=total_bytes_so_far + merged,
                                total_total=total_total,
                            )
            return current

        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [pool.submit(_download_part, idx) for idx in range(len(ranges))]
            for future in as_completed(futures):
                future.result()
                if self._cancel_event.is_set():
                    return total_bytes_so_far + sum(part_done)

        with dest.open("wb") as merged:
            for part_path in part_paths:
                with part_path.open("rb") as handle:
                    while True:
                        chunk = handle.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        merged.write(chunk)
                part_path.unlink(missing_ok=True)
        return total_bytes_so_far + expected_size

    def _probe_range(self, url: str, byte_range: tuple[int, int]) -> bool:
        start, end = byte_range
        assert self._session is not None
        response = self._session.get(
            url,
            headers={"Range": f"bytes={start}-{min(end, start + 1023)}"},
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        try:
            return response.status_code == 206
        finally:
            response.close()


_downloaders: dict[str, HololiveStyleBertDownloader] = {}
_downloaders_lock = threading.Lock()


def get_hololive_downloader(model_path: str) -> HololiveStyleBertDownloader:
    with _downloaders_lock:
        if model_path not in _downloaders:
            _downloaders[model_path] = HololiveStyleBertDownloader(model_path)
        return _downloaders[model_path]
