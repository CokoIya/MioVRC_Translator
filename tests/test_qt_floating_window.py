from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QPoint, QRect

from src.ui_qt.floating_window import FloatingWindow
from src.ui_qt.styles import build_floating_window_styles, build_text_input_styles


def test_floating_window_show_and_hide(qtbot):
    closed: list[bool] = []
    window = FloatingWindow(None, "zh-CN", on_close=lambda: closed.append(True))
    qtbot.addWidget(window)

    window.show_translation("你好", source="listen")

    assert window.isVisible() is True
    assert window._visible is True

    window.hide()

    assert window.isVisible() is False
    assert window._visible is False
    assert closed == [True]


def test_floating_window_reveal_makes_window_visible(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)

    window.reveal()

    assert window.isVisible() is True
    assert window._visible is True


def test_floating_window_service_hide_does_not_emit_close_callback(qtbot):
    closed: list[bool] = []
    window = FloatingWindow(None, "zh-CN", on_close=lambda: closed.append(True))
    qtbot.addWidget(window)

    window.reveal()
    window.hide_from_service()

    assert window.isVisible() is False
    assert window._visible is False
    assert closed == []


def test_floating_window_close_and_pin_keep_state_consistent(qtbot):
    closed: list[bool] = []
    window = FloatingWindow(None, "zh-CN", on_close=lambda: closed.append(True))
    qtbot.addWidget(window)

    window.reveal()
    window.toggle_topmost()

    assert window.isVisible() is True
    assert window._visible is True

    window.close()
    qtbot.wait(20)

    assert window.isVisible() is False
    assert window._visible is False
    assert closed == [True]


def test_floating_window_is_not_owned_by_main_window(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)

    window = FloatingWindow(parent, "zh-CN")
    qtbot.addWidget(window)

    assert window.parentWidget() is None


def test_floating_window_listen_status_updates_and_relocalizes(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)

    assert window._status_label.text() == "等待对方说话中..."

    window.set_listen_status(True)

    assert window._status_label.text() == "正在听别人说话..."

    window.update_language("en")

    assert window._status_label.text() == "Reverse translation listening..."


def test_floating_window_has_close_button_icon(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)

    assert not window._close_button.icon().isNull()
    assert window._close_button.toolTip() == "关闭"


def test_floating_window_reuses_text_input_styles():
    assert build_floating_window_styles("light") == build_text_input_styles("light")


def test_floating_window_top_region_starts_drag(qtbot):
    class DummyEvent:
        def __init__(self, pos, global_pos):
            self._pos = pos
            self._global_pos = global_pos
            self.accepted = False

        def button(self):
            from PySide6.QtCore import Qt

            return Qt.MouseButton.LeftButton

        def position(self):
            return self._pos

        def globalPosition(self):
            return self._global_pos

        def accept(self):
            self.accepted = True

    from PySide6.QtCore import QPointF

    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    event = DummyEvent(QPointF(18, 18), QPointF(120, 140))
    window.mousePressEvent(event)

    assert event.accepted is True
    assert window._drag_position is not None


def test_floating_window_header_event_filter_starts_drag(qtbot):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(4, 4),
        QPointF(140, 160),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(window._opacity_label, event)

    assert event.isAccepted() is True
    assert window._drag_position is not None


def test_floating_window_resize_geometry_supports_all_edges(qtbot):
    window = FloatingWindow(None, "zh-CN")
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


def test_floating_window_wrap_uses_viewport_width(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)
    window.resize(900, 420)
    window.show()
    qtbot.waitExposed(window)

    wrap = window._bubble_wraplength()

    assert wrap > 520
    assert wrap <= window._scroll_area.viewport().width()
