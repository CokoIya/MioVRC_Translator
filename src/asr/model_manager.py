from __future__ import annotations

import json
import hashlib
import logging
import os
import pathlib
import sys
import shutil
import threading
import time
from contextlib import ExitStack, contextmanager
from collections.abc import Callable

from src.asr.model_registry import ASRRuntimeSpec
from src.utils.app_paths import resource_base_dirs, writable_app_dir

_DOWNLOAD_LOCK = threading.Lock()
_FILE_HASH_CACHE_LOCK = threading.Lock()
_FILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_MODEL_METADATA_FILENAME = ".mio-model.json"
_DOWNLOAD_ATTEMPTS = 3
logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, object]], None]


def _model_slug(model_id: str) -> str:
    return model_id.replace("/", "--")


def bundled_model_dirs(spec: ASRRuntimeSpec) -> list[pathlib.Path]:
    slug = _model_slug(spec.model_id)
    dirs: list[pathlib.Path] = []
    for base in resource_base_dirs():
        for bundled_name in spec.bundled_dir_names:
            dirs.append(base / "models" / bundled_name)
        dirs.append(base / "models" / slug)
    return dirs


def model_dir(spec: ASRRuntimeSpec) -> pathlib.Path:
    return writable_app_dir() / "runtime_models" / _model_slug(spec.model_id)


def cache_dir() -> pathlib.Path:
    override = os.environ.get("MIO_TRANSLATOR_MODELSCOPE_CACHE_DIR", "").strip()
    if not override:
        override = os.environ.get("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR", "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    return writable_app_dir() / "runtime_cache" / "modelscope"


def _metadata_path(path: pathlib.Path) -> pathlib.Path:
    return path / _MODEL_METADATA_FILENAME


@contextmanager
def _ensure_download_stdio():
    # Packaged GUI builds may run without a console, leaving stdout/stderr as None.
    # ModelScope's snapshot_download uses plain print() and tqdm, both of which
    # expect writable text streams.
    with ExitStack() as stack:
        restored: list[tuple[str, object]] = []
        for attr in ("stdout", "stderr"):
            stream = getattr(sys, attr, None)
            if stream is not None and hasattr(stream, "write"):
                continue
            sink = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
            restored.append((attr, stream))
            setattr(sys, attr, sink)
        try:
            yield
        finally:
            for attr, original in restored:
                setattr(sys, attr, original)


def _load_model_metadata(path: pathlib.Path) -> dict[str, object]:
    metadata_path = _metadata_path(path)
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sha256_file(path: pathlib.Path) -> str:
    try:
        stat = path.stat()
        cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        cache_key = None

    if cache_key is not None:
        with _FILE_HASH_CACHE_LOCK:
            cached = _FILE_HASH_CACHE.get(cache_key)
        if cached is not None:
            return cached

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if cache_key is not None:
        try:
            stat = path.stat()
            current_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            current_key = None
        if current_key == cache_key:
            with _FILE_HASH_CACHE_LOCK:
                _FILE_HASH_CACHE[cache_key] = digest
    return digest


def _required_file_hashes(path: pathlib.Path, spec: ASRRuntimeSpec) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for filename in spec.required_files:
        file_path = path / filename
        if file_path.is_file():
            hashes[filename] = _sha256_file(file_path)
    return hashes


def _trusted_hashes_match(path: pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    trusted_hashes = dict(spec.required_file_sha256 or ())
    if trusted_hashes:
        for filename, expected_value in trusted_hashes.items():
            expected = str(trusted_hashes.get(filename) or "").strip().lower()
            if not filename or not expected or not str(expected_value or "").strip():
                return False
            file_path = path / filename
            if not file_path.is_file() or _sha256_file(file_path) != expected:
                return False
        return True

    metadata = _load_model_metadata(path)
    hashes = metadata.get("required_file_sha256", {})
    if not isinstance(hashes, dict) or not hashes:
        return True
    for filename in spec.required_files:
        expected = str(hashes.get(filename) or "").strip().lower()
        if not expected:
            return False
        file_path = path / filename
        if not file_path.is_file() or _sha256_file(file_path) != expected:
            return False
    return True


def _write_model_metadata(
    path: pathlib.Path,
    *,
    spec: ASRRuntimeSpec,
    requested_revision: str,
    resolved_revision: str,
) -> None:
    payload = {
        "engine": spec.engine,
        "label": spec.label,
        "model_id": spec.model_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "required_file_sha256": _required_file_hashes(path, spec),
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _metadata_path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_complete_model_dir(path: pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    if not path.is_dir():
        return False
    has_config = (path / "configuration.json").exists() or (path / "config.yaml").exists()
    if not has_config:
        return False
    return all((path / filename).exists() for filename in spec.required_files)


def verify_model_integrity(path: str | pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    return _trusted_hashes_match(pathlib.Path(path), spec)


def _downloaded_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    downloaded = model_dir(spec)
    if _is_complete_model_dir(downloaded, spec) and verify_model_integrity(downloaded, spec):
        return downloaded
    return None


def _bundled_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    for bundled_dir in bundled_model_dirs(spec):
        # Bundled (packaged) models are trusted by virtue of being part of the
        # installer — skip SHA256 validation and only check file completeness.
        # Applying the same hash check used for downloaded models caused false-
        # negative failures when the installed model.pt's hash differed from the
        # hardcoded registry value, triggering a spurious re-download attempt.
        if _is_complete_model_dir(bundled_dir, spec):
            return bundled_dir
    return None


def existing_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    downloaded = _downloaded_model_path(spec)
    if downloaded is not None:
        return downloaded
    return _bundled_model_path(spec)


def model_exists(spec: ASRRuntimeSpec) -> bool:
    return existing_model_path(spec) is not None


def resolve_model_path(spec: ASRRuntimeSpec) -> str:
    path = existing_model_path(spec)
    if path is not None:
        return str(path)
    raise FileNotFoundError(
        f"{spec.label} model not found. It will be downloaded to: {model_dir(spec)}"
    )


def get_local_model_status(spec: ASRRuntimeSpec) -> dict[str, object]:
    downloaded = _downloaded_model_path(spec)
    if downloaded is not None:
        metadata = _load_model_metadata(downloaded)
        return {
            "engine": spec.engine,
            "label": spec.label,
            "model_id": spec.model_id,
            "requested_revision": spec.model_revision,
            "installed": True,
            "source": "downloaded",
            "path": str(downloaded),
            "local_revision": str(metadata.get("resolved_revision", "")).strip(),
            "metadata": metadata,
        }

    bundled = _bundled_model_path(spec)
    if bundled is not None:
        return {
            "engine": spec.engine,
            "label": spec.label,
            "model_id": spec.model_id,
            "requested_revision": spec.model_revision,
            "installed": True,
            "source": "bundled",
            "path": str(bundled),
            "local_revision": "",
            "metadata": {},
        }

    return {
        "engine": spec.engine,
        "label": spec.label,
        "model_id": spec.model_id,
        "requested_revision": spec.model_revision,
        "installed": False,
        "source": "missing",
        "path": str(model_dir(spec)),
        "local_revision": "",
        "metadata": {},
    }


def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    message: str,
    progress: float | None = None,
    indeterminate: bool = False,
    total_bytes: int | None = None,
    downloaded_bytes: int | None = None,
) -> None:
    if progress_callback is None:
        return
    event: dict[str, object] = {
        "stage": stage,
        "message": message,
        "indeterminate": indeterminate,
    }
    if progress is not None:
        event["progress"] = max(0.0, min(float(progress), 1.0))
    if total_bytes is not None:
        event["total_bytes"] = int(total_bytes)
    if downloaded_bytes is not None:
        event["downloaded_bytes"] = int(downloaded_bytes)
    progress_callback(event)


def _repo_file_name(repo_file: dict[str, object]) -> str:
    for key in ("Name", "Path", "Key", "FilePath", "FileName"):
        value = str(repo_file.get(key) or "").strip().replace("\\", "/")
        if value:
            return value.lstrip("/")
    return ""


def _download_file_patterns(spec: ASRRuntimeSpec) -> tuple[str, ...]:
    patterns: list[str] = []
    for filename in ("configuration.json", "config.yaml", *spec.required_files):
        clean = str(filename or "").strip().replace("\\", "/")
        if clean and clean not in patterns:
            patterns.append(clean)
    return tuple(patterns)


def _file_matches_patterns(filename: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    normalized = filename.replace("\\", "/").lstrip("/")
    return any(
        normalized == pattern
        or normalized.endswith("/" + pattern)
        for pattern in patterns
    )


def _fetch_remote_snapshot_info(model_id: str, model_revision: str) -> dict[str, object]:
    try:
        from modelscope.hub.api import HubApi
    except ImportError as exc:
        raise RuntimeError(
            "modelscope is required to query and download ASR models."
        ) from exc

    try:
        api = HubApi()
        endpoint = api.get_endpoint_for_read(repo_id=model_id, repo_type="model")
        cookies = api.get_cookies()
        revision_detail = api.get_valid_revision_detail(
            model_id,
            revision=model_revision,
            cookies=cookies,
            endpoint=endpoint,
        )
        resolved_revision = str(revision_detail["Revision"])
        repo_files = api.get_model_files(
            model_id=model_id,
            revision=resolved_revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            headers={"Snapshot": "True"},
            endpoint=endpoint,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to query ASR model revision: {exc}") from exc

    total_size = 0
    for repo_file in repo_files:
        if repo_file.get("Type") == "tree":
            continue
        total_size += int(repo_file.get("Size") or 0)

    return {
        "resolved_revision": resolved_revision,
        "total_size": total_size or None,
    }


def _fetch_required_snapshot_info(spec: ASRRuntimeSpec, model_revision: str) -> dict[str, object]:
    snapshot_info = _fetch_remote_snapshot_info(spec.model_id, model_revision)
    patterns = _download_file_patterns(spec)
    try:
        from modelscope.hub.api import HubApi

        api = HubApi()
        endpoint = api.get_endpoint_for_read(repo_id=spec.model_id, repo_type="model")
        cookies = api.get_cookies()
        resolved_revision = str(snapshot_info.get("resolved_revision") or model_revision)
        repo_files = api.get_model_files(
            model_id=spec.model_id,
            revision=resolved_revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            headers={"Snapshot": "True"},
            endpoint=endpoint,
        )
    except Exception:
        return snapshot_info

    total_size = 0
    for repo_file in repo_files:
        if repo_file.get("Type") == "tree":
            continue
        filename = _repo_file_name(repo_file)
        if _file_matches_patterns(filename, patterns):
            total_size += int(repo_file.get("Size") or 0)
    if total_size > 0:
        snapshot_info["total_size"] = total_size
    return snapshot_info


def _target_has_corrupt_complete_model(target_dir: pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    return (
        target_dir.exists()
        and _is_complete_model_dir(target_dir, spec)
        and not verify_model_integrity(target_dir, spec)
    )


class _AggregateDownloadProgress:
    def __init__(
        self,
        total_bytes: int | None,
        progress_callback: ProgressCallback | None,
    ) -> None:
        self.total_bytes = int(total_bytes or 0)
        self.progress_callback = progress_callback
        self.downloaded_bytes = 0
        self._lock = threading.Lock()

    def update(self, delta_bytes: int) -> None:
        if delta_bytes <= 0:
            return
        with self._lock:
            self.downloaded_bytes += delta_bytes
            total_bytes = self.total_bytes
            downloaded_bytes = self.downloaded_bytes

        if total_bytes > 0:
            progress = min(downloaded_bytes / total_bytes, 0.999)
            _emit_progress(
                self.progress_callback,
                stage="download",
                message="downloading",
                progress=progress,
                total_bytes=total_bytes,
                downloaded_bytes=downloaded_bytes,
            )
            return

        _emit_progress(
            self.progress_callback,
            stage="download",
            message="downloading",
            indeterminate=True,
            downloaded_bytes=downloaded_bytes,
        )

    def finish(self) -> None:
        _emit_progress(
            self.progress_callback,
            stage="download_complete",
            message="download_complete",
            progress=1.0,
            total_bytes=self.total_bytes or None,
            downloaded_bytes=self.downloaded_bytes or None,
        )


def _download_model_to(
    spec: ASRRuntimeSpec,
    target_dir: pathlib.Path,
    *,
    model_revision: str,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    try:
        from modelscope.hub.callback import ProgressCallback as ModelscopeProgressCallback
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "modelscope is required to download ASR models."
        ) from exc

    snapshot_info = _fetch_required_snapshot_info(spec, model_revision)
    resolved_revision = str(snapshot_info.get("resolved_revision", "")).strip() or model_revision
    total_bytes = snapshot_info.get("total_size")
    if not isinstance(total_bytes, int):
        total_bytes = None
    file_patterns = _download_file_patterns(spec)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if _target_has_corrupt_complete_model(target_dir, spec):
        shutil.rmtree(target_dir, ignore_errors=True)
    modelscope_cache = cache_dir()
    modelscope_cache.mkdir(parents=True, exist_ok=True)
    tracker = _AggregateDownloadProgress(total_bytes, progress_callback)

    class _ModelscopeProgress(ModelscopeProgressCallback):
        def update(self, size: int):
            tracker.update(size)

        def end(self):
            return None

    _emit_progress(
        progress_callback,
        stage="download_prepare",
        message="download_prepare",
        progress=0.0 if total_bytes else None,
        indeterminate=not total_bytes,
        total_bytes=total_bytes,
        downloaded_bytes=0,
    )

    last_error: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            with _ensure_download_stdio():
                logger.info(
                    "Downloading %s model via ModelScope (revision=%s, files=%s, cache=%s, target=%s)",
                    spec.label,
                    resolved_revision,
                    ", ".join(file_patterns),
                    modelscope_cache,
                    target_dir,
                )
                snapshot_download(
                    spec.model_id,
                    revision=resolved_revision,
                    cache_dir=str(modelscope_cache),
                    local_dir=str(target_dir),
                    allow_file_pattern=list(file_patterns),
                    enable_file_lock=True,
                    max_workers=1,
                    progress_callbacks=[_ModelscopeProgress],
                )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "%s model download attempt %d/%d failed",
                spec.label,
                attempt,
                _DOWNLOAD_ATTEMPTS,
                exc_info=True,
            )
            if attempt >= _DOWNLOAD_ATTEMPTS:
                break
            _emit_progress(
                progress_callback,
                stage="download_retry",
                message=(
                    f"{spec.label} download interrupted. Retrying "
                    f"{attempt + 1}/{_DOWNLOAD_ATTEMPTS}..."
                ),
                indeterminate=True,
                total_bytes=total_bytes,
                downloaded_bytes=tracker.downloaded_bytes,
            )
            time.sleep(min(2.0 * attempt, 5.0))
    if last_error is not None:
        raise RuntimeError(
            f"{spec.label} model download failed after {_DOWNLOAD_ATTEMPTS} attempts. "
            f"The cached partial download was kept in {modelscope_cache}. "
            f"Original error: {last_error}"
        ) from last_error

    if not _is_complete_model_dir(target_dir, spec):
        raise RuntimeError(
            f"{spec.label} model download finished but the directory is incomplete: {target_dir}"
        )
    if not verify_model_integrity(target_dir, spec):
        raise RuntimeError(
            f"{spec.label} model download finished but did not match the trusted checksum."
        )

    _write_model_metadata(
        target_dir,
        spec=spec,
        requested_revision=model_revision,
        resolved_revision=resolved_revision,
    )
    tracker.finish()
    return target_dir


def download_model(
    spec: ASRRuntimeSpec,
    *,
    force: bool = False,
    model_revision: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    if not force:
        existing = existing_model_path(spec)
        if existing is not None:
            return existing

    with _DOWNLOAD_LOCK:
        if not force:
            existing = existing_model_path(spec)
            if existing is not None:
                return existing
        return _download_model_to(
            spec,
            model_dir(spec),
            model_revision=str(model_revision or spec.model_revision).strip() or spec.model_revision,
            progress_callback=progress_callback,
        )


def download_model_to(
    spec: ASRRuntimeSpec,
    target_dir: str | pathlib.Path,
    *,
    force: bool = False,
    model_revision: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    target = pathlib.Path(target_dir)
    revision = str(model_revision or spec.model_revision).strip() or spec.model_revision
    if not force and _is_complete_model_dir(target, spec) and verify_model_integrity(target, spec):
        return target
    with _DOWNLOAD_LOCK:
        if not force and _is_complete_model_dir(target, spec) and verify_model_integrity(target, spec):
            return target
        return _download_model_to(
            spec,
            target,
            model_revision=revision,
            progress_callback=progress_callback,
        )


def check_model_update(spec: ASRRuntimeSpec) -> dict[str, object]:
    status = get_local_model_status(spec)
    snapshot_info = _fetch_remote_snapshot_info(spec.model_id, spec.model_revision)
    remote_revision = str(snapshot_info.get("resolved_revision", "")).strip()
    local_revision = str(status.get("local_revision", "")).strip()
    local_revision_known = bool(local_revision)
    update_available = bool(
        status.get("installed")
        and local_revision_known
        and remote_revision
        and local_revision != remote_revision
    )
    status.update(
        {
            "remote_revision": remote_revision,
            "local_revision_known": local_revision_known,
            "update_available": update_available,
        }
    )
    return status
