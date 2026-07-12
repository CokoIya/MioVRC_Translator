import ast
import inspect
import threading

import pytest
from PySide6.QtWidgets import QDialog

from src.core.mode_manager import AppMode
from src.ui_qt.main_window import (
    CHATBOX_CHAR_LIMIT,
    FOOTER_BUTTON_SIZE,
    FOOTER_SPONSOR_BUTTON_WIDTH,
    MIC_SOURCE,
    MainWindow,
    UI_CALLBACK_DRAIN_MS,
    _freeze_snapshot_value,
)
from src.utils.i18n import tr


@pytest.fixture(autouse=True)
def _isolate_config_writes(monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)


def test_main_window_has_no_shadowed_method_definitions():
    tree = ast.parse(inspect.getsource(MainWindow))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    definitions: dict[str, list[int]] = {}
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(node.name, []).append(node.lineno)

    duplicates = {
        name: line_numbers
        for name, line_numbers in definitions.items()
        if len(line_numbers) > 1
    }
    assert duplicates == {}


def test_bottom_report_preserves_actionable_translation_error():
    window = MainWindow.__new__(MainWindow)
    window._copy = lambda key, **_kwargs: {
        "report_runtime_error": "Runtime error",
    }.get(key, key)
    message = "Translation failed: Claude Compatible returned an error"

    assert MainWindow._bottom_report_text(
        window,
        message,
        color="danger",
        key="translation_error",
    ) == message
    assert MainWindow._bottom_report_text(
        window,
        "Internal worker failed: private implementation detail",
        color="danger",
    ) == "Runtime error"


def _has_ancestor(widget, ancestor) -> bool:
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = current.parentWidget()
    return False


def test_drain_ui_callback_queue_reschedules_callbacks(qtbot):
    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = threading.get_ident()
    from queue import Queue
    window._ui_callback_queue = Queue()
    window._ui_priority_callback_queue = Queue()
    called: list[str] = []

    class FakeTimer:
        started = False
        def start(self, ms):
            self.started = True

    timer = FakeTimer()
    window._callback_drain_timer = timer
    window._ui_priority_callback_queue.put_nowait(
        (0, lambda: called.append("priority"))
    )
    window._ui_callback_queue.put_nowait((0, lambda: called.append("now")))
    window._ui_callback_queue.put_nowait((15, lambda: called.append("later")))

    MainWindow._drain_ui_callback_queue(window)

    qtbot.waitUntil(
        lambda: called == ["priority", "now", "later"],
        timeout=500,
    )
    assert timer.started is True


def test_call_in_ui_from_worker_wakes_callback_drain():
    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = -1
    from queue import Queue
    window._ui_callback_queue = Queue()

    class FakeSignal:
        emitted = False

        def emit(self):
            self.emitted = True

    signal = FakeSignal()
    window.sig_ui_callback = signal

    assert MainWindow._call_in_ui(window, lambda: None) is True
    assert window._ui_callback_queue.qsize() == 1
    assert signal.emitted is True


def test_call_in_ui_bounds_normal_backlog_and_preserves_priority_lane():
    from queue import Queue

    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = -1
    window._ui_callback_queue = Queue(maxsize=1)
    window._ui_priority_callback_queue = Queue(maxsize=1)
    window._ui_callback_drop_count = 0
    window._ui_callback_queue.put_nowait((0, lambda: None))

    class FakeSignal:
        emitted = 0

        def emit(self):
            self.emitted += 1

    signal = FakeSignal()
    window.sig_ui_callback = signal

    assert MainWindow._call_in_ui(window, lambda: None) is False
    assert MainWindow._call_in_ui(window, lambda: None, priority=True) is True
    assert window._ui_callback_queue.qsize() == 1
    assert window._ui_priority_callback_queue.qsize() == 1
    assert window._ui_callback_drop_count == 1
    assert signal.emitted == 1


def test_call_in_ui_from_worker_runs_on_qt_thread(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: None)

    window = MainWindow({"ui": {"main_window_theme": "dark", "osc_guide_seen": True}})
    qtbot.addWidget(window)
    called: list[int] = []
    returned: list[bool] = []

    worker = threading.Thread(
        target=lambda: returned.append(window._call_in_ui(lambda: called.append(threading.get_ident()))),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1)

    qtbot.waitUntil(lambda: bool(called), timeout=500)
    assert returned == [True]
    assert called == [window._ui_thread_id]
    window.destroy()


def test_window_constructs_with_minimal_config(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: None)

    window = MainWindow({"ui": {"main_window_theme": "dark", "osc_guide_seen": True}})
    qtbot.addWidget(window)

    assert window.windowTitle()
    assert window.minimumSize().width() == 900
    assert window.minimumSize().height() == 430
    assert window.width() == 940
    assert window.height() == 440
    assert window.maximumSize().width() > window.width()
    assert window.maximumSize().height() > window.height()
    assert window.statusBar().isHidden()
    assert window.statusBar().maximumHeight() == 0
    assert not window.statusBar().isSizeGripEnabled()
    assert window._start_btn is not None
    assert window._mute_btn is not None
    assert window._tgt_lang2_combo is not None
    assert window._flow_source_row is window._flow_target_row
    assert window._src_text_widget.minimumHeight() <= 130
    assert window._left_panel.maximumHeight() <= 170
    assert window._mode_translation_button.isCheckable()
    assert window._mode_simultaneous_button.isCheckable()
    assert window._mode_translation_button.isChecked()
    assert not window._mode_simultaneous_button.isChecked()
    assert window._mode_translation_button.property("modeActive") == "true"
    assert window._mode_simultaneous_button.property("modeActive") == "false"
    assert window._tweaks_btn is not None
    assert not window._tweaks_btn.icon().isNull()
    assert window._tweaks_btn.iconSize().width() >= 18
    assert window._side_panel.minimumWidth() >= 240
    assert window._listen_overlay_btn is not None
    assert window._guide_btn_secondary is not None
    assert _has_ancestor(window._listen_overlay_btn, window._side_panel)
    assert _has_ancestor(window._guide_btn_secondary, window._side_panel)
    assert window._listen_overlay_btn.height() == window._desktop_btn.height()
    assert window._guide_btn_secondary.height() == window._desktop_btn.height()
    assert window._sponsors_btn.height() == FOOTER_BUTTON_SIZE
    assert window._sponsors_btn.width() == FOOTER_SPONSOR_BUTTON_WIDTH
    assert all(btn.width() == FOOTER_BUTTON_SIZE and btn.height() == FOOTER_BUTTON_SIZE for btn, _ in window._social_buttons)

    window._set_app_mode(AppMode.SIMULTANEOUS, persist=True)

    assert not window._mode_translation_button.isChecked()
    assert window._mode_simultaneous_button.isChecked()
    assert window._mode_translation_button.property("modeActive") == "false"
    assert window._mode_simultaneous_button.property("modeActive") == "true"
    assert window._config["tts"]["enabled"] is True
    assert window._tts_enabled is True

    assert window._src_header_label.text()
    assert window._tgt_header_label.text()
    assert window._tgt2_header_label.text()
    window._set_source_text("x" * (CHATBOX_CHAR_LIMIT + 20))
    assert len(window._src_text) == CHATBOX_CHAR_LIMIT
    assert window._char_label is None
    window._on_theme_toggle()
    assert window._config["ui"]["main_window_theme"] == "light"
    assert not window._tweaks_btn.icon().isNull()
    assert window._tweaks_btn.iconSize().width() >= 18
    window.destroy()


def test_settings_window_theme_toggle_syncs_main_window(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: None)

    monkeypatch.setattr("src.ui_qt.settings_window.AudioRecorder.list_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.settings_window._list_desktop_output_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.settings_window.find_best_virtual_output_device", lambda: None)
    monkeypatch.setattr(
        "src.ui_qt.settings_window.create_tts_engine",
        lambda _engine: type("_DummyTTS", (), {"get_available_voices": lambda self: []})(),
    )
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
    window = MainWindow({"ui": {"main_window_theme": "dark", "osc_guide_seen": True}})
    qtbot.addWidget(window)

    window.show_settings()
    dialog = window._settings_window
    assert dialog is not None
    assert dialog._theme_var.value() == dialog._theme_labels()["dark"]

    dialog._on_theme_toggle()

    qtbot.waitUntil(lambda: window._main_theme == "light", timeout=2000)
    assert window._config["ui"]["main_window_theme"] == "light"
    assert window._main_theme == "light"
    assert dialog._theme_var.value() == dialog._theme_labels()["light"]
    assert dialog._active_theme == "light"

    window._on_theme_toggle()

    assert window._config["ui"]["main_window_theme"] == "dark"
    assert window._main_theme == "dark"
    assert dialog._theme_var.value() == dialog._theme_labels()["dark"]
    assert dialog._active_theme == "dark"

    window.destroy()


def test_main_theme_toggle_syncs_open_child_dialogs(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: None)

    window = MainWindow({"ui": {"main_window_theme": "dark", "osc_guide_seen": True, "mode_wizard_seen": True}})
    qtbot.addWidget(window)
    dialog = QDialog(window)
    dialog.setStyleSheet(window._base_stylesheet())
    window._mode_wizard_dialog = dialog

    before = dialog.styleSheet()
    window._on_theme_toggle()

    assert window._main_theme == "light"
    assert dialog.styleSheet() == window._base_stylesheet()
    assert dialog.styleSheet() != before
    assert not any(child.__class__.__name__ == "_ThemeRevealOverlay" for child in window.findChildren(QDialog))

    window.destroy()


def test_ui_language_switch_refreshes_dynamic_buttons(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: None)

    window = MainWindow({"ui": {"language": "zh-CN", "osc_guide_seen": True}})
    qtbot.addWidget(window)

    assert window._mute_btn.text() == window._copy("mic_mute_off")
    assert window._listen_overlay_btn.text() == window._copy("listen_overlay_off")

    window._on_ui_lang_selected("English")

    assert window._settings_btn.text() == "Settings"
    assert window._manual_input_btn.text() == tr("en", "manual_input")
    assert window._translate_btn.text() == tr("en", "translate")
    assert window._start_btn.text() == "Start"
    assert window._mute_btn.text() == "Mute"
    assert window._mode_translation_button.text() == "Text"
    assert window._mode_simultaneous_button.text() == "Simul"
    assert window._desktop_btn.text() == "Reverse TL"
    assert window._listen_overlay_btn.text() == "Overlay"
    assert window._config["ui"]["language"] == "en"
    window.destroy()


def test_main_window_target_language_2_selector_updates_config(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: [])
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._register_hotkeys", lambda self: None)
    saved: list[bool] = []
    monkeypatch.setattr("src.ui_qt.main_window.MainWindow._schedule_config_save", lambda self: saved.append(True))

    config = {
        "ui": {"language": "en", "main_window_theme": "dark", "osc_guide_seen": True},
        "translation": {"target_language": "ja", "target_language_2": "en"},
    }
    window = MainWindow(config)
    qtbot.addWidget(window)

    assert window._tgt_lang2_combo is not None
    assert window._current_tgt_lang_2 == "en"

    korean_label = next(label for label, code in window._all_target_lang_options if code == "ko")
    window._tgt_lang2_combo.setCurrentText(korean_label)

    assert window._current_tgt_lang_2 == "ko"
    assert config["translation"]["target_language_2"] == "ko"
    assert saved

    window.destroy()


def test_tts_manager_reuses_loaded_xtts_until_runtime_config_changes(monkeypatch):
    created = []
    stopped = []

    class FakeManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def is_available(self):
            return True

        def start(self):
            self.started = True

        def stop(self):
            stopped.append(self)

    monkeypatch.setattr("src.tts.manager.TTSManager", FakeManager)
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "tts": {
            "enabled": True,
            "engine": "xtts",
            "allow_fallback": False,
            "output_device": None,
            "output_device_name": "",
            "output_to_vrchat": False,
            "monitor_enabled": False,
            "xtts": {
                "device": "cpu",
                "language": "auto",
                "voice": "sample",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
        "performance": {
            "tts_cache_max_mb": 24,
            "tts_cache_max_items": 60,
        },
    }
    window._tts_manager = None
    window._tts_manager_signature = None

    first = MainWindow._ensure_tts_manager(window)
    second = MainWindow._ensure_tts_manager(window)
    window._config["tts"]["xtts"]["volume"] = 0.3
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert first is second
    assert window._tts_manager is first
    assert created == [first]
    assert stopped == []

    window._config["tts"]["xtts"]["language"] = "ja"
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert stopped == [first]
    assert window._tts_manager is None


def test_qwen_tts_runtime_rebuilds_for_derived_language_and_persona_not_voice(monkeypatch):
    created = []
    stopped = []

    class FakeManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def is_available(self):
            return True

        def start(self):
            self.started = True

        def stop(self):
            stopped.append(self)

    monkeypatch.setattr("src.tts.manager.TTSManager", FakeManager)
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {
            "output_format": "translated_only",
            "target_language": "ja",
            "social": {"mode": "standard"},
        },
        "tts": {
            "enabled": True,
            "engine": "qwen_tts",
            "allow_fallback": False,
            "output_device": None,
            "output_device_name": "",
            "output_to_vrchat": False,
            "monitor_enabled": False,
            "qwen_tts": {
                "model": "qwen3-tts-instruct-flash",
                "base_url": "https://example.invalid/v1",
                "voice": "Cherry",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
        "performance": {
            "tts_cache_max_mb": 24,
            "tts_cache_max_items": 60,
        },
    }
    window._tts_manager = None
    window._tts_manager_signature = None

    first = MainWindow._ensure_tts_manager(window)
    window._config["tts"]["qwen_tts"]["voice"] = "Serena"
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert window._tts_manager is first
    assert stopped == []

    window._current_tgt_lang = "en"
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert window._tts_manager is None
    assert stopped == [first]
    second = MainWindow._ensure_tts_manager(window)
    assert second.kwargs["engine_config"]["language_type"] == "English"

    window._config["translation"]["social"] = {
        "mode": "roleplay",
        "tone": "playful",
        "persona_name": "VR Friend",
    }
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert window._tts_manager is None
    assert stopped == [first, second]


def test_tts_runtime_rebuilds_when_cache_limits_change(monkeypatch):
    stopped = []

    class FakeManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def is_available(self):
            return True

        def start(self):
            pass

        def stop(self):
            stopped.append(self)

    monkeypatch.setattr("src.tts.manager.TTSManager", FakeManager)
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {"output_format": "translated_only"},
        "tts": {
            "engine": "edge",
            "edge": {"voice": "en-US-AriaNeural", "rate": 1.0, "volume": 0.8},
        },
        "performance": {
            "tts_cache_max_mb": 24,
            "tts_cache_max_items": 60,
        },
    }
    window._tts_manager = None
    window._tts_manager_signature = None

    first = MainWindow._ensure_tts_manager(window)
    window._config["performance"]["tts_cache_max_items"] = 80
    MainWindow._reset_tts_manager_if_runtime_changed(window)

    assert stopped == [first]
    assert window._tts_manager is None


def test_realtime_session_prewarm_queues_selected_xtts_voice():
    calls = []

    class FakeManager:
        def prewarm(self, voice):
            calls.append(voice)

    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._running = True
    window._listen_session = 7
    window._ensure_tts_manager = lambda: FakeManager()
    window._tts_voice_for_engine = lambda _manager: "sample-voice"

    MainWindow._prewarm_tts_for_session(window, 7)
    MainWindow._prewarm_tts_for_session(window, 6)

    assert calls == ["sample-voice"]


def test_style_bert_tts_manager_rebuilds_when_vrchat_route_changes(monkeypatch):
    created = []
    stopped = []

    class FakeManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def is_available(self):
            return True

        def start(self):
            self.started = True

        def stop(self):
            stopped.append(self)

    monkeypatch.setattr("src.tts.manager.TTSManager", FakeManager)
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "tts": {
            "enabled": True,
            "engine": "style_bert_vits2",
            "allow_fallback": False,
            "output_device": None,
            "output_device_name": "",
            "output_to_vrchat": False,
            "monitor_enabled": False,
            "style_bert_vits2": {
                "device": "cpu",
                "bert_language": "jp",
                "voice": "sample",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
        "performance": {
            "tts_cache_max_mb": 24,
            "tts_cache_max_items": 60,
        },
    }
    window._tts_manager = None
    window._tts_manager_signature = None

    first = MainWindow._ensure_tts_manager(window)

    window._config["tts"]["output_to_vrchat"] = True
    window._config["tts"]["output_device"] = 12
    window._config["tts"]["output_device_name"] = "Speakers (MIXLINE Stream)"
    window._config["tts"]["monitor_enabled"] = True
    MainWindow._reset_tts_manager_if_runtime_changed(window)
    second = MainWindow._ensure_tts_manager(window)

    assert first is not second
    assert stopped == [first]
    assert second.kwargs["output_device"] == 12
    assert second.kwargs["output_device_name"] == "Speakers (MIXLINE Stream)"
    assert second.kwargs["prefer_virtual_output"] is True
    assert second.kwargs["monitor_output"] is True


def _quick_switch_window(config: dict):
    window = MainWindow.__new__(MainWindow)
    window._config = config
    window._ui_lang = "en"
    window._settings_window = None
    window._translator = object()
    window._manual_translation_controller = None
    window._tts_manager = None
    window._tts_manager_signature = None
    saved: list[bool] = []
    bottom: list[str] = []
    window._schedule_config_save = lambda: saved.append(True)
    window._set_bottom = lambda message, *args, **kwargs: bottom.append(message)
    return window, saved, bottom


def test_quick_switch_output_format_persists_without_settings_window():
    config = {"translation": {"output_format": "translated_with_original"}}
    window, saved, bottom = _quick_switch_window(config)

    MainWindow._on_quick_switch_changed(window, "output_format", "translated_only")

    assert config["translation"]["output_format"] == "translated_only"
    assert saved == [True]
    assert bottom == ["Quick switch updated"]


def test_quick_switch_asr_rewrite_style_is_normalized_and_published():
    config = {"translation": {"asr_rewrite_style": "off"}}
    window, saved, _bottom = _quick_switch_window(config)
    window._running = True

    MainWindow._on_quick_switch_changed(
        window,
        "asr_rewrite_style",
        "catgirl",
    )

    assert config["translation"]["asr_rewrite_style"] == "catgirl"
    assert (
        window._realtime_config_snapshot["translation"]["asr_rewrite_style"]
        == "catgirl"
    )
    assert saved == [True]


def test_quick_switch_translation_model_resets_cached_translator():
    config = {
        "translation": {
            "backend": "qianwen",
            "backend_source": "auto",
            "qianwen": {"model": "old-model"},
        }
    }
    window, saved, _bottom = _quick_switch_window(config)
    controller = type("_Controller", (), {})()
    controller.translator = object()
    window._manual_translation_controller = controller
    window._translation_failure_streak = 3
    window._translation_cooldown_until = 999999.0
    window._translation_cooldown_category = "auth"

    MainWindow._on_quick_switch_changed(window, "translation_model", "new-model")

    assert config["translation"]["backend_source"] == "manual"
    assert config["translation"]["qianwen"]["model"] == "new-model"
    assert window._translator is None
    assert controller.translator is None
    assert window._translation_failure_streak == 0
    assert window._translation_cooldown_until == 0.0
    assert window._translation_cooldown_category is None
    assert saved == [True]


def test_quick_switch_publishes_immutable_config_for_future_realtime_tasks():
    config = {
        "translation": {
            "backend": "qianwen",
            "backend_source": "manual",
            "qianwen": {"model": "old-model"},
        }
    }
    window, saved, _bottom = _quick_switch_window(config)
    window._running = True
    previous_snapshot = _freeze_snapshot_value(
        {"translation": {"qianwen": {"model": "old-model"}}}
    )
    window._realtime_config_snapshot = previous_snapshot

    MainWindow._on_quick_switch_changed(window, "translation_model", "new-model")

    assert previous_snapshot["translation"]["qianwen"]["model"] == "old-model"
    assert (
        window._realtime_config_snapshot["translation"]["qianwen"]["model"]
        == "new-model"
    )
    assert window._realtime_config_snapshot is not previous_snapshot
    assert saved == [True]


def test_quick_switch_translation_provider_resets_cached_translator_and_initializes_backend():
    config = {
        "translation": {
            "backend": "qianwen",
            "backend_source": "auto",
            "qianwen": {"model": "qwen-mt-plus"},
        }
    }
    window, saved, _bottom = _quick_switch_window(config)
    controller = type("_Controller", (), {})()
    controller.translator = object()
    window._manual_translation_controller = controller

    MainWindow._on_quick_switch_changed(window, "translation_provider", "openai")

    assert config["translation"]["backend"] == "openai"
    assert config["translation"]["backend_source"] == "manual"
    assert config["translation"]["openai"]["base_url"] == "https://api.openai.com/v1"
    assert config["translation"]["openai"]["model"]
    assert "api_key" not in config["translation"]["openai"]
    assert window._translator is None
    assert controller.translator is None
    assert saved == [True]


def test_quick_switch_roleplay_profile_updates_social_config():
    config = {
        "translation": {
            "social": {
                "mode": "standard",
                "persona_preset": "custom",
            }
        },
        "tts": {"engine": "edge", "edge": {"voice": "en-US-AriaNeural"}},
    }
    window, saved, _bottom = _quick_switch_window(config)
    resets: list[bool] = []
    window._reset_tts_manager_if_runtime_changed = lambda: resets.append(True)

    MainWindow._on_quick_switch_changed(window, "roleplay_profile", "roleplay:frieren")

    social = config["translation"]["social"]
    assert social["mode"] == "roleplay"
    assert social["persona_preset"] == "frieren"
    assert social["persona_prompt"]
    assert window._translator is None
    assert resets == [True]
    assert saved == [True]


def test_quick_switch_tts_voice_does_not_reset_tts_runtime():
    config = {"tts": {"engine": "edge", "edge": {"voice": "old-voice"}}}
    window, saved, _bottom = _quick_switch_window(config)
    resets: list[bool] = []
    window._reset_tts_manager_if_runtime_changed = lambda: resets.append(True)

    MainWindow._on_quick_switch_changed(window, "tts_voice", "new-voice")

    assert config["tts"]["edge"]["voice"] == "new-voice"
    assert resets == []
    assert saved == [True]


def test_quick_switch_noise_reduction_updates_config_and_live_recorder():
    config = {"audio": {"denoise_strength": 0.0}}
    window, saved, _bottom = _quick_switch_window(config)
    applied: list[float] = []
    window._recorder = type("_Recorder", (), {"set_denoise_strength": lambda self, value: applied.append(value)})()

    MainWindow._on_quick_switch_changed(window, "noise_reduction", 0.65)

    assert config["audio"]["denoise_strength"] == 0.65
    assert applied == [0.65]
    assert saved == [True]


def test_settings_preload_is_opt_in_and_skips_heavy_tts_engines():
    window = MainWindow.__new__(MainWindow)

    window._config = {
        "performance": {"profile": "balanced"},
        "tts": {"engine": "edge"},
    }
    assert MainWindow._settings_preload_enabled(window) is False

    window._config["performance"]["preload_settings_window"] = True
    assert MainWindow._settings_preload_enabled(window) is True

    window._config["tts"]["engine"] = "xtts"
    assert MainWindow._settings_preload_enabled(window) is False

    window._config["tts"]["engine"] = "edge"
    window._config["performance"]["profile"] = "low_power"
    assert MainWindow._settings_preload_enabled(window) is False


def test_listen_overlay_toggle_updates_service_and_avatar_state(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    calls = []
    window._config = {
        "vrc_listen": {},
        "osc": {
            "avatar_sync": {
                "enabled": True,
                "params": {"overlay": "MioOverlayActive"},
            }
        },
    }
    window._listen_overlay_enabled = False
    window._floating_window = None
    window._overlay_service = None
    window._output_dispatcher = None
    window._set_bottom = lambda *args, **kwargs: None
    window._refresh_listen_overlay_button = lambda: calls.append("refresh")
    window._sync_settings_window_vrc_listen_state = lambda: calls.append("sync_settings")
    window._schedule_config_save = lambda: calls.append("save")

    class _OverlayBackend:
        def show_message(self, _message):
            return True

        def set_listen_status(self, _listening):
            pass

        def reveal(self):
            calls.append("reveal")

        def hide(self):
            calls.append("hide")

    window._ensure_floating_window = lambda: _OverlayBackend()
    window._ensure_sender = lambda: type(
        "_Sender",
        (),
        {"send_avatar_bool": lambda self, name, value, *, force=False: calls.append((name, value, force)) or True},
    )()

    window._set_listen_overlay_enabled(True, persist=True)

    assert window._listen_overlay_enabled is True
    assert window._config["vrc_listen"]["show_overlay"] is True
    assert ("MioOverlayActive", True, True) in calls
    assert "save" in calls

    window._set_listen_overlay_enabled(False, persist=False)

    assert window._listen_overlay_enabled is False
    assert window._config["vrc_listen"]["show_overlay"] is False
    assert ("MioOverlayActive", False, True) in calls


def test_listen_target_process_names_are_configurable():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "vrc_listen": {
            "target_process_names": ["Game.exe", "UnityPlayer.exe", "Game.exe", ""],
        }
    }

    assert window._listen_target_process_names() == ["Game.exe", "UnityPlayer.exe"]


def test_auto_microphone_resolves_current_default_device(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {"audio": {"input_device_mode": "auto", "input_device": ""}}
    window._devices = {}

    devices = [
        {"index": 7, "name": "PicoStreamingMicrophone"},
        {"index": 1, "name": "Razer Seiren V2 X"},
    ]
    monkeypatch.setattr("src.ui_qt.main_window._list_microphone_devices", lambda: devices)
    monkeypatch.setattr(
        MainWindow,
        "_current_default_input_device_name",
        lambda self, _devices: "Razer Seiren V2 X",
    )

    assert MainWindow._resolve_mic_input_device_name(window, refresh=True) == "Razer Seiren V2 X"
    assert window._devices["Razer Seiren V2 X"] == 1


def test_fixed_microphone_matches_stable_parenthesized_hardware_identity(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "audio": {
            "input_device_mode": "fixed",
            "input_device": "??? (Razer Seiren V2 X)",
        }
    }
    window._devices = {}
    actual_name = "Microphone (Razer Seiren V2 X)"
    monkeypatch.setattr(
        "src.ui_qt.main_window._list_microphone_devices",
        lambda: [{"index": 3, "name": actual_name}],
    )

    assert MainWindow._resolve_mic_input_device_name(window, refresh=True) == actual_name
    assert window._devices[actual_name] == 3


def test_start_microphone_capture_resolves_fixed_device_index(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "audio": {
            "input_device_mode": "fixed",
            "input_device": "Razer Seiren V2 X",
        }
    }
    window._devices = {}
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "src.ui_qt.main_window._list_microphone_devices",
        lambda: [{"index": 1, "name": "Razer Seiren V2 X"}],
    )

    class DummyRecorder:
        active_input_device_name = "Razer Seiren V2 X"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr("src.audio.recorder.AudioRecorder", DummyRecorder)

    MainWindow._start_microphone_capture(window)

    assert captured["input_device"] == 1
    assert window._active_mic_input_device_name == "Razer Seiren V2 X"


def test_microphone_watch_keeps_healthy_fallback_for_missing_fixed_device():
    window = MainWindow.__new__(MainWindow)
    fallback_name = "Microphone (PicoStreamingMicrophone)"
    configured_name = "??? (Razer Seiren V2 X)"
    signature = (
        (fallback_name,),
        fallback_name,
        "fixed",
        configured_name,
        configured_name,
    )
    window._destroying = False
    window._running = True
    window._mic_recovery_in_progress = False
    window._recorder = type("Recorder", (), {"is_running": True})()
    window._active_mic_input_device_name = fallback_name
    window._last_mic_device_signature = signature
    window._microphone_device_signature = lambda: signature
    window._maybe_log_mic_diagnostics = lambda: None
    window._schedule_mic_audio_watch = lambda: None
    restarted: list[str] = []
    window._restart_microphone_capture = lambda reason: restarted.append(reason)

    MainWindow._poll_mic_audio_watch(window)

    assert restarted == []


def test_microphone_watch_switches_back_when_fixed_device_appears():
    window = MainWindow.__new__(MainWindow)
    fallback_name = "Microphone (PicoStreamingMicrophone)"
    configured_name = "??? (Razer Seiren V2 X)"
    actual_name = "Microphone (Razer Seiren V2 X)"
    previous = (
        (fallback_name,),
        fallback_name,
        "fixed",
        configured_name,
        configured_name,
    )
    current = (
        tuple(sorted((fallback_name, actual_name))),
        fallback_name,
        "fixed",
        configured_name,
        actual_name,
    )
    window._destroying = False
    window._running = True
    window._mic_recovery_in_progress = False
    window._recorder = type("Recorder", (), {"is_running": True})()
    window._active_mic_input_device_name = fallback_name
    window._last_mic_device_signature = previous
    window._microphone_device_signature = lambda: current
    window._maybe_log_mic_diagnostics = lambda: None
    window._schedule_mic_audio_watch = lambda: None
    restarted: list[str] = []
    window._restart_microphone_capture = lambda reason: restarted.append(reason)

    MainWindow._poll_mic_audio_watch(window)

    assert restarted == ["configured microphone became available"]


def test_microphone_watch_tracks_default_changes_while_using_fallback():
    window = MainWindow.__new__(MainWindow)
    old_default = "Microphone (PicoStreamingMicrophone)"
    new_default = "Microphone (USB Audio Device)"
    configured_name = "Missing Microphone"
    previous = (
        (old_default,),
        old_default,
        "fixed",
        configured_name,
        configured_name,
    )
    current = (
        tuple(sorted((old_default, new_default))),
        new_default,
        "fixed",
        configured_name,
        configured_name,
    )
    window._destroying = False
    window._running = True
    window._mic_recovery_in_progress = False
    window._recorder = type("Recorder", (), {"is_running": True})()
    window._active_mic_input_device_name = old_default
    window._last_mic_device_signature = previous
    window._microphone_device_signature = lambda: current
    window._maybe_log_mic_diagnostics = lambda: None
    window._schedule_mic_audio_watch = lambda: None
    restarted: list[str] = []
    window._restart_microphone_capture = lambda reason: restarted.append(reason)

    MainWindow._poll_mic_audio_watch(window)

    assert restarted == ["fallback default microphone changed"]


def test_main_device_combo_restarts_microphone_while_running(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._config = {"audio": {}}
    restarted: list[str] = []

    monkeypatch.setattr(window, "_copy", lambda key, **_kwargs: {
        "input_device_missing": "No microphone",
        "mic_device_auto_option": "Auto",
    }.get(key, key))
    monkeypatch.setattr(window, "_refresh_device_combo", lambda: None)
    monkeypatch.setattr(window, "_schedule_config_save", lambda: None)
    monkeypatch.setattr(window, "_restart_microphone_capture", lambda reason: restarted.append(reason))

    MainWindow._on_device_combo_changed(window, "Razer Seiren V2 X")

    assert window._config["audio"]["input_device_mode"] == "fixed"
    assert window._config["audio"]["input_device"] == "Razer Seiren V2 X"
    assert restarted == ["microphone device changed by user"]


def test_mode_wizard_tts_recommendation_updates_config(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {"translation": {}, "vrc_listen": {}, "tts": {}, "ui": {}}
    window._desktop_capture_enabled = False
    window._listen_overlay_enabled = False
    window._mode_manager = type(
        "_ModeManager",
        (),
        {
            "mode": AppMode.TRANSLATION,
            "set_mode": lambda self, mode: type("_Change", (), {"tts_changed": False, "output_device_changed": False, "changed": True})(),
        },
    )()
    calls: list[str] = []

    monkeypatch.setattr(window, "_set_app_mode", lambda mode, persist: calls.append(mode.value))
    monkeypatch.setattr(window, "_ensure_overlay_service", lambda create_backend=True: type("_OverlayService", (), {"set_enabled": lambda self, enabled, reveal=True: None})())
    monkeypatch.setattr(window, "_sync_avatar_overlay_state", lambda force=False: None)
    monkeypatch.setattr(window, "_sync_tts_enabled_from_config", lambda: None)
    monkeypatch.setattr(window, "_refresh_mode_buttons", lambda: None)
    monkeypatch.setattr(window, "_refresh_desktop_capture_button", lambda: None)
    monkeypatch.setattr(window, "_refresh_listen_overlay_button", lambda: None)
    monkeypatch.setattr(window, "_sync_settings_window_vrc_listen_state", lambda: None)
    monkeypatch.setattr(window, "_schedule_config_save", lambda: None)
    monkeypatch.setattr(window, "_set_bottom", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_t", lambda key, **_kwargs: key)

    MainWindow._apply_mode_wizard_result(window, "tts")

    assert calls == ["simultaneous"]
    assert window._config["translation"]["send_to_chatbox"] is True
    assert window._config["tts"]["enabled"] is True
    assert window._config["tts"]["auto_read"] is True
    assert window._config["tts"]["output_to_vrchat"] is True
    assert window._config["vrc_listen"]["enabled"] is False
    assert window._config["ui"]["mode_wizard_seen"] is True


def test_mode_wizard_open_settings_targets_player_facing_page():
    assert MainWindow._settings_page_for_mode_wizard("chatbox") == "voice"
    assert MainWindow._settings_page_for_mode_wizard("listen") == "vrc_listen"
    assert MainWindow._settings_page_for_mode_wizard("overlay") == "vrc_listen"
    assert MainWindow._settings_page_for_mode_wizard("tts") == "tts"
    assert MainWindow._settings_page_for_mode_wizard("manual") == "translation"
    assert MainWindow._settings_page_for_mode_wizard("unknown") == "voice"


def test_desktop_audio_watch_restarts_when_output_signature_changes(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._running = True
    window._desktop_capture_enabled = True
    window._listen_available = True
    window._listen_recorder = type("_Recorder", (), {"is_running": True})()
    window._last_desktop_device_signature = (("Old Speakers",), "Old Speakers")
    restarted: list[bool] = []

    monkeypatch.setattr(window, "_refresh_listen_availability", lambda refresh_devices=False: True)
    monkeypatch.setattr(window, "_refresh_desktop_capture_button", lambda: None)
    monkeypatch.setattr(window, "_maybe_log_listen_diagnostics", lambda: None)
    monkeypatch.setattr(
        window,
        "_desktop_device_signature",
        lambda refresh=False: (("New Speakers",), "New Speakers"),
    )
    monkeypatch.setattr(window, "_restart_desktop_capture", lambda message=None: restarted.append(True))

    MainWindow._poll_desktop_audio_watch(window)

    assert restarted == [True]


def test_trilingual_output_format_uses_second_translation():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            # output_format_2 merged into output_format
            "output_format": "translated1_with_translated2_original",
        }
    }

    text = MainWindow._format_chatbox_output(window, "原文", "译文1", "译文2")

    assert text == "译文1(译文2)(原文)"


def test_chatbox_template_removes_empty_second_translation_line():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "translated_with_original",
            "chatbox_template": "{translatedText}\\n{translatedText2}\\n{text}",
        }
    }

    text = MainWindow._format_chatbox_output(window, "原文", "译文1", "")

    assert text == "译文1\n原文"


def test_realtime_mic_trilingual_output_translates_second_target(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._listen_session = 7
    window._mic_muted = False
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._translator = None
    window._translation_state_lock = threading.Lock()
    window._config = {
        "translation": {
            "output_format": "translated1_with_translated2_original",
            "send_to_chatbox": True,
        }
    }
    sent: list[str] = []
    shown: list[str] = []
    spoken: list[tuple[str, str]] = []
    successes: list[bool] = []

    class _Sender:
        def send_chatbox(self, text):
            sent.append(text)
            return True

    class _Translator:
        def translate(self, text, src, tgt, context_source=None):
            return f"{tgt}:{text}"

    monkeypatch.setattr("src.ui_qt.main_window.create_translator", lambda _config: _Translator())
    window._call_in_ui = lambda callback: callback()
    window._set_runtime_status = lambda *args, **kwargs: None
    window._restore_runtime_status = lambda *args, **kwargs: None
    window._transcribe_for_source = lambda *args, **kwargs: "你好"
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: successes.append(True)
    window._record_translation_failure = lambda _friendly: None
    window._format_translation_error = lambda error: type("_Friendly", (), {"short_message": str(error)})()
    window._set_bottom = lambda *args, **kwargs: None
    window._pulse_avatar_error = lambda: None
    window._set_source_text = lambda text: None
    window._show_tgt = lambda text, **kwargs: shown.append(text)
    window._ensure_sender = lambda: _Sender()
    window._auto_read_mic_translation = lambda **kwargs: spoken.append((kwargs["original_text"], kwargs["translated_text"]))

    MainWindow._process_final_audio_segment(window, b"audio", None, "zh", 7, MIC_SOURCE)

    assert successes == [True]
    assert sent == ["ja:你好(en:你好)(你好)"]
    assert shown == ["ja:你好\nen:你好"]
    assert window._last_tgt_text == "ja:你好"
    assert window._last_tgt2_text == "en:你好"
    assert spoken == [("你好", "ja:你好")]


def test_realtime_mic_template_translates_third_target(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._listen_session = 9
    window._mic_muted = False
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._current_tgt_lang_3 = "ko"
    window._translator = None
    window._translation_state_lock = threading.Lock()
    window._config = {
        "translation": {
            "output_format": "translated_only",
            "chatbox_template": "{translatedText}\\n{translatedText2}\\n{translatedText3}\\n{text}",
            "send_to_chatbox": True,
        }
    }
    sent: list[str] = []
    shown: list[str] = []
    successes: list[bool] = []

    class _Sender:
        def send_chatbox(self, text):
            sent.append(text)
            return True

    class _Translator:
        def translate(self, text, src, tgt, context_source=None):
            return f"{tgt}:{text}"

    monkeypatch.setattr("src.ui_qt.main_window.create_translator", lambda _config: _Translator())
    window._call_in_ui = lambda callback: callback()
    window._set_runtime_status = lambda *args, **kwargs: None
    window._restore_runtime_status = lambda *args, **kwargs: None
    window._transcribe_for_source = lambda *args, **kwargs: "你好"
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: successes.append(True)
    window._record_translation_failure = lambda _friendly: None
    window._format_translation_error = lambda error: type("_Friendly", (), {"short_message": str(error)})()
    window._set_bottom = lambda *args, **kwargs: None
    window._pulse_avatar_error = lambda: None
    window._set_source_text = lambda text: None
    window._show_tgt = lambda text, **kwargs: shown.append(text)
    window._ensure_sender = lambda: _Sender()
    window._auto_read_mic_translation = lambda **kwargs: None

    MainWindow._process_final_audio_segment(window, b"audio", None, "zh", 9, MIC_SOURCE)

    assert successes == [True]
    assert sent == ["ja:你好\nen:你好\nko:你好\n你好"]
    assert shown == ["ja:你好\nen:你好\nko:你好"]
    assert window._last_tgt_text == "ja:你好"
    assert window._last_tgt2_text == "en:你好"
    assert window._last_tgt3_text == "ko:你好"


def test_realtime_mic_stale_session_does_not_send_or_update(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._listen_session = 11
    window._mic_muted = False
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._current_tgt_lang_3 = ""
    window._translator = None
    window._config = {"translation": {"output_format": "translated_only", "send_to_chatbox": True}}
    sent: list[str] = []
    shown: list[str] = []
    spoken: list[dict[str, str]] = []
    successes: list[bool] = []

    class _Sender:
        def send_chatbox(self, text):
            sent.append(text)
            return True

    class _Translator:
        def translate(self, text, src, tgt, context_source=None):
            window._running = False
            window._listen_session += 1
            return f"{tgt}:{text}"

    monkeypatch.setattr("src.ui_qt.main_window.create_translator", lambda _config: _Translator())
    window._call_in_ui = lambda callback: callback()
    window._set_runtime_status = lambda *args, **kwargs: None
    window._restore_runtime_status = lambda *args, **kwargs: None
    window._transcribe_for_source = lambda *args, **kwargs: "你好"
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: successes.append(True)
    window._record_translation_failure = lambda _friendly: None
    window._format_translation_error = lambda error: type("_Friendly", (), {"short_message": str(error)})()
    window._set_bottom = lambda *args, **kwargs: None
    window._pulse_avatar_error = lambda: None
    window._set_source_text = lambda text: None
    window._show_tgt = lambda text, **kwargs: shown.append(text)
    window._ensure_sender = lambda: _Sender()
    window._auto_read_mic_translation = lambda **kwargs: spoken.append(kwargs)

    MainWindow._process_final_audio_segment(window, b"audio", None, "zh", 11, MIC_SOURCE)

    assert successes == []
    assert sent == []
    assert shown == []
    assert spoken == []


def test_original_only_output_ignores_stale_second_translation():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "original_only",
        }
    }

    text = MainWindow._format_chatbox_output(window, "原文", "译文1", "译文2")

    assert text == "原文"


def test_original_only_manual_translate_does_not_create_translator(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "original_only",
        }
    }
    window._src_text = "hello"
    window._translator = None
    window._manual_translation_controller = None
    window._manual_translation_generation = 0
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._current_tgt_lang_3 = ""
    window._translating = False
    window._last_tgt2_text = "old second"
    window._manual_send_after_translate = False
    window._manual_done_callback = None
    shown: list[tuple[str, bool]] = []
    finished: list[dict] = []

    monkeypatch.setattr(
        "src.ui_qt.main_window.create_translator",
        lambda _config: (_ for _ in ()).throw(AssertionError("translator should not be created")),
    )
    window._show_tgt = lambda text, *, is_error=False: shown.append((text, is_error))
    window._finish_manual_translation = lambda **kwargs: finished.append(kwargs)
    window._detect_source_lang = lambda _text: "en"
    window._format_translation_error = lambda error: type("_Friendly", (), {"short_message": str(error)})()

    window._do_manual_translate()

    assert shown == [("hello", False)]
    assert window._last_tgt2_text == ""
    assert len(finished) == 1
    assert finished[0]["output_message"].source == "manual"
    assert finished[0]["output_message"].original_text == "hello"
    assert finished[0]["output_message"].translated_text == "hello"
