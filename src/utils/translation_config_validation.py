from __future__ import annotations

from collections.abc import Mapping

from src.utils.ui_config import (
    get_backend_label,
    get_backend_spec,
    normalize_backend,
    normalize_output_format,
)


def missing_required_translation_api_key(config: Mapping[str, object] | None) -> tuple[bool, str]:
    if not isinstance(config, Mapping):
        return False, ""
    trans_cfg = config.get("translation")
    if not isinstance(trans_cfg, Mapping):
        return False, ""

    output_format = normalize_output_format(trans_cfg.get("output_format"))
    if output_format == "original_only":
        return False, ""

    backend = normalize_backend(trans_cfg.get("backend"))
    spec = get_backend_spec(backend)
    if not bool(spec.get("api_key_required", True)):
        return False, ""

    backend_cfg = trans_cfg.get(backend)
    api_key = ""
    if isinstance(backend_cfg, Mapping):
        api_key = str(backend_cfg.get("api_key", "") or "").strip()
    return (not bool(api_key), get_backend_label(backend))
