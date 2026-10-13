from __future__ import annotations

from collections.abc import Collection, Mapping

from src.utils.model_retirement import (  # noqa: F401 - re-exported
    ANTHROPIC_MODEL_ID_PREFIXES,
    is_retired_translation_model,
)

DEFAULT_UI_LANGUAGE = "zh-CN"

# Backends that stay in the catalog but cannot be selected or created. Empty
# since Claude came back; the guard is kept so a backend can be retired
# again without touching every caller.
DISABLED_TRANSLATION_BACKENDS: frozenset[str] = frozenset()
# The single relay-station backend ("中转站兼容"). It replaced the separate
# GPT-, Grok- and Claude-compatible backends; their ids stay as aliases so old
# configs, fallback chains and remote catalogs resolve to it.
RELAY_BACKEND = "openai_compatible"
LEGACY_RELAY_BACKENDS: dict[str, str] = {
    "grok_compatible": "openai",
    "anthropic_compatible": "anthropic",
}
RELAY_PROTOCOL_OPENAI = "openai"
RELAY_PROTOCOL_ANTHROPIC = "anthropic"
RELAY_PROTOCOLS: tuple[str, ...] = (RELAY_PROTOCOL_OPENAI, RELAY_PROTOCOL_ANTHROPIC)
# DeepSeek is offered as its flash model only: every DeepSeek "pro" id is
# hidden from fetched model lists (case-insensitive; matches deepseek-v4-pro,
# deepseek/deepseek-v4-pro:free, deepseek-ai/DeepSeek-V4-Pro, "DeepSeek V4
# Pro", deepseek-v4-pro-260425, but not deepseek-prover).
DEEPSEEK_PRO_MODEL_PATTERN = r"deepseek[^/]*[-_.\s]pro(?![a-z0-9])"


_RELAY_MODEL_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("grok-", "xai"),
    ("gemini-", "gemini"),
    ("deepseek-", "deepseek"),
    ("kimi-", "kimi"),
    ("glm-", "zhipu"),
    ("qwen", "qianwen"),
    ("mimo-", "xiaomi"),
    ("mistral-", "mistral"),
    ("ministral-", "mistral"),
    ("hunyuan-", "hunyuan"),
    ("doubao-", "doubao"),
)


def normalize_relay_protocol(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"anthropic", "claude", "messages", "anthropic_messages"}:
        return RELAY_PROTOCOL_ANTHROPIC
    return RELAY_PROTOCOL_OPENAI


def relay_protocol_from_config(trans_cfg: Mapping[str, object] | None) -> str:
    """Wire protocol of the relay backend in a translation config section."""

    relay_cfg = trans_cfg.get(RELAY_BACKEND) if isinstance(trans_cfg, Mapping) else None
    if isinstance(relay_cfg, Mapping):
        return normalize_relay_protocol(relay_cfg.get("protocol"))
    return RELAY_PROTOCOL_OPENAI


UI_FONT_851 = "851"
UI_FONT_SYSTEM = "system"
UI_FONT_PREFERENCES = (UI_FONT_851, UI_FONT_SYSTEM)


def normalize_ui_font_preference(value: object) -> str:
    """Normalize persisted UI font choices to a stable preference code."""

    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {
        "system",
        "system-default",
        "default",
        "native",
        "os",
    }:
        return UI_FONT_SYSTEM
    if normalized in {
        "851",
        "851tegakizatsu",
        "851-tegakizatsu",
        "custom",
        "bundled",
        "",
    }:
        return UI_FONT_851
    return UI_FONT_851


def ui_font_preference_from_config(config: object) -> str:
    if not isinstance(config, Mapping):
        return UI_FONT_851
    ui_config = config.get("ui", {})
    if not isinstance(ui_config, Mapping):
        return UI_FONT_851
    return normalize_ui_font_preference(ui_config.get("font_family", UI_FONT_851))

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

OUTPUT_FORMAT_ORIGINAL_ONLY = "original_only"
OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION = "original_only_read_translation"
ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY = (
    "original_only_read_translation_wait_for_tts"
)
ORIGINAL_ONLY_READ_TRANSLATION_OSC_DELAY_MS = 200
ORIGINAL_TEXT_ONLY_OUTPUT_FORMATS = frozenset(
    {
        OUTPUT_FORMAT_ORIGINAL_ONLY,
        OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION,
    }
)

OUTPUT_FORMAT_OPTIONS = (
    ("\u8bd1\u6587\uff08\u539f\u53e5\uff09", "translated_with_original"),
    ("\u4ec5\u8bd1\u6587", "translated_only"),
    ("\u4ec5\u539f\u53e5", OUTPUT_FORMAT_ORIGINAL_ONLY),
    (
        "\u4ec5\u539f\u53e5\uff08\u6717\u8bfb\u8bd1\u6587\uff09",
        OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION,
    ),
    ("\u539f\u53e5\uff08\u8bd1\u6587\uff09", "original_with_translated"),
    # Second-translation-aware formats (shown when target_2 is configured):
    (
        "\u539f\u53e5\uff08\u8bd1\u65871\uff09\uff08\u8bd1\u65872\uff09",
        "original_with_translated1_translated2",
    ),
    ("\u8bd1\u65871\uff08\u8bd1\u65872\uff09", "translated1_with_translated2"),
    (
        "\u8bd1\u65871\uff08\u8bd1\u65872\uff09\uff08\u539f\u53e5\uff09",
        "translated1_with_translated2_original",
    ),
)

OUTPUT_FORMAT_2_DISABLED = "disabled"
OUTPUT_FORMAT_2_OPTIONS = (
    (
        "\u539f\u6587\uff08\u8bd1\u65871\uff09\uff08\u8bd1\u65872\uff09",
        "original_with_translated1_translated2",
    ),
    ("\u8bd1\u65871\uff08\u8bd1\u65872\uff09", "translated1_with_translated2"),
    ("\u5173\u95ed\u8bd1\u65872", OUTPUT_FORMAT_2_DISABLED),
    (
        "\u8bd1\u65871\uff08\u8bd1\u65872\uff09\uff08\u539f\u6587\uff09",
        "translated1_with_translated2_original",
    ),
)

OUTPUT_FORMAT_LABELS = {
    "translated_with_original": {
        "zh-CN": "\u8bd1\u6587\uff08\u539f\u6587\uff09",
        "en": "Translation (Original)",
        "ja": "\u8a33\u6587\uff08\u539f\u6587\uff09",
        "ru": "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 (\u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b)",
        "ko": "\ubc88\uc5ed\ubb38(\uc6d0\ubb38)",
    },
    "translated_only": {
        "zh-CN": "\u4ec5\u8bd1\u6587",
        "en": "Translation only",
        "ja": "\u8a33\u6587\u306e\u307f",
        "ru": "\u0422\u043e\u043b\u044c\u043a\u043e \u043f\u0435\u0440\u0435\u0432\u043e\u0434",
        "ko": "\ubc88\uc5ed\ubb38\ub9cc",
    },
    OUTPUT_FORMAT_ORIGINAL_ONLY: {
        "zh-CN": "\u4ec5\u539f\u6587",
        "en": "Original only",
        "ja": "\u539f\u6587\u306e\u307f",
        "ru": "\u0422\u043e\u043b\u044c\u043a\u043e \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b",
        "ko": "\uc6d0\ubb38\ub9cc",
    },
    OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION: {
        "zh-CN": "\u4ec5\u539f\u6587\uff08\u6717\u8bfb\u8bd1\u6587\uff09",
        "en": "Original Text Only (Read Translation)",
        "ja": "\u539f\u6587\u306e\u307f\uff08\u7ffb\u8a33\u3092\u8aad\u307f\u4e0a\u3052\uff09",
        "ru": "\u0422\u043e\u043b\u044c\u043a\u043e \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u043e\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u0442\u044c \u043f\u0435\u0440\u0435\u0432\u043e\u0434)",
        "ko": "\uc6d0\ubb38\ub9cc (\ubc88\uc5ed \uc77d\uae30)",
    },
    "original_with_translated": {
        "zh-CN": "\u539f\u6587\uff08\u8bd1\u6587\uff09",
        "en": "Original (Translation)",
        "ja": "\u539f\u6587\uff08\u8a33\u6587\uff09",
        "ru": "\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u043f\u0435\u0440\u0435\u0432\u043e\u0434)",
        "ko": "\uc6d0\ubb38(\ubc88\uc5ed\ubb38)",
    },
    "original_with_translated1_translated2": {
        "zh-CN": "\u539f\u6587\uff08\u8bd1\u65871\uff09\uff08\u8bd1\u65872\uff09",
        "en": "Original (Translation 1) (Translation 2)",
        "ja": "\u539f\u6587\uff08\u8a33\u65871\uff09\uff08\u8a33\u65872\uff09",
        "ru": "\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 1) (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2)",
        "ko": "\uc6d0\ubb38(\ubc88\uc5ed1)(\ubc88\uc5ed2)",
    },
    "translated1_with_translated2": {
        "zh-CN": "\u8bd1\u65871\uff08\u8bd1\u65872\uff09",
        "en": "Translation 1 (Translation 2)",
        "ja": "\u8a33\u65871\uff08\u8a33\u65872\uff09",
        "ru": "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 1 (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2)",
        "ko": "\ubc88\uc5ed1(\ubc88\uc5ed2)",
    },
    "translated1_with_translated2_original": {
        "zh-CN": "\u8bd1\u65871\uff08\u8bd1\u65872\uff09\uff08\u539f\u6587\uff09",
        "en": "Translation 1 (Translation 2) (Original)",
        "ja": "\u8a33\u65871\uff08\u8a33\u65872\uff09\uff08\u539f\u6587\uff09",
        "ru": "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 1 (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2) (\u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b)",
        "ko": "\ubc88\uc5ed1(\ubc88\uc5ed2)(\uc6d0\ubb38)",
    },
}

OUTPUT_FORMAT_2_LABELS = {
    "original_with_translated1_translated2": {
        "zh-CN": "\u539f\u6587\uff08\u8bd1\u65871\uff09\uff08\u8bd1\u65872\uff09",
        "en": "Original (Translation 1) (Translation 2)",
        "ja": "\u539f\u6587\uff08\u8a33\u65871\uff09\uff08\u8a33\u65872\uff09",
        "ru": "\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 1) (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2)",
        "ko": "\uc6d0\ubb38(\ubc88\uc5ed1)(\ubc88\uc5ed2)",
    },
    "translated1_with_translated2": {
        "zh-CN": "\u8bd1\u65871\uff08\u8bd1\u65872\uff09",
        "en": "Translation 1 (Translation 2)",
        "ja": "\u8a33\u65871\uff08\u8a33\u65872\uff09",
        "ru": "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 1 (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2)",
        "ko": "\ubc88\uc5ed1(\ubc88\uc5ed2)",
    },
    OUTPUT_FORMAT_2_DISABLED: {
        "zh-CN": "\u5173\u95ed\u8bd1\u65872",
        "en": "Disable Translation 2",
        "ja": "\u7b2c2\u8a33\u6587\u3092\u7121\u52b9\u5316",
        "ru": "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u043f\u0435\u0440\u0435\u0432\u043e\u0434 2",
        "ko": "\ubc88\uc5ed2 \ub044\uae30",
    },
    "translated1_with_translated2_original": {
        "zh-CN": "\u8bd1\u65871\uff08\u8bd1\u65872\uff09\uff08\u539f\u6587\uff09",
        "en": "Translation 1 (Translation 2) (Original)",
        "ja": "\u8a33\u65871\uff08\u8a33\u65872\uff09\uff08\u539f\u6587\uff09",
        "ru": "\u041f\u0435\u0440\u0435\u0432\u043e\u0434 1 (\u043f\u0435\u0440\u0435\u0432\u043e\u0434 2) (\u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b)",
        "ko": "\ubc88\uc5ed1(\ubc88\uc5ed2)(\uc6d0\ubb38)",
    },
}

SOCIAL_MODE_CODES = ("standard", "language_exchange", "roleplay")
SOCIAL_POLITENESS_CODES = ("neutral", "casual", "polite", "very_polite")
SOCIAL_TONE_CODES = (
    "natural",
    "cute",
    "cool",
    "clear",
    "cheerful",
    "playful",
    "warm",
    "host",
)

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
        "clear": "\u6e05\u6670",
        "cheerful": "\u5f00\u6717",
        "playful": "\u4fcf\u76ae",
        "warm": "\u6e29\u67d4",
        "host": "\u4e3b\u6301/\u5f15\u5bfc",
    },
    "en": {
        "natural": "Natural",
        "cute": "Cute",
        "cool": "Cool",
        "clear": "Clear",
        "cheerful": "Cheerful",
        "playful": "Playful",
        "warm": "Warm",
        "host": "Host",
    },
    "ja": {
        "natural": "\u81ea\u7136",
        "cute": "\u53ef\u611b\u3044",
        "cool": "\u30af\u30fc\u30eb",
        "clear": "\u30af\u30ea\u30a2",
        "cheerful": "\u660e\u308b\u3044",
        "playful": "\u904a\u3073\u5fc3",
        "warm": "\u512a\u3057\u3044",
        "host": "\u53f8\u4f1a/\u30ac\u30a4\u30c9",
    },
    "ru": {
        "natural": "\u0415\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e",
        "cute": "\u041c\u0438\u043b\u043e",
        "cool": "\u0421\u0434\u0435\u0440\u0436\u0430\u043d\u043d\u043e",
        "clear": "\u042f\u0441\u043d\u043e",
        "cheerful": "\u0411\u043e\u0434\u0440\u043e",
        "playful": "\u0418\u0433\u0440\u0438\u0432\u043e",
        "warm": "\u0422\u0435\u043f\u043b\u043e",
        "host": "\u0412\u0435\u0434\u0443\u0449\u0438\u0439",
    },
    "ko": {
        "natural": "\uc790\uc5f0\uc2a4\ub7ec\uc6c0",
        "cute": "\uadc0\uc5ec\uc6c0",
        "cool": "\uce68\ucc29",
        "clear": "\uba85\ud655",
        "cheerful": "\ubc1d\uc74c",
        "playful": "\uc7a5\ub09c\uc2a4\ub7ec\uc6c0",
        "warm": "\ub530\ub73b\ud568",
        "host": "\uc9c4\ud589/\uc548\ub0b4",
    },
}

LEGACY_OUTPUT_FORMAT_ALIASES = {
    "ja(zh)": "translated_with_original",
    "ja_only": "translated_only",
    "zh_only": "original_only",
    "zh(ja)": "original_with_translated",
}

QWEN_TRANSLATION_BASE_URL_MAINLAND = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_TRANSLATION_BASE_URL_INTERNATIONAL = (
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)
# Use the international service as the neutral/global fallback. Mainland users
# are still selected automatically by ``backend_region_for_ui_language`` and
# existing saved regions are preserved during config migration.
QWEN_TRANSLATION_DEFAULT_REGION = "singapore"
QWEN_TRANSLATION_REGION_BASE_URLS = {
    "china_mainland": QWEN_TRANSLATION_BASE_URL_MAINLAND,
    "singapore": QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
    # Tokyo is workspace-scoped. The player must provide the URL issued for
    # their workspace; there is no shared Japan DashScope hostname.
    "japan": "",
}
QWEN_TRANSLATION_REGION_ALIASES = {
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "china-mainland": "china_mainland",
    "intl": "singapore",
    "international": "singapore",
    "sg": "singapore",
    "jp": "japan",
    "japan": "japan",
}
QWEN_TRANSLATION_KNOWN_BASE_URLS = frozenset(
    value for value in QWEN_TRANSLATION_REGION_BASE_URLS.values() if value
)

XIAOMI_TRANSLATION_BASE_URL_PAYG = "https://api.xiaomimimo.com/v1"
XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_CN = "https://token-plan-cn.xiaomimimo.com/v1"
XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG = "https://token-plan-sgp.xiaomimimo.com/v1"
XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_EU = "https://token-plan-ams.xiaomimimo.com/v1"
XIAOMI_TRANSLATION_DEFAULT_REGION = "global"
XIAOMI_TRANSLATION_REGION_BASE_URLS = {
    "global": XIAOMI_TRANSLATION_BASE_URL_PAYG,
    "china_cluster": XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_CN,
    "singapore_cluster": XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG,
    "europe_cluster": XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_EU,
}
XIAOMI_TRANSLATION_REGION_ALIASES = {
    "api": "global",
    "payg": "global",
    "pay_as_you_go": "global",
    "global_payg": "global",
    "china": "china_cluster",
    "cn": "china_cluster",
    "mainland": "china_cluster",
    "china_mainland": "china_cluster",
    "token_plan_cn": "china_cluster",
    "cn_cluster": "china_cluster",
    "sg": "singapore_cluster",
    "singapore": "singapore_cluster",
    "token_plan_sg": "singapore_cluster",
    "token_plan_sgp": "singapore_cluster",
    "sgp": "singapore_cluster",
    "eu": "europe_cluster",
    "europe": "europe_cluster",
    "ams": "europe_cluster",
    "amsterdam": "europe_cluster",
    "token_plan_eu": "europe_cluster",
    "token_plan_ams": "europe_cluster",
}
XIAOMI_TRANSLATION_KNOWN_BASE_URLS = frozenset(
    XIAOMI_TRANSLATION_REGION_BASE_URLS.values()
)

NVIDIA_TRANSLATION_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_TRANSLATION_DEFAULT_REGION = "global"
NVIDIA_TRANSLATION_REGION_BASE_URLS = {
    "global": NVIDIA_TRANSLATION_BASE_URL,
}
NVIDIA_TRANSLATION_REGION_ALIASES = {
    "api": "global",
    "catalog": "global",
    "cloud": "global",
    "hosted": "global",
    "integrate": "global",
    "nvidia_hosted": "global",
    "nim_hosted": "global",
}
NVIDIA_TRANSLATION_KNOWN_BASE_URLS = frozenset(
    NVIDIA_TRANSLATION_REGION_BASE_URLS.values()
)

# Tencent closed the old Hunyuan API (api.hunyuan.cloud.tencent.com) on
# 2026-09-30; Hunyuan models are now served by TokenHub, whose China and
# Global sites issue separate keys.
HUNYUAN_TRANSLATION_BASE_URL_CHINA = "https://tokenhub.tencentmaas.com/v1"
HUNYUAN_TRANSLATION_BASE_URL_GLOBAL = "https://tokenhub-intl.tencentmaas.com/v1"
HUNYUAN_TRANSLATION_LEGACY_BASE_URLS = frozenset(
    {
        "https://api.hunyuan.cloud.tencent.com/v1",
        "https://api.hunyuan.cloud.tencent.com",
        "https://hunyuan.tencentcloudapi.com",
    }
)
HUNYUAN_TRANSLATION_DEFAULT_REGION = "china"
HUNYUAN_TRANSLATION_REGION_BASE_URLS = {
    "china": HUNYUAN_TRANSLATION_BASE_URL_CHINA,
    "global": HUNYUAN_TRANSLATION_BASE_URL_GLOBAL,
}
HUNYUAN_TRANSLATION_REGION_ALIASES = {
    "cn": "china",
    "mainland": "china",
    "china_mainland": "china",
    "guangzhou": "china",
    "intl": "global",
    "international": "global",
    "singapore": "global",
    "sg": "global",
}
HUNYUAN_TRANSLATION_KNOWN_BASE_URLS = frozenset(
    HUNYUAN_TRANSLATION_REGION_BASE_URLS.values()
)

DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL = "https://api.deepseek.com"
DEEPSEEK_TRANSLATION_DEFAULT_REGION = "official"
DEEPSEEK_TRANSLATION_REGION_BASE_URLS = {
    "official": DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
}
DEEPSEEK_TRANSLATION_REGION_ALIASES = {
    "global": "official",
    "official_api": "official",
    "api": "official",
    "deepseek": "official",
    "cn": "official",
    "china": "official",
    "mainland": "official",
    "international": "official",
    "overseas": "official",
}
DEEPSEEK_TRANSLATION_KNOWN_BASE_URLS = frozenset(
    DEEPSEEK_TRANSLATION_REGION_BASE_URLS.values()
)

TRANSLATION_BACKEND_REGION_BASE_URLS = {
    "qianwen": QWEN_TRANSLATION_REGION_BASE_URLS,
    "deepseek": DEEPSEEK_TRANSLATION_REGION_BASE_URLS,
    "xiaomi": XIAOMI_TRANSLATION_REGION_BASE_URLS,
    "nvidia": NVIDIA_TRANSLATION_REGION_BASE_URLS,
    "hunyuan": HUNYUAN_TRANSLATION_REGION_BASE_URLS,
}
TRANSLATION_BACKEND_REGION_ALIASES = {
    "qianwen": QWEN_TRANSLATION_REGION_ALIASES,
    "deepseek": DEEPSEEK_TRANSLATION_REGION_ALIASES,
    "xiaomi": XIAOMI_TRANSLATION_REGION_ALIASES,
    "nvidia": NVIDIA_TRANSLATION_REGION_ALIASES,
    "hunyuan": HUNYUAN_TRANSLATION_REGION_ALIASES,
}
TRANSLATION_BACKEND_DEFAULT_REGIONS = {
    "qianwen": QWEN_TRANSLATION_DEFAULT_REGION,
    "deepseek": DEEPSEEK_TRANSLATION_DEFAULT_REGION,
    "xiaomi": XIAOMI_TRANSLATION_DEFAULT_REGION,
    "nvidia": NVIDIA_TRANSLATION_DEFAULT_REGION,
    "hunyuan": HUNYUAN_TRANSLATION_DEFAULT_REGION,
}
TRANSLATION_BACKEND_REGION_OPTION_KEYS = {
    "qianwen": (
        ("qwen_region_singapore", "singapore"),
        ("qwen_region_japan", "japan"),
        ("qwen_region_china_mainland", "china_mainland"),
        ("qwen_region_custom", "custom"),
    ),
    "deepseek": (
        ("deepseek_region_official", "official"),
        ("deepseek_region_custom", "custom"),
    ),
    "xiaomi": (
        ("xiaomi_region_global", "global"),
        ("xiaomi_region_china_cluster", "china_cluster"),
        ("xiaomi_region_singapore_cluster", "singapore_cluster"),
        ("xiaomi_region_europe_cluster", "europe_cluster"),
        ("xiaomi_region_custom", "custom"),
    ),
    "nvidia": (
        ("nvidia_region_global", "global"),
        ("nvidia_region_custom", "custom"),
    ),
    "hunyuan": (
        ("hunyuan_region_china", "china"),
        ("hunyuan_region_global", "global"),
        ("hunyuan_region_custom", "custom"),
    ),
}

TRANSLATION_BACKENDS: dict[str, dict[str, object]] = {
    "openai": {
        "label": "GPT",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-6-luna",
        "model_list": "remote",
        "model_list_include": ("gpt-",),
        "model_list_exclude": (
            "search",
            "codex",
            "computer-use",
            "deep-research",
            "cyber",
            "rosalind",
            "translate",
            "diarize",
            "-oss",
            "instruct",
            "-pro",
            "chat-latest",
        ),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "custom_headers_input": True,
        "streaming_input": True,
        "streaming": False,
        "granular_timeout_input": True,
    },
    # One entry for every relay station (中转站): GPT, Claude, Grok, Gemini ...
    # models behind one Base URL. The model list is fetched from the relay;
    # ``protocol`` picks the wire format (OpenAI chat completions by default,
    # Anthropic messages for relays called over /v1/messages, which may serve
    # non-Claude models too).
    "openai_compatible": {
        "label": "Relay Compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-6-luna",
        "model_list": "remote",
        # Relays list what the player's relay serves; DeepSeek pro models are
        # hidden everywhere (DeepSeek is offered as flash only). Slow "pro"
        # reasoning modes, coding-tuned codex models (Responses-first),
        # search-preview models, completion-only instruct models, multi-agent
        # and batch-only variants cannot serve live subtitles.
        "model_list_exclude_patterns": (
            DEEPSEEK_PRO_MODEL_PATTERN,
            r"(^|/)(o1-pro|o3-pro)",
            r"(^|/)gpt-[0-9.]+(-[a-z]+)?-pro([-:]|$)",
            r"^(?:[^/]+/)?gpt-[0-9.]+-codex",
            r"(^|/)codex-mini",
            r"search-preview",
            r"(^|/)gpt-3\.5-turbo-instruct",
            r"-multi-agent",
            r":batch$",
        ),
        # Recommended ids, matched by family id (openai/gpt-6-luna and
        # anthropic/claude-haiku-5.5 count). OpenRouter serves DeepSeek flash
        # as deepseek-v4.1-flash and ~deepseek/deepseek-flash-latest.
        "model_list_priority": (
            "gpt-6-luna",
            "gpt-5.6-luna",
            "claude-haiku-5-5",
            "deepseek-flash",
            "deepseek-v4.1-flash",
            "deepseek-flash-latest",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "grok-4.3",
        ),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "base_url_input": "entry",
        "custom_headers_input": True,
        "streaming_input": True,
        "streaming": False,
        "granular_timeout_input": True,
        "protocol_input": True,
        "protocol": "openai",
        "api_key_hint": (
            "Enter the Base URL and API key of your relay station (中转站). "
            "Mio reads the relay's model list so you can pick any model it "
            "serves - GPT, Claude, Grok, Gemini and others."
        ),
        "model_hint": (
            "Pick a model from the list read from your relay, or type a model "
            "id. Refresh the list after changing the Base URL, key or protocol."
        ),
    },
    "local_ai": {
        "label": "Local AI",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3:4b-instruct",
        "model_list": "remote",
        # The player's own server lists what is installed; DeepSeek pro tags
        # (deepseek-v4-pro:cloud) are hidden like everywhere else, and any
        # model name can still be typed.
        "model_list_exclude_patterns": (DEEPSEEK_PRO_MODEL_PATTERN,),
        "timeout_s": 30.0,
        "max_output_tokens": 256,
        "max_retries": 0,
        "model_input": "entry",
        "base_url_input": "entry",
        "api_key_required": False,
        "model_hint": "Pick a model your local OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM) reports, or type its exact name.",
        "api_key_hint": "Leave blank unless your local server requires an API key.",
    },
    "google_web": {
        "label": "Google Web",
        "base_url": "https://translate.googleapis.com/translate_a/single",
        "model": "google-web",
        "timeout_s": 8.0,
        "max_output_tokens": 192,
        "max_retries": 1,
        "model_input": "select",
        "api_key_required": False,
        "api_key_input": "hidden",
        "model_hint": (
            "No API key required. Uses Google's public web translation endpoint; "
            "quality is strong where Google services are reachable, but mainland "
            "China access may be unreliable."
        ),
    },
    "microsoft_edge_web": {
        "label": "Bing Translate (Free)",
        "base_url": "https://edge.microsoft.com/translate/translatetext",
        "model": "bing",
        "timeout_s": 8.0,
        "max_output_tokens": 192,
        "max_retries": 1,
        "model_input": "select",
        "api_key_required": False,
        "api_key_input": "hidden",
        "model_hint": (
            "No API key required. Uses Microsoft Edge's web translation service; "
            "it is not the supported Azure Translator API and has no availability SLA."
        ),
    },
    "mymemory": {
        "label": "MyMemory",
        "base_url": "https://api.mymemory.translated.net/get",
        "model": "mymemory",
        "timeout_s": 8.0,
        "max_output_tokens": 192,
        "max_retries": 1,
        "model_input": "select",
        "api_key_required": False,
        "api_key_input": "hidden",
        "model_hint": (
            "No API key required. Good as a zero-setup fallback, with public "
            "quota and translation-memory quality that can vary by language pair."
        ),
    },
    "deepl": {
        "label": "DeepL Free",
        "base_url": "https://api-free.deepl.com/v2",
        "model": "deepl-api",
        "timeout_s": 10.0,
        "max_output_tokens": 192,
        "max_retries": 1,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use a DeepL API Free key. The default endpoint is api-free.deepl.com; "
            "paid DeepL API keys should use https://api.deepl.com/v2."
        ),
        "model_hint": "Dedicated DeepL translation API. The model field is informational.",
    },
    "libretranslate": {
        "label": "LibreTranslate",
        "base_url": "http://127.0.0.1:5000",
        "model": "libretranslate",
        "timeout_s": 10.0,
        "max_output_tokens": 192,
        "max_retries": 1,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_required": False,
        "api_key_hint": (
            "Optional. Leave blank for a local/self-hosted LibreTranslate server "
            "that does not require an API key."
        ),
        "model_hint": (
            "Use a local LibreTranslate server for no provider-side quota. Public "
            "instances may require an API key or rate-limit requests."
        ),
    },
    "qianwen": {
        "label": "Qwen",
        "base_url": QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
        "model": "qwen-mt-flash",
        "model_list": "remote",
        # The backend speaks Qwen-MT's translation_options protocol, so only
        # Qwen-MT models can be routed through it.
        "model_list_include": ("qwen-mt-",),
        "model_list_exclude": ("qwen-mt-image", "qwen-mt-uni", "realtime", "livetranslate"),
        "timeout_s": 20.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": "Use the API key issued for the selected Qwen service region. Japan requires the workspace-specific Base URL issued by Model Studio.",
    },
    "hunyuan": {
        "label": "Tencent Hunyuan",
        "base_url": HUNYUAN_TRANSLATION_BASE_URL_CHINA,
        "model": "hy3",
        "model_list": "remote",
        # Tencent's own text models: hy3 / hy4-preview and the hy-mt2
        # translation models. TokenHub also lists image, video, speech and
        # third-party models, which this backend does not offer.
        "model_list_include": ("hy",),
        "model_list_exclude": (
            "image",
            "video",
            "vision",
            "embedding",
            "3d",
            "tts",
            "asr",
            "speech",
            "voice",
            "audio",
            "role",
            "-code",
            "-vl",
        ),
        "timeout_s": 20.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use a Tencent TokenHub API key from the site you choose (China or "
            "Global). Keys from the retired Hunyuan console no longer work."
        ),
        "model_hint": (
            "hy3 is the low-latency default; hy-mt2-plus and hy-mt2-lite are "
            "Tencent's dedicated translation models."
        ),
    },
    "xiaomi": {
        "label": "Xiaomi AI",
        "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
        "model": "mimo-v2.6-flash",
        "model_list": "remote",
        "model_list_include": ("mimo-",),
        "model_list_exclude": ("-tts", "-asr", "voiceclone", "voicedesign", "audio", "embed", "image", "video"),
        "extra_body": {"thinking": {"type": "disabled"}},
        "prefer_max_completion_tokens": True,
        "timeout_s": 20.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use the Xiaomi MiMo API Key that matches the selected service "
            "region. Do not mix pay-as-you-go keys with Token Plan clusters."
        ),
        "model_hint": (
            "mimo-v2.6-flash is the low-latency default; mimo-v2.6-pro gives "
            "higher quality, and the ultraspeed variant costs about 10x more."
        ),
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
        "model": "deepseek-flash",
        "model_list": "remote",
        # DeepSeek is offered as its flash model only (pro models hidden).
        "model_list_include": ("deepseek-",),
        "model_list_require": ("flash",),
        "model_list_exclude": ("reasoner", "coder", "prover", "math", "-exp", "preview"),
        "model_list_exclude_patterns": (DEEPSEEK_PRO_MODEL_PATTERN,),
        "timeout_s": 20.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": "Use a DeepSeek Open Platform API Key. If you use a proxy or third-party relay key, choose Custom in Service Region and enter the matching Base URL.",
    },
    "zhipu": {
        "label": "GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flashx",
        "model_list": "remote",
        # GLM's undocumented /models omits the flash/flashx ids, so the
        # built-in list is merged into whatever it returns.
        "model_list_merge_builtin": True,
        "model_list_include": ("glm-",),
        "model_list_exclude": (
            "-thinking",
            "voice",
            "realtime",
            "tts",
            "asr",
            "ocr",
            "image",
            "cogview",
            "cogvideo",
            "vidu",
            "autoglm",
            "codegeex",
            "charglm",
            "emohaa",
            "glm-4-long",
        ),
        "model_list_exclude_patterns": (r"^glm-[0-9.]+v($|-)",),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.6-flash",
        "model_list": "remote",
        "model_list_include": ("gemini-",),
        "model_list_exclude": (
            "embedding",
            "imagen",
            "veo",
            "tts",
            "audio",
            "live",
            "image",
            "nano-banana",
            "robotics",
            "computer-use",
            "cyber",
            "transcribe",
            "translate",
            "lyria",
            "omni",
            "deep-research",
            "antigravity",
            "gemma",
            "aqa",
            "learnlm",
            "-exp",
            "thinking",
            "customtools",
            "-pro",
            "-latest",
        ),
        "model_list_exclude_patterns": (r"^gemini-[12]\.",),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.6",
        "model_list": "remote",
        "model_list_include": ("kimi-",),
        "model_list_exclude": (
            "-code",
            "kimi-k2-",
            "kimi-latest",
            "thinking",
            "preview",
            "vision",
            "embedding",
            "audio",
        ),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "xai": {
        "label": "xAI",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "model_list": "remote",
        "model_list_include": ("grok-",),
        "model_list_exclude": (
            "imagine",
            "image",
            "video",
            "voice",
            "tts",
            "stt",
            "transcribe",
            "embed",
            "multi-agent",
            "grok-build",
            "grok-code",
        ),
        # "-reasoning" variants cannot switch reasoning off; "-non-reasoning"
        # ones are the fast picks.
        "model_list_exclude_patterns": (r"(?<!non)-reasoning$",),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "custom_headers_input": True,
        "streaming_input": True,
        "streaming": False,
        "granular_timeout_input": True,
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-medium-3-5",
        "model_list": "remote",
        "model_list_include": ("mistral-small-", "mistral-medium-", "mistral-large-", "ministral-", "ft:"),
        "model_list_exclude": (
            "labs-",
            "zai-",
            "embed",
            "ocr",
            "moderation",
            "shieldstral",
            "codestral",
            "devstral",
            "voxtral",
            "magistral",
            "pixtral",
            "leanstral",
            "tts",
            "transcribe",
            "realtime",
            "saba",
            "mamba",
            "mathstral",
        ),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "doubao": {
        "label": "Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-lite-260915",
        # Ark exposes no model list to an API key (only to account AK/SK), so
        # Doubao keeps a built-in list; any current model or ep- endpoint id
        # can still be typed.
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "model_hint": (
            "Pick a current Ark model or type any model id or ep- inference "
            "endpoint id from the Ark console."
        ),
    },
    "nvidia": {
        "label": "NVIDIA AI",
        "base_url": NVIDIA_TRANSLATION_BASE_URL,
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "model_list": "remote",
        "model_list_exclude": (
            "embed",
            "retriever",
            "rerank",
            "nvclip",
            "vision",
            "vlm",
            "vila",
            "neva",
            "fuyu",
            "kosmos",
            "deplot",
            "parse",
            "cosmos",
            "-omni-",
            "video",
            "detector",
            "guard",
            "safety",
            "reward",
            "jailbreak",
            "gliner",
            "topic-control",
            "starcoder",
            "codestral",
            "codellama",
            "codegemma",
            "usdcode",
            "coder",
            "poolside/",
            "riva-translate",
            "chatqa",
            "palmyra-med",
            "palmyra-fin",
            "/ising-",
            "calibration",
            "muse-glimmer",
            "recurrentgemma",
        ),
        "model_list_exclude_patterns": (
            r"-reasoning$",
            r"-thinking$",
            r"^moonshotai/kimi-k3$",
            r"-v0\.1$",
            DEEPSEEK_PRO_MODEL_PATTERN,
        ),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use an NVIDIA API Catalog key for the hosted endpoint, or choose "
            "Custom for a self-hosted NIM/proxy endpoint."
        ),
        "model_hint": (
            "Hosted NIM model ids use provider/model format. For self-hosted "
            "NIM endpoints, enter the model id exposed by that deployment."
        ),
    },
    "anthropic": {
        "label": "Claude",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-5",
        "model_list": "remote",
        "model_list_include": ("claude-",),
        # Fable / Mythos think on every request and cost several times more:
        # not a fit for live subtitles.
        "model_list_exclude": ("mythos", "fable"),
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "editable",
        "custom_headers_input": True,
        "streaming_input": True,
        "streaming": False,
        "granular_timeout_input": True,
    },
}

TRANSLATION_MODEL_PRESETS: dict[str, tuple[str, ...]] = {
    # Built-in recommendations. Backends that read their list from the
    # provider only show these until the first successful refresh (or when
    # the provider cannot be reached).
    "openai": (
        "gpt-6-luna",
        "gpt-6-sol",
        "gpt-5.4-mini",
        "gpt-5.6-luna",
    ),
    # The relay list comes from the relay itself (fetched at runtime).
    "openai_compatible": (),
    "local_ai": (),
    "google_web": ("google-web",),
    "microsoft_edge_web": ("bing",),
    "mymemory": ("mymemory",),
    "deepl": ("deepl-api",),
    "libretranslate": ("libretranslate",),
    "deepseek": ("deepseek-flash",),
    "zhipu": (
        "glm-4.7-flashx",
        "glm-4.7-flash",
        "glm-5.2",
        "glm-4.7",
        "glm-5.3-flash",
    ),
    "qianwen": (
        "qwen-mt-flash",
        "qwen-mt-lite",
        "qwen-mt-plus",
    ),
    "hunyuan": (
        "hy3",
        "hy-mt2-plus",
        "hy-mt2-lite",
        "hy4-preview",
    ),
    "xiaomi": (
        "mimo-v2.6-flash",
        "mimo-v2.6-pro",
        "mimo-v2.6-pro-ultraspeed",
    ),
    "gemini": (
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ),
    "kimi": (
        "kimi-k2.6",
        "kimi-k3",
    ),
    "xai": (
        "grok-4.3",
        "grok-4.20-0309-non-reasoning",
        "grok-4.7",
        "grok-4.6",
    ),
    "mistral": (
        "mistral-medium-3-5",
        "mistral-small-2603",
        "ministral-14b-2512",
        "ministral-8b-2512",
    ),
    "doubao": (
        "doubao-seed-2-1-lite-260915",
        "doubao-seed-2-0-mini-260428",
        "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-1-pro-260915",
        "deepseek-v4-1-flash-260910",
    ),
    "nvidia": (
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "deepseek-ai/deepseek-v4.1-flash",
        "google/gemma-4-31b-it",
        "nvidia/nemotron-3-super-120b-a12b",
    ),
    "anthropic": (
        "claude-opus-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ),
}

TRANSLATION_MODEL_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "openai": {
        "gpt-6-luna": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "gpt-6-sol": {
            "speed": "fast",
            "quality": "very_high",
            "fit": "recommended",
            "note": "quality_first",
        },
        "gpt-5.4-mini": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "economy_first",
        },
        "gpt-5.6-luna": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
    },
    "openai_compatible": {},
    "google_web": {
        "google-web": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "no_key_simple",
        },
    },
    "microsoft_edge_web": {
        "bing": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "no_key_simple",
        },
    },
    "mymemory": {
        "mymemory": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "no_key_fallback",
        },
    },
    "deepl": {
        "deepl-api": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "mt_quality",
        },
    },
    "libretranslate": {
        "libretranslate": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "self_hosted_free",
        },
    },
    "deepseek": {
        "deepseek-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
    },
    "zhipu": {
        "glm-4.7-flashx": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "glm-4.7-flash": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "glm-5.2": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "quality_first",
        },
        "glm-4.7": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "glm-5.3-flash": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
    },
    "qianwen": {
        "qwen-mt-flash": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "qwen-mt-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "qwen-mt-plus": {
            "speed": "fast",
            "quality": "very_high",
            "fit": "recommended",
            "note": "mt_quality",
        },
    },
    "hunyuan": {
        "hy3": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "hy-mt2-plus": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "mt_quality",
        },
        "hy-mt2-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "hy4-preview": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "general",
            "note": "quality_first",
        },
    },
    "xiaomi": {
        "mimo-v2.6-flash": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "mimo-v2.6-pro": {
            "speed": "fast",
            "quality": "very_high",
            "fit": "recommended",
            "note": "quality_first",
        },
        "mimo-v2.6-pro-ultraspeed": {
            "speed": "very_fast",
            "quality": "very_high",
            "fit": "general",
            "note": "ultra_fast",
        },
    },
    "gemini": {
        "gemini-3.6-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "gemini-3.5-flash-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "gemini-3.8-flash": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "recommended",
            "note": "quality_first",
        },
    },
    "kimi": {
        "kimi-k2.6": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "kimi-k3": {
            "speed": "slow",
            "quality": "very_high",
            "fit": "general",
            "note": "quality_first",
        },
    },
    "xai": {
        "grok-4.3": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "grok-4.20-0309-non-reasoning": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "grok-4.7": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "general",
            "note": "quality_first",
        },
        "grok-4.6": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "general",
            "note": "general_high_quality",
        },
    },
    "mistral": {
        "mistral-medium-3-5": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "mistral-small-2603": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "ministral-14b-2512": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "ministral-8b-2512": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "ultra_fast",
        },
    },
    "nvidia": {
        "nvidia/nemotron-3.5-lightning-30b-a3b": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "deepseek-ai/deepseek-v4.1-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "google/gemma-4-31b-it": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "nvidia/nemotron-3-super-120b-a12b": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "general",
            "note": "quality_first",
        },
    },
    "doubao": {
        "doubao-seed-2-1-lite-260915": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "doubao-seed-2-0-mini-260428": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "economy_first",
        },
        "doubao-seed-2-1-turbo-260628": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "doubao-seed-2-1-pro-260915": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "recommended",
            "note": "quality_first",
        },
        "deepseek-v4-1-flash-260910": {
            "speed": "fast",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
    },
    "anthropic": {
        "claude-opus-5": {
            "speed": "balanced",
            "quality": "very_high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "claude-sonnet-5-5": {
            "speed": "fast",
            "quality": "very_high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "claude-haiku-5-5": {
            "speed": "very_fast",
            "quality": "high",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "claude-sonnet-5": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "claude-haiku-4-5": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
    },
}

TRANSLATION_BACKEND_LABELS: dict[str, dict[str, str]] = {
    "openai": {"zh-CN": "GPT", "en": "GPT", "ja": "GPT", "ru": "GPT", "ko": "GPT"},
    "openai_compatible": {
        "zh-CN": "中转站兼容",
        "en": "Relay Compatible",
        "ja": "中継サービス互換",
        "ru": "Совместимость с ретранслятором",
        "ko": "중계 서비스 호환",
    },
    "local_ai": {
        "zh-CN": "本地 AI",
        "en": "Local AI",
        "ja": "ローカル AI",
        "ru": "Локальный ИИ",
        "ko": "로컬 AI",
    },
    "google_web": {
        "zh-CN": "Google 网页翻译",
        "en": "Google Web",
        "ja": "Google ウェブ翻訳",
        "ru": "Google Веб-перевод",
        "ko": "Google 웹 번역",
    },
    "microsoft_edge_web": {
        "zh-CN": "Bing翻译(免费)",
        "en": "Bing Translate (Free)",
        "ja": "Bing 翻訳（無料）",
        "ru": "Bing Переводчик (бесплатно)",
        "ko": "Bing 번역(무료)",
    },
    "mymemory": {"zh-CN": "MyMemory", "en": "MyMemory", "ja": "MyMemory", "ru": "MyMemory", "ko": "MyMemory"},
    "deepl": {
        "zh-CN": "DeepL 免费版",
        "en": "DeepL Free",
        "ja": "DeepL 無料版",
        "ru": "DeepL (бесплатный тариф)",
        "ko": "DeepL 무료",
    },
    "libretranslate": {"zh-CN": "LibreTranslate", "en": "LibreTranslate", "ja": "LibreTranslate", "ru": "LibreTranslate", "ko": "LibreTranslate"},
    "qianwen": {"zh-CN": "Qwen", "en": "Qwen", "ja": "Qwen", "ru": "Qwen", "ko": "Qwen"},
    "hunyuan": {"zh-CN": "腾讯混元", "en": "Tencent Hunyuan", "ja": "Tencent Hunyuan", "ru": "Tencent Hunyuan", "ko": "Tencent Hunyuan"},
    "xiaomi": {"zh-CN": "小米 AI", "en": "Xiaomi AI", "ja": "Xiaomi AI", "ru": "Xiaomi AI", "ko": "Xiaomi AI"},
    "deepseek": {"zh-CN": "DeepSeek", "en": "DeepSeek", "ja": "DeepSeek", "ru": "DeepSeek", "ko": "DeepSeek"},
    "zhipu": {"zh-CN": "GLM", "en": "GLM", "ja": "GLM", "ru": "GLM", "ko": "GLM"},
    "gemini": {"zh-CN": "Gemini", "en": "Gemini", "ja": "Gemini", "ru": "Gemini", "ko": "Gemini"},
    "kimi": {"zh-CN": "Kimi", "en": "Kimi", "ja": "Kimi", "ru": "Kimi", "ko": "Kimi"},
    "xai": {"zh-CN": "xAI", "en": "xAI", "ja": "xAI", "ru": "xAI", "ko": "xAI"},
    "mistral": {"zh-CN": "Mistral", "en": "Mistral", "ja": "Mistral", "ru": "Mistral", "ko": "Mistral"},
    "doubao": {"zh-CN": "豆包", "en": "Doubao", "ja": "Doubao", "ru": "Doubao", "ko": "Doubao"},
    "nvidia": {"zh-CN": "NVIDIA AI", "en": "NVIDIA AI", "ja": "NVIDIA AI", "ru": "NVIDIA AI", "ko": "NVIDIA AI"},
    "anthropic": {"zh-CN": "Claude", "en": "Claude", "ja": "Claude", "ru": "Claude", "ko": "Claude"},
}

# Keep profiles strictly aligned with selectable models. This prevents removed,
# deprecated, reasoning-only, or known slow models from resurfacing through
# recommendation metadata even when an older catalog is cached.
for _profile_backend, _profiles in tuple(TRANSLATION_MODEL_PROFILES.items()):
    if _profile_backend == RELAY_BACKEND:
        TRANSLATION_MODEL_PROFILES[_profile_backend] = {}
        continue
    _allowed_models = set(TRANSLATION_MODEL_PRESETS.get(_profile_backend, ()))
    TRANSLATION_MODEL_PROFILES[_profile_backend] = {
        _model: _profile
        for _model, _profile in _profiles.items()
        if _model in _allowed_models
    }

TRANSLATION_MODEL_RECOMMENDATION_SCORES: dict[str, dict[str, str]] = {
    "openai": {
        "gpt-6-luna": "9.5",
        "gpt-6-sol": "9.3",
        "gpt-5.4-mini": "8.9",
        "gpt-5.6-luna": "9.0",
    },
    "openai_compatible": {},
    "google_web": {
        "google-web": "8.6",
    },
    "microsoft_edge_web": {
        "bing": "8.5",
    },
    "mymemory": {
        "mymemory": "7.2",
    },
    "deepl": {
        "deepl-api": "9.1",
    },
    "libretranslate": {
        "libretranslate": "8.0",
    },
    "deepseek": {
        "deepseek-flash": "9.2",
    },
    "zhipu": {
        "glm-4.7-flashx": "9.1",
        "glm-4.7-flash": "8.7",
        "glm-5.2": "9.2",
        "glm-4.7": "8.9",
        "glm-5.3-flash": "8.8",
    },
    "qianwen": {
        "qwen-mt-flash": "9.5",
        "qwen-mt-lite": "8.6",
        "qwen-mt-plus": "9.6",
    },
    "hunyuan": {
        "hy3": "9.1",
        "hy-mt2-plus": "9.0",
        "hy-mt2-lite": "8.4",
        "hy4-preview": "8.8",
    },
    "xiaomi": {
        "mimo-v2.6-flash": "9.1",
        "mimo-v2.6-pro": "9.2",
        "mimo-v2.6-pro-ultraspeed": "8.8",
    },
    "gemini": {
        "gemini-3.6-flash": "9.4",
        "gemini-3.5-flash-lite": "8.7",
        "gemini-3.8-flash": "9.3",
    },
    "kimi": {
        "kimi-k2.6": "9.1",
        "kimi-k3": "8.8",
    },
    "xai": {
        "grok-4.3": "9.2",
        "grok-4.20-0309-non-reasoning": "8.8",
        "grok-4.7": "8.9",
        "grok-4.6": "8.8",
    },
    "mistral": {
        "mistral-medium-3-5": "8.9",
        "mistral-small-2603": "8.8",
        "ministral-14b-2512": "8.3",
        "ministral-8b-2512": "7.8",
    },
    "nvidia": {
        "nvidia/nemotron-3.5-lightning-30b-a3b": "8.9",
        "deepseek-ai/deepseek-v4.1-flash": "8.7",
        "google/gemma-4-31b-it": "8.5",
        "nvidia/nemotron-3-super-120b-a12b": "8.6",
    },
    "doubao": {
        "doubao-seed-2-1-lite-260915": "9.1",
        "doubao-seed-2-0-mini-260428": "8.5",
        "doubao-seed-2-1-turbo-260628": "8.8",
        "doubao-seed-2-1-pro-260915": "9.0",
        "deepseek-v4-1-flash-260910": "8.7",
    },
    "anthropic": {
        "claude-opus-5": "9.6",
        "claude-sonnet-5-5": "9.5",
        "claude-haiku-5-5": "9.1",
        "claude-sonnet-5": "9.3",
        "claude-haiku-4-5": "8.5",
    },
}

for _score_backend, _scores in tuple(
    TRANSLATION_MODEL_RECOMMENDATION_SCORES.items()
):
    if _score_backend == RELAY_BACKEND:
        TRANSLATION_MODEL_RECOMMENDATION_SCORES[_score_backend] = {}
        continue
    _allowed_models = set(TRANSLATION_MODEL_PRESETS.get(_score_backend, ()))
    TRANSLATION_MODEL_RECOMMENDATION_SCORES[_score_backend] = {
        _model: _score
        for _model, _score in _scores.items()
        if _model in _allowed_models
    }


def get_backend_order() -> tuple:
    return _catalog_backend_order()


BACKEND_ORDER = tuple(TRANSLATION_BACKENDS.keys())
DEFAULT_CHINESE_BACKEND = "microsoft_edge_web"
DEFAULT_INTERNATIONAL_BACKEND = "microsoft_edge_web"
DEFAULT_BACKEND = DEFAULT_CHINESE_BACKEND
# Edge speech recognition needs no download, no key and no GPU, so a fresh
# install can start translating immediately instead of stalling on a model
# download the player has not asked for yet.
DEFAULT_ASR_ENGINE = "edge-stt"

# Speech engine naming lives here rather than in the settings window, because
# the quick-switch panel needs the same names and must not import that module.
TTS_ENGINE_LABELS = {
    "qwen_vc": "Voice Cloning（API）",
    "voicevox": "VOICEVOX（本地）",
    "mimo_tts": "MiMo TTS（API）",
    "qwen_tts": "Qwen TTS（API）",
    "style_bert_vits2": "自定义音色",
}

TTS_ENGINE_I18N_KEYS = {
    "qwen_vc": "tts_engine_voice_clone",
    "voicevox": "tts_engine_voicevox",
    "mimo_tts": "tts_engine_mimo_tts",
    "qwen_tts": "tts_engine_qwen_tts",
    "style_bert_vits2": "tts_engine_style_bert_vits2",
}
UI_LANGUAGE_LABELS = {code: label for label, code in UI_LANGUAGE_OPTIONS}
TARGET_LANGUAGE_OPTIONS = ()
MANUAL_SOURCE_LANGUAGE_OPTIONS = ()


def _resolve_ui_language(language: str | None) -> str:
    if isinstance(language, str):
        normalized = language.strip().replace("_", "-").casefold()
        aliases = {
            "zh": "zh-CN",
            "zh-cn": "zh-CN",
            "zh-hans": "zh-CN",
            "cn": "zh-CN",
            "en-us": "en",
            "en-gb": "en",
            "ja-jp": "ja",
            "jp": "ja",
            "ru-ru": "ru",
            "ko-kr": "ko",
            "kr": "ko",
        }
        exact = {candidate.casefold(): candidate for candidate in LANGUAGE_DISPLAY_NAMES}
        if normalized in exact:
            return exact[normalized]
        if normalized in aliases:
            return aliases[normalized]
        base = normalized.split("-", 1)[0]
        for candidate in LANGUAGE_DISPLAY_NAMES:
            if candidate.casefold().split("-", 1)[0] == base:
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
    if backend in LEGACY_RELAY_BACKENDS:
        backend = RELAY_BACKEND
    if backend in _catalog_backends() and backend not in DISABLED_TRANSLATION_BACKENDS:
        return str(backend)
    return DEFAULT_BACKEND


def default_backend_for_ui_language(language: str | None) -> str:
    return DEFAULT_BACKEND


def _region_backend(backend: str | None) -> str:
    text = str(backend or "").strip()
    region_base_urls = _catalog_region_base_urls()
    if text in region_base_urls:
        return text
    if text in _catalog_backends():
        normalized = normalize_backend(text)
        if normalized in region_base_urls:
            return normalized
    return ""


def _normalize_region_token(region: object) -> str:
    return str(region or "").strip().lower().replace(" ", "_").replace("-", "_")


def backend_has_service_regions(backend: str | None) -> bool:
    return bool(_region_backend(backend))


def get_backend_region_options(backend: str | None) -> tuple[tuple[str, str], ...]:
    backend_code = _region_backend(backend)
    return tuple(TRANSLATION_BACKEND_REGION_OPTION_KEYS.get(backend_code, ()))


def get_backend_known_base_urls(backend: str | None) -> frozenset[str]:
    backend_code = _region_backend(backend)
    base_urls = _catalog_region_base_urls().get(backend_code, {})
    return frozenset(value for value in base_urls.values() if value)


def normalize_backend_region(
    backend: str | None,
    region: object,
    default_region: str | None = None,
) -> str:
    backend_code = _region_backend(backend)
    if not backend_code:
        return ""
    aliases = _catalog_region_aliases().get(backend_code, {})
    base_urls = _catalog_region_base_urls().get(backend_code, {})
    text = _normalize_region_token(region)
    text = aliases.get(text, text)
    if text in base_urls or text == "custom":
        return text
    fallback = _normalize_region_token(default_region)
    fallback = aliases.get(fallback, fallback)
    if fallback in base_urls or fallback == "custom":
        return fallback
    return _catalog_default_regions().get(backend_code, "")


def get_backend_region_base_url(backend: str | None, region: object) -> str:
    backend_code = _region_backend(backend)
    if not backend_code:
        return ""
    region_code = normalize_backend_region(backend_code, region)
    return _catalog_region_base_urls()[backend_code].get(region_code, "")


def backend_region_from_base_url(backend: str | None, base_url: object) -> str:
    backend_code = _region_backend(backend)
    if not backend_code:
        return ""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    for region, url in _catalog_region_base_urls()[backend_code].items():
        if text == str(url).rstrip("/"):
            return region
    return "custom"


def backend_region_for_ui_language(backend: str | None, language: str | None) -> str:
    backend_code = _region_backend(backend)
    if not backend_code:
        return ""
    resolved = _resolve_ui_language(language)
    if backend_code == "qianwen":
        if resolved.startswith("zh"):
            return "china_mainland"
        return "singapore"
    if backend_code == "xiaomi":
        return "china_cluster" if resolved.startswith("zh") else "global"
    if backend_code == "hunyuan":
        return "china" if resolved.startswith("zh") else "global"
    return _catalog_default_regions().get(backend_code, "")


def normalize_qwen_translation_region(region: object) -> str:
    return normalize_backend_region("qianwen", region)


def get_qwen_translation_base_url(region: object) -> str:
    return get_backend_region_base_url("qianwen", region)


def qwen_translation_region_from_base_url(base_url: object) -> str:
    return backend_region_from_base_url("qianwen", base_url)


def qwen_translation_region_for_ui_language(language: str | None) -> str:
    return backend_region_for_ui_language("qianwen", language)


def qwen_translation_base_url_for_ui_language(language: str | None) -> str:
    return get_qwen_translation_base_url(
        qwen_translation_region_for_ui_language(language)
    )


def _localized_static_label(labels: Mapping[str, str], ui_language: str | None) -> str:
    resolved = _resolve_ui_language(ui_language)
    return (
        labels.get(resolved)
        or labels.get(resolved.split("-", 1)[0])
        or labels.get("en")
        or labels.get(DEFAULT_UI_LANGUAGE)
        or next(iter(labels.values()))
    )


def get_output_format_label(
    fmt_code: str | None, ui_language: str | None = None
) -> str:
    code = normalize_output_format(str(fmt_code or ""))
    return _localized_static_label(OUTPUT_FORMAT_LABELS[code], ui_language)


def get_output_format_options(
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (get_output_format_label(code, ui_language), code)
        for _label, code in OUTPUT_FORMAT_OPTIONS
    )


# Deprecated: output_format_2 has been merged into output_format.
# Keep the label/options functions for backward compatibility with callers
# that still reference them during migration.
def get_output_format_2_label(
    fmt_code: str | None, ui_language: str | None = None
) -> str:
    code = normalize_output_format_2(str(fmt_code or ""))
    return _localized_static_label(OUTPUT_FORMAT_2_LABELS[code], ui_language)


def get_output_format_2_options(
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (get_output_format_2_label(code, ui_language), code)
        for _label, code in OUTPUT_FORMAT_2_OPTIONS
    )


def normalize_output_format(output_format: str | None) -> str:
    if output_format in LEGACY_OUTPUT_FORMAT_ALIASES:
        return LEGACY_OUTPUT_FORMAT_ALIASES[str(output_format)]
    valid_codes = {code for _label, code in OUTPUT_FORMAT_OPTIONS}
    if output_format in valid_codes:
        return str(output_format)
    return OUTPUT_FORMAT_OPTIONS[0][1]


# Deprecated: output_format_2 has been merged into output_format.
def normalize_output_format_2(output_format: str | None) -> str:
    valid_codes = {code for _label, code in OUTPUT_FORMAT_2_OPTIONS}
    if output_format in valid_codes:
        return str(output_format)
    return OUTPUT_FORMAT_2_DISABLED


def get_backend_spec(backend: str | None) -> dict[str, object]:
    return _catalog_backends()[normalize_backend(backend)]


def get_backend_label(
    backend: str | None,
    ui_language: str | None = None,
) -> str:
    normalized = normalize_backend(backend)
    if ui_language is not None:
        labels = TRANSLATION_BACKEND_LABELS.get(normalized)
        if labels:
            return _localized_static_label(labels, ui_language)
    return str(get_backend_spec(normalized)["label"])


def get_backend_value(backend: str | None, key: str) -> str:
    value = get_backend_spec(backend).get(key, "")
    return str(value)


def relay_protocol_default(protocol: object, key: str) -> str:
    """Default ``key`` of the relay backend when it speaks ``protocol``.

    The relay's own defaults are OpenAI's. A relay switched to the Anthropic
    messages protocol starts from Claude's endpoint and default model instead;
    an OpenAI URL or a GPT model id would never work there.
    """

    if key in {"model", "base_url"} and (
        normalize_relay_protocol(protocol) == RELAY_PROTOCOL_ANTHROPIC
    ):
        return get_backend_value("anthropic", key)
    return get_backend_value(RELAY_BACKEND, key)


def backend_model_is_selectable(backend: str | None) -> bool:
    return str(get_backend_spec(backend).get("model_input", "select")) in {
        "select",
        "editable",
    }


def backend_model_is_editable(backend: str | None) -> bool:
    return str(get_backend_spec(backend).get("model_input", "select")) == "editable"


def get_backend_model_hint(backend: str | None) -> str:
    return str(get_backend_spec(backend).get("model_hint", "")).strip()


def backend_base_url_is_editable(backend: str | None) -> bool:
    return str(get_backend_spec(backend).get("base_url_input", "fixed")) == "entry"


def backend_api_key_is_required(backend: str | None) -> bool:
    return bool(get_backend_spec(backend).get("api_key_required", True))


def get_backend_api_key_hint(backend: str | None) -> str:
    return str(get_backend_spec(backend).get("api_key_hint", "")).strip()


def get_backend_config_value(
    trans_cfg: Mapping[str, object] | None,
    backend: str | None,
    key: str,
) -> str:
    normalized_backend = normalize_backend(backend)
    backend_cfg = (
        trans_cfg.get(normalized_backend, {}) if isinstance(trans_cfg, Mapping) else {}
    )
    if isinstance(backend_cfg, Mapping):
        value = backend_cfg.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
        if normalized_backend == RELAY_BACKEND:
            return relay_protocol_default(backend_cfg.get("protocol"), key)
    return get_backend_value(normalized_backend, key)


def model_list_source_key(backend: str | None, base_url: object, protocol: object = "") -> str:
    """Identify where a fetched model list came from.

    A list read from one endpoint (or one relay protocol) is never offered for
    another, so changing the Base URL invalidates a saved list.
    """

    normalized_backend = normalize_backend(backend)
    url = str(base_url or "").strip().rstrip("/").casefold()
    key = f"{normalized_backend}|{url}"
    if normalized_backend == RELAY_BACKEND:
        key += f"|{normalize_relay_protocol(protocol)}"
    return key


def backend_config_source_key(backend: str | None, backend_cfg: Mapping[str, object] | None) -> str:
    cfg = backend_cfg if isinstance(backend_cfg, Mapping) else {}
    base_url = str(cfg.get("base_url", "") or "").strip()
    if not base_url:
        base_url = (
            relay_protocol_default(cfg.get("protocol"), "base_url")
            if normalize_backend(backend) == RELAY_BACKEND
            else get_backend_value(backend, "base_url")
        )
    return model_list_source_key(backend, base_url, cfg.get("protocol"))


def saved_remote_model_list(
    backend: str | None,
    backend_cfg: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """The model list last read from the provider and saved in the config.

    Empty when nothing was saved or when the saved list belongs to a
    different endpoint than the one now configured.
    """

    if not isinstance(backend_cfg, Mapping):
        return ()
    models = backend_cfg.get("remote_models")
    source = str(backend_cfg.get("remote_models_source", "") or "")
    if not isinstance(models, (list, tuple)) or not models or not source:
        return ()
    normalized_backend = normalize_backend(backend)
    if source != backend_config_source_key(normalized_backend, backend_cfg):
        return ()
    result: list[str] = []
    for item in models:
        model = str(item or "").strip()
        if not model or len(model) > 200 or model in result:
            continue
        if is_retired_translation_model(normalized_backend, model):
            continue
        result.append(model)
        if len(result) >= 500:
            break
    return tuple(result)


def get_backend_model_options(
    backend: str | None,
    current_model: str | None = None,
    backend_cfg: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Models offered in selectors: the provider's own list when one was read.

    Falls back to the built-in recommendations when no list has been fetched
    for the configured endpoint yet.
    """

    normalized_backend = normalize_backend(backend)
    models = list(saved_remote_model_list(normalized_backend, backend_cfg))
    if not models:
        models = list(_catalog_model_presets().get(normalized_backend, ()))
    current = str(current_model or "").strip()
    if current and current not in models:
        models.insert(0, current)
    if models:
        return tuple(models)
    if normalized_backend == RELAY_BACKEND:
        protocol = backend_cfg.get("protocol") if isinstance(backend_cfg, Mapping) else ""
        fallback = relay_protocol_default(protocol, "model")
    else:
        fallback = get_backend_value(normalized_backend, "model")
    return (fallback,) if fallback else ()


def get_backend_model_profile(
    backend: str | None,
    model: str | None,
) -> dict[str, str]:
    normalized_backend = normalize_backend(backend)
    resolved_model = str(model or "").strip() or get_backend_value(
        normalized_backend, "model"
    )
    profile_backend = normalized_backend
    if normalized_backend == RELAY_BACKEND:
        # A relay serves models of many vendors; describe a known model with
        # its vendor's profile.
        lowered_model = resolved_model.casefold()
        profile_backend = next(
            (
                vendor
                for prefix, vendor in _RELAY_MODEL_VENDOR_PREFIXES
                if lowered_model.startswith(prefix)
            ),
            "openai",
        )
    backend_profiles = _catalog_model_profiles().get(profile_backend, {})
    profile = backend_profiles.get(resolved_model, {})
    score = TRANSLATION_MODEL_RECOMMENDATION_SCORES.get(profile_backend, {}).get(
        resolved_model,
        "6.5",
    )
    return {
        "model": resolved_model,
        "score": score,
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


# --------------------------------------------------------------------
# Catalog support — allows remote updates of translation backends/models
# --------------------------------------------------------------------


# None means the builtin catalog: the accessors below fall back to this
# module's own tables, which are exactly what catalog_loader.BUILTIN_CATALOG
# copies. Importing catalog_loader here would be circular (it imports this
# module), and when catalog_loader was imported first the import failed and
# silently left the catalog unset anyway.
_CATALOG = None
_CATALOG_LOCK = None  # placeholder for future thread-safety needs


def set_catalog(catalog) -> None:
    """Replace the active translation catalog with a remote-loaded one."""
    global _CATALOG
    _CATALOG = catalog


def _catalog_backends() -> dict:
    if _CATALOG is not None:
        backends = _CATALOG.translation_backends
    else:
        backends = TRANSLATION_BACKENDS
    return {
        backend: spec
        for backend, spec in backends.items()
        if backend not in DISABLED_TRANSLATION_BACKENDS
    }


def _catalog_model_presets() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_model_presets
    return TRANSLATION_MODEL_PRESETS


def _catalog_model_profiles() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_model_profiles
    return TRANSLATION_MODEL_PROFILES


def _catalog_region_base_urls() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_backend_region_base_urls
    return TRANSLATION_BACKEND_REGION_BASE_URLS


def _catalog_region_aliases() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_backend_region_aliases
    return TRANSLATION_BACKEND_REGION_ALIASES


def _catalog_default_regions() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_backend_default_regions
    return TRANSLATION_BACKEND_DEFAULT_REGIONS


def _catalog_backend_order() -> tuple:
    return tuple(_catalog_backends().keys())


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


def get_social_mode_options(
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
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


def get_social_tone_options(
    ui_language: str | None = None,
) -> tuple[tuple[str, str], ...]:
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
        return _with_code(
            _localized_language_name(default_code, ui_language), default_code
        )
    return _with_code(_localized_language_name(code_text, ui_language), code_text)


def get_target_language_name(code: str | None, ui_language: str | None = None) -> str:
    code_text = str(code or "") or SUPPORTED_TARGET_LANGUAGE_CODES[0]
    return _localized_language_name(code_text, ui_language)


def get_manual_source_label(code: str | None, ui_language: str | None = None) -> str:
    code_text = str(code or "") or SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES[0]
    return _localized_language_name(code_text, ui_language)


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
MANUAL_SOURCE_LANGUAGE_OPTIONS = get_manual_source_language_options(
    ui_language=DEFAULT_UI_LANGUAGE
)


def get_ui_language(config: Mapping[str, object] | None) -> str:
    ui_cfg = config.get("ui", {}) if isinstance(config, Mapping) else {}
    if isinstance(ui_cfg, Mapping):
        language = ui_cfg.get("language")
        if language is not None:
            return _resolve_ui_language(str(language))
    return DEFAULT_UI_LANGUAGE
