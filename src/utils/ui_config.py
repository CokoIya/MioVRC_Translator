from __future__ import annotations

from collections.abc import Collection, Mapping

DEFAULT_UI_LANGUAGE = "zh-CN"

UI_LANGUAGE_OPTIONS = (
    ("\u7b80\u4f53\u4e2d\u6587", "zh-CN"),
    ("English", "en"),
    ("\u65e5\u672c\u8a9e", "ja"),
    ("\u0420\u0443\u0441\u0441\u043a\u0438\u0439", "ru"),
    ("\ud55c\uad6d\uc5b4", "ko"),
)

SUPPORTED_TARGET_LANGUAGE_CODES = (
    "ja",
    "en",
    "zh",
    "ko",
    "ru",
    "fr",
    "de",
    "es",
    "pt",
    "it",
    "th",
    "vi",
    "id",
    "ms",
)

SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES = ("auto",) + SUPPORTED_TARGET_LANGUAGE_CODES
ASR_HINT_LANGUAGE_CODES = ("zh", "en", "ja", "ko", "ru", "yue")

NATIVE_LANGUAGE_LABELS = {
    "auto": "Auto",
    "zh": "\u4e2d\u6587",
    "ja": "\u65e5\u672c\u8a9e",
    "en": "English",
    "ko": "\ud55c\uad6d\uc5b4",
    "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439",
    "fr": "Fran\u00e7ais",
    "de": "Deutsch",
    "es": "Espa\u00f1ol",
    "pt": "Portugu\u00eas",
    "it": "Italiano",
    "th": "\u0e44\u0e17\u0e22",
    "vi": "Ti\u1ebfng Vi\u1ec7t",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
}

LANGUAGE_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "zh-CN": {
        "auto": "\u81ea\u52a8",
        "zh": "\u4e2d\u6587",
        "ja": "\u65e5\u8bed",
        "en": "\u82f1\u8bed",
        "ko": "\u97e9\u8bed",
        "ru": "\u4fc4\u8bed",
        "fr": "\u6cd5\u8bed",
        "de": "\u5fb7\u8bed",
        "es": "\u897f\u73ed\u7259\u8bed",
        "pt": "\u8461\u8404\u7259\u8bed",
        "it": "\u610f\u5927\u5229\u8bed",
        "th": "\u6cf0\u8bed",
        "vi": "\u8d8a\u5357\u8bed",
        "id": "\u5370\u5c3c\u8bed",
        "ms": "\u9a6c\u6765\u8bed",
    },
    "en": {
        "auto": "Auto",
        "zh": "Chinese",
        "ja": "Japanese",
        "en": "English",
        "ko": "Korean",
        "ru": "Russian",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "pt": "Portuguese",
        "it": "Italian",
        "th": "Thai",
        "vi": "Vietnamese",
        "id": "Indonesian",
        "ms": "Malay",
    },
    "ja": {
        "auto": "\u81ea\u52d5",
        "zh": "\u4e2d\u56fd\u8a9e",
        "ja": "\u65e5\u672c\u8a9e",
        "en": "\u82f1\u8a9e",
        "ko": "\u97d3\u56fd\u8a9e",
        "ru": "\u30ed\u30b7\u30a2\u8a9e",
        "fr": "\u30d5\u30e9\u30f3\u30b9\u8a9e",
        "de": "\u30c9\u30a4\u30c4\u8a9e",
        "es": "\u30b9\u30da\u30a4\u30f3\u8a9e",
        "pt": "\u30dd\u30eb\u30c8\u30ac\u30eb\u8a9e",
        "it": "\u30a4\u30bf\u30ea\u30a2\u8a9e",
        "th": "\u30bf\u30a4\u8a9e",
        "vi": "\u30d9\u30c8\u30ca\u30e0\u8a9e",
        "id": "\u30a4\u30f3\u30c9\u30cd\u30b7\u30a2\u8a9e",
        "ms": "\u30de\u30ec\u30fc\u8a9e",
    },
    "ru": {
        "auto": "\u0410\u0432\u0442\u043e",
        "zh": "\u041a\u0438\u0442\u0430\u0439\u0441\u043a\u0438\u0439",
        "ja": "\u042f\u043f\u043e\u043d\u0441\u043a\u0438\u0439",
        "en": "\u0410\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u0438\u0439",
        "ko": "\u041a\u043e\u0440\u0435\u0439\u0441\u043a\u0438\u0439",
        "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439",
        "fr": "\u0424\u0440\u0430\u043d\u0446\u0443\u0437\u0441\u043a\u0438\u0439",
        "de": "\u041d\u0435\u043c\u0435\u0446\u043a\u0438\u0439",
        "es": "\u0418\u0441\u043f\u0430\u043d\u0441\u043a\u0438\u0439",
        "pt": "\u041f\u043e\u0440\u0442\u0443\u0433\u0430\u043b\u044c\u0441\u043a\u0438\u0439",
        "it": "\u0418\u0442\u0430\u043b\u044c\u044f\u043d\u0441\u043a\u0438\u0439",
        "th": "\u0422\u0430\u0439\u0441\u043a\u0438\u0439",
        "vi": "\u0412\u044c\u0435\u0442\u043d\u0430\u043c\u0441\u043a\u0438\u0439",
        "id": "\u0418\u043d\u0434\u043e\u043d\u0435\u0437\u0438\u0439\u0441\u043a\u0438\u0439",
        "ms": "\u041c\u0430\u043b\u0430\u0439\u0441\u043a\u0438\u0439",
    },
    "ko": {
        "auto": "\uc790\ub3d9",
        "zh": "\uc911\uad6d\uc5b4",
        "ja": "\uc77c\ubcf8\uc5b4",
        "en": "\uc601\uc5b4",
        "ko": "\ud55c\uad6d\uc5b4",
        "ru": "\ub7ec\uc2dc\uc544\uc5b4",
        "fr": "\ud504\ub791\uc2a4\uc5b4",
        "de": "\ub3c5\uc77c\uc5b4",
        "es": "\uc2a4\ud398\uc778\uc5b4",
        "pt": "\ud3ec\ub974\ud22c\uac08\uc5b4",
        "it": "\uc774\ud0c8\ub9ac\uc544\uc5b4",
        "th": "\ud0dc\uad6d\uc5b4",
        "vi": "\ubca0\ud2b8\ub0a8\uc5b4",
        "id": "\uc778\ub3c4\ub124\uc2dc\uc544\uc5b4",
        "ms": "\ub9d0\ub808\uc774\uc5b4",
    },
}

OUTPUT_FORMAT_OPTIONS = (
    ("\u8bd1\u6587\uff08\u539f\u6587\uff09", "translated_with_original"),
    ("\u4ec5\u8bd1\u6587", "translated_only"),
    ("\u4ec5\u539f\u6587", "original_only"),
    ("\u539f\u6587\uff08\u8bd1\u6587\uff09", "original_with_translated"),
)

SOCIAL_MODE_CODES = ("standard", "language_exchange", "roleplay")
SOCIAL_POLITENESS_CODES = ("neutral", "casual", "polite", "very_polite")
SOCIAL_TONE_CODES = ("natural", "cute", "cool", "host")

SOCIAL_MODE_LABELS = {
    "zh-CN": {
        "standard": "\u6807\u51c6",
        "language_exchange": "\u4ea4\u6d41",
        "roleplay": "RP",
    },
    "en": {
        "standard": "Standard",
        "language_exchange": "Exchange",
        "roleplay": "RP",
    },
    "ja": {
        "standard": "\u6a19\u6e96",
        "language_exchange": "\u4ea4\u6d41",
        "roleplay": "RP",
    },
    "ru": {
        "standard": "\u0421\u0442\u0430\u043d\u0434\u0430\u0440\u0442",
        "language_exchange": "\u041e\u0431\u043c\u0435\u043d",
        "roleplay": "RP",
    },
    "ko": {
        "standard": "\ud45c\uc900",
        "language_exchange": "\uad50\ub958",
        "roleplay": "RP",
    },
}

SOCIAL_POLITENESS_LABELS = {
    "zh-CN": {
        "neutral": "\u4e2d\u6027",
        "casual": "\u968f\u610f",
        "polite": "\u793c\u8c8c",
        "very_polite": "\u975e\u5e38\u793c\u8c8c",
    },
    "en": {
        "neutral": "Neutral",
        "casual": "Casual",
        "polite": "Polite",
        "very_polite": "Very Polite",
    },
    "ja": {
        "neutral": "\u4e2d\u7acb",
        "casual": "\u30ab\u30b8\u30e5\u30a2\u30eb",
        "polite": "\u4e01\u5be7",
        "very_polite": "\u3068\u3066\u3082\u4e01\u5be7",
    },
    "ru": {
        "neutral": "\u041d\u0435\u0439\u0442\u0440\u0430\u043b\u044c\u043d\u043e",
        "casual": "\u041d\u0435\u0444\u043e\u0440\u043c\u0430\u043b\u044c\u043d\u043e",
        "polite": "\u0412\u0435\u0436\u043b\u0438\u0432\u043e",
        "very_polite": "\u041e\u0447\u0435\u043d\u044c \u0432\u0435\u0436\u043b\u0438\u0432\u043e",
    },
    "ko": {
        "neutral": "\uc911\ub9bd",
        "casual": "\uce90\uc8fc\uc5bc",
        "polite": "\uc815\uc911",
        "very_polite": "\ub9e4\uc6b0 \uc815\uc911",
    },
}

SOCIAL_TONE_LABELS = {
    "zh-CN": {
        "natural": "\u81ea\u7136",
        "cute": "\u53ef\u7231",
        "cool": "\u51b7\u9759",
        "host": "\u4e3b\u6301/\u5f15\u5bfc",
    },
    "en": {
        "natural": "Natural",
        "cute": "Cute",
        "cool": "Cool",
        "host": "Host",
    },
    "ja": {
        "natural": "\u81ea\u7136",
        "cute": "\u53ef\u611b\u3044",
        "cool": "\u30af\u30fc\u30eb",
        "host": "\u53f8\u4f1a/\u30ac\u30a4\u30c9",
    },
    "ru": {
        "natural": "\u0415\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e",
        "cute": "\u041c\u0438\u043b\u043e",
        "cool": "\u0421\u0434\u0435\u0440\u0436\u0430\u043d\u043d\u043e",
        "host": "\u0412\u0435\u0434\u0443\u0449\u0438\u0439",
    },
    "ko": {
        "natural": "\uc790\uc5f0\uc2a4\ub7ec\uc6c0",
        "cute": "\uadc0\uc5ec\uc6c0",
        "cool": "\uce68\ucc29",
        "host": "\uc9c4\ud589/\uc548\ub0b4",
    },
}

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
        "model": "gpt-5.4-mini",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "zhipu": {
        "label": "GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "extra_body": {"thinking": {"type": "disabled"}},
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "qianwen": {
        "label": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-mt-flash",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "doubao": {
        "label": "Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "entry",
        "model_hint": (
            "Enter your Ark endpoint ID in the Model field. "
            "Doubao chat requests use endpoint IDs instead of a shared model alias."
        ),
    },
    "anthropic": {
        "label": "Claude",
        "base_url": "https://api.anthropic.com",
        "model": "claude-haiku-4-5-20251001",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "model_input": "select",
    },
}

TRANSLATION_MODEL_PRESETS: dict[str, tuple[str, ...]] = {
    "openai": (
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.4",
    ),
    "deepseek": (
        "deepseek-chat",
        "deepseek-reasoner",
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
        "qwen-mt-turbo",
        "qwen-mt-lite",
    ),
    "gemini": (
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ),
    "doubao": (),
    "anthropic": (
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-3-5-haiku-20241022",
        "claude-sonnet-4-20250514",
    ),
}

TRANSLATION_MODEL_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "openai": {
        "gpt-5.4-pro": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "gpt-5.4-mini": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "gpt-5.4-nano": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "gpt-5.4": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
    },
    "deepseek": {
        "deepseek-chat": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "deepseek-reasoner": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "reasoning",
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
        "qwen-mt-turbo": {
            "speed": "balanced",
            "quality": "balanced",
            "fit": "general",
            "note": "legacy_mt",
        },
        "qwen-mt-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
    },
    "gemini": {
        "gemini-2.5-flash-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "live_default",
        },
        "gemini-2.5-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "balanced_quality",
        },
        "gemini-2.5-pro": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
    },
    "anthropic": {
        "claude-haiku-4-5-20251001": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "claude-haiku-4-5": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "claude-sonnet-4-6": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "claude-opus-4-6": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-3-5-haiku-20241022": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "live_default",
        },
        "claude-sonnet-4-20250514": {
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
UI_LANGUAGE_LABELS = {code: label for label, code in UI_LANGUAGE_OPTIONS}
TARGET_LANGUAGE_OPTIONS = ()
MANUAL_SOURCE_LANGUAGE_OPTIONS = ()


def _resolve_ui_language(language: str | None) -> str:
    if language in LANGUAGE_DISPLAY_NAMES:
        return str(language)
    if isinstance(language, str):
        base = language.split("-", 1)[0]
        for candidate in LANGUAGE_DISPLAY_NAMES:
            if candidate.split("-", 1)[0] == base:
                return candidate
    return DEFAULT_UI_LANGUAGE


def _native_language_name(code: str) -> str:
    return NATIVE_LANGUAGE_LABELS.get(code, code)


def _localized_language_name(code: str, ui_language: str | None) -> str:
    resolved_ui_language = _resolve_ui_language(ui_language)
    localized_names = LANGUAGE_DISPLAY_NAMES.get(
        resolved_ui_language,
        LANGUAGE_DISPLAY_NAMES[DEFAULT_UI_LANGUAGE],
    )
    return localized_names.get(code, _native_language_name(code))


def _with_code(label: str, code: str) -> str:
    return f"{label} ({code})"


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


def backend_model_is_selectable(backend: str | None) -> bool:
    return str(get_backend_spec(backend).get("model_input", "select")) == "select"


def get_backend_model_hint(backend: str | None) -> str:
    return str(get_backend_spec(backend).get("model_hint", "")).strip()


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


def _localized_option_label(
    mapping: Mapping[str, Mapping[str, str]],
    code: str,
    ui_language: str | None,
) -> str:
    resolved_ui_language = _resolve_ui_language(ui_language)
    labels = mapping.get(resolved_ui_language, mapping[DEFAULT_UI_LANGUAGE])
    return labels.get(code, code)


def normalize_social_mode(mode: str | None) -> str:
    if mode in SOCIAL_MODE_CODES:
        return str(mode)
    return SOCIAL_MODE_CODES[0]


def normalize_social_politeness(level: str | None) -> str:
    if level in SOCIAL_POLITENESS_CODES:
        return str(level)
    return SOCIAL_POLITENESS_CODES[0]


def normalize_social_tone(tone: str | None) -> str:
    if tone in SOCIAL_TONE_CODES:
        return str(tone)
    return SOCIAL_TONE_CODES[0]


def get_social_mode_options(ui_language: str | None = None) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            _localized_option_label(SOCIAL_MODE_LABELS, code, ui_language),
            code,
        )
        for code in SOCIAL_MODE_CODES
    )


def get_social_politeness_options(
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            _localized_option_label(SOCIAL_POLITENESS_LABELS, code, ui_language),
            code,
        )
        for code in SOCIAL_POLITENESS_CODES
    )


def get_social_tone_options(ui_language: str | None = None) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            _localized_option_label(SOCIAL_TONE_LABELS, code, ui_language),
            code,
        )
        for code in SOCIAL_TONE_CODES
    )


def target_language_osc_value(code: str | None) -> int:
    normalized = str(code or "").strip()
    if normalized in SUPPORTED_TARGET_LANGUAGE_CODES:
        return SUPPORTED_TARGET_LANGUAGE_CODES.index(normalized) + 1
    return 0


def get_target_language_label(code: str | None, ui_language: str | None = None) -> str:
    code_text = str(code or "")
    if not code_text:
        default_code = SUPPORTED_TARGET_LANGUAGE_CODES[0]
        return _with_code(_localized_language_name(default_code, ui_language), default_code)
    return _with_code(_localized_language_name(code_text, ui_language), code_text)


def get_target_language_name(code: str | None, ui_language: str | None = None) -> str:
    code_text = str(code or "") or SUPPORTED_TARGET_LANGUAGE_CODES[0]
    return _localized_language_name(code_text, ui_language)


def get_manual_source_label(code: str | None, ui_language: str | None = None) -> str:
    del ui_language
    code_text = str(code or "") or SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES[0]
    return _native_language_name(code_text)


def get_target_language_options(
    exclude_codes: Collection[str] | None = None,
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
    excluded = {str(code) for code in (exclude_codes or ()) if code}
    return tuple(
        (get_target_language_label(code, ui_language=ui_language), code)
        for code in SUPPORTED_TARGET_LANGUAGE_CODES
        if code not in excluded
    )


def get_manual_source_language_options(
    exclude_codes: Collection[str] | None = None,
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
    excluded = {str(code) for code in (exclude_codes or ()) if code}
    return tuple(
        (get_manual_source_label(code, ui_language=ui_language), code)
        for code in SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES
        if code == "auto" or code not in excluded
    )


TARGET_LANGUAGE_OPTIONS = get_target_language_options(ui_language=DEFAULT_UI_LANGUAGE)
MANUAL_SOURCE_LANGUAGE_OPTIONS = get_manual_source_language_options(ui_language=DEFAULT_UI_LANGUAGE)


def get_ui_language(config: Mapping[str, object] | None) -> str:
    ui_cfg = config.get("ui", {}) if isinstance(config, Mapping) else {}
    if isinstance(ui_cfg, Mapping):
        language = ui_cfg.get("language")
        if language in UI_LANGUAGE_LABELS:
            return str(language)
    return DEFAULT_UI_LANGUAGE
