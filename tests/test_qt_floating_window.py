from PySide6.QtWidgets import QWidget

from src.ui_qt.floating_window import FloatingWindow


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
