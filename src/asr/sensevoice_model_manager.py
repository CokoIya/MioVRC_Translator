"""SenseVoiceSmall の同梱配置と初回ダウンロードを管理する  """

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

ProgressCallback = Callable[[str], None]


def _model_slug(model_id: str) -> str:
    return model_id.replace("/", "--")


def bundled_model_dirs(model_id: str = MODEL_ID) -> list[pathlib.Path]:
    slug = _model_slug(model_id)
    return [
        base / "models" / BUNDLED_DIR_NAME
        for base in resource_base_dirs()
    ] + [
        base / "models" / slug
        for base in resource_base_dirs()
    ]


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
        f"未找到可用的 SenseVoiceSmall 模型。  首次使用时会自动下载到：{model_dir(model_id)}"
    )


def _download_model_to(
    target_dir: pathlib.Path,
    model_id: str,
    model_revision: str,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "缺少 modelscope 依赖，无法下载 SenseVoiceSmall 模型。"
        ) from exc

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    modelscope_cache = cache_dir()
    modelscope_cache.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("正在下载 SenseVoiceSmall 模型…")

    try:
        snapshot_download(
            model_id,
            revision=model_revision,
            cache_dir=str(modelscope_cache),
            local_dir=str(target_dir),
            enable_file_lock=True,
        )
    except Exception as exc:
        raise RuntimeError(f"SenseVoiceSmall 模型下载失败：{exc}") from exc

    if not _is_complete_model_dir(target_dir):
        raise RuntimeError(f"SenseVoiceSmall 模型下载完成后仍不完整：{target_dir}")

    if progress_callback:
        progress_callback("SenseVoiceSmall 模型下载完成")

    return target_dir


def ensure_model(
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    existing = _existing_model_path(model_id)
    if existing is not None:
        return existing

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
