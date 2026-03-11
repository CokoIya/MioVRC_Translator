from __future__ import annotations

from collections.abc import Collection, Mapping

DEFAULT_UI_LANGUAGE = "zh-CN"

UI_LANGUAGE_OPTIONS = (
    ("简体中文", "zh-CN"),
    ("English", "en"),
    ("日本語", "ja"),
    ("Русский", "ru"),
    ("한국어", "ko"),
)

TARGET_LANGUAGE_OPTIONS = (
    ("日本語 (ja)", "ja"),
    ("English (en)", "en"),
    ("中文 (zh)", "zh"),
    ("한국어 (ko)", "ko"),
    ("Русский (ru)", "ru"),
    ("Français (fr)", "fr"),
    ("Deutsch (de)", "de"),
    ("Español (es)", "es"),
)

MANUAL_SOURCE_LANGUAGE_OPTIONS = (
    ("Auto", "auto"),
    ("中文", "zh"),
    ("日本語", "ja"),
    ("English", "en"),
    ("한국어", "ko"),
    ("Русский", "ru"),
)

OUTPUT_FORMAT_OPTIONS = (
    ("译文（原文）", "translated_with_original"),
    ("仅译文", "translated_only"),
    ("仅原文", "original_only"),
    ("原文（译文）", "original_with_translated"),
)

LEGACY_OUTPUT_FORMAT_ALIASES = {
    "ja(zh)": "translated_with_original",
    "ja_only": "translated_only",
    "zh_only": "original_only",
    "zh(ja)": "original_with_translated",
}

TRANSLATION_BACKENDS: dict[str, dict[str, object]] = {
    "openai": {
        "label": "GPT",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
    },
    "zhipu": {
        "label": "GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "extra_body": {"thinking": {"type": "disabled"}},
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
    },
    "qianwen": {
        "label": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-mt-flash",
        "extra_body": {"enable_thinking": False},
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
    },
    "anthropic": {
        "label": "Claude",
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-20241022",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
    },
}

TRANSLATION_MODEL_PRESETS: dict[str, tuple[str, ...]] = {
    "openai": (
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4.1-nano",
    ),
    "deepseek": (
        "deepseek-reasoner",
        "deepseek-chat",
    ),
    "zhipu": (
        "glm-4.7-flash",
        "glm-4.7-flashx",
        "glm-4.5-airx",
        "glm-4.5-air",
        "glm-4.7",
        "glm-4.6",
    ),
    "qianwen": (
        "qwen-mt-flash",
        "qwen-mt-plus",
        "qwen-mt-lite",
        "qwen-mt-turbo",
    ),
    "anthropic": (
        "claude-3-5-haiku-20241022",
        "claude-3-5-haiku-latest",
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-0",
        "claude-3-7-sonnet-20250219",
        "claude-3-7-sonnet-latest",
    ),
}

TRANSLATION_MODEL_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "openai": {
        "gpt-4o-mini": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "live_default",
        },
        "gpt-4.1-mini": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "gpt-4.1": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "gpt-4.1-nano": {
            "speed": "very_fast",
            "quality": "basic",
            "fit": "general",
            "note": "economy_first",
        },
    },
    "deepseek": {
        "deepseek-reasoner": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "reasoning",
        },
        "deepseek-chat": {
            "speed": "balanced",
            "quality": "balanced",
            "fit": "recommended",
            "note": "live_default",
        },
    },
    "zhipu": {
        "glm-4.7-flash": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "glm-4.7-flashx": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "glm-4.5-airx": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "glm-4.5-air": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "quality_first",
        },
        "glm-4.7": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "general_high_quality",
        },
        "glm-4.6": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "general_high_quality",
        },
    },
    "qianwen": {
        "qwen-mt-flash": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "flash_mt",
        },
        "qwen-mt-plus": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "mt_quality",
        },
        "qwen-mt-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "qwen-mt-turbo": {
            "speed": "balanced",
            "quality": "balanced",
            "fit": "general",
            "note": "legacy_mt",
        },
    },
    "anthropic": {
        "claude-3-5-haiku-20241022": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "live_default",
        },
        "claude-3-5-haiku-latest": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "live_default",
        },
        "claude-sonnet-4-20250514": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-sonnet-4-0": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-3-7-sonnet-20250219": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-3-7-sonnet-latest": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
    },
}

BACKEND_ORDER = tuple(TRANSLATION_BACKENDS.keys())
DEFAULT_BACKEND = BACKEND_ORDER[0]
DEFAULT_ASR_ENGINE = "sensevoice-small"

TARGET_LANGUAGE_LABELS = {code: label for label, code in TARGET_LANGUAGE_OPTIONS}
TARGET_LANGUAGE_NAMES = {
    code: label.split(" (", 1)[0] for label, code in TARGET_LANGUAGE_OPTIONS
}
MANUAL_SOURCE_LABELS = {code: label for label, code in MANUAL_SOURCE_LANGUAGE_OPTIONS}
UI_LANGUAGE_LABELS = {code: label for label, code in UI_LANGUAGE_OPTIONS}


def normalize_backend(backend: str | None) -> str:
    if backend in TRANSLATION_BACKENDS:
        return str(backend)
    return DEFAULT_BACKEND


def normalize_output_format(output_format: str | None) -> str:
    if output_format in LEGACY_OUTPUT_FORMAT_ALIASES:
        return LEGACY_OUTPUT_FORMAT_ALIASES[str(output_format)]
    valid_codes = {code for _label, code in OUTPUT_FORMAT_OPTIONS}
    if output_format in valid_codes:
        return str(output_format)
    return OUTPUT_FORMAT_OPTIONS[0][1]


def get_backend_spec(backend: str | None) -> dict[str, object]:
    return TRANSLATION_BACKENDS[normalize_backend(backend)]


def get_backend_label(backend: str | None) -> str:
    return str(get_backend_spec(backend)["label"])


def get_backend_value(backend: str | None, key: str) -> str:
    value = get_backend_spec(backend).get(key, "")
    return str(value)


def get_backend_config_value(
    trans_cfg: Mapping[str, object] | None,
    backend: str | None,
    key: str,
) -> str:
    normalized_backend = normalize_backend(backend)
    backend_cfg = (
        trans_cfg.get(normalized_backend, {})
        if isinstance(trans_cfg, Mapping)
        else {}
    )
    if isinstance(backend_cfg, Mapping):
        value = backend_cfg.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return get_backend_value(normalized_backend, key)


def get_backend_model_options(
    backend: str | None,
    current_model: str | None = None,
) -> tuple[str, ...]:
    normalized_backend = normalize_backend(backend)
    models = list(TRANSLATION_MODEL_PRESETS.get(normalized_backend, ()))
    current = str(current_model or "").strip()
    if current and current not in models:
        models.insert(0, current)
    if models:
        return tuple(models)
    fallback = get_backend_value(normalized_backend, "model")
    return (fallback,) if fallback else ()


def get_backend_model_profile(
    backend: str | None,
    model: str | None,
) -> dict[str, str]:
    normalized_backend = normalize_backend(backend)
    resolved_model = str(model or "").strip() or get_backend_value(normalized_backend, "model")
    backend_profiles = TRANSLATION_MODEL_PROFILES.get(normalized_backend, {})
    profile = backend_profiles.get(resolved_model, {})
    return {
        "model": resolved_model,
        "speed": str(profile.get("speed", "balanced")),
        "quality": str(profile.get("quality", "balanced")),
        "fit": str(profile.get("fit", "general")),
        "note": str(profile.get("note", "custom")),
    }


def get_target_language_label(code: str | None) -> str:
    return TARGET_LANGUAGE_LABELS.get(str(code or ""), TARGET_LANGUAGE_OPTIONS[0][0])


def get_target_language_name(code: str | None) -> str:
    return TARGET_LANGUAGE_NAMES.get(str(code or ""), TARGET_LANGUAGE_NAMES["ja"])


def get_manual_source_label(code: str | None) -> str:
    return MANUAL_SOURCE_LABELS.get(str(code or ""), MANUAL_SOURCE_LANGUAGE_OPTIONS[0][0])


def get_target_language_options(
    exclude_codes: Collection[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    excluded = {str(code) for code in (exclude_codes or ()) if code}
    return tuple(
        (label, code) for label, code in TARGET_LANGUAGE_OPTIONS if code not in excluded
    )


def get_manual_source_language_options(
    exclude_codes: Collection[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    excluded = {str(code) for code in (exclude_codes or ()) if code}
    return tuple(
        (label, code)
        for label, code in MANUAL_SOURCE_LANGUAGE_OPTIONS
        if code == "auto" or code not in excluded
    )


def get_ui_language(config: Mapping[str, object] | None) -> str:
    ui_cfg = config.get("ui", {}) if isinstance(config, Mapping) else {}
    if isinstance(ui_cfg, Mapping):
        language = ui_cfg.get("language")
        if language in UI_LANGUAGE_LABELS:
            return str(language)
    return DEFAULT_UI_LANGUAGE
