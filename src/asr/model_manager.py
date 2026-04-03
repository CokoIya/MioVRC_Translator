from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from collections.abc import Callable

from src.asr.model_registry import ASRRuntimeSpec
from src.utils.app_paths import resource_base_dirs, writable_app_dir

_DOWNLOAD_LOCK = threading.Lock()
_MODEL_METADATA_FILENAME = ".mio-model.json"

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
    override = os.environ.get("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR")
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


def _downloaded_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    downloaded = model_dir(spec)
    if _is_complete_model_dir(downloaded, spec):
        return downloaded
    return None


def _bundled_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    for bundled_dir in bundled_model_dirs(spec):
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

    snapshot_info = _fetch_remote_snapshot_info(spec.model_id, model_revision)
    resolved_revision = str(snapshot_info.get("resolved_revision", "")).strip() or model_revision
    total_bytes = snapshot_info.get("total_size")
    if not isinstance(total_bytes, int):
        total_bytes = None

    target_dir.parent.mkdir(parents=True, exist_ok=True)
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

    try:
        with _ensure_download_stdio():
            snapshot_download(
                spec.model_id,
                revision=resolved_revision,
                cache_dir=str(modelscope_cache),
                local_dir=str(target_dir),
                enable_file_lock=True,
                progress_callbacks=[_ModelscopeProgress],
            )
    except Exception as exc:
        raise RuntimeError(f"{spec.label} model download failed: {exc}") from exc

    if not _is_complete_model_dir(target_dir, spec):
        raise RuntimeError(
            f"{spec.label} model download finished but the directory is incomplete: {target_dir}"
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
