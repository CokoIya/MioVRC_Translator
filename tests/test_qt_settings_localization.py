from __future__ import annotations

import ast
from pathlib import Path

from src.ui_qt.settings.localized_tab import (
    SETTINGS_UI_LANGUAGES,
    normalize_settings_language,
)
from src.ui_qt.settings.settings_window_tabbed import SettingsWindowTabbed
from src.ui_qt.settings_window import (
    FIELD_HINTS,
    QT_SETTINGS_COPY,
    _BACKEND_API_HINT_KEYS,
    _BACKEND_MODEL_HINT_KEYS,
)
from src.utils.i18n import (
    UI_TEXTS,
    _SETTINGS_TABBED_KO_TEXTS,
    _SETTINGS_TABBED_NEW_TEXTS,
    _SETTINGS_TABBED_RU_TEXTS,
    tr,
)
from src.utils.localization import placeholder_fields
from src.utils.ui_config import get_manual_source_label


SETTINGS_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "ui_qt" / "settings"
SETTINGS_WINDOW_SOURCE = SETTINGS_PACKAGE.parent / "settings_window.py"


def _settings_translation_keys() -> set[str]:
    keys: set[str] = set()
    for path in SETTINGS_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
    return keys


def _config() -> dict:
    return {
        "ui": {"language": "en", "main_window_theme": "dark"},
        "translation": {
            "backend": "qianwen",
            "source_language": "auto",
            "target_language": "ja",
            "qianwen": {
                "api_key": "test-key",
                "region": "singapore",
                "model": "qwen-plus",
            },
        },
        "audio": {
            "input_device_mode": "auto",
            "vad_sensitivity": 2,
            "vad_speech_ratio": 0.6,
        },
        "tts": {
            "engine": "edge",
            "voice": "en-US-JennyNeural",
            "output_device": "virtual_cable",
        },
        "vrc": {"osc_enabled": True, "output_format": "translated"},
        "vrc_listen": {"enabled": False, "target_language": "zh-CN"},
    }


def test_settings_translation_keys_cover_every_supported_language():
    keys = _settings_translation_keys()
    assert keys
    for key in keys:
        reference_fields = placeholder_fields(UI_TEXTS["en"][key])
        for language in SETTINGS_UI_LANGUAGES:
            value = UI_TEXTS[language].get(key)
            assert value and value != key, (language, key)
            assert placeholder_fields(value) == reference_fields, (language, key)


def test_settings_specific_catalog_keys_are_referenced_by_settings_ui():
    source_literals: set[str] = set()
    for path in (*SETTINGS_PACKAGE.glob("*.py"), SETTINGS_WINDOW_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        source_literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    catalog_keys = set(_SETTINGS_TABBED_RU_TEXTS) | set(_SETTINGS_TABBED_KO_TEXTS)
    catalog_keys.update(
        key
        for values in _SETTINGS_TABBED_NEW_TEXTS.values()
        for key in values
    )
    assert sorted(catalog_keys - source_literals) == []


def test_settings_russian_and_korean_are_not_english_fallbacks():
    for key in (
        "quick_setup_title",
        "audio_mic_title",
        "tts_voice_title",
        "vrchat_title",
        "advanced_title",
        "xtts_language_hint",
        "voice_xiaoxiao",
    ):
        assert UI_TEXTS["ru"][key] != UI_TEXTS["en"][key]
        assert UI_TEXTS["ko"][key] != UI_TEXTS["en"][key]


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
    assert normalize_settings_language("ja-JP") == "ja"
    assert normalize_settings_language("ko_KR") == "ko"
    assert normalize_settings_language("unsupported") in SETTINGS_UI_LANGUAGES


def test_tabbed_settings_runtime_language_switch_preserves_unsaved_qwen_values(qtbot):
    dialog = SettingsWindowTabbed(_config(), ui_language="en")
    qtbot.addWidget(dialog)

    quick = dialog._quick_setup_tab
    api = dialog._api_models_tab
    quick._provider_combo.setCurrentIndex(quick._provider_combo.findText(tr("en", "provider_qwen")))
    quick._api_key_input.setText("unsaved-qwen-key")
    api._qwen_key_input.setText("unsaved-qwen-key")
    api._qwen_region_combo.setCurrentIndex(api._qwen_region_combo.findData("china_mainland"))
    dialog._tabs.setCurrentIndex(1)

    dialog.set_ui_language("ru-RU")

    assert dialog.windowTitle() == tr("ru", "settings_title")
    assert tr("ru", "quick_setup_tab") in dialog._tabs.tabText(0)
    assert dialog._tabs.currentIndex() == 1
    assert quick._api_key_input.text() == "unsaved-qwen-key"
    assert api._qwen_key_input.text() == "unsaved-qwen-key"
    assert api._qwen_region_combo.currentData() == "china_mainland"

    collected = dialog._collect_config()
    assert collected["ui"]["language"] == "ru"
    assert collected["translation"]["backend"] == "qianwen"
    assert collected["translation"]["qianwen"]["api_key"] == "unsaved-qwen-key"
    assert collected["translation"]["qianwen"]["region"] == "china_mainland"
