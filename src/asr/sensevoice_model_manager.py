from __future__ import annotations

import os
import pathlib
import threading
from collections.abc import Callable

from src.utils.app_paths import project_root, resource_base_dirs, writable_app_dir

MODEL_ID = "iic/SenseVoiceSmall"
MODEL_REVISION = "master"
BUNDLED_DIR_NAME = "sensevoice-small"
_DOWNLOAD_LOCK = threading.Lock()

ProgressCallback = Callable[[dict[str, object]], None]


def _model_slug(model_id: str) -> str:
    return model_id.replace("/", "--")


def bundled_model_dirs(model_id: str = MODEL_ID) -> list[pathlib.Path]:
    slug = _model_slug(model_id)
    dirs: list[pathlib.Path] = []
    for base in resource_base_dirs():
        dirs.append(base / "models" / BUNDLED_DIR_NAME)
        dirs.append(base / "models" / slug)
    return dirs


def packaging_models_dir() -> pathlib.Path:
    return project_root() / "models"


def packaging_model_dir(model_id: str = MODEL_ID) -> pathlib.Path:
    del model_id
    return packaging_models_dir() / BUNDLED_DIR_NAME


def model_dir(model_id: str = MODEL_ID) -> pathlib.Path:
    override = os.environ.get("MIO_TRANSLATOR_SENSEVOICE_MODEL_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    return writable_app_dir() / "runtime_models" / _model_slug(model_id)


def cache_dir() -> pathlib.Path:
    override = os.environ.get("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    return writable_app_dir() / "runtime_cache" / "modelscope"


def _is_complete_model_dir(path: pathlib.Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "configuration.json").exists():
        return True
    return (path / "config.yaml").exists() and (path / "model.pt").exists()


def _existing_model_path(model_id: str = MODEL_ID) -> pathlib.Path | None:
    for bundled_dir in bundled_model_dirs(model_id):
        if _is_complete_model_dir(bundled_dir):
            return bundled_dir

    downloaded = model_dir(model_id)
    if _is_complete_model_dir(downloaded):
        return downloaded

    return None


def model_exists(model_id: str = MODEL_ID) -> bool:
    return _existing_model_path(model_id) is not None


def resolve_model_path(model_id: str = MODEL_ID) -> str:
    path = _existing_model_path(model_id)
    if path is not None:
        return str(path)
    raise FileNotFoundError(
        f"SenseVoiceSmall model not found. It will be downloaded to: {model_dir(model_id)}"
    )


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


def _try_remote_model_size(model_id: str, model_revision: str) -> int | None:
    try:
        from modelscope.hub.api import HubApi
    except ImportError:
        return None

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
        revision = revision_detail["Revision"]
        repo_files = api.get_model_files(
            model_id=model_id,
            revision=revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            headers={"Snapshot": "True"},
            endpoint=endpoint,
        )
    except Exception:
        return None

    total_size = 0
    for repo_file in repo_files:
        if repo_file.get("Type") == "tree":
            continue
        total_size += int(repo_file.get("Size") or 0)
    return total_size or None


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
    target_dir: pathlib.Path,
    model_id: str,
    model_revision: str,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    try:
        from modelscope.hub.callback import ProgressCallback as ModelscopeProgressCallback
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "modelscope is required to download the SenseVoiceSmall model."
        ) from exc

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    modelscope_cache = cache_dir()
    modelscope_cache.mkdir(parents=True, exist_ok=True)

    total_bytes = _try_remote_model_size(model_id, model_revision)
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
        snapshot_download(
            model_id,
            revision=model_revision,
            cache_dir=str(modelscope_cache),
            local_dir=str(target_dir),
            enable_file_lock=True,
            progress_callbacks=[_ModelscopeProgress],
        )
    except Exception as exc:
        raise RuntimeError(f"SenseVoiceSmall model download failed: {exc}") from exc

    if not _is_complete_model_dir(target_dir):
        raise RuntimeError(
            f"SenseVoiceSmall model download finished but the directory is incomplete: {target_dir}"
        )

    tracker.finish()
    return target_dir


def ensure_model(
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    existing = _existing_model_path(model_id)
    if existing is not None:
        return existing

    # 加锁后二次检查，防止多线程同时触发下载
    with _DOWNLOAD_LOCK:
        existing = _existing_model_path(model_id)
        if existing is not None:
            return existing
        return _download_model_to(
            model_dir(model_id),
            model_id=model_id,
            model_revision=model_revision,
            progress_callback=progress_callback,
        )


def ensure_packaging_model(
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    target = packaging_model_dir(model_id)
    if _is_complete_model_dir(target):
        return target

    with _DOWNLOAD_LOCK:
        if _is_complete_model_dir(target):
            return target
        return _download_model_to(
            target,
            model_id=model_id,
            model_revision=model_revision,
            progress_callback=progress_callback,
        )
