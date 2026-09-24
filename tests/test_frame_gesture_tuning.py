"""Gesture sensitivity, the frame's delays, quick mode, and the main window's
controller settings, blocking and panel sharing."""

from __future__ import annotations

import types

import pytest

from src.core.in_place_layout import HeadAnchor
from src.core.vr_frame_gesture import (
    DEFAULT_TUNING,
    QUICK_REGION_SIZE,
    FrameGestureTracker,
    FrameInputs,
    region_of,
    tuning_for_sensitivity,
)
from src.core.vr_history import ReadHistory
from src.ui_qt import main_window as main_window_module
from src.ui_qt.main_window import MainWindow

ANCHOR = HeadAnchor(
    pose=((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0)),
    eye_offset=(0.0, 0.0, 0.0),
    tangents=(-1.0, 1.0, -1.0, 1.0),
)
# A narrow diagonal: 0.06 m across at 0.4 m is 0.075 of the view wide.
NARROW = ((-0.03, 0.06, -0.4), (0.03, -0.06, -0.4))
SIDE_BY_SIDE = ((-0.15, 0.0, -0.4), (0.15, 0.0, -0.4))


def _inputs(now, hands, *, grips=True, triggers=False):
    left, right = hands
    return FrameInputs(
        anchor=ANCHOR,
        left=left,
        right=right,
        left_grip=grips,
        right_grip=grips,
        left_trigger=triggers,
        right_trigger=triggers,
        now=now,
    )


class TestSensitivity:
    def test_the_middle_is_the_default(self):
        tuning = tuning_for_sensitivity(0.5)

        assert tuning.min_hand_distance == pytest.approx(DEFAULT_TUNING.min_hand_distance)
        assert tuning.min_region_width == pytest.approx(DEFAULT_TUNING.min_region_width)
        assert tuning.view_slack == pytest.approx(DEFAULT_TUNING.view_slack)

    def test_lenient_takes_a_frame_strict_refuses(self):
        # Hands only 0.11 m apart: too close at the default, fine when lenient.
        hands = ((-0.04, 0.03, -0.4), (0.05, -0.04, -0.4))

        assert region_of(ANCHOR, *hands, tuning_for_sensitivity(1.0)) is not None
        assert region_of(ANCHOR, *hands, tuning_for_sensitivity(0.0)) is None

    def test_the_tracker_follows_its_sensitivity(self):
        tracker = FrameGestureTracker(sensitivity=0.0)
        hands = ((-0.04, 0.03, -0.4), (0.05, -0.04, -0.4))

        tracker.update(_inputs(0.0, hands))
        assert not tracker.update(_inputs(1.0, hands)).active

        tracker.sensitivity = 1.0
        tracker.update(_inputs(2.0, hands))
        assert tracker.update(_inputs(3.0, hands)).active


class TestDelays:
    def test_the_frame_waits_as_long_as_asked(self):
        tracker = FrameGestureTracker(arm_seconds=0.8)
        hands = ((-0.2, 0.1, -0.4), (0.2, -0.1, -0.4))

        tracker.update(_inputs(0.0, hands))
        assert tracker.arming
        assert not tracker.update(_inputs(0.5, hands)).active
        assert tracker.update(_inputs(0.81, hands)).active
        assert not tracker.arming

    def test_it_survives_a_longer_flicker_when_asked(self):
        tracker = FrameGestureTracker(release_seconds=0.8)
        hands = ((-0.2, 0.1, -0.4), (0.2, -0.1, -0.4))
        tracker.update(_inputs(0.0, hands))
        tracker.update(_inputs(0.5, hands))

        tracker.update(_inputs(1.0, hands, grips=False))
        assert tracker.update(_inputs(1.5, hands, grips=False)).active
        assert not tracker.update(_inputs(1.81, hands, grips=False)).active


class TestQuickMode:
    def test_side_by_side_hands_are_no_frame_without_it(self):
        tracker = FrameGestureTracker(quick=False)

        tracker.update(_inputs(0.0, SIDE_BY_SIDE, triggers=True))

        assert not tracker.update(_inputs(1.0, SIDE_BY_SIDE, triggers=True)).active

    def test_all_four_buttons_open_a_padded_frame(self):
        tracker = FrameGestureTracker(quick=True)

        tracker.update(_inputs(0.0, SIDE_BY_SIDE, triggers=True))
        state = tracker.update(_inputs(0.5, SIDE_BY_SIDE, triggers=True))

        assert state.active
        u0, v0, u1, v1 = state.region
        assert v1 - v0 == pytest.approx(QUICK_REGION_SIZE[1])
        assert u1 - u0 >= QUICK_REGION_SIZE[0]
        # The held triggers were what opened it, not a capture.
        assert "capture" not in state.events

    def test_once_open_the_grips_keep_it_and_a_new_pull_keeps_the_read(self):
        tracker = FrameGestureTracker(quick=True)
        tracker.update(_inputs(0.0, SIDE_BY_SIDE, triggers=True))
        tracker.update(_inputs(0.5, SIDE_BY_SIDE, triggers=True))

        held = tracker.update(_inputs(0.6, SIDE_BY_SIDE, triggers=False))
        assert held.active
        pulled = tracker.update(_inputs(0.7, SIDE_BY_SIDE, triggers=True))
        assert "capture" in pulled.events

    def test_grips_alone_still_need_the_pose(self):
        tracker = FrameGestureTracker(quick=True)

        tracker.update(_inputs(0.0, SIDE_BY_SIDE, triggers=False))

        assert not tracker.update(_inputs(1.0, SIDE_BY_SIDE, triggers=False)).active

    def test_a_narrow_frame_is_padded_not_refused(self):
        found = region_of(ANCHOR, *NARROW, pad_to=QUICK_REGION_SIZE)

        assert found is not None
        u0, _v0, u1, _v1 = found[0]
        assert u1 - u0 == pytest.approx(QUICK_REGION_SIZE[0])


class _Panels:
    def __init__(self, pointing=()):
        self._pointing = set(pointing)

    def pointing(self, hand):
        return hand in self._pointing


class TestMainWindow:
    @pytest.fixture
    def window(self, monkeypatch):
        win = MainWindow.__new__(MainWindow)
        win._config = {
            "vrc_listen": {
                "screenshot_translation": {
                    "enabled": True,
                    "frame_gesture": True,
                    "gesture_sensitivity": 0.9,
                    "frame_arm_seconds": 0.6,
                    "frame_release_seconds": 0.4,
                    "quick_frame": True,
                    "view_eye": "left",
                },
                "vr_controls": {"trigger_threshold": 0.7, "grip_threshold": 0.2, "swap_trigger_grip": True},
            }
        }
        win._frame_tracker = None
        win._vr_result_panels = None
        return win

    def test_the_tracker_takes_the_settings(self, window):
        tracker = MainWindow._ensure_frame_tracker(window)

        assert tracker.sensitivity == pytest.approx(0.9)
        assert tracker.arm_seconds == pytest.approx(0.6)
        assert tracker.release_seconds == pytest.approx(0.4)
        assert tracker.quick is True

    def test_the_controller_settings_reach_the_input(self, window):
        target = types.SimpleNamespace(trigger_threshold=0.5, grip_threshold=0.4, swap_trigger_grip=False)

        MainWindow._apply_vr_controls(window, target)

        assert (target.trigger_threshold, target.grip_threshold, target.swap_trigger_grip) == (0.7, 0.2, True)

    def test_the_view_eye_follows_the_setting(self, window):
        assert MainWindow._view_eye(window) == "left"
        window._config["vrc_listen"]["screenshot_translation"]["view_eye"] = "wrong"
        assert MainWindow._view_eye(window) == "right"

    def test_framing_keeps_both_hands_from_the_game(self, window):
        window._frame_tracker = types.SimpleNamespace(active=False, arming=True)

        assert MainWindow._hands_kept_from_game(window, None) == {"left", "right"}

    def test_a_panel_or_the_wrist_keeps_only_the_hand_on_it(self, window):
        window._frame_tracker = types.SimpleNamespace(active=False, arming=False)
        window._vr_result_panels = _Panels(pointing={"left"})
        wrist = types.SimpleNamespace(pointer_on_panel=True, pointing_hand="right")

        assert MainWindow._hands_kept_from_game(window, None) == {"left"}
        assert MainWindow._hands_kept_from_game(window, wrist) == {"left", "right"}

    def test_nothing_is_kept_when_switched_off(self, window):
        window._config["vrc_listen"]["vr_controls"]["block_game_input"] = False
        window._frame_tracker = types.SimpleNamespace(active=True, arming=False)

        assert MainWindow._hands_kept_from_game(window, None) == set()


class _Clipboard:
    def __init__(self):
        self.text = None

    def setText(self, text):  # noqa: N802
        self.text = text


class _Sender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list = []

    def send_chatbox(self, text):
        self.sent.append(text)
        return text if self.ok else ""


class TestPanelShare:
    @pytest.fixture
    def window(self, monkeypatch):
        win = MainWindow.__new__(MainWindow)
        history = ReadHistory()
        entry = history.add(card=None, pairs=[("出口", "Exit"), ("危険", "")])
        win._read_history = history
        win._entry = entry
        clipboard = _Clipboard()
        sender = _Sender()
        toasts: list = []
        monkeypatch.setattr(main_window_module.QApplication, "clipboard", staticmethod(lambda: clipboard))
        monkeypatch.setattr(win, "_ensure_sender", lambda: sender)
        monkeypatch.setattr(win, "_t", lambda key, **kw: key)
        monkeypatch.setattr(win, "_flash_vr_toast", lambda text, **kw: toasts.append(text) or True)
        win._test = types.SimpleNamespace(clipboard=clipboard, sender=sender, toasts=toasts)
        return win

    def test_copy_takes_the_side_that_is_shown(self, window):
        MainWindow._on_panel_share(window, window._entry.entry_id, "copy", False)
        assert window._test.clipboard.text == "Exit\n危険"

        MainWindow._on_panel_share(window, window._entry.entry_id, "copy", True)
        assert window._test.clipboard.text == "出口\n危険"
        assert window._test.toasts[-1] == "vr_panel_copied"

    def test_the_chatbox_always_gets_the_translation(self, window):
        MainWindow._on_panel_share(window, window._entry.entry_id, "chatbox", True)

        assert window._test.sender.sent == ["Exit\n危険"]
        assert window._test.toasts[-1] == "vr_panel_sent_chatbox"

    def test_a_failed_send_says_so(self, window):
        window._test.sender.ok = False

        MainWindow._on_panel_share(window, window._entry.entry_id, "chatbox", False)

        assert window._test.toasts[-1] == "vr_panel_chatbox_failed"

    def test_an_unknown_read_is_ignored(self, window):
        MainWindow._on_panel_share(window, 999, "copy", False)

        assert window._test.clipboard.text is None
