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

OPENAI_COMPATIBLE_BACKENDS = {
    "openai",
    "deepseek",
    "zhipu",
    "qianwen",
    "gemini",
    "kimi",
    "xai",
    "mistral",
    "doubao",
}


def _parse_glossary_entries(raw_text: object) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    parts = []
    for chunk in text.replace("\r", "\n").replace(";", "\n").split("\n"):
        entry = chunk.strip()
        if entry:
            parts.append(entry)
    return parts


def _translation_prompt_profile(trans_cfg: Mapping[str, object]) -> dict[str, object]:
    social_cfg = trans_cfg.get("social", {})
    if not isinstance(social_cfg, Mapping):
        social_cfg = {}
    return {
        "mode": str(social_cfg.get("mode", "standard")).strip() or "standard",
        "politeness": str(social_cfg.get("politeness", "neutral")).strip() or "neutral",
        "tone": str(social_cfg.get("tone", "natural")).strip() or "natural",
        "persona_name": str(social_cfg.get("persona_name", "")).strip(),
        "persona_prompt": str(social_cfg.get("persona_prompt", "")).strip(),
        "glossary": _parse_glossary_entries(social_cfg.get("persona_glossary", "")),
    }


def _require_text(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is not configured")
    return text


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
    label = get_backend_label(backend)
    api_key = _require_text(backend_cfg.get("api_key", ""), f"{label} API Key")
    model = _require_text(
        get_backend_config_value(trans_cfg, backend, "model"),
        f"{label} Model",
    )
    return OpenAITranslator(
        api_key=api_key,
        model=model,
        base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
        timeout_s=float(spec.get("timeout_s", 15.0)),
        max_output_tokens=int(spec.get("max_output_tokens", 192)),
        max_retries=int(spec.get("max_retries", 0)),
        extra_body=dict(spec.get("extra_body", {})),
        prompt_profile=_translation_prompt_profile(trans_cfg),
    )


def create_translator(config: dict) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    backend = normalize_backend(trans_cfg.get("backend", DEFAULT_BACKEND))

    if backend in OPENAI_COMPATIBLE_BACKENDS:
        return _create_openai_compatible_translator(trans_cfg, backend)

    if backend == "anthropic":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        api_key = _require_text(backend_cfg.get("api_key", ""), f"{get_backend_label(backend)} API Key")
        model = _require_text(
            get_backend_config_value(trans_cfg, backend, "model"),
            f"{get_backend_label(backend)} Model",
        )
        return AnthropicTranslator(
            api_key=api_key,
            model=model,
            timeout_s=float(spec.get("timeout_s", 15.0)),
            max_output_tokens=int(spec.get("max_output_tokens", 192)),
            prompt_profile=_translation_prompt_profile(trans_cfg),
        )

    raise ValueError(f"Unknown translation backend: {backend}")
