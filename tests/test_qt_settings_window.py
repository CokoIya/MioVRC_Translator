import pytest

from src.ui_qt.settings_window import CapsuleSwitch, SettingsWindow, NAV_ITEMS


class _DummyTTS:
    def get_available_voices(self):
        return []


@pytest.fixture
def config():
    return {
        "ui": {"language": "zh-CN", "main_window_theme": "system"},
        "translation": {
            "backend": "openai",
            "target_language": "ja",
            "source_language": "auto",
            "output_format": "translated_with_original",
        },
        "asr": {"engine": "sensevoice-small"},
        "tts": {"engine": "edge", "rate": 1.0, "volume": 0.8},
        "vrc_listen": {"enabled": False, "source_language": "auto", "target_language": "zh"},
        "hotkeys": {"mic_mute": "Ctrl+Alt+F2"},
        "text_input_window": {"hotkey": "Alt+X"},
        "osc": {"avatar_sync": {"enabled": False, "params": {}}},
    }


def _patch_dialog_deps(monkeypatch):
    monkeypatch.setattr("src.ui_qt.settings_window.AudioRecorder.list_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.settings_window._list_desktop_output_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.settings_window.find_best_virtual_output_device", lambda: None)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: _DummyTTS())
    monkeypatch.setattr("src.asr.model_manager.model_exists", lambda _spec: True)
    monkeypatch.setattr(
        "src.ui_qt.settings_window.dictionary_status",
        lambda: {
            "layers": [
                {"name": "bundled", "entry_count": 3, "version": "1"},
                {"name": "user", "entry_count": 1, "version": ""},
            ],
            "user_path": "D:/tmp/asr_terms.user.json",
        },
    )


def test_settings_window_constructs_and_shows_nav(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle()
    assert dialog.minimumSize().width() == 980
    assert dialog.minimumSize().height() == 620
    assert dialog.width() == 1180
    assert dialog.height() == 740
    assert dialog.maximumSize().width() > dialog.width()
    assert dialog.maximumSize().height() > dialog.height()
    assert dialog._nav_list is not None
    assert dialog._page_stack is not None
    assert dialog._theme_btn is not None
    assert dialog._page_stack.count() == len(NAV_ITEMS)
    assert {page_id for page_id, _label in NAV_ITEMS} == set(dialog._pages.keys())
    assert dialog._built_pages == {"common"}
    assert dialog._dictionary_status_label is None
    assert dialog._tts_voices_loaded == {}

    dialog.accept()


def test_settings_window_nav_switches_pages(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    for i in range(len(NAV_ITEMS)):
        dialog._nav_list.setCurrentRow(i)
        qtbot.wait(30)
        assert dialog._page_stack.currentIndex() == i
        assert NAV_ITEMS[i][0] in dialog._built_pages

    dialog.reject()


def test_settings_window_common_page_has_theme_and_bg(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    dialog._nav_list.setCurrentRow(0)
    qtbot.wait(30)

    page = dialog._pages.get("common")
    assert page is not None
    assert dialog._theme_var.value() == dialog._theme_labels()["system"]

    dialog.accept()


def test_settings_window_voice_page_shows_dictionary_status(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    voice_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "voice")
    dialog._nav_list.setCurrentRow(voice_row)
    qtbot.wait(30)

    assert dialog._pages.get("voice") is not None
    assert dialog._dictionary_status_label is not None
    assert "D:/tmp/asr_terms.user.json" in dialog._dictionary_status_label.text()

    dialog.reject()


def test_settings_window_save_ui_language_and_avatar_sync(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    english_label = next(label for label, code in dialog._ui_lang_codes.items() if code == "en")
    dialog._ui_lang_var.set(english_label)
    dialog._avatar_sync_enabled_var.set(True)
    dialog._avatar_translating_var.set("MioTranslating")
    dialog._avatar_speaking_var.set("MioSpeaking")
    dialog._avatar_error_var.set("MioError")
    dialog._avatar_target_language_var.set("MioTargetLanguage")

    dialog._save()

    assert config["ui"]["language"] == "en"
    assert config["ui"]["main_window_theme"] == "system"
    assert config["osc"]["avatar_sync"]["enabled"] is True
    assert config["osc"]["avatar_sync"]["params"] == {
        "translating": "MioTranslating",
        "speaking": "MioSpeaking",
        "error": "MioError",
        "target_language": "MioTargetLanguage",
    }


def test_settings_window_switches_are_capsules(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    assert dialog.findChildren(CapsuleSwitch)

    dialog.reject()
