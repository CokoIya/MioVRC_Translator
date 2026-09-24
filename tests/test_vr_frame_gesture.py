"""The two-hand frame (VRHandsFrame's gesture) and the geometry under it."""

from __future__ import annotations

import pytest

from src.core.in_place_layout import HeadAnchor, card_transform, region_quad, view_fraction
from src.core.vr_frame_gesture import (
    ARM_SECONDS,
    LOST_GRACE_SECONDS,
    MOVE_TOLERANCE,
    STILL_SECONDS,
    FrameGestureTracker,
    FrameInputs,
    region_of,
)


def _anchor() -> HeadAnchor:
    return HeadAnchor(
        pose=((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0)),
        eye_offset=(0.0, 0.0, 0.0),
        tangents=(-1.0, 1.0, -1.0, 1.0),
    )


LEFT = (-0.2, 0.1, -0.4)
RIGHT = (0.2, -0.1, -0.4)


def _inputs(now, *, left=LEFT, right=RIGHT, grips=(True, True), triggers=(False, False), anchor=None):
    return FrameInputs(
        anchor=anchor or _anchor(),
        left=left,
        right=right,
        left_grip=grips[0],
        right_grip=grips[1],
        left_trigger=triggers[0],
        right_trigger=triggers[1],
        now=now,
    )


def _open(tracker, start=0.0):
    tracker.update(_inputs(start))
    return tracker.update(_inputs(start + ARM_SECONDS + 0.01))


class TestGeometry:
    def test_a_point_ahead_lands_in_the_middle_of_the_view(self):
        assert view_fraction(_anchor(), (0.0, 0.0, -1.0)) == pytest.approx((0.5, 0.5, 1.0))

    def test_up_is_the_top_of_the_frame(self):
        u, v, _depth = view_fraction(_anchor(), (0.5, 0.5, -1.0))

        assert u == pytest.approx(0.75)
        assert v == pytest.approx(0.25)

    def test_a_point_behind_is_nowhere(self):
        assert view_fraction(_anchor(), (0.0, 0.0, 1.0)) is None

    def test_view_fraction_inverts_the_card_direction(self):
        rows = card_transform(_anchor(), (0.3, 0.7), 2.0)

        u, v, depth = view_fraction(_anchor(), (rows[0][3], rows[1][3], rows[2][3]))

        assert (u, v) == pytest.approx((0.3, 0.7))
        assert depth == pytest.approx(2.0)

    def test_the_region_quad_covers_exactly_the_region(self):
        rows, width, height = region_quad(_anchor(), (0.25, 0.375, 0.75, 0.625), 0.4)

        assert width == pytest.approx(0.4)
        assert height == pytest.approx(0.2)
        assert (rows[0][3], rows[1][3], rows[2][3]) == pytest.approx((0.0, 0.0, -0.4))

    def test_two_hands_on_a_diagonal_make_a_region(self):
        region, depth = region_of(_anchor(), LEFT, RIGHT)

        assert region == pytest.approx((0.25, 0.375, 0.75, 0.625))
        assert depth == pytest.approx(0.4)

    def test_hands_side_by_side_make_no_frame(self):
        assert region_of(_anchor(), (-0.2, 0.0, -0.4), (0.2, 0.0, -0.4)) is None

    def test_hands_at_the_face_or_far_away_make_no_frame(self):
        assert region_of(_anchor(), (-0.05, 0.03, -0.08), (0.05, -0.03, -0.08)) is None
        assert region_of(_anchor(), (-0.4, 0.3, -1.2), (0.4, -0.3, -1.2)) is None

    def test_hands_together_make_no_frame(self):
        assert region_of(_anchor(), (0.0, 0.0, -0.4), (0.05, -0.05, -0.4)) is None


class TestTracker:
    def test_the_frame_opens_after_the_pose_is_held(self):
        tracker = FrameGestureTracker()

        first = tracker.update(_inputs(0.0))
        assert not first.active and first.events == []

        state = tracker.update(_inputs(ARM_SECONDS + 0.01))
        assert state.active
        assert state.events == ["opened"]
        assert state.region == pytest.approx((0.25, 0.375, 0.75, 0.625))
        assert state.phase == "ready"

    def test_one_grip_is_not_a_frame(self):
        tracker = FrameGestureTracker()

        for step in range(20):
            state = tracker.update(_inputs(step * 0.05, grips=(True, False)))

        assert not state.active

    def test_unbound_grips_are_not_a_frame(self):
        tracker = FrameGestureTracker()

        for step in range(20):
            state = tracker.update(_inputs(step * 0.05, grips=(None, None)))

        assert not state.active

    def test_holding_still_asks_for_a_read_once(self):
        tracker = FrameGestureTracker()
        _open(tracker)
        start = ARM_SECONDS + 0.01

        half = tracker.update(_inputs(start + STILL_SECONDS / 2))
        assert half.still_fraction == pytest.approx(0.5, abs=0.05)
        assert "still" not in half.events

        state = tracker.update(_inputs(start + STILL_SECONDS + 0.01))
        assert "still" in state.events

        tracker.reading_started()
        later = tracker.update(_inputs(start + STILL_SECONDS + 0.2))
        assert later.phase == "reading" and "still" not in later.events

    def test_without_auto_read_stillness_does_nothing(self):
        tracker = FrameGestureTracker(auto_read=False)
        _open(tracker)

        state = tracker.update(_inputs(10.0))

        assert "still" not in state.events
        assert state.still_fraction == 0.0

    def test_a_shown_reading_clears_when_the_frame_moves_off_it(self):
        tracker = FrameGestureTracker()
        _open(tracker)
        tracker.reading_started()
        tracker.reading_done(shown=True)
        assert tracker.update(_inputs(1.0)).phase == "shown"

        moved = None
        for step in range(30):
            shift = 0.1 + 0.01 * step
            moved = tracker.update(_inputs(1.1 + step * 0.03, left=(-0.2 + shift, 0.1, -0.4), right=(0.2 + shift, -0.1, -0.4)))
            if "moved" in moved.events:
                break

        assert moved is not None and "moved" in moved.events
        assert moved.phase == "ready"
        assert MOVE_TOLERANCE > 0

    def test_a_read_that_found_nothing_waits_for_a_move(self):
        tracker = FrameGestureTracker()
        _open(tracker)
        tracker.reading_started()

        tracker.reading_done(shown=False)
        state = tracker.update(_inputs(20.0))

        assert state.phase == "ready"
        assert "still" not in state.events

    def test_a_trigger_pull_while_framing_is_a_capture(self):
        tracker = FrameGestureTracker()
        _open(tracker)

        state = tracker.update(_inputs(1.0, triggers=(False, True)))
        held = tracker.update(_inputs(1.05, triggers=(False, True)))

        assert "capture" in state.events
        assert "capture" not in held.events

    def test_letting_go_closes_after_a_grace(self):
        tracker = FrameGestureTracker()
        _open(tracker)

        brief = tracker.update(_inputs(1.0, grips=(True, False)))
        assert brief.active
        gone = tracker.update(_inputs(1.0 + LOST_GRACE_SECONDS + 0.01, grips=(True, False)))

        assert not gone.active
        assert gone.events == ["closed"]

    def test_after_a_capture_it_waits_for_the_grips_to_open(self):
        tracker = FrameGestureTracker()
        _open(tracker)

        tracker.close(require_release=True)
        for step in range(10):
            state = tracker.update(_inputs(2.0 + step * 0.1))
        assert not state.active

        tracker.update(_inputs(3.5, grips=(False, False)))
        tracker.update(_inputs(3.6))
        reopened = tracker.update(_inputs(3.6 + ARM_SECONDS + 0.01))
        assert reopened.active
