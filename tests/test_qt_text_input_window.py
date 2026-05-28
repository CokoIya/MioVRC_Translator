from PySide6.QtWidgets import QWidget

from src.ui_qt.text_input_window import TextInputWindow


def test_text_input_window_uses_translated_labels(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    config = {
        "ui": {"language": "zh-CN", "main_window_theme": "light"},
        "text_input_window": {},
    }
    sent: list[str] = []

    window = TextInputWindow(None, config, on_send=lambda text: sent.append(text) or True)
    qtbot.addWidget(window)

    assert window._input_edit.placeholderText() != "text_input_placeholder"
    assert window._send_btn.text() == "翻译并发送"
    assert not window._send_btn.isEnabled()

    window._input_edit.setPlainText("hello")
    qtbot.wait(20)
    assert window._send_btn.isEnabled()

    window._on_send_clicked()

    assert sent == ["hello"]


def test_text_input_window_uses_floating_controls_and_icons(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    config = {
        "ui": {"language": "en", "main_window_theme": "dark"},
        "text_input_window": {"topmost": True, "opacity": 0.72},
    }

    window = TextInputWindow(None, config)
    qtbot.addWidget(window)

    assert window._opacity_slider.value() == 72
    assert not window._pin_button.icon().isNull()
    assert not window._send_btn.icon().isNull()
    assert not window._clear_btn.icon().isNull()
    assert not window._close_btn.icon().isNull()

    window._opacity_slider.setValue(61)
    assert config["text_input_window"]["opacity"] == 0.61
    assert "61" in window._opacity_label.text()

    window.show()
    qtbot.wait(20)
    assert window.isVisible()
    window.toggle_topmost()

    assert window.isVisible()
    assert config["text_input_window"]["topmost"] is False


def test_text_input_window_is_not_owned_by_main_window(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    parent = QWidget()
    qtbot.addWidget(parent)
    config = {
        "ui": {"language": "en", "main_window_theme": "dark"},
        "text_input_window": {},
    }

    window = TextInputWindow(parent, config)
    qtbot.addWidget(window)

    assert window.parentWidget() is None
