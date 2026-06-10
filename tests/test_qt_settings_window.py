import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from src.ui_qt.settings_window import (
    CapsuleSwitch,
    NAV_ITEMS,
    ROLEPLAY_PRESETS,
    SETTINGS_UPDATE_BUTTON_PADDING,
    STYLE_BERT_TTS_TEST_TIMEOUT_MS,
    SettingsWindow,
    TTS_TEST_TIMEOUT_MS,
    TTS_TEST_TEXT_BY_LANGUAGE,
)
from src.tts.api_tts_config import QWEN_TTS_BASE_URL_MAINLAND
from src.updater.update_checker import UpdateInfo
from src.utils.ui_config import (
    DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
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
    monkeypatch.setattr("src.ui_qt.settings_window.missing_required_translation_api_key", lambda _cfg: (False, ""))
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


def _select_settings_page(qtbot, dialog: SettingsWindow, page_id: str) -> None:
    row = next(i for i, (candidate, _label) in enumerate(NAV_ITEMS) if candidate == page_id)
    dialog._nav_list.setCurrentRow(row)
    qtbot.wait(30)


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


def test_preloaded_settings_window_defers_tts_voice_loading_until_shown(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    config["tts"] = {
        "engine": "style_bert_vits2",
        "style_bert_vits2": {"voice": None, "device": "cpu", "bert_language": "jp"},
    }
    created_engines: list[str] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.create_tts_engine",
        lambda engine: created_engines.append(engine) or _DummyTTS(),
    )

    dialog = SettingsWindow(None, config, preload=True)
    qtbot.addWidget(dialog)
    qtbot.wait(450)

    assert created_engines == []
    assert dialog._tts_voices_loaded == {}

    dialog.show()
    qtbot.waitUntil(lambda: "style_bert_vits2" in dialog._tts_voices_loaded, timeout=2000)

    assert created_engines == []
    dialog.reject()


def test_settings_window_deferred_initial_page_builds_after_show(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config, defer_initial_page=True)
    qtbot.addWidget(dialog)

    assert "common" not in dialog._built_pages

    dialog.show()

    qtbot.waitUntil(lambda: "common" in dialog._built_pages, timeout=1000)
    assert dialog._page_stack is not None
    assert dialog._page_stack.currentIndex() == 0

    dialog.reject()


def test_settings_update_buttons_resize_for_localized_labels(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    for code in ("zh-CN", "en", "ja", "ru", "ko"):
        if code != dialog._ui_lang:
            label = next(label for label, value in dialog._ui_lang_codes.items() if value == code)
            dialog._on_ui_lang_changed(label)
        for button in dialog._check_update_buttons:
            text_width = button.fontMetrics().horizontalAdvance(button.text())
            assert button.minimumWidth() >= text_width + SETTINGS_UPDATE_BUTTON_PADDING

    dialog.reject()


def test_roleplay_presets_are_popular_anime_character_labels():
    expected_ids = [
        "frieren",
        "violet_evergarden",
        "artoria_pendragon",
        "marin_kitagawa",
        "maomao",
        "kurisu_makise",
        "rem_rezero",
        "holo",
        "yor_forger",
        "mikasa_ackerman",
    ]
    assert list(ROLEPLAY_PRESETS.keys()) == ["custom", *expected_ids]

    for preset_id in expected_ids:
        profile = ROLEPLAY_PRESETS[preset_id]
        label = profile["labels"]["zh-CN"]
        assert label.count(" / ") == 2
        assert profile["persona_name"] == label
        assert profile["persona_prompt"]
        assert profile["persona_glossary"]


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


def test_settings_window_nav_labels_are_player_friendly(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    assert [dialog._nav_list.item(i).text() for i in range(dialog._nav_list.count())] == [
        "基础外观设置",
        "麦克风设置",
        "逆向翻译设置",
        "总翻译设置",
        "同声传译设置",
        "VRChat 联动设置",
        "快捷键设置",
        "下载和更新",
        "翻译风格设置",
        "高级",
    ]

    dialog.reject()


def test_settings_window_select_page_builds_deferred_target(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    dialog = SettingsWindow(None, config, defer_initial_page=True)
    qtbot.addWidget(dialog)

    dialog.select_page("vrc_listen")
    qtbot.wait(30)

    row = next(i for i, (page_id, _label) in enumerate(NAV_ITEMS) if page_id == "vrc_listen")
    assert dialog._nav_list.currentRow() == row
    assert dialog._page_stack.currentIndex() == row
    assert "vrc_listen" in dialog._built_pages

    dialog.reject()


def test_settings_window_advanced_can_open_logs_folder(qtbot, config, monkeypatch, tmp_path):
    _patch_dialog_deps(monkeypatch)
    opened = []
    monkeypatch.setattr("src.ui_qt.settings_window.logs_dir", lambda: tmp_path)
    monkeypatch.setattr("src.ui_qt.settings_window.QDesktopServices.openUrl", lambda url: opened.append(url) or True)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    _select_settings_page(qtbot, dialog, "advanced")
    labels = [label.text() for label in dialog._pages["advanced"].findChildren(QLabel)]
    assert "问题日志" in labels
    assert any("mio.log" in text for text in labels)

    dialog._open_logs_folder()

    assert opened
    assert opened[0].toLocalFile().replace("\\", "/") == tmp_path.as_posix()
    dialog.reject()


def test_updates_page_lists_models_and_marks_downloaded_green(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)

    def fake_model_exists(spec):
        return getattr(spec, "engine", "") == "sensevoice-small"

    monkeypatch.setattr("src.asr.model_manager.model_exists", fake_model_exists)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    _select_settings_page(qtbot, dialog, "model")
    page = dialog._pages["model"]
    labels = page.findChildren(QLabel)
    label_text = "\n".join(label.text() for label in labels)

    assert "模型下载" in label_text
    assert "请按需求下载" in label_text
    assert "SenseVoice Small" in label_text
    assert "Whisper Small" in label_text
    assert any(label.text() == "已下载" and label.objectName() == "successLabel" for label in labels)
    assert any(button.text() == "下载模型" for button in page.findChildren(QPushButton))

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


def test_settings_theme_toggle_uses_lightweight_fade(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    config["ui"]["main_window_theme"] = "dark"

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._on_theme_toggle()

    qtbot.waitUntil(lambda: dialog._theme_btn is not None and dialog._theme_btn.isEnabled(), timeout=1000)
    assert dialog._active_theme == "light"
    assert not any(child.__class__.__name__ == "_ThemeRevealOverlay" for child in dialog.findChildren(QWidget))

    dialog.reject()


def test_settings_window_nav_uses_function_domain_pages():
    page_ids = [page_id for page_id, _label in NAV_ITEMS]

    assert page_ids[:4] == ["common", "voice", "vrc_listen", "translation"]
    assert {"tts", "vr_integration", "hotkeys", "model", "advanced"}.issubset(page_ids)


def test_settings_window_can_request_mode_wizard(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    requested: list[bool] = []

    dialog = SettingsWindow(None, config, on_mode_wizard_requested=lambda: requested.append(True))
    qtbot.addWidget(dialog)

    dialog._request_mode_wizard()

    assert requested == [True]
    dialog.reject()


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
    dialog._avatar_muted_var.set("MioMuted")
    dialog._avatar_error_var.set("MioError")
    dialog._avatar_target_language_var.set("MioTargetLanguage")

    dialog._save()

    assert config["ui"]["language"] == "en"
    assert config["ui"]["main_window_theme"] == "system"
    assert config["osc"]["avatar_sync"]["enabled"] is True
    assert config["osc"]["avatar_sync"]["params"] == {
        "translating": "MioTranslating",
        "speaking": "MioSpeaking",
        "muted": "MioMuted",
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


def test_settings_save_restores_footer_buttons_before_reopen(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._save()
    assert dialog._save_btn is not None
    assert dialog._cancel_btn is not None
    assert dialog._save_btn.isEnabled() is False
    assert dialog._cancel_btn.isEnabled() is False

    qtbot.waitUntil(lambda: not dialog._saving, timeout=2000)
    dialog.show()

    assert dialog._save_btn.isEnabled() is True
    assert dialog._cancel_btn.isEnabled() is True


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
    _select_settings_page(qtbot, dialog, "translation")

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


def test_deepseek_translation_region_controls_base_url_and_saves(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)

    config["translation"]["backend"] = "deepseek"
    config["translation"]["deepseek"] = {
        "api_key": "test-key",
        "region": "official",
        "base_url": DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
        "model": "deepseek-v4-flash",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "translation")

    custom_label = next(
        label for label, code in dialog._qwen_translation_region_codes.items()
        if code == "custom"
    )
    dialog._qwen_translation_region_var.set(custom_label)
    dialog._on_qwen_translation_region_changed(custom_label)

    assert dialog._backend_base_url_entry is not None
    assert dialog._backend_base_url_entry.isReadOnly() is False

    dialog._backend_base_url_var.set("https://proxy.example.com/v1")
    dialog._save()

    assert config["translation"]["deepseek"]["region"] == "custom"
    assert config["translation"]["deepseek"]["base_url"] == "https://proxy.example.com/v1"

    dialog.reject()


def test_asr_engine_lists_whisper_for_main_and_listen(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    config["ui"]["language"] = "en"

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    main_options = {code: label for label, code in dialog._asr_engine_options()}
    listen_options = {code: label for label, code in dialog._listen_asr_engine_options()}

    assert "whisper-large-v3-turbo" in main_options
    assert "Whisper Small" in main_options["whisper-large-v3-turbo"]
    assert "whisper-large-v3-turbo" in listen_options
    assert "Whisper Small" in listen_options["whisper-large-v3-turbo"]

    dialog.reject()


def test_xiaomi_translation_region_controls_base_url_and_saves(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)

    config["translation"]["backend"] = "xiaomi"
    config["translation"]["xiaomi"] = {
        "api_key": "test-key",
        "region": "global",
        "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
        "model": "mimo-v2.5-pro",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "translation")

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
    # output_format_2 merged into output_format; fmt2_codes no longer exists

    dialog.reject()


def test_original_only_output_saved_on_format_change(qtbot, config, monkeypatch):
    """Changing output_format to original_only saves only output_format (output_format_2 is now merged)."""
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    config["translation"]["output_format"] = "translated1_with_translated2_original"

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    original_only_label = next(label for label, code in dialog._fmt_codes.items() if code == "original_only")
    target3_label = next(label for label, code in dialog._lang3_codes.items() if code == "ko")
    dialog._target_lang3_var.set(target3_label)
    dialog._output_format_var.set(original_only_label)
    dialog._chatbox_template_var.set("{translatedText}\\n{text}")
    dialog._fallback_backends_var.set("deepseek, local_ai")
    dialog._on_output_format_changed(original_only_label)
    dialog._save()

    assert config["translation"]["output_format"] == "original_only"
    assert config["translation"]["target_language_3"] == "ko"
    assert config["translation"]["chatbox_template"] == "{translatedText}\\n{text}"
    assert config["translation"]["fallback_backends"] == ["deepseek", "local_ai"]


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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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


def test_style_bert_gpu_device_option_resets_when_cuda_missing(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: False)
    monkeypatch.setattr("src.ui_qt.settings_window.cuda_pytorch_installed", lambda: False)
    shown: list[bool] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._show_tts_gpu_unavailable_dialog",
        lambda self: shown.append(True),
    )
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": None,
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

    gpu_label = next(label for label, code in dialog._tts_device_codes.items() if code == "cuda")
    cpu_label = next(label for label, code in dialog._tts_device_codes.items() if code == "cpu")
    assert "GPU" in gpu_label

    dialog._tts_device_combo.setCurrentText(gpu_label)

    assert shown == [True]
    assert dialog._tts_device_var.value() == cpu_label
    assert dialog._tts_device_combo.currentText() == cpu_label


def test_style_bert_gpu_device_saves_when_cuda_available(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: True)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": None,
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

    gpu_label = next(label for label, code in dialog._tts_device_codes.items() if code == "cuda")
    dialog._tts_device_combo.setCurrentText(gpu_label)
    dialog._save()

    assert config["tts"]["style_bert_vits2"]["device"] == "cuda"


def test_style_bert_gpu_device_does_not_prompt_when_cuda_pytorch_installed(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: False)
    monkeypatch.setattr("src.ui_qt.settings_window.cuda_pytorch_installed", lambda: True)
    shown: list[bool] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._show_tts_gpu_unavailable_dialog",
        lambda self: shown.append(True),
    )
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {
            "voice": None,
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

    gpu_label = next(label for label, code in dialog._tts_device_codes.items() if code == "cuda")
    dialog._tts_device_combo.setCurrentText(gpu_label)

    assert shown == []
    assert dialog._tts_device_codes[dialog._tts_device_var.value()] == "cuda"


def test_local_asr_gpu_device_option_resets_when_cuda_missing(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: False)
    monkeypatch.setattr("src.ui_qt.settings_window.cuda_pytorch_installed", lambda: False)
    shown: list[bool] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._show_tts_gpu_unavailable_dialog",
        lambda self: shown.append(True),
    )
    config["asr"] = {
        "engine": "whisper-large-v3-turbo",
        "device": "cpu",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "voice")

    gpu_label = next(label for label, code in dialog._asr_device_codes.items() if code == "cuda")
    cpu_label = next(label for label, code in dialog._asr_device_codes.items() if code == "cpu")
    assert "GPU" in gpu_label

    dialog._asr_device_combo.setCurrentText(gpu_label)

    assert shown == [True]
    assert dialog._asr_device_var.value() == cpu_label
    assert dialog._asr_device_combo.currentText() == cpu_label


def test_local_asr_gpu_device_saves_when_cuda_available(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: True)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    config["asr"] = {
        "engine": "sensevoice-small",
        "device": "cpu",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "voice")

    gpu_label = next(label for label, code in dialog._asr_device_codes.items() if code == "cuda")
    dialog._asr_device_combo.setCurrentText(gpu_label)
    dialog._save()

    assert config["asr"]["device"] == "cuda"


def test_local_asr_gpu_device_does_not_prompt_when_cuda_pytorch_installed(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: False)
    monkeypatch.setattr("src.ui_qt.settings_window.cuda_pytorch_installed", lambda: True)
    shown: list[bool] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._show_tts_gpu_unavailable_dialog",
        lambda self: shown.append(True),
    )
    config["asr"] = {
        "engine": "whisper-large-v3-turbo",
        "device": "cpu",
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "voice")

    gpu_label = next(label for label, code in dialog._asr_device_codes.items() if code == "cuda")
    dialog._asr_device_combo.setCurrentText(gpu_label)

    assert shown == []
    assert dialog._asr_device_codes[dialog._asr_device_var.value()] == "cuda"


def test_online_asr_hides_local_inference_device_and_saves_cpu(qtbot, config, monkeypatch):
    _patch_dialog_deps(monkeypatch)
    monkeypatch.setattr("src.ui_qt.settings_window.gpu_runtime_available", lambda: False)
    monkeypatch.setattr("src.ui_qt.settings_window.config_manager.save_config", lambda cfg: None)
    shown: list[bool] = []
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._show_tts_gpu_unavailable_dialog",
        lambda self: shown.append(True),
    )
    config["asr"] = {
        "engine": "qwen3-asr",
        "device": "cuda",
        "qwen3_asr": {"api_key": "key"},
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    _select_settings_page(qtbot, dialog, "voice")

    assert dialog._asr_device_combo.isVisible() is False
    assert dialog._asr_device_var.value() in dialog._asr_device_codes
    assert dialog._asr_device_codes[dialog._asr_device_var.value()] == "cpu"

    gpu_label = next(label for label, code in dialog._asr_device_codes.items() if code == "cuda")
    dialog._asr_device_combo.setCurrentText(gpu_label)

    assert shown == []
    assert dialog._asr_device_codes[dialog._asr_device_var.value()] == "cpu"

    dialog._save()

    assert config["asr"]["device"] == "cpu"


def test_style_bert_gpu_unavailable_dialog_installs_pytorch_when_driver_ready(qtbot, config, monkeypatch):
    from src.utils.gpu_support import NvidiaDriverStatus

    _patch_dialog_deps(monkeypatch)
    opened_urls: list[str] = []
    install_dialogs: list[bool] = []

    class FakeMessageBox:
        instances = []

        class Icon:
            Information = object()

        class ButtonRole:
            ActionRole = object()
            RejectRole = object()

        def __init__(self, parent):
            self.parent = parent
            self.title = ""
            self.text = ""
            self.buttons = []
            self._clicked_button = None
            self.instances.append(self)

        def setIcon(self, _icon):
            pass

        def setWindowTitle(self, title):
            self.title = title

        def setText(self, text):
            self.text = text

        def addButton(self, text, role):
            button = object()
            self.buttons.append((text, role, button))
            return button

        def exec(self):
            self._clicked_button = self.buttons[0][2]

        def clickedButton(self):
            return self._clicked_button

    monkeypatch.setattr("src.ui_qt.settings_window.QMessageBox", FakeMessageBox)
    monkeypatch.setattr(
        "src.ui_qt.settings_window.detect_nvidia_driver",
        lambda: NvidiaDriverStatus(True, name="NVIDIA RTX"),
    )
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._open_external_url",
        lambda self, url: opened_urls.append(url),
    )
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._open_pytorch_cuda_install_dialog",
        lambda self: install_dialogs.append(True),
    )

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    dialog._show_tts_gpu_unavailable_dialog()

    message = FakeMessageBox.instances[0]
    button_texts = [text for text, _role, _button in message.buttons]
    assert "CUDA" in message.text
    assert not any("NVIDIA" in text for text in button_texts)
    assert any("PyTorch" in text for text in button_texts)
    assert opened_urls == []
    assert install_dialogs == [True]


def test_style_bert_gpu_unavailable_dialog_offers_driver_when_missing(qtbot, config, monkeypatch):
    from src.utils.gpu_support import NvidiaDriverStatus

    _patch_dialog_deps(monkeypatch)
    opened_urls: list[str] = []

    class FakeMessageBox:
        instances = []

        class Icon:
            Information = object()

        class ButtonRole:
            ActionRole = object()
            RejectRole = object()

        def __init__(self, parent):
            self.parent = parent
            self.text = ""
            self.buttons = []
            self._clicked_button = None
            self.instances.append(self)

        def setIcon(self, _icon):
            pass

        def setWindowTitle(self, _title):
            pass

        def setText(self, text):
            self.text = text

        def addButton(self, text, role):
            button = object()
            self.buttons.append((text, role, button))
            return button

        def exec(self):
            self._clicked_button = self.buttons[0][2]

        def clickedButton(self):
            return self._clicked_button

    monkeypatch.setattr("src.ui_qt.settings_window.QMessageBox", FakeMessageBox)
    monkeypatch.setattr(
        "src.ui_qt.settings_window.detect_nvidia_driver",
        lambda: NvidiaDriverStatus(False),
    )
    monkeypatch.setattr(
        "src.ui_qt.settings_window.SettingsWindow._open_external_url",
        lambda self, url: opened_urls.append(url),
    )

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    dialog._show_tts_gpu_unavailable_dialog()

    message = FakeMessageBox.instances[0]
    button_texts = [text for text, _role, _button in message.buttons]
    assert any("NVIDIA" in text for text in button_texts)
    assert any("PyTorch" in text for text in button_texts)
    assert opened_urls == ["https://www.nvidia.com/Download/index.aspx"]


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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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
    monkeypatch.setattr("src.ui_qt.settings_window.list_style_bert_vits2_voices", lambda _language="jp": [FakeVoice()])
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


def test_style_bert_tts_test_uses_extended_timeout(qtbot, config, monkeypatch):
    monkeypatch.setattr("src.ui_qt.settings_window.create_tts_engine", lambda _engine: _DummyTTS())
    config["tts"] = {
        "enabled": True,
        "engine": "style_bert_vits2",
        "style_bert_vits2": {"voice": None, "rate": 1.0, "volume": 0.8},
    }

    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)

    assert dialog._current_tts_test_timeout_ms("edge") == TTS_TEST_TIMEOUT_MS
    assert (
        dialog._current_tts_test_timeout_ms("style_bert_vits2")
        == STYLE_BERT_TTS_TEST_TIMEOUT_MS
    )

    dialog._tts_test_timeout_ms = 10
    assert dialog._current_tts_test_timeout_ms("style_bert_vits2") == 10


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
    assert dialog._tts_api_model_entry is not None
    model_items = [
        dialog._tts_api_model_entry.itemText(i)
        for i in range(dialog._tts_api_model_entry.count())
    ]
    assert "qwen3-tts-flash" in model_items
    assert "qwen3-tts-instruct-flash" in model_items
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
