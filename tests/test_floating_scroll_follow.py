"""The desktop caption window keeps up with the newest line.

Rows report their height a moment after they are added, so a scroll taken
right away landed one row short; the next message then found the view "not
at the bottom" and stopped following, and the player had to drag down by
hand. Following now tracks the scroll range, and only a reader who scrolls
up themselves is left alone.
"""

from __future__ import annotations

from src.ui_qt.floating_window import FloatingWindow


def _fill(window: FloatingWindow, count: int, prefix: str = "句") -> None:
    for i in range(count):
        window.show_translation(f"{prefix}{i} " + "内容" * 40, source="listen")


def test_the_view_follows_new_lines(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)
    window.resize(420, 240)
    window.show()
    qtbot.waitExposed(window)
    bar = window._scroll_area.verticalScrollBar()

    _fill(window, 12)
    qtbot.wait(120)

    assert bar.maximum() > 0
    assert bar.value() == bar.maximum()

    _fill(window, 3, prefix="又")
    qtbot.wait(120)

    assert bar.value() == bar.maximum()


def test_a_reader_who_scrolled_up_is_left_alone_until_they_return(qtbot):
    window = FloatingWindow(None, "zh-CN")
    qtbot.addWidget(window)
    window.resize(420, 240)
    window.show()
    qtbot.waitExposed(window)
    bar = window._scroll_area.verticalScrollBar()
    _fill(window, 12)
    qtbot.wait(120)

    # The reader drags to the top to re-read something.
    bar.setValue(0)
    _fill(window, 2, prefix="新")
    qtbot.wait(120)

    assert bar.value() == 0

    # Back at the bottom, the view follows again.
    bar.setValue(bar.maximum())
    _fill(window, 2, prefix="再")
    qtbot.wait(120)

    assert bar.value() == bar.maximum()
