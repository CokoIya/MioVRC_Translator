from __future__ import annotations

from collections.abc import Mapping
from typing import Callable
import logging

from .anthropic_translator import AnthropicTranslator
from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from src.utils.config_manager import is_protected_secret_blob
from src.utils.ui_config import (
    DEFAULT_BACKEND,
    get_backend_order,
    get_backend_config_value,
    get_backend_label,
    get_backend_spec,
    normalize_backend,
)

logger = logging.getLogger(__name__)

OPENAI_COMPATIBLE_BACKENDS = {
    "openai",
    "local_ai",
    "deepseek",
    "zhipu",
    "qianwen",
    "xiaomi",
    "gemini",
    "kimi",
    "xai",
    "mistral",
    "doubao",
    "nvidia",
}


class FallbackTranslator(BaseTranslator):
    def __init__(
        self,
        primary: BaseTranslator,
        fallback_factories: list[tuple[str, Callable[[], BaseTranslator]]],
    ):
        super().__init__()
        self._primary = primary
        self._fallback_factories = list(fallback_factories)
        self._fallbacks: dict[str, BaseTranslator] = {}

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        try:
            return self._primary.translate(
                text,
                src_lang,
                tgt_lang,
                context_source=context_source,
            )
        except Exception as primary_exc:
            logger.warning(
                "Primary translation backend failed; trying fallbacks: %s",
                primary_exc,
            )
            for backend, factory in self._fallback_factories:
                try:
                    translator = self._fallbacks.get(backend)
                    if translator is None:
                        translator = factory()
                        self._fallbacks[backend] = translator
                    return translator.translate(
                        text,
                        src_lang,
                        tgt_lang,
                        context_source=context_source,
                    )
                except Exception as fallback_exc:
                    logger.warning(
                        "Fallback translation backend failed (backend=%s): %s",
                        backend,
                        fallback_exc,
                    )
            raise primary_exc


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
    social_mode = str(social_cfg.get("mode", "standard")).strip() or "standard"
    if social_mode in {"", "standard"}:
        return {}
    if social_mode not in {"language_exchange", "roleplay"}:
        return {}

    persona_name = ""
    persona_prompt = ""
    glossary: list[str] = []
    if social_mode == "roleplay":
        persona_name = str(social_cfg.get("persona_name", "")).strip()
        persona_prompt = str(social_cfg.get("persona_prompt", "")).strip()
        glossary = _parse_glossary_entries(social_cfg.get("persona_glossary", ""))

    return {
        "mode": social_mode,
        "politeness": str(social_cfg.get("politeness", "neutral")).strip() or "neutral",
        "tone": str(social_cfg.get("tone", "natural")).strip() or "natural",
        "persona_name": persona_name,
        "persona_prompt": persona_prompt,
        "glossary": glossary,
    }


def _require_text(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is not configured")
    if is_protected_secret_blob(text):
        raise ValueError(
            f"{label} is stored as DPAPI ciphertext but could not be decrypted "
            "on this host. Please re-enter the value in Settings."
        )
    return text


def _float_setting(value: object, default: object, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        try:
            parsed = float(default)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _int_setting(value: object, default: object, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(default)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _backend_cfg(trans_cfg: Mapping[str, object], backend: str) -> Mapping[str, object]:
    backend_cfg = trans_cfg.get(backend, {})
    if isinstance(backend_cfg, Mapping):
        return backend_cfg
    return {}


def _fallback_backends(trans_cfg: Mapping[str, object], primary_backend: str) -> list[str]:
    raw = trans_cfg.get("fallback_backends", ())
    if isinstance(raw, str):
        candidates = [item.strip() for item in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item).strip() for item in raw]
    else:
        candidates = []

    valid_backends = set(get_backend_order())
    seen = {primary_backend}
    result: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            backend = normalize_backend(candidate)
        except Exception:
            continue
        if backend in seen or backend not in valid_backends:
            continue
        seen.add(backend)
        result.append(backend)
    return result


def _create_openai_compatible_translator(
    trans_cfg: Mapping[str, object],
    backend: str,
) -> OpenAITranslator:
    spec = get_backend_spec(backend)
    backend_cfg = _backend_cfg(trans_cfg, backend)
    label = get_backend_label(backend)
    api_key_required = bool(spec.get("api_key_required", True))
    api_key = str(backend_cfg.get("api_key", "")).strip()
    if api_key_required:
        api_key = _require_text(api_key, f"{label} API Key")
    else:
        api_key = api_key or "local-ai"
    model = _require_text(
        get_backend_config_value(trans_cfg, backend, "model"),
        f"{label} Model",
    )
    return OpenAITranslator(
        api_key=api_key,
        model=model,
        base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
        timeout_s=_float_setting(
            backend_cfg.get("timeout_s"),
            spec.get("timeout_s", 15.0),
            minimum=3.0,
            maximum=120.0,
        ),
        max_output_tokens=_int_setting(
            backend_cfg.get("max_output_tokens"),
            spec.get("max_output_tokens", 192),
            minimum=48,
            maximum=4096,
        ),
        max_retries=_int_setting(
            backend_cfg.get("max_retries"),
            spec.get("max_retries", 0),
            minimum=0,
            maximum=3,
        ),
        extra_body=dict(spec.get("extra_body", {})),
        prefer_max_completion_tokens=bool(
            spec.get("prefer_max_completion_tokens", False)
        ),
        prompt_profile=_translation_prompt_profile(trans_cfg),
    )


def _create_translator_for_backend(
    trans_cfg: Mapping[str, object],
    backend: str,
) -> BaseTranslator:
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


def create_translator(config: dict) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    if not isinstance(trans_cfg, Mapping):
        trans_cfg = {}
    backend = normalize_backend(trans_cfg.get("backend", DEFAULT_BACKEND))
    primary = _create_translator_for_backend(trans_cfg, backend)
    fallback_backends = _fallback_backends(trans_cfg, backend)
    if not fallback_backends:
        return primary

    factories: list[tuple[str, Callable[[], BaseTranslator]]] = []
    for fallback_backend in fallback_backends:
        factories.append(
            (
                fallback_backend,
                lambda fallback_backend=fallback_backend: _create_translator_for_backend(
                    trans_cfg,
                    fallback_backend,
                ),
            )
        )
    return FallbackTranslator(primary, factories)
