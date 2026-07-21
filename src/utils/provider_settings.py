# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Qt-independent provider settings definitions and validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
import math


PROVIDER_CHOICES: tuple[tuple[str, str], ...] = (
    ("provider_openai_official", "openai"),
    ("provider_openai_compatible", "openai_compatible"),
    ("provider_anthropic_official", "anthropic"),
    ("provider_anthropic_compatible", "anthropic_compatible"),
    ("provider_deepseek", "deepseek"),
    ("provider_gemini", "gemini"),
    ("provider_qwen", "qianwen"),
    ("provider_xai_official", "xai"),
    ("provider_grok_compatible", "grok_compatible"),
)

AI_PROVIDER_BACKENDS: tuple[str, ...] = (
    "openai",
    "openai_compatible",
    "anthropic",
    "anthropic_compatible",
    "xai",
    "grok_compatible",
)


def preserve_provider_id(value: object) -> str:
    """Return a provider id without migrating unknown ids to OpenAI."""

    text = str(value or "").strip()
    if text == "qwen":
        return "qianwen"
    return text or "openai"


def _provider_api_key(translation: Mapping[str, object], provider: str) -> str:
    provider_cfg = translation.get(provider)
    if provider == "qianwen" and not isinstance(provider_cfg, Mapping):
        provider_cfg = translation.get("qwen")
    if not isinstance(provider_cfg, Mapping):
        return ""
    return str(provider_cfg.get("api_key", "") or "").strip()


def resolve_tabbed_provider_authority(
    original_translation: object,
    api_models_translation: object,
    quick_provider: object,
    quick_api_key: object,
) -> tuple[str, str]:
    """Resolve conflicting edits from API Models and Quick Setup.

    API Models owns a provider selection when it changed from the original
    configuration. Otherwise Quick Setup may change the provider. For the
    selected provider, an API Models key edit (including clearing the key)
    wins; only an unchanged API Models key may be replaced by a changed Quick
    Setup key.
    """

    original = (
        original_translation
        if isinstance(original_translation, Mapping)
        else {}
    )
    api_models = (
        api_models_translation
        if isinstance(api_models_translation, Mapping)
        else {}
    )
    original_provider = preserve_provider_id(original.get("backend", "openai"))
    api_provider = preserve_provider_id(
        api_models.get("backend", original_provider)
    )
    quick_provider_id = preserve_provider_id(quick_provider)

    if api_provider != original_provider:
        selected_provider = api_provider
    elif quick_provider_id != original_provider:
        selected_provider = quick_provider_id
    else:
        selected_provider = original_provider

    original_key = _provider_api_key(original, selected_provider)
    api_key = _provider_api_key(api_models, selected_provider)
    quick_key = str(quick_api_key or "").strip()
    if api_key != original_key:
        selected_key = api_key
    elif quick_provider_id == selected_provider and quick_key != original_key:
        selected_key = quick_key
    else:
        selected_key = api_key

    return selected_provider, selected_key


def parse_optional_timeout(
    value: object,
    *,
    minimum: float,
    maximum: float,
    optional: bool,
) -> float | None:
    """Parse a timeout while retaining blank optional overrides as ``None``."""

    text = str(value or "").strip()
    if not text and optional:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError
    return parsed
