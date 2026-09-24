"""The kept-translation panel's picture: buttons, views and pages."""

from __future__ import annotations

from PySide6.QtGui import QColor, QImage

from src.ui_qt.vr_result_panel import MAX_TEXT_PX, MIN_TEXT_PX, PANEL_SIZE, VRResultPanelView

TEXTS = {
    "text_view": "Text",
    "picture_view": "Picture",
    "show_original": "Original",
    "show_translation": "Translation",
    "pin": "Keep",
    "unpin": "Kept",
    "close": "Close",
}


def _image(color=QColor(200, 40, 40)):
    image = QImage(640, 320, QImage.Format.Format_RGBA8888)
    image.fill(color)
    return image


def _labels(view):
    view.render_image()
    return {button.action: button.label for button in view._buttons}


def _centre(view, action):
    view.render_image()
    rect = next(button.rect for button in view._buttons if button.action == action)
    return rect.center().x(), rect.center().y()


def test_a_read_with_a_picture_opens_on_the_picture(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="12:30", card=_image(), picture=_image(QColor(0, 0, 0)), pairs=[("a", "b")])

    image = view.render_image()

    assert (image.width(), image.height()) == PANEL_SIZE
    assert view.mode == "picture"
    labels = _labels(view)
    assert labels["view"] == "Text"
    assert labels["original"] == "Original"
    assert labels["pin"] == "Keep"
    assert labels["close"] == "Close"
    # Paging and font buttons belong to the text view.
    assert "font_up" not in labels and "page_next" not in labels


def test_every_button_is_found_where_it_is_drawn(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=_image(), picture=_image(), pairs=[("a", "b")])

    for action in ("view", "original", "pin", "close"):
        assert view.hit_test(*_centre(view, action)) == action
    assert view.hit_test(PANEL_SIZE[0] / 2, PANEL_SIZE[1] / 2) is None


def test_switching_to_text_shows_font_and_page_buttons(qapp):
    view = VRResultPanelView(TEXTS)
    long_pairs = [("original " * 8, "translated " * 12)] * 12
    view.set_result(title="t", card=_image(), picture=_image(), pairs=long_pairs)

    assert view.apply("view")

    assert view.mode == "text"
    labels = _labels(view)
    assert {"font_down", "font_up", "page_prev", "page_next"} <= set(labels)
    assert labels["view"] == "Picture"
    assert view.page_count > 1
    assert view.apply("page_next") and view.page == 1
    assert view.apply("page_prev") and view.page == 0
    assert not view.apply("page_prev")
    while view.apply("page_next"):
        pass
    assert view.page == view.page_count - 1


def test_a_read_without_a_picture_is_text_only(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=None, picture=None, pairs=[("a", "b")])

    assert view.mode == "text"
    assert "view" not in _labels(view)
    assert not view.apply("view")


def test_original_and_translation_swap(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=None, picture=None, pairs=[("hello", "你好")])
    assert view._text_rows() == ["你好"]

    assert view.apply("original")

    assert view.show_original
    assert view._text_rows() == ["hello"]
    assert _labels(view)["original"] == "Translation"


def test_the_font_grows_and_shrinks_within_limits(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=None, picture=None, pairs=[("a", "b")])

    for _ in range(40):
        view.apply("font_up")
    assert view.text_px == MAX_TEXT_PX
    for _ in range(40):
        view.apply("font_down")
    assert view.text_px == MIN_TEXT_PX


def test_pin_and_close_are_left_to_the_owner(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=None, picture=None, pairs=[("a", "b")])

    assert not view.apply("pin")
    assert not view.apply("close")
    view.set_pinned(True)
    assert view.pinned
    assert _labels(view)["pin"] == "Kept"


def test_the_tutorial_has_no_pin_and_no_original(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_tutorial("How it works", ["Hold both grips", "", "Hold still"])

    labels = _labels(view)

    assert view.tutorial
    assert "pin" not in labels and "original" not in labels and "view" not in labels
    assert "close" in labels
    assert view._text_rows() == ["Hold both grips", "Hold still"]


def test_the_cursor_and_dwell_ring_draw_without_error(qapp):
    view = VRResultPanelView(TEXTS)
    view.set_result(title="t", card=_image(), picture=_image(), pairs=[("a", "b")])

    image = view.render_image(hover="close", pressed="close", cursor=(500.0, 400.0), dwell=0.5)

    assert not image.isNull()


def _dashboard(state):
    from src.ui_qt.vr_dashboard_panel import VRDashboardPanel

    panel = VRDashboardPanel("en")
    panel.set_state(state)
    panel.render_image()
    return panel


def test_the_main_page_reaches_the_frame_switch_and_the_reads(qapp):
    panel = _dashboard({"page": "main", "frame_gesture": True, "reads": [{"id": 1, "label": "x"}], "note": "close the menu"})
    actions = {button.action: button for button in panel._buttons}

    assert actions["toggle_frame_gesture"].active
    assert "page:reads" in actions
    # Everything fits, even with a note on top.
    assert max(button.rect.bottom() for button in panel._buttons) < panel.size[1]


def test_the_reads_page_lists_reads_to_recall_and_pin(qapp):
    from src.ui_qt.vr_dashboard_panel import MAX_LISTED_READS

    reads = [{"id": i, "label": f"12:0{i} line", "pinned": i == 2, "open": i == 3} for i in range(1, 12)]
    panel = _dashboard({"page": "reads", "frame_gesture": False, "open_panels": 2, "reads": reads, "note": "n"})
    actions = {button.action: button for button in panel._buttons}

    assert {"page:main", "screenshot", "toggle_frame_gesture", "gather_panels", "close_panels", "tutorial"} <= set(actions)
    recalls = [action for action in actions if action.startswith("recall:")]
    assert len(recalls) == MAX_LISTED_READS
    assert actions["pin:2"].active and actions["pin:2"].label == "★"
    assert actions["recall:3"].active
    assert not actions["toggle_frame_gesture"].active
    assert max(button.rect.bottom() for button in panel._buttons) < panel.size[1]
    for action in ("recall:1", "pin:1", "page:main"):
        rect = actions[action].rect
        assert panel.hit_test(rect.center().x(), rect.center().y()) == action


def test_an_empty_reads_page_says_how_to_fill_it(qapp):
    panel = _dashboard({"page": "reads", "reads": []})

    texts = [text for _rect, text, *_ in panel._texts]

    assert any("Nothing read yet" in text for text in texts)
    assert not any(button.action.startswith("recall:") for button in panel._buttons)


def test_a_read_can_be_copied_or_sent_to_the_chatbox(qapp):
    view = VRResultPanelView({**TEXTS, "copy": "Copy", "chatbox": "Chatbox"})
    view.set_result(title="t", card=_image(), picture=_image(), pairs=[("a", "b")])

    labels = _labels(view)

    assert labels["copy"] == "Copy" and labels["chatbox"] == "Chatbox"
    for action in ("copy", "chatbox"):
        assert view.hit_test(*_centre(view, action)) == action
        assert not view.apply(action)

    view.set_tutorial("How", ["x"])
    assert "copy" not in _labels(view) and "chatbox" not in _labels(view)
