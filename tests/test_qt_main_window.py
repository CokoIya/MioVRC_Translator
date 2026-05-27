import threading

from src.ui_qt.main_window import MainWindow, UI_CALLBACK_DRAIN_MS
from src.utils.i18n import tr


def test_drain_ui_callback_queue_reschedules_callbacks(qtbot):
    window = MainWindow.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = threading.get_ident()
    from queue import Queue
    window._ui_callback_queue = Queue()
    window._callback_drain_timer = None
    called: list[str] = []
    window._ui_callback_queue.put_nowait((0, lambda: called.append("now")))
    window._ui_callback_queue.put_nowait((15, lambda: called.append("later")))

    MainWindow._drain_ui_callback_queue(window)

    qtbot.waitUntil(lambda: called == ["now", "later"], timeout=500)


def test_window_constructs_with_minimal_config(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window.AudioRecorder.list_devices", lambda: [])
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
    assert window._src_header_label.text()
    assert window._tgt_header_label.text()
    window._on_theme_toggle()
    assert window._config["ui"]["main_window_theme"] == "light"
    window.destroy()


def test_ui_language_switch_refreshes_dynamic_buttons(qtbot, monkeypatch):
    monkeypatch.setattr("src.ui_qt.main_window.AudioRecorder.list_devices", lambda: [])
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
