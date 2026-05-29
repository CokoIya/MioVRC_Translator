import pytest

from src.ui_qt.settings_window import CapsuleSwitch, SettingsWindow, NAV_ITEMS, TTS_TEST_TEXT_BY_LANGUAGE
from src.tts.api_tts_config import QWEN_TTS_BASE_URL_MAINLAND
from src.updater.update_checker import UpdateInfo
from src.utils.ui_config import (
    QWEN_TRANSLATION_BASE_URL_MAINLAND,
    XIAOMI_TRANSLATION_BASE_URL_PAYG,
    XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG,
)


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


def test_settings_save_preserves_app_mode_when_tts_is_enabled(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    config["app_mode"] = "translation"
    config["tts"]["enabled"] = True

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    dialog._tts_enabled_var.set(True)

    dialog._save()

    assert config["app_mode"] == "translation"


def test_settings_window_switches_are_capsules(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    assert dialog.findChildren(CapsuleSwitch)

    dialog.reject()


def test_qwen_translation_region_controls_base_url(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    config["translation"]["backend"] = "qianwen"
    config["translation"]["qianwen"] = {
        "api_key": "test-key",
        "region": "singapore",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-mt-flash",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    mainland_label = next(
        label for label, code in dialog._qwen_translation_region_codes.items()
        if code == "china_mainland"
    )
    dialog._qwen_translation_region_var.set(mainland_label)
    dialog._on_qwen_translation_region_changed(mainland_label)

    assert dialog._backend_base_url_var.value() == QWEN_TRANSLATION_BASE_URL_MAINLAND
    assert dialog._backend_base_url_entry is not None
    assert dialog._backend_base_url_entry.isReadOnly() is True

    dialog.reject()


def test_xiaomi_translation_region_controls_base_url_and_saves(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)

    config["translation"]["backend"] = "xiaomi"
    config["translation"]["xiaomi"] = {
        "api_key": "test-key",
        "region": "global",
        "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
        "model": "mimo-v2-flash",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    singapore_label = next(
        label for label, code in dialog._qwen_translation_region_codes.items()
        if code == "singapore_cluster"
    )
    dialog._qwen_translation_region_var.set(singapore_label)
    dialog._on_qwen_translation_region_changed(singapore_label)

    assert dialog._backend_base_url_var.value() == XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG
    assert dialog._backend_base_url_entry is not None
    assert dialog._backend_base_url_entry.isReadOnly() is True

    dialog._save()

    assert config["translation"]["backend"] == "xiaomi"
    assert config["translation"]["xiaomi"]["region"] == "singapore_cluster"
    assert config["translation"]["xiaomi"]["base_url"] == XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG


def test_output_format_labels_follow_ui_language(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    config["ui"]["language"] = "ru"

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    assert "Только перевод" in dialog._fmt_codes
    assert "Отключить перевод 2" in dialog._fmt2_codes

    dialog.reject()


def test_original_only_output_disables_second_format_on_save(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    config["translation"]["output_format_2"] = "translated1_with_translated2_original"

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    original_only_label = next(label for label, code in dialog._fmt_codes.items() if code == "original_only")
    dialog._output_format_var.set(original_only_label)
    dialog._on_output_format_changed(original_only_label)
    dialog._save()

    assert config["translation"]["output_format"] == "original_only"
    assert config["translation"]["output_format_2"] == "disabled"


def test_style_bert_saved_voice_id_selects_display_and_tests_with_id(qtbot, config, monkeypatch):
    voice_id = "demo-model :: mio-speaker :: Neutral"
    display_name = "Mio Voice / Neutral"
    spoken_voices: list[str] = []

    class FakeVoice:
        id = voice_id
        name = display_name

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            pass

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, _text, voice, rate=1.0, volume=1.0, callback=None):
            del rate, volume
            spoken_voices.append(voice)
            if callback is not None:
                callback(True, "")
            return True

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": voice_id,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "jp",
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    assert dialog._tts_voice_var.value() == display_name
    assert dialog._selected_tts_voice_id() == voice_id

    dialog._on_tts_test()
    dialog._save()

    assert spoken_voices == [voice_id]
    assert config["tts"]["style_bert_vits2"]["voice"] == voice_id


def test_style_bert_preset_voice_keeps_manual_bert_language_and_test_uses_selection(qtbot, config, monkeypatch):
    voice_id = "SBV2_HoloAus :: TsukumoSana :: Sana"
    display_name = "Tsukumo Sana / Sana"
    captured_kwargs: list[dict[str, object]] = []
    spoken_voices: list[str] = []

    class FakeVoice:
        id = voice_id
        name = display_name

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            captured_kwargs.append(dict(kwargs))

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, _text, voice, rate=1.0, volume=1.0, callback=None):
            del rate, volume
            spoken_voices.append(voice)
            if callback is not None:
                callback(True, "")
            return True

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": voice_id,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "jp",
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    assert dialog._tts_voice_var.value() == display_name
    assert dialog._selected_tts_voice_id() == voice_id
    assert dialog._selected_tts_bert_language() == "jp"

    dialog._on_tts_test()
    qtbot.waitUntil(lambda: spoken_voices == [voice_id], timeout=2000)

    assert captured_kwargs
    assert captured_kwargs[0]["sbv2_bert_language"] == "jp"
    assert dialog._selected_tts_bert_language() == "jp"

    dialog._save()
    assert config["tts"]["style_bert_vits2"]["bert_language"] == "jp"
    assert config["tts"]["style_bert_vits2"]["voice"] == voice_id


def test_style_bert_manual_bert_language_persists_after_reopen(qtbot, config, monkeypatch):
    voice_id = "SBV2_HoloAus :: TsukumoSana :: Sana"
    display_name = "Tsukumo Sana / Sana"

    class FakeVoice:
        id = voice_id
        name = display_name

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": voice_id,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "en",
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    jp_label = next(label for label, code in dialog._tts_bert_language_codes.items() if code == "jp")
    dialog._tts_bert_language_var.set(jp_label)
    dialog._save()
    dialog.accept()

    assert config["tts"]["style_bert_vits2"]["bert_language"] == "jp"

    reopened = SettingsWindow(None, config)
    qtbot.addWidget(reopened)
    reopened._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    assert reopened._selected_tts_bert_language() == "jp"


def test_style_bert_test_text_matches_selected_bert_language(qtbot, config, monkeypatch):
    voice_id = "SBV2_HoloAus :: TsukumoSana :: Sana"
    display_name = "Tsukumo Sana / Sana"
    spoken_texts: list[str] = []

    class FakeVoice:
        id = voice_id
        name = display_name

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            pass

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, text, voice, rate=1.0, volume=1.0, callback=None):
            del voice, rate, volume
            spoken_texts.append(text)
            if callback is not None:
                callback(True, "")
            return True

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": voice_id,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "jp",
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    for language in ("jp", "en", "zh"):
        label = next(label for label, code in dialog._tts_bert_language_codes.items() if code == language)
        dialog._tts_bert_language_var.set(label)
        dialog._on_tts_test()
        assert spoken_texts[-1] == TTS_TEST_TEXT_BY_LANGUAGE[language]


def test_style_bert_test_reports_missing_selected_bert_model(qtbot, config, monkeypatch):
    voice_id = "SBV2_HoloAus :: TsukumoSana :: Sana"
    display_name = "Tsukumo Sana / Sana"
    warnings: list[tuple[object, ...]] = []

    class FakeVoice:
        id = voice_id
        name = display_name

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            raise AssertionError("TTSManager should not be created when the voice BERT model is missing")

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: False)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    monkeypatch.setattr("src.ui_qt.settings_window.QMessageBox.warning", lambda *args: warnings.append(args))
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": voice_id,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "jp",
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.waitUntil(lambda: dialog._selected_tts_voice_id() == voice_id, timeout=2000)

    dialog._on_tts_test()

    assert dialog._tts_testing is False
    assert warnings
    assert "ku-nlp/deberta-v2-large-japanese-char-wwm" in str(warnings[0])


def test_tts_test_button_disables_until_callback(qtbot, config, monkeypatch):
    spoken_voices: list[str] = []
    callbacks: list[object] = []
    voice_id = "ja-JP-NanamiNeural"

    class FakeTTS:
        def get_available_voices(self):
            class Voice:
                id = voice_id
                name = "Nanami"

            return [Voice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            pass

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, _text, voice, rate=1.0, volume=1.0, callback=None):
            del rate, volume
            spoken_voices.append(voice)
            callbacks.append(callback)
            return True

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "edge",
        "edge": {"voice": voice_id, "rate": 1.0, "volume": 0.8},
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    test_btn = dialog._tts_test_btn
    assert test_btn is not None
    assert test_btn.isEnabled() is True

    dialog._on_tts_test()

    assert spoken_voices == [voice_id]
    assert test_btn.text() == "测试中..."
    assert test_btn.isEnabled() is False
    assert dialog._tts_testing is True

    assert callbacks and callbacks[0] is not None
    callbacks[0](True, "")

    qtbot.waitUntil(lambda: dialog._tts_testing is False, timeout=3000)
    assert test_btn.text() == "测试"
    assert test_btn.isEnabled() is True


def test_tts_test_button_recovers_on_timeout(qtbot, config, monkeypatch):
    class FakeTTS:
        def get_available_voices(self):
            return []

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            pass

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, _text, voice, rate=1.0, volume=1.0, callback=None):
            del rate, volume, callback
            return True

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: True)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "edge",
        "edge": {"voice": "ja-JP-NanamiNeural", "rate": 1.0, "volume": 0.8},
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    dialog._tts_test_timeout_ms = 10
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    test_btn = dialog._tts_test_btn
    assert test_btn is not None

    dialog._on_tts_test()

    assert test_btn.text() == "测试中..."
    assert test_btn.isEnabled() is False
    assert dialog._tts_testing is True

    qtbot.waitUntil(lambda: dialog._tts_testing is False, timeout=3000)
    assert test_btn.text() == "测试"
    assert test_btn.isEnabled() is True


def test_qwen_tts_settings_save_region_and_pass_test_config(qtbot, config, monkeypatch):
    captured_kwargs: list[dict[str, object]] = []
    spoken: list[tuple[str, str]] = []

    class FakeVoice:
        id = "Cherry"
        name = "Cherry"

    class FakeTTS:
        def get_available_voices(self):
            return [FakeVoice()]

    class FakeTTSManager:
        def __init__(self, *args, **kwargs):
            captured_kwargs.append(kwargs)

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, text, voice, rate=1.0, volume=1.0, callback=None):
            del rate, volume
            spoken.append((text, voice))
            if callback is not None:
                callback(True, "")
            return True

        def stop(self):
            pass

    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: FakeTTS())
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    monkeypatch.setattr("src.ui_qt.settings_window.TTSManager", FakeTTSManager)
    config["tts"] = {
        "enabled": True,
        "engine": "qwen_tts",
        "qwen_tts": {
            "api_key": "old-key",
            "region": "singapore",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
            "rate": 1.0,
            "volume": 0.8,
        },
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    mainland_label = next(
        label for label, code in dialog._tts_api_region_codes.items()
        if code == "china_mainland"
    )
    dialog._tts_api_key_var.set("new-key")
    dialog._tts_api_model_var.set("qwen3-tts-flash")
    dialog._tts_api_region_var.set(mainland_label)
    dialog._on_tts_api_region_changed(mainland_label)

    dialog._on_tts_test()
    qtbot.waitUntil(lambda: bool(spoken), timeout=2000)

    engine_config = captured_kwargs[0]["engine_config"]
    assert engine_config["api_key"] == "new-key"
    assert engine_config["region"] == "china_mainland"
    assert engine_config["base_url"] == QWEN_TTS_BASE_URL_MAINLAND
    assert engine_config["model"] == "qwen3-tts-flash"
    assert spoken[0] == (TTS_TEST_TEXT_BY_LANGUAGE["jp"], "Cherry")

    dialog._save()

    assert config["tts"]["qwen_tts"]["api_key"] == "new-key"
    assert config["tts"]["qwen_tts"]["region"] == "china_mainland"
    assert config["tts"]["qwen_tts"]["base_url"] == QWEN_TTS_BASE_URL_MAINLAND
    assert config["tts"]["qwen_tts"]["model"] == "qwen3-tts-flash"
    assert config["tts"]["qwen_tts"]["voice"] == "Cherry"


def test_bert_model_download_opens_progress_window(qtbot, config, monkeypatch):
    from src.asr.hf_model_downloader import DownloadProgress, DownloadState

    _patch_dialog_deps(monkeypatch)
    config["tts"] = {
        "engine": "style_bert_vits2",
        "style_bert_vits2": {"bert_language": "en", "device": "cpu"},
    }

    class FakeDownloader:
        def __init__(self):
            self.progress = DownloadProgress(state=DownloadState.IDLE)
            self.state = DownloadState.IDLE
            self.listeners = []
            self.started = 0

        def add_listener(self, callback):
            self.listeners.append(callback)

        def start(self):
            self.started += 1
            self.state = DownloadState.DOWNLOADING
            self.progress = DownloadProgress(state=DownloadState.DOWNLOADING)
            for callback in list(self.listeners):
                callback(self.progress)

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            pass

    downloader = FakeDownloader()
    monkeypatch.setattr("src.ui_qt.settings_window.model_is_complete", lambda _model_id: False)
    monkeypatch.setattr("src.ui_qt.settings_window.get_downloader", lambda _model_id: downloader)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    tts_row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "tts")
    dialog._nav_list.setCurrentRow(tts_row)
    qtbot.wait(30)

    dialog._download_bert_model()

    assert downloader.started == 1
    assert dialog._bert_download_window is not None
    assert dialog._bert_download_window.isVisible()

    dialog._bert_download_window.close()
    dialog.reject()


def test_settings_check_update_opens_update_window(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    opened: list[tuple[object, UpdateInfo, str]] = []

    class FakeUpdateWindow:
        def __init__(self, parent, info, ui_lang):
            opened.append((parent, info, ui_lang))

        def show(self):
            pass

        def raise_(self):
            pass

        def activateWindow(self):
            pass

    def fake_check_for_update(on_update, **kwargs):
        assert kwargs["max_retries"] == 2
        assert kwargs["retry_delays"] == (2,)
        on_update(
            UpdateInfo(
                version="v9.9.9",
                download_url="https://78hejiu.top/MioTranslator-Setup.exe",
                notes="Update notes",
                sha256="a" * 64,
            )
        )
        return None

    monkeypatch.setattr("src.ui_qt.settings_window.check_for_update", fake_check_for_update)
    monkeypatch.setattr("src.ui_qt.update_window.UpdateWindow", FakeUpdateWindow)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    dialog._on_check_update()

    assert dialog._update_checking is False
    assert opened
    assert opened[0][1].version == "v9.9.9"
    assert opened[0][2] == "zh-CN"
    assert dialog._check_update_btn.isEnabled() is True

    dialog.reject()


def test_settings_check_update_reports_no_update(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    messages: list[tuple[str, str]] = []

    def fake_check_for_update(_on_update, *, on_no_update, **_kwargs):
        on_no_update()
        return None

    monkeypatch.setattr("src.ui_qt.settings_window.check_for_update", fake_check_for_update)
    monkeypatch.setattr(
        "src.ui_qt.settings_window.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    dialog._on_check_update()

    assert messages == [(dialog._copy("settings_check_update"), dialog._copy("settings_up_to_date"))]
    assert dialog._update_checking is False

    dialog.reject()
