from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QWidget

from src.ui_qt.text_input_window import (
    DEFAULT_GEOMETRY,
    DEFAULT_SIZE,
    HEADER_CONTROL_SIZE,
    INPUT_MIN_HEIGHT,
    MIN_SIZE,
    TEXT_INPUT_CHAR_LIMIT,
    TextInputWindow,
)


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

    # Controls track the display rather than any one pixel size, so assert the
    # relationship to the base metrics instead of the numbers of a past layout.
    assert window._pin_button.width() >= int(HEADER_CONTROL_SIZE * 1.25)
    assert window._pin_button.width() > HEADER_CONTROL_SIZE
    assert window._input_edit.minimumHeight() >= int(INPUT_MIN_HEIGHT * 1.25)


def test_composer_and_overlay_share_chrome_without_sharing_a_sheet():
    """One definition drives both windows' shell, caption, buttons and slider.

    They are separate windows with different jobs, so neither sheet may
    contain the other's widgets; but a player sees them side by side, so the
    chrome they do have in common is generated once.
    """

    from src.ui_qt.styles import build_floating_window_styles, build_text_input_styles

    composer = build_text_input_styles("dark")
    overlay = build_floating_window_styles("dark")

    assert composer != overlay
    assert "QFrame#textInputShell" in composer
    assert "QFrame#floatingShell" not in composer
    assert "QTextEdit#inputTextEdit" in composer
    assert "QTextEdit#inputTextEdit" not in overlay

    # The shared chrome must land in both, with the same metrics.
    for fragment in (
        "min-height: 22px;",
        "border-radius: 9px;",
        'QSlider::handle:horizontal',
        "width: 10px;",
    ):
        assert fragment in composer, fragment
        assert fragment in overlay, fragment


def test_text_input_counter_warns_before_vrchat_truncates(qtbot, monkeypatch):
    """VRChat cuts the message silently, so the count has to speak up first."""

    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    window = TextInputWindow(None, {"ui": {"language": "en"}, "text_input_window": {}})
    qtbot.addWidget(window)

    window._input_edit.setPlainText("a")
    assert window._counter_label.property("limit") == ""

    window._input_edit.setPlainText("a" * int(TEXT_INPUT_CHAR_LIMIT * 0.9))
    assert window._counter_label.property("limit") == "warn"

    window._input_edit.setPlainText("a" * TEXT_INPUT_CHAR_LIMIT)
    assert window._counter_label.property("limit") == "full"


def test_text_input_footer_hides_the_hint_rather_than_overlapping(qtbot, monkeypatch):
    """Half a shortcut is worth less than the room it costs."""

    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    window = TextInputWindow(None, {"ui": {"language": "zh-CN"}, "text_input_window": {}})
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    window.resize(640, 260)
    window._refresh_header_labels()
    assert window._key_hint_label.isVisible() is True
    full_hint = window._key_hint_label.text()

    window.resize(window.minimumWidth(), window.minimumHeight())
    window._refresh_header_labels()

    hint = window._key_hint_label
    assert hint.isVisible() is False or hint.text() == full_hint


def test_text_input_window_geometry_default_is_composer_sized():
    """A 144-character composer should not open a third of the screen."""

    assert DEFAULT_SIZE == (460, 210)
    assert DEFAULT_GEOMETRY.startswith("460x210")
    assert MIN_SIZE[0] < 360 and MIN_SIZE[1] < 220
