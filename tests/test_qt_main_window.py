import threading

from src.core.mode_manager import AppMode
from src.ui_qt.main_window import MainWindow, UI_CALLBACK_DRAIN_MS
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
    monkeypatch.setattr("src.ui_qt.settings_window.play_theme_reveal", lambda *args, **kwargs: None)

    window = MainWindow({"ui": {"main_window_theme": "dark", "osc_guide_seen": True}})
    qtbot.addWidget(window)

    window.show_settings()
    dialog = window._settings_window
    assert dialog is not None
    assert dialog._theme_var.value() == dialog._theme_labels()["dark"]

    dialog._on_theme_toggle()

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


def test_trilingual_output_format_uses_second_translation():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "translated_with_original",
            "output_format_2": "translated1_with_translated2_original",
        }
    }

    text = MainWindow._format_chatbox_output(window, "原文", "译文1", "译文2")

    assert text == "译文1（译文2）（原文）"


def test_original_only_output_ignores_stale_second_translation():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "original_only",
            "output_format_2": "translated1_with_translated2_original",
        }
    }

    text = MainWindow._format_chatbox_output(window, "原文", "译文1", "译文2")

    assert text == "原文"


def test_original_only_manual_translate_does_not_create_translator(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "original_only",
            "output_format_2": "translated1_with_translated2_original",
        }
    }
    window._src_text = "hello"
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

    window._do_manual_translate()

    assert shown == [("hello", False)]
    assert window._last_tgt2_text == ""
    assert finished == [{}]
