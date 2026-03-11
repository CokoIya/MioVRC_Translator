from __future__ import annotations

from collections.abc import Mapping

from .anthropic_translator import AnthropicTranslator
from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from src.utils.ui_config import (
    DEFAULT_BACKEND,
    get_backend_config_value,
    get_backend_label,
    get_backend_spec,
    normalize_backend,
)


def _require_text(value: str, label: str):
    if not str(value or "").strip():
        raise ValueError(f"{label} 未设置")


def _backend_cfg(trans_cfg: Mapping[str, object], backend: str) -> Mapping[str, object]:
    backend_cfg = trans_cfg.get(backend, {})
    if isinstance(backend_cfg, Mapping):
        return backend_cfg
    return {}


def _create_openai_compatible_translator(
    trans_cfg: Mapping[str, object],
    backend: str,
) -> OpenAITranslator:
    spec = get_backend_spec(backend)
    backend_cfg = _backend_cfg(trans_cfg, backend)
    _require_text(backend_cfg.get("api_key", ""), f"{get_backend_label(backend)} API Key")
    return OpenAITranslator(
        api_key=str(backend_cfg.get("api_key", "")).strip(),
        model=get_backend_config_value(trans_cfg, backend, "model"),
        base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
        timeout_s=float(spec.get("timeout_s", 15.0)),
        max_output_tokens=int(spec.get("max_output_tokens", 192)),
        max_retries=int(spec.get("max_retries", 0)),
        extra_body=dict(spec.get("extra_body", {})),
    )


def create_translator(config: dict) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    backend = normalize_backend(trans_cfg.get("backend", DEFAULT_BACKEND))

    if backend == "openai":
        return _create_openai_compatible_translator(trans_cfg, backend)

    if backend == "deepseek":
        return _create_openai_compatible_translator(trans_cfg, backend)

    if backend == "zhipu":
        return _create_openai_compatible_translator(trans_cfg, backend)

    if backend == "qianwen":
        return _create_openai_compatible_translator(trans_cfg, backend)

    if backend == "anthropic":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        _require_text(backend_cfg.get("api_key", ""), f"{get_backend_label(backend)} API Key")
        return AnthropicTranslator(
            api_key=str(backend_cfg.get("api_key", "")).strip(),
            model=get_backend_config_value(trans_cfg, backend, "model"),
            timeout_s=float(spec.get("timeout_s", 15.0)),
            max_output_tokens=int(spec.get("max_output_tokens", 192)),
        )

    raise ValueError(f"未知翻译后端: {backend}")
