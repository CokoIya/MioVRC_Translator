from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QWidget

from src.ui_qt.text_input_window import TEXT_INPUT_CHAR_LIMIT, TextInputWindow


def test_text_input_window_uses_translated_labels(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    config = {
        "ui": {"language": "zh-CN", "main_window_theme": "light"},
        "text_input_window": {},
    }
    sent: list[str] = []

    window = TextInputWindow(None, config, on_send=lambda text: sent.append(text) or True)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(20)
    assert window.isVisible()

    assert window._input_edit.placeholderText() != "text_input_placeholder"
    assert window._send_btn.text() == "翻译并发送"
    assert not window._send_btn.isEnabled()

    window._input_edit.setPlainText("hello")
    qtbot.wait(20)
    assert window._send_btn.isEnabled()

    window._on_send_clicked()

    assert sent == ["hello"]
    assert window.isVisible()
    assert window._closed is False


def test_text_input_window_trims_to_vrchat_chatbox_limit(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    sent: list[str] = []
    window = TextInputWindow(
        None,
        {"ui": {"language": "en"}, "text_input_window": {}},
        on_send=lambda text: sent.append(text) or True,
    )
    qtbot.addWidget(window)

    window._input_edit.setPlainText("x" * (TEXT_INPUT_CHAR_LIMIT + 10))
    qtbot.wait(20)

    assert len(window._input_edit.toPlainText()) == TEXT_INPUT_CHAR_LIMIT
    assert window._counter_label.text() == f"{TEXT_INPUT_CHAR_LIMIT} / {TEXT_INPUT_CHAR_LIMIT}"

    window._on_send_clicked()

    assert sent == ["x" * TEXT_INPUT_CHAR_LIMIT]


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


def test_text_input_window_resize_geometry_supports_edges(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    window = TextInputWindow(None, {"ui": {"language": "en"}, "text_input_window": {}})
    qtbot.addWidget(window)
    window.setMinimumSize(360, 220)

    start = QRect(100, 100, 520, 320)

    east = window._geometry_for_resize_delta("e", start, QPoint(80, 0))
    assert east.width() == 600
    assert east.left() == 100

    west = window._geometry_for_resize_delta("w", start, QPoint(-60, 0))
    assert west.width() == 580
    assert west.left() == 40

    north_west = window._geometry_for_resize_delta("nw", start, QPoint(-40, -30))
    assert north_west.left() == 60
    assert north_west.top() == 70
    assert north_west.width() == 560
    assert north_west.height() == 350

    south = window._geometry_for_resize_delta("s", start, QPoint(0, 70))
    assert south.height() == 390


def test_text_input_window_resize_hit_testing_and_scaling(qtbot, monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    window = TextInputWindow(None, {"ui": {"language": "en"}, "text_input_window": {}})
    qtbot.addWidget(window)
    window.resize(520, 320)
    window.show()
    qtbot.waitExposed(window)

    assert window._get_resize_mode(QPoint(2, 2)) == "nw"
    assert window._get_resize_mode(QPoint(window.width() - 2, window.height() - 2)) == "se"
    assert window._get_resize_mode(QPoint(window.width() // 2, window.height() - 2)) == "s"
    assert window._get_resize_mode(QPoint(window.width() // 2, window.height() // 2)) == ""

    monkeypatch.setattr(window, "_ui_scale", lambda: 1.25)
    window._apply_scaled_layout(force=True)

    assert window._pin_button.width() >= 38
    assert window._pin_button.width() > 30
    assert window._input_edit.minimumHeight() >= 165
