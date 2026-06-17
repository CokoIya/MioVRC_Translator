import threading

from PySide6.QtWidgets import QDialog

from src.core.mode_manager import AppMode
from src.ui_qt.main_window import MIC_SOURCE, MainWindow, UI_CALLBACK_DRAIN_MS
from src.utils.i18n import tr


def test_drain_ui_callback_queue_reschedules_callbacks(qtbot):
    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = threading.get_ident()
    from queue import Queue
    window._ui_callback_queue = Queue()
    called: list[str] = []

    class FakeTimer:
        started = False
        def start(self, ms):
            self.started = True

    timer = FakeTimer()
    window._callback_drain_timer = timer
    window._ui_callback_queue.put_nowait((0, lambda: called.append("now")))
    window._ui_callback_queue.put_nowait((15, lambda: called.append("later")))

    MainWindow._drain_ui_callback_queue(window)

    qtbot.waitUntil(lambda: called == ["now", "later"], timeout=500)
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
    assert window.minimumSize().width() == 1040
    assert window.minimumSize().height() == 640
    assert window.width() == 1180
    assert window.height() == 720
    assert window.maximumSize().width() > window.width()
    assert window.maximumSize().height() > window.height()
    assert window._start_btn is not None
    assert window._mute_btn is not None
    assert window._mode_translation_button.isCheckable()
    assert window._mode_simultaneous_button.isCheckable()
    assert window._mode_translation_button.isChecked()
    assert not window._mode_simultaneous_button.isChecked()
    assert window._mode_translation_button.property("modeActive") == "true"
    assert window._mode_simultaneous_button.property("modeActive") == "false"
    assert window._tweaks_btn is not None
    assert not window._tweaks_btn.icon().isNull()
    assert window._tweaks_btn.iconSize().width() == 18

    window._set_app_mode(AppMode.SIMULTANEOUS, persist=True)

    assert not window._mode_translation_button.isChecked()
    assert window._mode_simultaneous_button.isChecked()
    assert window._mode_translation_button.property("modeActive") == "false"
    assert window._mode_simultaneous_button.property("modeActive") == "true"
    assert window._config["tts"]["enabled"] is True
    assert window._tts_enabled is True

    assert window._src_header_label.text()
    assert window._tgt_header_label.text()
    window._on_theme_toggle()
    assert window._config["ui"]["main_window_theme"] == "light"
    assert not window._tweaks_btn.icon().isNull()
    assert window._tweaks_btn.iconSize().width() == 18
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
    assert window._mode_translation_button.text() == "Translate"
    assert window._mode_simultaneous_button.text() == "Simul"
    assert window._desktop_btn.text() == "Reverse TL"
    assert window._listen_overlay_btn.text() == "Overlay"
    assert window._config["ui"]["language"] == "en"
    window.destroy()


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
