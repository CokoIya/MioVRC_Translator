"""デスクトップ UI と設定処理で共有するメタデータ。"""

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
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "qianwen": {
        "label": "Qianwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-mt-turbo",
        "extra_body": {"enable_thinking": False},
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-haiku-4-5-20251001",
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
