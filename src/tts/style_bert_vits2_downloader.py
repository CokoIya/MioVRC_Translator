"""High-throughput downloader for bundled Hololive Style-Bert-VITS2 packs."""
from __future__ import annotations

import copy
import logging
import os
import re
import tempfile
import threading
import time
import weakref
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable
import requests

from src.utils.hf_model_downloader import (
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
    _response_content_length,
    _is_safe_model_download_url,
    _secure_file_matches_sha256,
    _validate_content_range,
)
from src.utils.app_paths import (
    atomic_replace_secure_file,
    open_secure_append,
    open_secure_read,
    require_existing_real_directory,
    require_real_directory,
    secure_file_path,
    secure_file_size,
    secure_unlink,
)
from src.utils.secure_http import open_validated_requests_response
from .style_bert_vits2_models import (
    StyleBertVits2ModelBundle,
    StyleBertVits2ModelError,
    hololive_model_bundle,
    inspect_style_bert_model_dir,
    style_bert_models_dir,
)

logger = logging.getLogger(__name__)

_HF_REPO = "spaces/Kit-Lemonfoot/Hololive-Style-Bert-VITS2"
_MAX_BUNDLE_FILE_BYTES = 8 * 1024 * 1024 * 1024
_MAX_BUNDLE_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
_MODEL_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")

ProgressCallback = Callable[[DownloadProgress], None]


def _validated_bundle(model_path: str) -> StyleBertVits2ModelBundle:
    bundle = hololive_model_bundle(model_path)
    if bundle is None:
        raise ValueError(f"Unknown Hololive model pack: {model_path}")
    if (
        not _MODEL_COMPONENT_RE.fullmatch(bundle.model_path)
        or bundle.model_path in {".", ".."}
        or Path(bundle.model_path).name != bundle.model_path
    ):
        raise ValueError(f"Unsafe Hololive model pack name: {bundle.model_path!r}")
    for filename in bundle.files:
        if (
            not filename
            or Path(filename).name != filename
            or filename in {".", ".."}
        ):
            raise ValueError(f"Unsafe Hololive model filename: {filename!r}")
    hashes = dict(bundle.file_sha256)
    if (
        not re.fullmatch(r"[0-9a-f]{40}", bundle.revision)
        or set(hashes) != set(bundle.files)
        or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())
    ):
        raise ValueError(f"Incomplete Hololive model integrity manifest: {model_path}")
    return bundle


def _model_dir(model_path: str, *, create: bool = True) -> Path:
    bundle = _validated_bundle(model_path)
    candidate = style_bert_models_dir() / bundle.model_path
    if create:
        return require_real_directory(candidate)
    return require_existing_real_directory(candidate)


def _repo_url(base: str, model_path: str, filename: str) -> str:
    safe_base = _normalise_base_url(base)
    if not safe_base:
        raise ValueError(f"Unsafe Hugging Face mirror base URL: {base!r}")
    bundle = _validated_bundle(model_path)
    if filename not in bundle.files:
        raise ValueError(f"Unknown file for Hololive model pack {model_path}: {filename}")
    return (
        f"{safe_base}/{_HF_REPO}/resolve/{bundle.revision}/"
        f"model_assets/{bundle.model_path}/{filename}"
    )


def hololive_bundle_is_complete(model_path: str) -> bool:
    try:
        bundle = _validated_bundle(model_path)
        directory = _model_dir(bundle.model_path, create=False)
        trusted_hashes = dict(bundle.file_sha256)
        total_size = 0
        for filename in bundle.files:
            path = secure_file_path(directory / filename, must_exist=True)
            size = secure_file_size(path)
            if size <= 0 or size > _MAX_BUNDLE_FILE_BYTES:
                return False
            if not _secure_file_matches_sha256(path, trusted_hashes[filename]):
                return False
            total_size += size
            if total_size > _MAX_BUNDLE_TOTAL_BYTES:
                return False
        if not _looks_like_numpy_file(directory / "style_vectors.npy"):
            return False
        inspect_style_bert_model_dir(directory)
    except (FileNotFoundError, OSError, RuntimeError, ValueError, StyleBertVits2ModelError):
        return False
    return True


def _looks_like_numpy_file(path: Path) -> bool:
    try:
        with open_secure_read(path, binary=True) as handle:
            return handle.read(6) == b"\x93NUMPY"
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False


def _probe_bundle_throughput(base: str, model_path: str, filename: str) -> float | None:
    url = _repo_url(base, model_path, filename)
    headers = dict(_DEFAULT_HEADERS)
    headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    try:
        started = time.monotonic()
        with open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            timeout=(_PROBE_TIMEOUT, _PROBE_TIMEOUT),
            label="Hololive mirror probe",
            headers=headers,
            stream=True,
            max_redirects=5,
            request_get=requests.get,
        ) as response:
            if response.status_code >= 400:
                return None
            bytes_read = 0
            deadline = started + _PROBE_TIMEOUT
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read >= _PROBE_BYTES or time.monotonic() >= deadline:
                    break
        elapsed = max(time.monotonic() - started, 1e-3)
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
        preferred = _normalise_base_url(_preferred_base_for_locale() or "")
        fallback = preferred if preferred in bases else bases[0]
        logger.warning("All Hololive model mirrors failed, defaulting to %s", fallback)
        return fallback

    fastest = max(results, key=results.__getitem__)
    preferred = _normalise_base_url(_preferred_base_for_locale() or "")
    chosen = fastest
    if preferred in results and preferred != fastest:
        if results[preferred] * _PROBE_LOCALE_BONUS >= results[fastest]:
            chosen = preferred
    logger.info("Selected Hololive model mirror: %s", chosen)
    return chosen


class HololiveStyleBertDownloader:
    """Pause/resume/cancel downloader for one shared Hololive model pack."""

    def __init__(self, model_path: str) -> None:
        self._bundle = _validated_bundle(model_path)
        self._model_path = self._bundle.model_path
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = DownloadProgress()
        # Downloaders are shared process-wide.  Bound UI callbacks must stay
        # weak so closing a dialog also releases the dialog and its Qt graph.
        self._listeners: list[ProgressCallback | weakref.WeakMethod] = []
        self._speed_samples: deque[tuple[float, int]] = deque()
        self._last_emit_t = 0.0
        self._session: requests.Session | None = None

    def add_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            live_listeners: list[ProgressCallback | weakref.WeakMethod] = []
            already_registered = False
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None:
                    continue
                live_listeners.append(entry)
                if target == cb:
                    already_registered = True
            self._listeners = live_listeners
            if already_registered:
                return
            if getattr(cb, "__self__", None) is not None:
                try:
                    self._listeners.append(weakref.WeakMethod(cb))
                    return
                except TypeError:
                    pass
            self._listeners.append(cb)

    def remove_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            retained: list[ProgressCallback | weakref.WeakMethod] = []
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None or target == cb:
                    continue
                retained.append(entry)
            self._listeners = retained

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
            if (
                self._progress.state in (
                    DownloadState.DOWNLOADING,
                    DownloadState.COMPLETED,
                )
                or (self._thread is not None and self._thread.is_alive())
            ):
                return
            self._progress = DownloadProgress(state=DownloadState.DOWNLOADING)
            self._cancel_event.clear()
            self._pause_event.set()
            self._last_emit_t = 0.0
            self._speed_samples.clear()
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
            listeners: list[ProgressCallback] = []
            retained: list[ProgressCallback | weakref.WeakMethod] = []
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None:
                    continue
                retained.append(entry)
                listeners.append(target)
            self._listeners = retained
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
                first_t, first_bytes = self._speed_samples[0]
                last_t, last_bytes = self._speed_samples[-1]
                elapsed = last_t - first_t
                if elapsed > 0:
                    speed_bps = (last_bytes - first_bytes) / elapsed
            remaining = max(0, total_total - total_bytes)
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
            if self._is_user_cancelled():
                logger.info("Hololive model download cancelled: %s", self._model_path)
            else:
                logger.exception("Hololive model download failed: %s", self._model_path)
                self._set_state(DownloadState.ERROR, str(exc))
                self._emit(force=True)
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    def _download_all(self) -> None:
        files = self._bundle.files
        trusted_hashes = dict(self._bundle.file_sha256)
        directory = _model_dir(self._model_path)
        base_url = _select_base(self._model_path, files[0])
        base_urls = _mirror_fallback_order(base_url)
        with self._lock:
            self._progress.mirror = base_url

        file_sizes: list[int] = []
        for filename in files:
            size = self._head_size(base_url, filename)
            if size <= 0:
                for fallback_base in base_urls[1:]:
                    size = self._head_size(fallback_base, filename)
                    if size > 0:
                        break
            if size > _MAX_BUNDLE_FILE_BYTES:
                raise RuntimeError(f"Remote model file is oversized: {filename}")
            file_sizes.append(size)

        total_total = sum(file_sizes)
        if total_total > _MAX_BUNDLE_TOTAL_BYTES:
            raise RuntimeError("Model bundle exceeds the maximum allowed total size")

        total_bytes_done = 0
        for index, (filename, expected_size) in enumerate(zip(files, file_sizes)):
            if self._cancel_event.is_set():
                return

            dest = secure_file_path(directory / filename)
            existing = secure_file_size(dest, missing_ok=True)
            if existing > _MAX_BUNDLE_FILE_BYTES:
                raise RuntimeError(f"Existing model file is oversized: {filename}")

            if (
                expected_size > 0
                and existing == expected_size
                and _secure_file_matches_sha256(dest, trusted_hashes[filename])
            ):
                total_bytes_done += existing
                if total_bytes_done > _MAX_BUNDLE_TOTAL_BYTES:
                    raise RuntimeError(
                        "Model bundle exceeds the maximum allowed total size"
                    )
                self._update_progress(
                    file_name=filename,
                    file_index=index,
                    file_count=len(files),
                    file_bytes=existing,
                    file_total=expected_size,
                    total_bytes=total_bytes_done,
                    total_total=max(total_total, total_bytes_done),
                )
                continue
            if existing > 0:
                secure_unlink(dest)

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
            if total_bytes_done > _MAX_BUNDLE_TOTAL_BYTES:
                raise RuntimeError(
                    "Model bundle exceeds the maximum allowed total size"
                )

            final_size = secure_file_size(dest)
            if final_size <= 0 or final_size > _MAX_BUNDLE_FILE_BYTES:
                raise RuntimeError(
                    f"Downloaded model file has an invalid size: {filename}"
                )
            if expected_size > 0 and final_size != expected_size:
                raise RuntimeError(
                    f"Downloaded model file size mismatch for {filename}: "
                    f"{final_size} != {expected_size}"
                )
            if not _secure_file_matches_sha256(dest, trusted_hashes[filename]):
                secure_unlink(dest, missing_ok=True)
                raise RuntimeError(
                    f"Downloaded model file failed SHA256 verification: {filename}"
                )

        if self._cancel_event.is_set():
            return

        completed_total = sum(
            secure_file_size(
                secure_file_path(directory / filename, must_exist=True)
            )
            for filename in files
        )
        if completed_total > _MAX_BUNDLE_TOTAL_BYTES:
            raise RuntimeError("Model bundle exceeds the maximum allowed total size")
        if not hololive_bundle_is_complete(self._model_path):
            raise RuntimeError("Downloaded Hololive model bundle failed validation")

        progress_total = max(total_total, completed_total)
        self._set_state(DownloadState.COMPLETED)
        with self._lock:
            self._progress.total_bytes = completed_total
            self._progress.total_total = progress_total
            self._progress.file_bytes = (
                secure_file_size(directory / files[-1]) if files else 0
            )
            self._progress.file_total = (
                file_sizes[-1] or self._progress.file_bytes if file_sizes else 0
            )
        self._emit(force=True)
        logger.info(
            "Hololive model download complete: %s (via %s)",
            self._model_path,
            base_url,
        )

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
        for mirror_index, base_url in enumerate(base_urls):
            if self._cancel_event.is_set() and self._is_user_cancelled():
                return total_bytes_so_far
            if not self._pause_event.is_set():
                self._pause_event.wait()
            if mirror_index:
                self._clear_partial_files(dest)
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
                    "Hololive model download from %s failed for %s; "
                    "trying next mirror",
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

    @staticmethod
    def _single_partial_path(dest: Path) -> Path:
        return secure_file_path(dest.with_name(f"{dest.name}.part"))

    @staticmethod
    def _parallel_part_paths(dest: Path) -> list[Path]:
        return [
            secure_file_path(dest.with_suffix(dest.suffix + f".part{index}"))
            for index in range(_PARALLEL_MAX_PARTS)
        ]

    def _clear_parallel_parts(self, dest: Path) -> None:
        for part_path in self._parallel_part_paths(dest):
            secure_unlink(part_path, missing_ok=True)

    def _clear_partial_files(self, dest: Path) -> None:
        secure_unlink(self._single_partial_path(dest), missing_ok=True)
        self._clear_parallel_parts(dest)

    def _head_size(self, base_url: str, filename: str) -> int:
        url = _repo_url(base_url, self._model_path, filename)
        try:
            session = self._session
            if session is None:
                raise RuntimeError("Model downloader session is not initialized")
            with open_validated_requests_response(
                url,
                url_validator=_is_safe_model_download_url,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                label=filename,
                stream=True,
                max_redirects=5,
                request_get=session.head,
            ) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Unexpected HTTP status while probing {filename}: "
                        f"{response.status_code}"
                    )
                size = _response_content_length(response, label=filename) or 0
                if size > _MAX_BUNDLE_FILE_BYTES:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                return size
        except RuntimeError:
            raise
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
                if not self._is_user_cancelled():
                    self._cancel_event.clear()
                    self._pause_event.set()
                    self._clear_parallel_parts(dest)
                logger.info(
                    "Hololive model host refused Range; using single stream for %s",
                    filename,
                )

        if expected_size < _PARALLEL_THRESHOLD_BYTES:
            self._clear_parallel_parts(dest)
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
        remaining_budget = _MAX_BUNDLE_TOTAL_BYTES - total_bytes_so_far
        file_limit = min(_MAX_BUNDLE_FILE_BYTES, remaining_budget)
        if file_limit <= 0:
            raise RuntimeError("Model bundle exceeds the maximum allowed total size")

        partial = self._single_partial_path(dest)
        resume_from = secure_file_size(partial, missing_ok=True)
        if resume_from > file_limit:
            raise RuntimeError(f"Partial model file is oversized: {filename}")
        if expected_size > 0:
            if expected_size > file_limit:
                raise RuntimeError(f"Remote model file is oversized: {filename}")
            if resume_from > expected_size:
                raise RuntimeError(
                    f"Partial model file exceeds expected size: {filename}"
                )
            if resume_from == expected_size:
                expected_sha256 = dict(self._bundle.file_sha256)[filename]
                if not _secure_file_matches_sha256(partial, expected_sha256):
                    secure_unlink(partial, missing_ok=True)
                    raise RuntimeError(
                        f"Partial model file failed SHA256 verification: {filename}"
                    )
                atomic_replace_secure_file(
                    partial,
                    dest,
                    expected_size=expected_size,
                )
                return total_bytes_so_far + expected_size

        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            logger.info("Resuming %s from byte %d", filename, resume_from)

        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        response_context = open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            label=filename,
            headers=headers,
            stream=True,
            max_redirects=5,
            request_get=session.get,
        )
        response = response_context.__enter__()
        try:
            response.raise_for_status()
            if resume_from > 0 and response.status_code == 200:
                secure_unlink(partial)
                resume_from = 0
            elif resume_from > 0:
                if response.status_code != 206:
                    raise RuntimeError(
                        f"Unexpected HTTP status while resuming {filename}: "
                        f"{response.status_code}"
                    )
                content_length = _response_content_length(
                    response,
                    label=filename,
                )
                if content_length is None or content_length <= 0:
                    raise RuntimeError(
                        f"Resume response omitted Content-Length for {filename}"
                    )
                response_total = _validate_content_range(
                    response,
                    requested_start=resume_from,
                    requested_end=resume_from + content_length - 1,
                    expected_total=expected_size or None,
                    label=filename,
                )
                if response_total > file_limit:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                if expected_size <= 0:
                    expected_size = response_total
            elif response.status_code != 200:
                raise RuntimeError(
                    f"Unexpected HTTP status while downloading {filename}: "
                    f"{response.status_code}"
                )

            content_length = _response_content_length(response, label=filename)
            if response.status_code == 200 and content_length is not None:
                if content_length <= 0:
                    raise RuntimeError(f"Remote model file was empty: {filename}")
                if content_length > file_limit:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                if expected_size > 0 and content_length != expected_size:
                    raise RuntimeError(
                        f"Remote model file size changed for {filename}: "
                        f"{content_length} != {expected_size}"
                    )
                if expected_size <= 0:
                    expected_size = content_length

            file_bytes = resume_from
            total_bytes = total_bytes_so_far + resume_from
            with open_secure_append(partial, binary=True) as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        handle.flush()
                        os.fsync(handle.fileno())
                        return total_bytes
                    if not chunk:
                        continue
                    next_size = file_bytes + len(chunk)
                    size_limit = expected_size or file_limit
                    if next_size > size_limit:
                        raise RuntimeError(
                            f"Remote model file exceeded its allowed size: {filename}"
                        )
                    handle.write(chunk)
                    file_bytes = next_size
                    total_bytes += len(chunk)
                    self._update_progress(
                        file_name=filename,
                        file_index=file_index,
                        file_count=file_count,
                        file_bytes=file_bytes,
                        file_total=expected_size,
                        total_bytes=total_bytes,
                        total_total=max(total_total, total_bytes),
                    )
                handle.flush()
                os.fsync(handle.fileno())

            if file_bytes <= 0:
                raise RuntimeError(f"Remote model file was empty: {filename}")
            if expected_size > 0 and file_bytes != expected_size:
                raise RuntimeError(
                    f"Remote model file was truncated for {filename}: "
                    f"{file_bytes} != {expected_size}"
                )
            expected_sha256 = dict(self._bundle.file_sha256)[filename]
            if not _secure_file_matches_sha256(partial, expected_sha256):
                secure_unlink(partial, missing_ok=True)
                raise RuntimeError(
                    f"Downloaded model file failed SHA256 verification: {filename}"
                )
            atomic_replace_secure_file(
                partial,
                dest,
                expected_size=file_bytes,
            )
            self._clear_parallel_parts(dest)
            return total_bytes_so_far + file_bytes
        finally:
            response_context.__exit__(None, None, None)

    def _plan_segments(self, expected_size: int) -> list[tuple[int, int]]:
        count = max(
            _PARALLEL_MIN_PARTS,
            min(
                _PARALLEL_MAX_PARTS,
                (expected_size + _PARALLEL_PART_TARGET - 1)
                // _PARALLEL_PART_TARGET,
            ),
        )
        segment_size = expected_size // count
        ranges: list[tuple[int, int]] = []
        for index in range(count):
            start = index * segment_size
            end = (
                start + segment_size - 1
                if index < count - 1
                else expected_size - 1
            )
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
        file_limit = min(
            _MAX_BUNDLE_FILE_BYTES,
            _MAX_BUNDLE_TOTAL_BYTES - total_bytes_so_far,
        )
        if expected_size <= 0 or expected_size > file_limit:
            raise RuntimeError(f"Invalid expected model size for {filename}")

        url = _repo_url(base_url, self._model_path, filename)
        ranges = self._plan_segments(expected_size)
        part_paths = self._parallel_part_paths(dest)[: len(ranges)]
        logger.info(
            "Parallel Hololive download: %s in %d parts (%.0f MB total)",
            filename,
            len(ranges),
            expected_size / 1_048_576,
        )

        if not self._probe_range(url, ranges[0], expected_size):
            raise _RangeNotSupported()

        part_sizes: list[int] = []
        for part_path, (start, end) in zip(part_paths, ranges):
            size = secure_file_size(part_path, missing_ok=True)
            expected_part_size = end - start + 1
            if size > expected_part_size:
                raise RuntimeError(
                    f"Parallel part exceeds expected size: {part_path.name} "
                    f"({size} > {expected_part_size})"
                )
            part_sizes.append(size)

        baseline = sum(part_sizes)
        if baseline > expected_size or total_bytes_so_far + baseline > _MAX_BUNDLE_TOTAL_BYTES:
            raise RuntimeError(f"Parallel partials exceed the allowed size: {filename}")

        progress_lock = threading.Lock()
        bytes_done = total_bytes_so_far + baseline
        file_bytes = baseline
        emit_state = {"last_emit": 0.0}

        def _on_chunk(size: int) -> None:
            nonlocal bytes_done, file_bytes
            with progress_lock:
                bytes_done += size
                file_bytes += size
                if file_bytes > expected_size:
                    raise RuntimeError(
                        f"Parallel download exceeded expected size: {filename}"
                    )
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
                total_total=max(total_total, snapshot[1]),
            )

        worker_errors: list[BaseException] = []
        worker_lock = threading.Lock()

        def _worker(index: int, span: tuple[int, int]) -> None:
            if self._cancel_event.is_set():
                return
            try:
                self._download_segment(
                    url=url,
                    part_path=part_paths[index],
                    span=span,
                    total_size=expected_size,
                    on_chunk=_on_chunk,
                )
            except BaseException as exc:  # noqa: BLE001 - propagated below
                with worker_lock:
                    worker_errors.append(exc)
                self._cancel_event.set()
                self._pause_event.set()

        with ThreadPoolExecutor(
            max_workers=len(ranges),
            thread_name_prefix="hololive-range",
        ) as pool:
            futures = [
                pool.submit(_worker, index, span)
                for index, span in enumerate(ranges)
            ]
            for _ in as_completed(futures):
                pass

        if worker_errors:
            raise worker_errors[0]
        if self._cancel_event.is_set():
            return total_bytes_so_far + sum(
                secure_file_size(part_path, missing_ok=True)
                for part_path in part_paths
            )

        for part_path, (start, end) in zip(part_paths, ranges):
            expected_part_size = end - start + 1
            actual_size = secure_file_size(part_path)
            if actual_size != expected_part_size:
                raise RuntimeError(
                    f"Parallel download part is incomplete: {part_path.name} "
                    f"({actual_size} != {expected_part_size})"
                )

        staging_path: Path | None = None
        staging_fd = -1
        published = False
        try:
            staging_fd, staging_name = tempfile.mkstemp(
                prefix=f".{dest.name}.",
                suffix=".merge.tmp",
                dir=dest.parent,
            )
            staging_path = Path(staging_name)
            try:
                os.chmod(staging_path, 0o600)
            except OSError:
                pass

            merged_size = 0
            with os.fdopen(staging_fd, "wb", closefd=True) as output:
                staging_fd = -1
                for part_path in part_paths:
                    with open_secure_read(part_path, binary=True) as source:
                        while True:
                            chunk = source.read(8 * 1024 * 1024)
                            if not chunk:
                                break
                            merged_size += len(chunk)
                            if merged_size > expected_size:
                                raise RuntimeError(
                                    f"Merged model file exceeded expected size: {filename}"
                                )
                            output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            if merged_size != expected_size:
                raise RuntimeError(
                    f"Merged model file size mismatch for {filename}: "
                    f"{merged_size} != {expected_size}"
                )
            expected_sha256 = dict(self._bundle.file_sha256)[filename]
            if not _secure_file_matches_sha256(staging_path, expected_sha256):
                raise RuntimeError(
                    f"Merged model file failed SHA256 verification: {filename}"
                )
            atomic_replace_secure_file(
                staging_path,
                dest,
                expected_size=expected_size,
            )
            published = True
        finally:
            if staging_fd >= 0:
                os.close(staging_fd)
            if not published and staging_path is not None:
                try:
                    secure_unlink(staging_path, missing_ok=True)
                except (OSError, RuntimeError):
                    logger.debug(
                        "Could not remove merge staging file %s",
                        staging_path,
                        exc_info=True,
                    )

        for part_path in part_paths:
            secure_unlink(part_path)
        secure_unlink(self._single_partial_path(dest), missing_ok=True)
        self._update_progress(
            file_name=filename,
            file_index=file_index,
            file_count=file_count,
            file_bytes=expected_size,
            file_total=expected_size,
            total_bytes=total_bytes_so_far + expected_size,
            total_total=max(total_total, total_bytes_so_far + expected_size),
        )
        return total_bytes_so_far + expected_size

    def _probe_range(
        self,
        url: str,
        span: tuple[int, int],
        expected_total: int,
    ) -> bool:
        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        start, end = span
        probe_end = min(start + 1023, end)
        try:
            with open_validated_requests_response(
                url,
                url_validator=_is_safe_model_download_url,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                label="range probe",
                headers={"Range": f"bytes={start}-{probe_end}"},
                stream=True,
                max_redirects=5,
                request_get=session.get,
            ) as response:
                if response.status_code != 206:
                    return False
                total = _validate_content_range(
                    response,
                    requested_start=start,
                    requested_end=probe_end,
                    expected_total=expected_total,
                    label="range probe",
                )
                if total > _MAX_BUNDLE_FILE_BYTES:
                    raise RuntimeError("Range probe reported an oversized model file")
                return True
        except Exception:
            logger.debug("Range probe failed for %s", url, exc_info=True)
            return False

    def _download_segment(
        self,
        *,
        url: str,
        part_path: Path,
        span: tuple[int, int],
        total_size: int,
        on_chunk: Callable[[int], None],
    ) -> None:
        start, end = span
        part_path = secure_file_path(part_path)
        segment_size = end - start + 1
        existing = secure_file_size(part_path, missing_ok=True)
        if existing > segment_size:
            raise RuntimeError(
                f"Parallel part exceeds expected size: {part_path.name}"
            )
        if existing == segment_size:
            return

        segment_start = start + existing
        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        response_context = open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            label=part_path.name,
            headers={"Range": f"bytes={segment_start}-{end}"},
            stream=True,
            max_redirects=5,
            request_get=session.get,
        )
        response = response_context.__enter__()
        try:
            response.raise_for_status()
            if response.status_code != 206:
                raise _RangeNotSupported()
            response_total = _validate_content_range(
                response,
                requested_start=segment_start,
                requested_end=end,
                expected_total=total_size,
                label=part_path.name,
            )
            if response_total != total_size or response_total > _MAX_BUNDLE_FILE_BYTES:
                raise RuntimeError(
                    f"Range response reported an invalid total: {part_path.name}"
                )

            written = existing
            with open_secure_append(part_path, binary=True) as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        handle.flush()
                        os.fsync(handle.fileno())
                        return
                    if not chunk:
                        continue
                    next_size = written + len(chunk)
                    if next_size > segment_size:
                        raise RuntimeError(
                            "Range response exceeded the requested segment: "
                            f"{part_path.name}"
                        )
                    handle.write(chunk)
                    written = next_size
                    on_chunk(len(chunk))
                handle.flush()
                os.fsync(handle.fileno())

            if written != segment_size:
                raise RuntimeError(
                    f"Range response was truncated for {part_path.name}: "
                    f"{written} != {segment_size}"
                )
        finally:
            response_context.__exit__(None, None, None)


_downloaders: weakref.WeakValueDictionary[str, HololiveStyleBertDownloader] = (
    weakref.WeakValueDictionary()
)
_downloaders_lock = threading.Lock()


def get_hololive_downloader(model_path: str) -> HololiveStyleBertDownloader:
    bundle = _validated_bundle(model_path)
    with _downloaders_lock:
        downloader = _downloaders.get(bundle.model_path)
        if downloader is None:
            downloader = HololiveStyleBertDownloader(bundle.model_path)
            _downloaders[bundle.model_path] = downloader
        return downloader
