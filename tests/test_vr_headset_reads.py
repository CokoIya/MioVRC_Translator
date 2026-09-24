"""The headset side of the main window, VRHandsFrame style.

The two-hand frame reads what it covers into itself, a trigger pull keeps a
read as a panel, every read is remembered for the wrist panel, overlays are
taken down for a capture, push-to-talk owns the mic, and the tutorial shows
once. Every surface is faked.
"""

from __future__ import annotations

import types

import pytest

from src.core.in_place_layout import HeadAnchor, PlacedLine
from src.core.screenshot_translation import ScreenshotTranslation
from src.core.steamvr_frame import Tracking
from src.core.vr_frame_gesture import ARM_SECONDS, STILL_SECONDS
from src.core.vr_input import HandButtons
from src.ui_qt import main_window as main_window_module
from src.ui_qt.main_window import MainWindow

ANCHOR = HeadAnchor(
    pose=((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 1.6), (0.0, 0.0, 1.0, 0.0)),
    eye_offset=(0.0, 0.0, 0.0),
    tangents=(-1.0, 1.0, -1.0, 1.0),
)
HEAD_ROWS = [list(row) for row in ANCHOR.pose]
REGION = (0.25, 0.375, 0.75, 0.625)


def _rows(x, y, z):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z]]


def _tracking():
    return Tracking(anchor=ANCHOR, head=HEAD_ROWS, left=_rows(-0.2, 1.7, -0.4), right=_rows(0.2, 1.5, -0.4))


class _Input:
    def __init__(self):
        self.buttons = {"left": HandButtons(trigger=False, grip=True), "right": HandButtons(trigger=False, grip=True)}
        self.push_to_talk = None
        self.pulses: list = []

    def hand(self, hand):
        return self.buttons[hand]

    def pointer_ray(self, hand):
        return None

    def pulse(self, hand, **kwargs):
        self.pulses.append(hand)
        return True


class _HandFrame:
    def __init__(self):
        self.placed: list = []
        self.visible = False
        self.paused = False
        self.resumed = 0

    def place(self, anchor, region, depth, painter, *, key, now=None):
        self.placed.append((tuple(region), depth, key))
        self.visible = True
        return True

    def hide(self):
        self.visible = False

    def pause(self):
        self.paused = True
        self.visible = False

    def resume(self):
        self.paused = False
        self.resumed += 1

    def stop(self):
        self.visible = False


class _Panels:
    def __init__(self):
        self.opened: list = []
        self.tutorials: list = []
        self.gathered: list = []
        self.closed_all = 0
        self.pins: list = []
        self.paused = False
        self.grabbing = False

    @property
    def count(self):
        return len(self.opened)

    def open_result(self, **kwargs):
        self.opened.append(kwargs)
        return len(self.opened)

    def open_tutorial(self, title, lines, rows, width_m=0.5):
        self.tutorials.append((title, lines))
        return 1

    def entry_ids(self):
        return [item["entry_id"] for item in self.opened]

    def gather(self, head):
        self.gathered.append(head)
        return len(self.opened)

    def close_all(self):
        self.closed_all += 1
        return 0

    def set_pinned(self, entry_id, pinned):
        self.pins.append((entry_id, pinned))

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


class _Translator:
    running = False


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def window(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(main_window_module.time, "monotonic", clock)
    win = MainWindow.__new__(MainWindow)
    win._config = {
        "vrc_listen": {
            "screenshot_translation": {"enabled": True, "frame_gesture": True, "auto_translate": True},
            "vr_wrist": {"enabled": True, "show_mode": "twist"},
        },
        "audio": {},
        "ui": {},
    }
    win._vr_overlay_backend = types.SimpleNamespace(available=True, openvr_module=None, _visible=False)
    win._selection_active = False
    win._pending_screenshot = None
    win._vr_input = None
    frame = _HandFrame()
    panels = _Panels()
    cues: list = []
    reads: list = []
    bottom: list = []
    cards: list = []
    monkeypatch.setattr(win, "_ensure_hand_frame", lambda: frame)
    win._vr_hand_frame = frame
    monkeypatch.setattr(win, "_ensure_result_panels", lambda: panels)
    win._vr_result_panels = None
    monkeypatch.setattr(win, "_vr_cue", lambda name, hand=None: cues.append(name))
    monkeypatch.setattr(win, "_ensure_screenshot_translator", lambda: _Translator())
    monkeypatch.setattr(win, "_run_screenshot_translation", lambda region: reads.append(region))
    monkeypatch.setattr(win, "_t", lambda key, **kw: key)
    monkeypatch.setattr(win, "_set_bottom", lambda text, *a: bottom.append(text))
    monkeypatch.setattr(win, "_show_card_briefly", lambda result: cards.append(result) or True)
    monkeypatch.setattr(win, "_flash_vr_toast", lambda *a, **k: True)
    monkeypatch.setattr(win, "_schedule_config_save", lambda: None)
    monkeypatch.setattr(win, "_refresh_vr_dashboard", lambda: None)
    monkeypatch.setattr(win, "_sync_settings_window_vr_overlay_controls", lambda: None)
    win._test = types.SimpleNamespace(
        clock=clock, frame=frame, panels=panels, cues=cues, reads=reads, bottom=bottom, cards=cards
    )
    return win


def _png(width, height):
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


def _result(region=REGION, **overrides):
    fields = dict(
        pairs=[("出口", "Exit")],
        lines=[PlacedLine("出口", "Exit", 420, 350, 200, 40)],
        source="vr_eye",
        frame_size=(1600, 900),
        anchor=ANCHOR,
        region=region,
        png=_png(800, 225),
        offset=(400, 337),
        crop_size=(800, 225),
    )
    fields.update(overrides)
    return ScreenshotTranslation(**fields)


def _read_result(win, **overrides):
    """The result of the read the frame asked for (its region, exactly)."""

    return _result(region=tuple(win._test.reads[-1]), **overrides)


def _tick(win, inp, seconds):
    win._test.clock.now += seconds
    MainWindow._poll_frame_gesture(win, inp, _tracking(), None)


def _frame_open(win, inp):
    _tick(win, inp, 0.0)
    _tick(win, inp, ARM_SECONDS + 0.01)
    assert win._frame_tracker.active


class TestFrameGesture:
    def test_framing_opens_the_frame_with_a_cue(self, window):
        inp = _Input()

        _frame_open(window, inp)

        assert window._test.cues == ["frame_ready"]
        region, depth, key = window._test.frame.placed[-1]
        assert region == pytest.approx(REGION)
        assert depth == pytest.approx(0.4)
        assert key[0].startswith("ready:")

    def test_holding_still_reads_what_the_frame_covers(self, window, qapp):
        inp = _Input()
        _frame_open(window, inp)

        _tick(window, inp, STILL_SECONDS + 0.05)

        assert window._test.reads == [pytest.approx(REGION)]
        assert window._frame_read["keep"] is False
        assert "reading" in window._test.cues
        _tick(window, inp, 0.05)
        assert window._test.frame.placed[-1][2][0].startswith("reading:")

    def test_the_translation_is_drawn_into_the_frame(self, window, qapp):
        inp = _Input()
        _frame_open(window, inp)
        _tick(window, inp, STILL_SECONDS + 0.05)

        MainWindow._on_screenshot_result(window, _read_result(window))

        picture, rect = window._frame_picture
        # The picture sat at (400, 337)-(1200, 562) of a 1600x900 view; the
        # frame covers (400, 337.5)-(1200, 562.5).
        assert rect == pytest.approx((0.0, -0.0022, 1.0, 0.9978), abs=1e-3)
        assert window._frame_read is None
        _tick(window, inp, 0.05)
        assert window._test.frame.placed[-1][2][0].startswith("shown:1")
        assert "result" in window._test.cues
        # Remembered for the wrist panel, not yet a panel.
        assert len(window._read_history) == 1
        assert window._test.panels.opened == []

    def test_a_trigger_pull_on_a_shown_read_keeps_it_as_a_panel(self, window, qapp):
        inp = _Input()
        _frame_open(window, inp)
        _tick(window, inp, STILL_SECONDS + 0.05)
        MainWindow._on_screenshot_result(window, _read_result(window))

        inp.buttons["right"] = HandButtons(trigger=True, grip=True)
        _tick(window, inp, 0.05)

        opened = window._test.panels.opened
        assert len(opened) == 1
        assert opened[0]["pairs"] == [("出口", "Exit")]
        assert "capture" in window._test.cues
        assert not window._frame_tracker.active
        assert not window._test.frame.visible
        # Still holding the grips: the frame stays away until they open.
        _tick(window, inp, ARM_SECONDS + 0.5)
        assert not window._frame_tracker.active

    def test_a_trigger_pull_before_the_read_keeps_what_it_finds(self, window, qapp):
        inp = _Input()
        _frame_open(window, inp)

        inp.buttons["left"] = HandButtons(trigger=True, grip=True)
        _tick(window, inp, 0.05)
        assert window._test.reads == [pytest.approx(REGION)]
        assert window._frame_read["keep"] is True

        MainWindow._on_screenshot_result(window, _read_result(window))

        assert len(window._test.panels.opened) == 1
        assert not window._frame_tracker.active

    def test_a_read_that_finds_nothing_says_so_in_the_frame(self, window):
        inp = _Input()
        _frame_open(window, inp)
        _tick(window, inp, STILL_SECONDS + 0.05)

        MainWindow._on_screenshot_result(window, _read_result(window, pairs=[], lines=[], error="no_text_found"))

        assert "error" in window._test.cues
        text, _until = window._frame_hint_override
        assert text == "screenshot_error_no_text_found"
        _tick(window, inp, 0.05)
        assert window._test.frame.placed[-1][2][0].endswith("override")
        # It waits for a move instead of reading the same empty spot again.
        _tick(window, inp, STILL_SECONDS * 3)
        assert len(window._test.reads) == 1

    def test_letting_go_during_a_read_shows_it_briefly(self, window, qapp):
        inp = _Input()
        _frame_open(window, inp)
        _tick(window, inp, STILL_SECONDS + 0.05)
        inp.buttons = {"left": HandButtons(False, False), "right": HandButtons(False, False)}
        _tick(window, inp, 0.05)
        _tick(window, inp, 1.0)
        assert not window._frame_tracker.active

        MainWindow._on_screenshot_result(window, _read_result(window))

        assert len(window._test.cards) == 1
        assert window._test.panels.opened == []
        assert len(window._read_history) == 1

    def test_the_frame_steps_aside_for_the_selection_frame_and_the_menu(self, window):
        inp = _Input()
        _frame_open(window, inp)

        window._selection_active = True
        _tick(window, inp, 0.05)
        assert not window._frame_tracker.active

        window._selection_active = False
        _frame_open(window, inp)
        MainWindow._poll_frame_gesture(window, inp, _tracking(), types.SimpleNamespace(visible=True))
        assert not window._frame_tracker.active

    def test_switching_the_gesture_off_puts_the_frame_away(self, window):
        inp = _Input()
        _frame_open(window, inp)

        MainWindow._set_frame_gesture_enabled(window, False)

        assert not window._frame_tracker.active
        assert not window._test.frame.visible
        _tick(window, inp, 1.0)
        assert not window._frame_tracker.active

    def test_the_wrist_switch_turns_screenshot_reads_on_too(self, window):
        window._config["vrc_listen"]["screenshot_translation"] = {"enabled": False, "frame_gesture": False}

        MainWindow._toggle_frame_gesture_from_vr(window)

        shot = window._config["vrc_listen"]["screenshot_translation"]
        assert shot["enabled"] is True and shot["frame_gesture"] is True
        assert "toggle" in window._test.cues


class TestCapture:
    def test_the_frame_and_panels_leave_the_picture_and_come_back(self, window):
        window._vr_result_panels = window._test.panels
        window._vr_hand_frame.visible = True

        MainWindow._prepare_vr_capture(window)

        assert window._test.frame.paused and not window._test.frame.visible
        assert window._test.panels.paused

        MainWindow._resume_vr_overlays_after_capture(window)

        assert not window._test.frame.paused and window._test.frame.resumed == 1
        assert not window._test.panels.paused


class TestKeptReads:
    def _remember(self, window, pairs):
        return MainWindow._remember_read(window, types.SimpleNamespace(pairs=pairs), None)

    def test_the_wrist_panel_lists_reads_pinned_first(self, window):
        first = self._remember(window, [("a", "甲")])
        self._remember(window, [("b", "乙")])
        window._read_history.set_pinned(first.entry_id, True)

        reads = MainWindow._vr_dashboard_reads(window)

        assert [item["id"] for item in reads] == [first.entry_id, first.entry_id + 1]
        assert reads[0]["pinned"] and reads[0]["label"].endswith("甲")

    def test_a_read_picked_on_the_wrist_comes_back_in_front(self, window, monkeypatch):
        entry = self._remember(window, [("a", "甲")])
        monkeypatch.setattr(window, "_current_head_pose", lambda: HEAD_ROWS)

        MainWindow._on_vr_dashboard_action(window, f"recall:{entry.entry_id}")

        opened = window._test.panels.opened[0]
        assert opened["entry_id"] == entry.entry_id
        assert opened["rows"][2][3] < 0  # in front of the head
        assert "result" in window._test.cues

    def test_a_star_on_the_wrist_pins_the_read_and_its_panel(self, window):
        entry = self._remember(window, [("a", "甲")])
        window._vr_result_panels = window._test.panels

        MainWindow._on_vr_dashboard_action(window, f"pin:{entry.entry_id}")

        assert window._read_history.get(entry.entry_id).pinned
        assert window._test.panels.pins == [(entry.entry_id, True)]

    def test_gather_close_and_pages(self, window, monkeypatch):
        window._vr_result_panels = window._test.panels
        monkeypatch.setattr(window, "_current_head_pose", lambda: HEAD_ROWS)

        MainWindow._on_vr_dashboard_action(window, "gather_panels")
        MainWindow._on_vr_dashboard_action(window, "close_panels")
        MainWindow._on_vr_dashboard_action(window, "page:reads")

        assert window._test.panels.gathered == [HEAD_ROWS]
        assert window._test.panels.closed_all == 1
        assert window._vr_dash_page == "reads"
        MainWindow._on_vr_dashboard_action(window, "page:main")
        assert window._vr_dash_page == "main"

    def test_a_malformed_action_is_ignored(self, window):
        MainWindow._on_vr_dashboard_action(window, "recall:nope")

        assert window._test.panels.opened == []


class TestTutorial:
    def _backend(self, level=1):
        system = types.SimpleNamespace(getTrackedDeviceActivityLevel=lambda index: level)
        module = types.SimpleNamespace(
            VRSystem=lambda: system,
            k_unTrackedDeviceIndex_Hmd=0,
            k_EDeviceActivityLevel_UserInteraction=1,
        )
        return types.SimpleNamespace(available=True, openvr_module=module)

    def test_it_shows_once_when_the_headset_is_on(self, window):
        MainWindow._maybe_show_vr_tutorial(window, self._backend(), _tracking())
        MainWindow._maybe_show_vr_tutorial(window, self._backend(), _tracking())

        assert len(window._test.panels.tutorials) == 1
        title, lines = window._test.panels.tutorials[0]
        assert title == "vr_tutorial_title"
        assert lines[0] == "vr_tutorial_frame"
        assert "vr_tutorial_wrist_twist" in lines
        assert window._config["ui"]["vr_tutorial_seen"] is True

    def test_it_waits_while_the_headset_sits_on_the_desk(self, window):
        MainWindow._maybe_show_vr_tutorial(window, self._backend(level=0), _tracking())

        assert window._test.panels.tutorials == []
        assert not getattr(window, "_vr_tutorial_checked", False)

    def test_seen_means_never_again(self, window):
        window._config["ui"]["vr_tutorial_seen"] = True

        MainWindow._maybe_show_vr_tutorial(window, self._backend(), _tracking())

        assert window._test.panels.tutorials == []

    def test_it_tells_how_to_switch_the_frame_on_when_it_is_off(self, window):
        window._config["vrc_listen"]["screenshot_translation"]["frame_gesture"] = False
        window._config["vrc_listen"]["vr_wrist"]["show_mode"] = "look"

        lines = MainWindow._vr_tutorial_lines(window)

        assert "vr_tutorial_frame" not in lines
        assert "vr_tutorial_enable_frame" in lines
        assert "vr_tutorial_wrist_look" in lines


class TestPushToTalk:
    @pytest.fixture
    def talk(self, window, monkeypatch):
        window._config["audio"]["push_to_talk"] = True
        window._mic_muted = False
        changes: list = []

        def set_muted(muted, **_kwargs):
            changes.append(muted)
            window._mic_muted = muted

        monkeypatch.setattr(window, "_set_mic_muted", set_muted)
        return window, changes

    def test_the_mic_is_open_only_while_the_button_is_held(self, talk):
        window, changes = talk
        inp = _Input()

        inp.push_to_talk = False
        MainWindow._poll_push_to_talk(window, inp)
        inp.push_to_talk = True
        MainWindow._poll_push_to_talk(window, inp)
        MainWindow._poll_push_to_talk(window, inp)
        inp.push_to_talk = False
        MainWindow._poll_push_to_talk(window, inp)

        assert changes == [True, False, True]
        assert window._test.cues == ["talk_on", "talk_off"]

    def test_an_unbound_button_leaves_the_mic_alone(self, talk):
        window, changes = talk

        MainWindow._poll_push_to_talk(window, _Input())

        assert changes == []
        assert not MainWindow._push_to_talk_owns_mute(window)

    def test_vrchat_mute_does_not_fight_the_button(self, talk, monkeypatch):
        window, changes = talk
        window._config["osc"] = {"sync_mute_self": True}
        inp = _Input()
        inp.push_to_talk = True
        MainWindow._poll_push_to_talk(window, inp)

        MainWindow._handle_vrchat_mute_self(window, True)

        assert window._mic_muted is False

    def test_switching_it_off_gives_the_mic_back(self, talk):
        window, changes = talk
        inp = _Input()
        inp.push_to_talk = False
        MainWindow._poll_push_to_talk(window, inp)
        assert window._mic_muted

        window._config["audio"]["push_to_talk"] = False
        MainWindow._poll_push_to_talk(window, inp)

        assert window._mic_muted is False
