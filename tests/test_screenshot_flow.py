"""The press-to-labels flow in the main window, with every surface faked.

Covers the selection state machine (press opens a frame, a drag reads a
region, a click or a second press reads the whole view, silence cancels) and
where a result lands: over the signs in the headset, on the hand panel, or
over the desktop.
"""

from __future__ import annotations

import types

import pytest

from src.core.in_place_layout import HeadAnchor, PlacedLine
from src.core.screenshot_translation import ScreenshotTranslation
from src.ui_qt.main_window import MainWindow


def _anchor() -> HeadAnchor:
    return HeadAnchor(
        pose=((1, 0, 0, 0), (0, 1, 0, 1.6), (0, 0, 1, 0)),
        eye_offset=(-0.034, 0.0, 0.0),
        tangents=(-1.28, 1.28, -1.28, 1.28),
    )


class _Translator:
    def __init__(self):
        self.runs: list = []

    def trigger(self, region=None):
        self.runs.append(region)
        return True


class _Frame:
    def __init__(self, shows=True):
        self.shows = shows
        self.shown = 0
        self.hidden = 0
        self.pushes = 0
        self.on_drag = None
        self.on_release = None
        self.on_hover = None

    def show(self, buffer, width, height, anchor, *, depth, fov_scale, button_held=False):
        self.shown += 1
        self.button_held = button_held
        return self.shows

    def push(self, buffer, width, height):
        self.pushes += 1

    def hide(self):
        self.hidden += 1

    def stop(self):
        pass


class _Sheet:
    def __init__(self, shows=True):
        self.shows = shows
        self.calls: list = []
        self.cards: list = []
        self.hidden = 0
        self.now = _anchor()

    def show(self, buffer, width, height, anchor, *, depth, fov_scale):
        self.calls.append((width, height, anchor, depth, fov_scale))
        return self.shows

    def show_card(self, buffer, width, height, anchor, centre_uv, width_meters, *, depth):
        self.cards.append((width, height, anchor, centre_uv, width_meters, depth))
        return self.shows

    def snapshot_head_anchor(self, eye=None):
        self.eye = eye
        return self.now

    def hide(self):
        self.hidden += 1


class _HandPanel:
    def __init__(self):
        self.panel = types.SimpleNamespace(
            lines=[],
            statuses=[],
            pictures=[],
            show_lines=lambda pairs: self.panel.lines.append(pairs),
            show_status=lambda text: self.panel.statuses.append(text),
            show_picture=lambda image: self.panel.pictures.append(image) or True,
        )
        self.shown = 0
        self.hidden = 0

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1


class _Desktop:
    def __init__(self):
        self.results: list = []
        self.cleared = 0

    def show_result(self, result, **_kwargs):
        self.results.append(result)
        return True

    def clear(self):
        self.cleared += 1


class _FakeImage:
    def __init__(self, width, height):
        self._w, self._h = width, height

    def width(self):
        return self._w

    def height(self):
        return self._h

    def isNull(self):
        return False


class _Panels:
    """The kept-panel manager: records what it was asked to open."""

    def __init__(self, opens=True):
        self.opens = opens
        self.opened: list = []

    @property
    def count(self):
        return len(self.opened) if self.opens else 0

    def open_result(self, **kwargs):
        self.opened.append(kwargs)
        return len(self.opened) if self.opens else None

    def entry_ids(self):
        return []


class _Timer:
    def __init__(self):
        self.started: list[int] = []
        self.active = False

    def start(self, ms):
        self.started.append(ms)
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


def _window(monkeypatch, *, vr=True, selection=True, placement="card"):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "vrc_listen": {
            "screenshot_translation": {
                "enabled": True,
                "selection_mode": selection,
                "placement": placement,
                "depth_meters": 2.5,
                "mirror_fov_scale": 1.1,
            }
        }
    }
    window._vr_overlay_backend = (
        types.SimpleNamespace(available=vr, dashboard_visible=lambda: False, _visible=False)
        if vr
        else None
    )
    window._pending_screenshot = None
    window._dashboard_wait_noted = False
    window._vr_input = None
    # The headset path defers the capture a few frames; run it at once here.
    monkeypatch.setattr("src.ui_qt.main_window.QTimer.singleShot", lambda ms, callback: callback())
    window._selection_active = False
    window._selection_last_push = 0.0
    window._selection_timer = _Timer()
    translator = _Translator()
    frame = _Frame()
    sheet = _Sheet()
    hand = _HandPanel()
    desktop = _Desktop()
    window._vr_selection_frame = frame
    window._vr_label_sheet = sheet
    window._vr_hand_panel = hand
    window._desktop_inplace_overlay = desktop
    window._vr_result_panels = None
    panels = _Panels()
    cues: list = []
    monkeypatch.setattr(window, "_ensure_result_panels", lambda: panels if vr else None)
    monkeypatch.setattr(window, "_vr_cue", lambda name, hand=None: cues.append(name))
    messages: list = []
    monkeypatch.setattr(window, "_ensure_screenshot_translator", lambda: translator)
    monkeypatch.setattr(window, "_ensure_vr_selection_frame", lambda: frame if vr else None)
    monkeypatch.setattr(window, "_ensure_vr_label_sheet", lambda: sheet if vr else None)
    monkeypatch.setattr(window, "_ensure_vr_hand_panel", lambda: hand if vr else None)
    monkeypatch.setattr(window, "_ensure_desktop_inplace_overlay", lambda: desktop)
    monkeypatch.setattr(window, "_expected_frame_size", lambda: (1600, 900))
    monkeypatch.setattr(window, "_set_bottom", lambda text, *a: messages.append(text))
    monkeypatch.setattr(window, "_t", lambda key, **kw: key)
    monkeypatch.setattr(
        window,
        "_schedule_screenshot_hide",
        lambda seconds=None: messages.append("<hide-later>" if seconds is None else f"<hide-{seconds}>"),
    )
    frame_renders: list = []
    monkeypatch.setattr(
        "src.ui_qt.in_place_painter.render_selection_frame_image",
        lambda size, region, hint="", box=None: (
            frame_renders.append((region, box)),
            _FakeImage(size[0], size[1]),
        )[1],
    )
    window._test_frame_renders = frame_renders
    monkeypatch.setattr(
        "src.ui_qt.in_place_painter.render_label_sheet_image",
        lambda lines, frame_size: _FakeImage(frame_size[0], frame_size[1]),
    )
    monkeypatch.setattr(
        "src.ui_qt.in_place_painter.render_toast_image",
        lambda size, lines: _FakeImage(size[0], size[1]),
    )
    monkeypatch.setattr(
        "src.ui_qt.in_place_painter.render_translation_card",
        lambda picture, lines, offset=(0, 0), **_kwargs: _FakeImage(picture.width(), picture.height()),
    )
    return window, types.SimpleNamespace(
        translator=translator,
        frame=frame,
        sheet=sheet,
        hand=hand,
        desktop=desktop,
        messages=messages,
        panels=panels,
        cues=cues,
    )


class TestSelectionFlow:
    def test_a_press_in_vr_opens_the_frame_instead_of_reading(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow.trigger_screenshot_translation(window)

        assert f.frame.shown == 1
        assert window._selection_active
        assert f.translator.runs == []
        assert window._selection_timer.started

    def test_a_second_press_reads_the_whole_view(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)

        MainWindow.trigger_screenshot_translation(window)

        assert not window._selection_active
        assert f.frame.hidden == 1
        assert f.translator.runs == [None]

    def test_a_drag_reads_the_region(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)

        MainWindow._on_selection_release(window, (0.2, 0.3), (0.6, 0.7))

        assert f.translator.runs == [(0.2, 0.3, 0.6, 0.7)]
        assert not window._selection_active

    def test_a_click_on_the_frame_reads_the_box_under_the_laser(self, monkeypatch):
        from src.core.in_place_layout import pointer_box

        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)

        MainWindow._on_selection_release(window, (0.5, 0.5), (0.505, 0.502))

        assert f.translator.runs == [pointer_box((0.505, 0.502))]

    def test_the_box_follows_the_laser_and_keeps_the_frame_alive(self, monkeypatch):
        from src.core.in_place_layout import pointer_box

        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)
        starts = len(window._selection_timer.started)
        # The frame opened with the box in the middle.
        assert window._test_frame_renders[-1] == (None, pointer_box((0.5, 0.5)))

        MainWindow._on_selection_hover(window, (0.2, 0.7))

        assert f.frame.pushes == 1
        assert window._test_frame_renders[-1] == (None, pointer_box((0.2, 0.7)))
        assert len(window._selection_timer.started) == starts + 1

    def test_dragging_redraws_the_frame_and_keeps_it_alive(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)
        starts = len(window._selection_timer.started)

        MainWindow._on_selection_drag(window, (0.1, 0.1), (0.4, 0.4))

        assert f.frame.pushes == 1
        assert len(window._selection_timer.started) == starts + 1

    def test_silence_cancels_the_frame(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow.trigger_screenshot_translation(window)

        MainWindow._cancel_selection(window)

        assert not window._selection_active
        assert f.frame.hidden == 1
        assert f.translator.runs == []

    def test_selection_off_reads_at_once(self, monkeypatch):
        window, f = _window(monkeypatch, selection=False)

        MainWindow.trigger_screenshot_translation(window)

        assert f.frame.shown == 0
        assert f.translator.runs == [None]

    def test_without_a_headset_a_press_reads_at_once(self, monkeypatch):
        window, f = _window(monkeypatch, vr=False)

        MainWindow.trigger_screenshot_translation(window)

        assert f.translator.runs == [None]

    def test_a_frame_that_will_not_show_falls_back_to_reading(self, monkeypatch):
        window, f = _window(monkeypatch)
        f.frame.shows = False

        MainWindow.trigger_screenshot_translation(window)

        assert f.translator.runs == [None]
        assert not window._selection_active

    def test_a_new_run_clears_the_previous_labels(self, monkeypatch):
        window, f = _window(monkeypatch, selection=False)

        MainWindow.trigger_screenshot_translation(window)

        assert f.sheet.hidden >= 1
        assert f.desktop.cleared >= 1


def _png(width: int, height: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(storage.data())


_FRAME_PNG = _png(1600, 900)


def _result(**overrides):
    fields = dict(
        pairs=[("こんにちは", "你好")],
        lines=[PlacedLine("こんにちは", "你好", 10, 20, 100, 30)],
        source="vr_eye",
        frame_size=(1600, 900),
        anchor=_anchor(),
        # The whole frame was framed deliberately, so the card shows all of it.
        region=(0.0, 0.0, 1.0, 1.0),
        png=_FRAME_PNG,
        offset=(0, 0),
        crop_size=(1600, 900),
    )
    fields.update(overrides)
    return ScreenshotTranslation(**fields)


class TestResultRouting:
    def test_the_default_is_a_card_hung_where_the_text_was(self, monkeypatch):
        window, f = _window(monkeypatch)
        result = _result()

        MainWindow._on_screenshot_result(window, result)

        assert len(f.sheet.cards) == 1
        width, height, anchor, centre, width_m, depth = f.sheet.cards[0]
        assert (width, height) == (1600, 900)
        assert anchor is result.anchor
        assert centre == (0.5, 0.5)
        # Life size would be the whole view; the card stops short of that.
        assert width_m == pytest.approx(2.56 * 2.5 * 0.85)
        assert depth == 2.5
        assert f.sheet.calls == []
        # No second box: the hand panel is never brought in for the card.
        assert f.hand.shown == 0
        assert f.hand.panel.lines == []
        assert "<hide-later>" in f.messages

    def test_a_whole_view_read_is_trimmed_to_the_text(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(region=None))

        width, height, _anchor, centre, width_m, _depth = f.sheet.cards[0]
        # The one line at (10, 20, 100x30) plus a margin of 0.8 lines + 12 px.
        assert (width, height) == (146, 86)
        assert centre == pytest.approx((73 / 1600, 43 / 900))
        assert width_m == pytest.approx(max(0.3, 2.56 * 2.5 * (146 / 1600) * 1.25))

    def test_any_button_puts_a_shown_card_away(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow._on_screenshot_result(window, _result())
        assert window._screenshot_result_visible

        MainWindow._dismiss_screenshot_result_on_press(window)

        assert f.sheet.hidden == 1
        assert f.hand.hidden == 1
        assert not window._screenshot_result_visible

    def test_a_press_while_the_frame_is_open_is_a_selection_not_a_dismissal(self, monkeypatch):
        window, f = _window(monkeypatch)
        MainWindow._on_screenshot_result(window, _result())
        MainWindow.trigger_screenshot_translation(window)
        hidden_before = f.sheet.hidden

        MainWindow._dismiss_screenshot_result_on_press(window)

        assert f.sheet.hidden == hidden_before

    def test_a_press_with_nothing_shown_does_nothing(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._dismiss_screenshot_result_on_press(window)

        assert f.sheet.hidden == 0 and f.hand.hidden == 0

    def test_a_card_without_a_pose_uses_where_the_head_is_now(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(anchor=None))

        assert f.sheet.cards[0][2] is f.sheet.now

    def test_the_old_in_place_value_now_means_the_panel(self, monkeypatch):
        window, f = _window(monkeypatch, placement="in_place")

        MainWindow._on_screenshot_result(window, _result())

        assert len(f.panels.opened) == 1
        assert f.sheet.cards == [] and f.sheet.calls == []

    def test_the_panel_hangs_where_the_text_was_until_it_is_closed(self, monkeypatch):
        import math

        window, f = _window(monkeypatch, placement="panel")

        MainWindow._on_screenshot_result(window, _result())

        assert len(f.panels.opened) == 1
        opened = f.panels.opened[0]
        rows = opened["rows"]
        # Along the direction the text was seen in, no further than arm's
        # length plus a little (the configured 2.5 m is for the card).
        assert (rows[0][3], rows[1][3], rows[2][3]) == pytest.approx((-0.034, 1.6, -1.0))
        # Upright and facing the head.
        facing = (rows[0][2], rows[1][2], rows[2][2])
        to_head = (0.034, 0.0, 1.0)
        length = math.sqrt(sum(c * c for c in to_head))
        assert facing == pytest.approx(tuple(c / length for c in to_head))
        assert rows[1][0] == pytest.approx(0.0)
        assert opened["pairs"] == [("こんにちは", "你好")]
        # No countdown and no card: it stays until closed.
        assert "<hide-later>" not in f.messages
        assert not getattr(window, "_screenshot_result_visible", False)
        assert f.sheet.cards == []
        assert f.cues == ["result"]

    def test_every_read_is_kept_for_the_wrist_panel(self, monkeypatch):
        window, f = _window(monkeypatch, placement="card")

        MainWindow._on_screenshot_result(window, _result())
        MainWindow._on_screenshot_result(window, _result(pairs=[("A", "甲")]))

        history = window._read_history
        assert [entry.pairs for entry in history.recent()] == [[("A", "甲")], [("こんにちは", "你好")]]
        assert history.recent()[0].card is not None

    def test_a_panel_that_will_not_open_falls_back_to_the_card(self, monkeypatch):
        window, f = _window(monkeypatch, placement="panel")
        f.panels.opens = False

        MainWindow._on_screenshot_result(window, _result())

        assert len(f.sheet.cards) == 1

    def test_hand_placement_puts_the_card_on_the_hand(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")

        MainWindow._on_screenshot_result(window, _result())

        assert f.sheet.calls == [] and f.sheet.cards == []
        assert len(f.hand.panel.pictures) == 1
        assert f.hand.panel.lines == []
        assert f.hand.shown == 1

    def test_a_desktop_capture_in_vr_is_a_card_too(self, monkeypatch):
        """A screen grab does not map onto the headset view, but a card of
        it with the translations on it is still worth having."""

        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(source="screen"))

        assert len(f.sheet.cards) == 1
        assert f.sheet.calls == []
        assert f.hand.shown == 0
        assert f.hand.panel.lines == []

    def test_without_a_headset_the_labels_go_over_the_desktop(self, monkeypatch):
        window, f = _window(monkeypatch, vr=False)
        result = _result(anchor=None)

        MainWindow._on_screenshot_result(window, result)

        assert f.desktop.results == [result]
        assert f.sheet.calls == []

    def test_a_result_without_geometry_is_listed_on_the_sheet(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(lines=[], frame_size=(0, 0)))

        assert len(f.sheet.calls) == 1
        assert f.hand.panel.lines == []

    def test_hand_placement_still_routes_everything_to_the_hand(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")

        MainWindow._on_screenshot_status(window, "reading")
        MainWindow._on_screenshot_result(window, _result(lines=[], frame_size=(0, 0)))

        assert f.hand.panel.statuses == ["screenshot_status_reading"]
        assert f.hand.panel.lines == [[("こんにちは", "你好")]]
        assert f.sheet.calls == []

    def test_progress_is_a_toast_on_the_sheet_not_a_panel(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_status(window, "reading")

        assert len(f.sheet.calls) == 1
        assert f.hand.shown == 0

    def test_an_error_is_a_brief_toast_on_the_sheet(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(pairs=[], lines=[], error="no_text_found"))

        assert len(f.sheet.calls) == 1
        assert f.hand.panel.statuses == []
        assert f.desktop.results == []
        assert "screenshot_error_no_text_found" in f.messages
        # Errors are brief: read once, then gone.
        assert "<hide-1.0>" in f.messages

    def test_a_translation_failure_is_said_in_plain_words(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(
            window, _result(pairs=[], lines=[], error="translation_failed", error_kind="auth")
        )

        assert len(f.sheet.calls) == 1
        assert "screenshot_error_translation_auth" in f.messages

    def test_a_partial_failure_shows_lines_and_notes_the_reason_once(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._on_screenshot_result(window, _result(error_kind="network"))

        assert len(f.sheet.cards) == 1
        assert f.messages.count("screenshot_error_translation_network") == 1

    def test_a_sheet_that_will_not_show_does_not_conjure_a_hand_panel(self, monkeypatch):
        window, f = _window(monkeypatch)
        f.sheet.shows = False

        MainWindow._on_screenshot_result(window, _result())

        assert f.hand.shown == 0
        assert f.desktop.results == []
        # The status bar still carries the outcome.
        assert any("screenshot_done" in m for m in f.messages)


class TestHideAll:
    def test_hiding_clears_every_surface(self, monkeypatch):
        window, f = _window(monkeypatch)

        MainWindow._hide_screenshot_panel(window)

        assert f.hand.hidden == 1
        assert f.sheet.hidden == 1
        assert f.desktop.cleared == 1


class TestQueuedBox:
    """A box drawn while the previous read is still running is not lost.

    It used to be dropped without a word, and the earlier read's picture then
    came up for a box the player never drew - "it shows the last picture".
    """

    def test_a_box_drawn_during_a_read_waits_and_the_stale_result_is_set_aside(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")
        busy = {"value": True}
        f.translator.trigger = lambda region=None: (f.translator.runs.append(region), not busy["value"])[1]

        MainWindow._run_screenshot_translation(window, (0.2, 0.3, 0.6, 0.7))

        assert f.translator.runs == [(0.2, 0.3, 0.6, 0.7)]
        assert window._queued_screenshot_region == ((0.2, 0.3, 0.6, 0.7),)

        busy["value"] = False
        MainWindow._on_screenshot_result(window, _result())

        # The stale picture never reached the hand; the queued box was read.
        assert f.hand.panel.pictures == []
        assert f.translator.runs[-1] == (0.2, 0.3, 0.6, 0.7)
        assert window._queued_screenshot_region is None

        MainWindow._on_screenshot_result(window, _result())
        assert len(f.hand.panel.pictures) == 1

    def test_a_read_that_starts_at_once_queues_nothing(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")

        MainWindow._run_screenshot_translation(window, (0.2, 0.3, 0.6, 0.7))

        assert window._queued_screenshot_region is None


class TestCaptureHidesOurOverlays:
    """The compositor's picture includes every Mio overlay.

    The hand panel still showed the previous read's card and sat right under
    the sign the player boxed, so the card itself was read: every read came
    out as "the previous picture". The hand panel and the wrist panel go
    away before the picture is taken.
    """

    def test_the_hand_panel_and_the_wrist_panel_leave_the_picture(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")
        events: list = []
        window._vr_wrist_panel = types.SimpleNamespace(
            suspend=lambda: events.append("suspend"), resume=lambda: events.append("resume")
        )
        f.translator.trigger = lambda region=None: (events.append(("trigger", region)), True)[1]

        MainWindow._run_screenshot_translation(window, (0.2, 0.3, 0.6, 0.7))

        assert f.hand.hidden == 1
        # Suspended before the capture inside the trigger, resumed straight after.
        assert events == ["suspend", ("trigger", (0.2, 0.3, 0.6, 0.7)), "resume"]

    def test_the_wrist_panel_comes_back_even_when_the_read_is_queued(self, monkeypatch):
        window, f = _window(monkeypatch, placement="hand")
        events: list = []
        window._vr_wrist_panel = types.SimpleNamespace(
            suspend=lambda: events.append("suspend"), resume=lambda: events.append("resume")
        )
        f.translator.trigger = lambda region=None: False

        MainWindow._run_screenshot_translation(window, (0.2, 0.3, 0.6, 0.7))

        assert events == ["suspend", "resume"]
        assert window._queued_screenshot_region == ((0.2, 0.3, 0.6, 0.7),)
