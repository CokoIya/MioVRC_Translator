from __future__ import annotations

from src.asr.model_manager import (
    bundled_model_dirs as _bundled_model_dirs,
    cache_dir,
    download_model_to,
    download_model,
    existing_model_path,
    model_dir as _model_dir,
    model_exists as _model_exists,
    resolve_model_path as _resolve_model_path,
)
from src.asr.model_registry import get_asr_engine_spec

_SPEC = get_asr_engine_spec("sensevoice-small")

MODEL_ID = _SPEC.model_id
MODEL_REVISION = _SPEC.model_revision
BUNDLED_DIR_NAME = _SPEC.bundled_dir_names[0]


def bundled_model_dirs(model_id: str = MODEL_ID):
    del model_id
    return _bundled_model_dirs(_SPEC)


def model_dir(model_id: str = MODEL_ID):
    return _model_dir(_resolve_spec(model_id))


def packaging_models_dir():
    from src.utils.app_paths import project_root

    return project_root() / "models"


def packaging_model_dir(model_id: str = MODEL_ID):
    del model_id
    return packaging_models_dir() / BUNDLED_DIR_NAME


def ensure_model(
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    progress_callback=None,
):
    spec = _resolve_spec(model_id, model_revision)
    return download_model(spec, progress_callback=progress_callback)


def ensure_packaging_model(
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    progress_callback=None,
):
    spec = _resolve_spec(model_id, model_revision)
    return download_model_to(
        spec,
        packaging_model_dir(model_id),
        progress_callback=progress_callback,
    )


def _resolve_spec(model_id: str, model_revision: str = MODEL_REVISION):
    required_file_sha256 = (
        _SPEC.required_file_sha256
        if model_id == _SPEC.model_id
        else ()
    )
    return _SPEC.__class__(
        engine=_SPEC.engine,
        label=_SPEC.label,
        config_key=_SPEC.config_key,
        model_id=model_id,
        model_revision=model_revision,
        bundled_dir_names=_SPEC.bundled_dir_names,
        required_files=_SPEC.required_files,
        required_file_sha256=required_file_sha256,
    )


def _existing_model_path(model_id: str = MODEL_ID):
    return existing_model_path(_resolve_spec(model_id))


def model_exists(model_id: str = MODEL_ID) -> bool:
    return _model_exists(_resolve_spec(model_id))


def resolve_model_path(model_id: str = MODEL_ID) -> str:
    return _resolve_model_path(_resolve_spec(model_id))
