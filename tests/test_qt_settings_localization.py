from __future__ import annotations

import ast
from pathlib import Path

from src.ui_qt.settings_window import (
    FIELD_HINTS,
    QT_SETTINGS_COPY,
    _BACKEND_API_HINT_KEYS,
    _BACKEND_MODEL_HINT_KEYS,
)
from src.utils.i18n import UI_TEXTS
from src.utils.localization import (
    SUPPORTED_UI_LANGUAGES,
    normalize_ui_language,
    placeholder_fields,
)
from src.utils.ui_config import get_manual_source_label


SETTINGS_WINDOW_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "ui_qt" / "settings_window.py"
)

# Names that read the same in every language, so an English match proves
# nothing about whether the catalog was actually translated.
UNTRANSLATED_BY_DESIGN = {
    "provider_deepseek",
    "provider_gemini",
    "provider_qwen",
    "tts_device_cpu",
}


def _settings_translation_keys() -> set[str]:
    """Collect the global catalog keys the settings window asks for by name."""

    keys: set[str] = set()
    tree = ast.parse(
        SETTINGS_WINDOW_SOURCE.read_text(encoding="utf-8"),
        filename=str(SETTINGS_WINDOW_SOURCE),
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else ""
        )
        argument = None
        if name == "_t" and node.args:
            argument = node.args[0]
        elif name == "tr" and len(node.args) > 1:
            argument = node.args[1]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            keys.add(argument.value)
    return {key for key in keys if key in UI_TEXTS["en"]}


def test_settings_translation_keys_cover_every_supported_language():
    keys = _settings_translation_keys()
    assert keys
    for key in keys:
        reference_fields = placeholder_fields(UI_TEXTS["en"][key])
        for language in SUPPORTED_UI_LANGUAGES:
            value = UI_TEXTS[language].get(key)
            assert value and value != key, (language, key)
            assert placeholder_fields(value) == reference_fields, (language, key)


def test_settings_russian_and_korean_are_not_english_fallbacks():
    """Catch a language that was added by copying the English column.

    The keys are derived from the window itself so the check keeps covering
    the settings UI as it grows, instead of a list someone has to remember.
    """

    unchanged: list[tuple[str, str]] = []
    for key in sorted(_settings_translation_keys() - UNTRANSLATED_BY_DESIGN):
        english = UI_TEXTS["en"][key]
        for language in ("ru", "ko"):
            if UI_TEXTS[language][key] == english:
                unchanged.append((language, key))

    assert unchanged == []


def test_backend_help_and_manual_source_labels_are_localized():
    for key in (*_BACKEND_API_HINT_KEYS.values(), *_BACKEND_MODEL_HINT_KEYS.values()):
        values = QT_SETTINGS_COPY[key]
        for language in ("zh-CN", "ja", "ru", "ko"):
            assert values[language] != values["en"], (language, key)

    assert get_manual_source_label("auto", "en") == "Auto"
    assert get_manual_source_label("auto", "ru") == "Авто"
    assert get_manual_source_label("auto", "ko") == "자동"


def test_region_labels_and_long_hints_do_not_fall_back_to_english_fragments():
    assert "official" not in QT_SETTINGS_COPY["deepseek_region_official"]["ru"].casefold()
    assert "hosted api" not in QT_SETTINGS_COPY["nvidia_region_global"]["ru"].casefold()
    assert "custom" not in FIELD_HINTS["deepseek_translation_region"]["ru"].casefold()
    assert not FIELD_HINTS["chatbox_template"]["ja"].startswith("Optional")
    assert not FIELD_HINTS["fallback_backends"]["ja"].startswith("Optional")
    assert not FIELD_HINTS["target_language_3"]["ru"].startswith("Translated")


def test_settings_language_normalization_uses_supported_fallbacks():
    assert normalize_ui_language("ja-JP") == "ja"
    assert normalize_ui_language("ko_KR") == "ko"
    assert normalize_ui_language("unsupported") in SUPPORTED_UI_LANGUAGES
