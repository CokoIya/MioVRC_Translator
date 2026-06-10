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
    ("\u8bd1\u6587\uff08\u539f\u53e5\uff09", "translated_with_original"),
    ("\u4ec5\u8bd1\u6587", "translated_only"),
    ("\u4ec5\u539f\u53e5", "original_only"),
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
    "original_only": {
        "zh-CN": "\u4ec5\u539f\u6587",
        "en": "Original only",
        "ja": "\u539f\u6587\u306e\u307f",
        "ru": "\u0422\u043e\u043b\u044c\u043a\u043e \u043e\u0440\u0438\u0433\u0438\u043d\u0430\u043b",
        "ko": "\uc6d0\ubb38\ub9cc",
    },
    "original_with_translated": {
        "zh-CN": "\u539f\u6587\uff08\u8bd1\u6587\uff09",
        "en": "Original (Translation)",
        "ja": "\u539f\u6587\uff08\u8a33\u6587\uff09",
        "ru": "\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u043f\u0435\u0440\u0435\u0432\u043e\u0434)",
        "ko": "\uc6d0\ubb38(\ubc88\uc5ed\ubb38)",
    },
    "original_with_translated1_translated2": {
        "zh-CN": "\u539f\u6587\uff08\u8bd1\uff081\uff09\uff08\u8bd1\uff082\uff09",
        "en": "Original (TL1) (TL2)",
        "ja": "\u539f\u6587\uff08\u8a33\uff081\uff09\uff08\u8a33\uff082\uff09",
        "ru": "\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b (\u041f\u041f1) (\u041f\u041f2)",
        "ko": "\uc6d0\ubb38\uff08\ubc88\uff081\uff09\uff08\ubc88\uff082\uff09",
    },
    "translated1_with_translated2": {
        "zh-CN": "\u8bd1\uff081\uff09\uff08\u8bd1\uff082\uff09",
        "en": "TL1 (TL2)",
        "ja": "\u8a33\uff081\uff09\uff08\u8a33\uff082\uff09",
        "ru": "\u041f\u041f1 (\u041f\u041f2)",
        "ko": "\ubc88\uff081\uff09\uff08\ubc88\uff082\uff09",
    },
    "translated1_with_translated2_original": {
        "zh-CN": "\u8bd1\uff081\uff09\uff08\u8bd1\uff082\uff09\uff08\u539f\u6587",
        "en": "TL1 (TL2) (Original)",
        "ja": "\u8a33\uff081\uff09\uff08\u8a33\uff082\uff09\uff08\u539f\u6587",
        "ru": "\u041f\u041f1 (\u041f\u041f2) (\u041e\u0440\u0438\u0433\u0438\u043d\u0430\u043b)",
        "ko": "\ubc88\uff081\uff09\uff08\ubc88\uff082\uff09\uff08\uc6d0\ubb38",
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
QWEN_TRANSLATION_DEFAULT_REGION = "china_mainland"
QWEN_TRANSLATION_REGION_BASE_URLS = {
    "china_mainland": QWEN_TRANSLATION_BASE_URL_MAINLAND,
    "singapore": QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
}
QWEN_TRANSLATION_REGION_ALIASES = {
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "china-mainland": "china_mainland",
    "intl": "singapore",
    "international": "singapore",
    "sg": "singapore",
}
QWEN_TRANSLATION_KNOWN_BASE_URLS = frozenset(QWEN_TRANSLATION_REGION_BASE_URLS.values())

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
}
TRANSLATION_BACKEND_REGION_ALIASES = {
    "qianwen": QWEN_TRANSLATION_REGION_ALIASES,
    "deepseek": DEEPSEEK_TRANSLATION_REGION_ALIASES,
    "xiaomi": XIAOMI_TRANSLATION_REGION_ALIASES,
    "nvidia": NVIDIA_TRANSLATION_REGION_ALIASES,
}
TRANSLATION_BACKEND_DEFAULT_REGIONS = {
    "qianwen": QWEN_TRANSLATION_DEFAULT_REGION,
    "deepseek": DEEPSEEK_TRANSLATION_DEFAULT_REGION,
    "xiaomi": XIAOMI_TRANSLATION_DEFAULT_REGION,
    "nvidia": NVIDIA_TRANSLATION_DEFAULT_REGION,
}
TRANSLATION_BACKEND_REGION_OPTION_KEYS = {
    "qianwen": (
        ("qwen_region_singapore", "singapore"),
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
}

TRANSLATION_BACKENDS: dict[str, dict[str, object]] = {
    "openai": {
        "label": "GPT",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "openai_compatible": {
        "label": "GPT Compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.5",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use this for OpenAI-compatible proxy or relay providers. Enter "
            "the Base URL and model id supplied by that provider."
        ),
        "model_hint": (
            "Choose the model id exposed by your proxy. If your saved model is "
            "not in the preset list, Mio keeps it available in this dropdown."
        ),
    },
    "local_ai": {
        "label": "Local AI",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b-instruct",
        "timeout_s": 30.0,
        "max_output_tokens": 256,
        "max_retries": 0,
        "model_input": "entry",
        "base_url_input": "entry",
        "api_key_required": False,
        "model_hint": "Enter the local model name exposed by your OpenAI-compatible server.",
        "api_key_hint": "Leave blank unless your local server requires an API key.",
    },
    "qianwen": {
        "label": "Qwen",
        "base_url": QWEN_TRANSLATION_BASE_URL_MAINLAND,
        "model": "qwen-mt-plus",
        "timeout_s": 20.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": "Mainland China should use the mainland DashScope endpoint. Overseas users should prefer the international endpoint or a nearby proxy.",
    },
    "xiaomi": {
        "label": "Xiaomi AI",
        "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
        "model": "mimo-v2.5-pro",
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
            "mimo-v2.5-pro is the current quality default; switch to "
            "mimo-v2-flash when latency matters more than quality."
        ),
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
        "model": "deepseek-v4-flash",
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
        "model": "glm-5.1",
        "extra_body": {"thinking": {"type": "disabled"}},
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.5-flash",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.6",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "xai": {
        "label": "xAI",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-medium-3-5",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
    },
    "doubao": {
        "label": "Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-pro-260215",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "entry",
        "model_hint": (
            "Enter a current Ark model id in the Model field, such as "
            "doubao-seed-2-0-pro-260215."
        ),
    },
    "nvidia": {
        "label": "NVIDIA AI",
        "base_url": NVIDIA_TRANSLATION_BASE_URL,
        "model": "nvidia/nemotron-3-super-120b-a12b",
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
        "model": "claude-opus-4-8",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "model_input": "select",
    },
    "anthropic_compatible": {
        "label": "Claude Compatible",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-6",
        "timeout_s": 15.0,
        "max_output_tokens": 192,
        "max_retries": 0,
        "model_input": "select",
        "base_url_input": "entry",
        "api_key_hint": (
            "Use this for Claude-compatible proxy or relay providers. Enter "
            "the Base URL and model id supplied by that provider."
        ),
        "model_hint": (
            "Choose the Claude model id exposed by your proxy. If your saved "
            "model is not in the preset list, Mio keeps it available in this dropdown."
        ),
    },
}

TRANSLATION_MODEL_PRESETS: dict[str, tuple[str, ...]] = {
    "openai": (
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ),
    "openai_compatible": (
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ),
    "deepseek": (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ),
    "zhipu": (
        "glm-5.1",
        "glm-5-turbo",
        "glm-5",
        "glm-4.7-flash",
        "glm-4.7-flashx",
        "glm-4.7",
        "glm-4.6",
    ),
    "qianwen": (
        "qwen-mt-plus",
        "qwen-mt-flash",
        "qwen3.7-max",
        "qwen-mt-turbo",
        "qwen-mt-lite",
    ),
    "xiaomi": (
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-flash",
        "mimo-v2-pro",
        "mimo-v2-omni",
    ),
    "gemini": (
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ),
    "kimi": (
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-0905-preview",
        "kimi-k2-turbo-preview",
        "kimi-k2-thinking",
        "kimi-k2-thinking-turbo",
    ),
    "xai": (
        "grok-4.3",
        "grok-4.20",
        "grok-4.20-multi-agent-0309",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
    ),
    "mistral": (
        "mistral-medium-3-5",
        "mistral-medium-latest",
        "mistral-small-latest",
        "mistral-large-latest",
        "ministral-8b-latest",
        "ministral-3b-latest",
        "magistral-small-latest",
        "magistral-medium-latest",
    ),
    "doubao": (),
    "nvidia": (
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/llama-3.1-nemotron-nano-8b-v1",
    ),
    "anthropic": (
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-1-20250805",
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ),
    "anthropic_compatible": (
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-1-20250805",
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ),
}

TRANSLATION_MODEL_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "openai": {
        "gpt-5.5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
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
        "gpt-5.4-pro": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "gpt-5-mini": {
            "speed": "fast",
            "quality": "high",
            "fit": "general",
            "note": "economy_first",
        },
        "gpt-5-nano": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "gpt-5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "gpt-4.1": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
        "gpt-4.1-mini": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "gpt-4.1-nano": {
            "speed": "very_fast",
            "quality": "basic",
            "fit": "general",
            "note": "economy_first",
        },
    },
    "openai_compatible": {},
    "deepseek": {
        "deepseek-v4-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "deepseek-v4-pro": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
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
        "glm-5.1": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "glm-5-turbo": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "glm-5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
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
        "qwen3.7-max": {
            "speed": "slow",
            "quality": "high",
            "fit": "general",
            "note": "quality_first",
        },
        "qwen-mt-flash": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
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
        "qwen-plus": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "qwen-flash": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "qwen-turbo": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "qwen3-max": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
    },
    "xiaomi": {
        "mimo-v2-flash": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "very_recommended",
            "note": "ultra_fast",
        },
        "mimo-v2.5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "mimo-v2.5-pro": {
            "speed": "balanced",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "mimo-v2-pro": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
        "mimo-v2-omni": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
    },
    "gemini": {
        "gemini-3.5-flash": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "gemini-3.1-pro-preview": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "gemini-3-flash-preview": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "gemini-3.1-flash-lite": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "gemini-3.1-flash-lite-preview": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "ultra_fast",
        },
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
    "kimi": {
        "kimi-k2.6": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "kimi-k2.5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "kimi-k2-0905-preview": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "kimi-k2-turbo-preview": {
            "speed": "fast",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "kimi-k2-thinking": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "reasoning",
        },
        "kimi-k2-thinking-turbo": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "reasoning",
        },
        "moonshot-v1-8k": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "moonshot-v1-32k": {
            "speed": "balanced",
            "quality": "balanced",
            "fit": "general",
            "note": "balanced_quality",
        },
        "moonshot-v1-128k": {
            "speed": "slow",
            "quality": "balanced",
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
        "grok-4.20-multi-agent-0309": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "grok-4.20-0309-reasoning": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "reasoning",
        },
        "grok-4.20-0309-non-reasoning": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "grok-4.20": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "grok-4": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "general_high_quality",
        },
        "grok-code-fast-1": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "ultra_fast",
        },
        "grok-3": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
        "grok-3-mini": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
    },
    "mistral": {
        "mistral-small-latest": {
            "speed": "fast",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "mistral-medium-3-5": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "mistral-medium-latest": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "mistral-large-latest": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "ministral-8b-latest": {
            "speed": "very_fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "ministral-3b-latest": {
            "speed": "very_fast",
            "quality": "basic",
            "fit": "general",
            "note": "economy_first",
        },
        "magistral-small-latest": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "reasoning",
        },
        "magistral-medium-latest": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "reasoning",
        },
    },
    "nvidia": {
        "nvidia/llama-3.1-nemotron-nano-8b-v1": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "general",
            "note": "economy_first",
        },
        "nvidia/nemotron-3-nano-30b-a3b": {
            "speed": "balanced",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "nvidia/nemotron-3-super-120b-a12b": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
    },
    "anthropic": {
        "claude-opus-4-8": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-sonnet-4-6": {
            "speed": "balanced",
            "quality": "high",
            "fit": "very_recommended",
            "note": "live_default",
        },
        "claude-haiku-4-5-20251001": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "recommended",
            "note": "ultra_fast",
        },
        "claude-opus-4-1-20250805": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-opus-4-20250514": {
            "speed": "slow",
            "quality": "high",
            "fit": "not_recommended",
            "note": "quality_first",
        },
        "claude-sonnet-4-20250514": {
            "speed": "balanced",
            "quality": "high",
            "fit": "recommended",
            "note": "balanced_quality",
        },
        "claude-3-7-sonnet-20250219": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "general_high_quality",
        },
        "claude-3-5-sonnet-20241022": {
            "speed": "balanced",
            "quality": "high",
            "fit": "general",
            "note": "balanced_quality",
        },
        "claude-3-5-haiku-20241022": {
            "speed": "fast",
            "quality": "balanced",
            "fit": "very_recommended",
            "note": "live_default",
        },
    },
    "anthropic_compatible": {},
}

TRANSLATION_MODEL_RECOMMENDATION_SCORES: dict[str, dict[str, str]] = {
    "openai": {
        "gpt-5.5": "9.5",
        "gpt-5.4-mini": "9.2",
        "gpt-5.4": "9.0",
        "gpt-5.4-nano": "7.6",
        "gpt-5.4-pro": "8.4",
        "gpt-5-mini": "8.4",
        "gpt-5-nano": "7.3",
        "gpt-5": "8.6",
        "gpt-4.1": "7.8",
        "gpt-4.1-mini": "7.4",
        "gpt-4.1-nano": "6.2",
    },
    "openai_compatible": {},
    "deepseek": {
        "deepseek-v4-flash": "9.1",
        "deepseek-v4-pro": "8.7",
        "deepseek-chat": "8.8",
        "deepseek-reasoner": "6.8",
    },
    "zhipu": {
        "glm-5.1": "8.8",
        "glm-5-turbo": "9.0",
        "glm-5": "8.5",
        "glm-4.7-flash": "8.4",
        "glm-4.7-flashx": "8.1",
        "glm-4.5-airx": "8.2",
        "glm-4.5-air": "7.7",
        "glm-4.7": "7.9",
        "glm-4.6": "7.5",
    },
    "qianwen": {
        "qwen-mt-plus": "9.7",
        "qwen3.7-max": "8.4",
        "qwen-mt-flash": "8.7",
        "qwen-mt-lite": "7.8",
        "qwen-mt-turbo": "7.4",
        "qwen-plus": "8.2",
        "qwen-flash": "8.1",
        "qwen-turbo": "7.6",
        "qwen3-max": "8.5",
    },
    "xiaomi": {
        "mimo-v2.5-pro": "9.0",
        "mimo-v2.5": "8.5",
        "mimo-v2-flash": "8.4",
        "mimo-v2-pro": "7.8",
        "mimo-v2-omni": "6.7",
    },
    "gemini": {
        "gemini-3.5-flash": "9.3",
        "gemini-2.5-flash": "8.8",
        "gemini-2.5-flash-lite": "8.4",
        "gemini-2.5-pro": "7.9",
        "gemini-3-flash-preview": "8.2",
        "gemini-3.1-flash-lite": "8.1",
        "gemini-3.1-flash-lite-preview": "7.9",
        "gemini-3.1-pro-preview": "7.2",
    },
    "kimi": {
        "kimi-k2.6": "9.1",
        "kimi-k2.5": "8.6",
        "kimi-k2-0905-preview": "8.2",
        "kimi-k2-turbo-preview": "8.5",
        "kimi-k2-thinking": "6.9",
        "kimi-k2-thinking-turbo": "7.5",
        "moonshot-v1-8k": "7.1",
        "moonshot-v1-32k": "7.0",
        "moonshot-v1-128k": "6.3",
    },
    "xai": {
        "grok-4.3": "9.0",
        "grok-4.20": "9.0",
        "grok-4.20-0309-non-reasoning": "8.4",
        "grok-4.20-0309-reasoning": "6.8",
        "grok-4.20-multi-agent-0309": "6.4",
        "grok-4": "8.3",
        "grok-code-fast-1": "7.2",
        "grok-3": "7.8",
        "grok-3-mini": "7.0",
    },
    "mistral": {
        "mistral-small-latest": "8.8",
        "mistral-medium-3-5": "8.7",
        "mistral-medium-latest": "8.5",
        "mistral-large-latest": "7.2",
        "ministral-8b-latest": "7.5",
        "ministral-3b-latest": "6.5",
        "magistral-small-latest": "6.8",
        "magistral-medium-latest": "6.2",
    },
    "nvidia": {
        "nvidia/nemotron-3-nano-30b-a3b": "8.4",
        "nvidia/nemotron-3-super-120b-a12b": "7.4",
        "nvidia/llama-3.1-nemotron-nano-8b-v1": "6.8",
    },
    "anthropic": {
        "claude-sonnet-4-6": "9.2",
        "claude-haiku-4-5-20251001": "8.4",
        "claude-opus-4-8": "7.8",
        "claude-sonnet-4-20250514": "8.5",
        "claude-3-7-sonnet-20250219": "8.0",
        "claude-3-5-sonnet-20241022": "7.8",
        "claude-3-5-haiku-20241022": "8.0",
        "claude-opus-4-1-20250805": "7.2",
        "claude-opus-4-20250514": "7.0",
    },
    "anthropic_compatible": {},
}


def get_backend_order() -> tuple:
    return _catalog_backend_order()


BACKEND_ORDER = tuple(TRANSLATION_BACKENDS.keys())
DEFAULT_CHINESE_BACKEND = "qianwen"
DEFAULT_INTERNATIONAL_BACKEND = "qianwen"
DEFAULT_BACKEND = DEFAULT_CHINESE_BACKEND
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
    if backend in _catalog_backends():
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
    return frozenset(base_urls.values())


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
        return "china_mainland" if resolved.startswith("zh") else "singapore"
    if backend_code == "xiaomi":
        return "china_cluster" if resolved.startswith("zh") else "global"
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


def get_backend_label(backend: str | None) -> str:
    return str(get_backend_spec(backend)["label"])


def get_backend_value(backend: str | None, key: str) -> str:
    value = get_backend_spec(backend).get(key, "")
    return str(value)


def backend_model_is_selectable(backend: str | None) -> bool:
    return str(get_backend_spec(backend).get("model_input", "select")) == "select"


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
    return get_backend_value(normalized_backend, key)


def get_backend_model_options(
    backend: str | None,
    current_model: str | None = None,
) -> tuple[str, ...]:
    normalized_backend = normalize_backend(backend)
    models = list(_catalog_model_presets().get(normalized_backend, ()))
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
    resolved_model = str(model or "").strip() or get_backend_value(
        normalized_backend, "model"
    )
    profile_backend = {
        "openai_compatible": "openai",
        "anthropic_compatible": "anthropic",
    }.get(normalized_backend, normalized_backend)
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


def _load_catalog_module():
    try:
        from src.utils.catalog_loader import BUILTIN_CATALOG

        return BUILTIN_CATALOG
    except Exception:
        return None


_CATALOG = _load_catalog_module()
_CATALOG_LOCK = None  # placeholder for future thread-safety needs


def set_catalog(catalog) -> None:
    """Replace the active translation catalog with a remote-loaded one."""
    global _CATALOG
    _CATALOG = catalog


def _catalog_backends() -> dict:
    if _CATALOG is not None:
        return _CATALOG.translation_backends
    return TRANSLATION_BACKENDS


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
MANUAL_SOURCE_LANGUAGE_OPTIONS = get_manual_source_language_options(
    ui_language=DEFAULT_UI_LANGUAGE
)


def get_ui_language(config: Mapping[str, object] | None) -> str:
    ui_cfg = config.get("ui", {}) if isinstance(config, Mapping) else {}
    if isinstance(ui_cfg, Mapping):
        language = ui_cfg.get("language")
        if language in UI_LANGUAGE_LABELS:
            return str(language)
    return DEFAULT_UI_LANGUAGE
