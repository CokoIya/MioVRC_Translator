"""The controller-at-the-ear gesture, the wrist twist and the new switches' defaults."""

from __future__ import annotations

import math
import types

import pytest

from src.core.vr_gesture import (
    NEAR_HEAD_METERS,
    SHORT_PRESS_MAX_SECONDS,
    TWIST_WINDOW_SECONDS,
    EarGesture,
    TwistDetector,
    controller_axes,
    controller_near_head,
    near_head,
)


def _axes(roll_degrees: float, *, forward=(0.0, 0.0, 1.0)):
    """Sideways and forward axes of a controller rolled about ``forward``."""

    c, s = math.cos(math.radians(roll_degrees)), math.sin(math.radians(roll_degrees))
    if forward == (0.0, 0.0, 1.0):
        return (c, s, 0.0), forward
    # Pointing at the floor: the sideways axis turns in the horizontal plane.
    assert forward == (0.0, -1.0, 0.0)
    return (c, 0.0, s), forward


class TestNearHead:
    def test_a_controller_at_the_temple_is_near(self):
        assert near_head((0.0, 1.6, 0.0), [(0.12, 1.62, -0.05)]) is True

    def test_a_controller_pointing_at_the_world_is_not(self):
        assert near_head((0.0, 1.6, 0.0), [(0.3, 1.2, -0.4)]) is False

    def test_the_threshold_is_the_documented_one(self):
        assert near_head((0.0, 0.0, 0.0), [(NEAR_HEAD_METERS, 0.0, 0.0)]) is True
        assert near_head((0.0, 0.0, 0.0), [(NEAR_HEAD_METERS + 0.01, 0.0, 0.0)]) is False

    def test_reading_the_runtime(self):
        class _Pose:
            def __init__(self, x, y, z, valid=True):
                self.bPoseIsValid = valid
                self.mDeviceToAbsoluteTracking = [[1, 0, 0, x], [0, 1, 0, y], [0, 0, 1, z]]

        poses = [_Pose(0.0, 1.6, 0.0), _Pose(0.1, 1.6, -0.1), _Pose(0.4, 1.1, -0.5)]

        class _System:
            def getDeviceToAbsoluteTrackingPose(self, origin, seconds, count):
                return poses

            def getTrackedDeviceIndexForControllerRole(self, role):
                return 1 if role == 1 else 2

        openvr = types.SimpleNamespace(
            VRSystem=lambda: _System(),
            TrackingUniverseStanding=1,
            k_unMaxTrackedDeviceCount=3,
            k_unTrackedDeviceIndex_Hmd=0,
            TrackedControllerRole_LeftHand=1,
            TrackedControllerRole_RightHand=2,
            k_unTrackedDeviceIndexInvalid=0xFFFFFFFF,
        )

        assert controller_near_head(openvr) is True
        poses[1].bPoseIsValid = False
        assert controller_near_head(openvr) is False
        poses[0].bPoseIsValid = False
        assert controller_near_head(openvr) is None


class TestEarGesture:
    def test_a_short_pull_at_the_ear_fires_on_release(self):
        gesture = EarGesture()

        assert gesture.update(near_ear=True, trigger_down=True, now=10.0) is None
        assert gesture.update(near_ear=True, trigger_down=False, now=10.0 + SHORT_PRESS_MAX_SECONDS) == "short"

    def test_a_long_pull_at_the_ear_is_left_alone(self):
        """The player asked for no long-press start/stop: a held trigger is the game's."""

        gesture = EarGesture()

        gesture.update(near_ear=True, trigger_down=True, now=10.0)
        assert gesture.update(near_ear=True, trigger_down=True, now=11.5) is None
        assert gesture.update(near_ear=True, trigger_down=False, now=11.6) is None

    def test_a_pull_away_from_the_ear_is_the_games_business(self):
        gesture = EarGesture()

        gesture.update(near_ear=False, trigger_down=True, now=10.0)
        assert gesture.update(near_ear=False, trigger_down=False, now=10.2) is None

    def test_the_hand_may_drift_once_the_press_began_at_the_ear(self):
        gesture = EarGesture()

        gesture.update(near_ear=True, trigger_down=True, now=10.0)
        assert gesture.update(near_ear=False, trigger_down=False, now=10.3) == "short"

    def test_unknown_positions_never_arm(self):
        gesture = EarGesture()

        gesture.update(near_ear=None, trigger_down=True, now=10.0)
        assert gesture.update(near_ear=None, trigger_down=False, now=10.2) is None


class TestTwistDetector:
    def test_two_quick_twists_toggle(self):
        detector = TwistDetector()

        fired = [
            detector.update(*_axes(roll), now=10.0 + i * 0.2)
            for i, roll in enumerate((0.0, 70.0, 0.0, 70.0, 0.0))
        ]

        assert fired == [False, False, False, False, True]

    def test_one_twist_is_not_enough_and_the_window_expires(self):
        detector = TwistDetector()

        for i, roll in enumerate((0.0, 70.0, 0.0)):
            assert detector.update(*_axes(roll), now=10.0 + i * 0.2) is False
        late = 10.4 + TWIST_WINDOW_SECONDS + 0.5
        assert detector.update(*_axes(70.0), now=late) is False
        assert detector.update(*_axes(0.0), now=late + 0.2) is False

    def test_the_resting_roll_is_relative_not_absolute(self):
        # A hand that rests already rolled still twists from there.
        detector = TwistDetector()

        fired = [
            detector.update(*_axes(roll), now=10.0 + i * 0.2)
            for i, roll in enumerate((40.0, -30.0, 40.0, -30.0, 40.0))
        ]

        assert fired[-1] is True

    def test_a_hanging_arm_twists_the_same_way(self):
        detector = TwistDetector()
        floor = (0.0, -1.0, 0.0)

        fired = [
            detector.update(*_axes(roll, forward=floor), now=10.0 + i * 0.2)
            for i, roll in enumerate((0.0, 70.0, 0.0, 70.0, 0.0))
        ]

        assert fired[-1] is True

    def test_swinging_the_arm_is_not_a_twist(self):
        detector = TwistDetector()

        assert detector.update((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), now=10.0) is False
        # The sideways axis turns 90 degrees, but so does the forward axis:
        # the arm swung from pointing ahead to pointing right.
        for i in range(2):
            assert detector.update((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), now=10.2 + i * 0.2) is False
            assert detector.update((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), now=10.4 + i * 0.2) is False

    def test_a_half_swing_of_the_arm_reads_as_no_roll(self):
        detector = TwistDetector()
        detector.update((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), now=10.0)

        # Pointing 40 degrees to the right: the forward axis moved, the
        # sideways axis merely tilted toward it.
        c, s = math.cos(math.radians(40)), math.sin(math.radians(40))
        detector.update((c, 0.0, -s), (s, 0.0, c), now=10.2)

        assert abs(detector.roll) < 5.0

    def test_a_hand_turned_nearly_palm_up_does_not_flicker_into_twists(self):
        """Near 180 degrees the raw angle flips sign; unwrapped it must not count."""

        detector = TwistDetector()

        fired = [
            detector.update(*_axes(roll), now=10.0 + i * 0.15)
            for i, roll in enumerate((0.0, 170.0, -170.0, -160.0, 170.0, 0.0))
        ]

        assert fired == [False] * 6
        assert detector.count == 1
        assert detector.last_peak == pytest.approx(200.0, abs=1.0)

    def test_the_peak_of_a_twist_is_reported(self):
        detector = TwistDetector()

        for i, roll in enumerate((0.0, 40.0, 95.0, 60.0, 5.0)):
            detector.update(*_axes(roll), now=10.0 + i * 0.1)

        assert detector.count == 1
        assert detector.last_peak == pytest.approx(95.0, abs=1.0)

    def test_a_small_roll_counts_there_is_no_upper_limit(self):
        detector = TwistDetector()

        fired = [
            detector.update(*_axes(roll), now=10.0 + i * 0.2)
            for i, roll in enumerate((0.0, 35.0, 0.0, 150.0, 0.0))
        ]

        assert fired[-1] is True

    def test_a_fast_back_and_forth_counts_each_side(self):
        """Rolling straight through the rest to the other side is a twist too."""

        detector = TwistDetector()

        fired = [
            detector.update(*_axes(roll), now=10.0 + i * 0.15)
            for i, roll in enumerate((0.0, 70.0, -70.0, 70.0))
        ]

        assert fired == [False, False, False, True]

    def test_a_twist_about_an_axis_off_the_controllers_own_still_counts(self):
        """The wrist turns about the forearm, which is not quite the controller's axis."""

        def rotated(vector, axis, degrees):
            c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
            dot = sum(v * a for v, a in zip(vector, axis))
            cross = (
                axis[1] * vector[2] - axis[2] * vector[1],
                axis[2] * vector[0] - axis[0] * vector[2],
                axis[0] * vector[1] - axis[1] * vector[0],
            )
            return tuple(vector[i] * c + cross[i] * s + axis[i] * dot * (1 - c) for i in range(3))

        forearm = (math.sin(math.radians(40)), 0.0, math.cos(math.radians(40)))
        detector = TwistDetector()
        fired = []
        for i, degrees in enumerate((0.0, 90.0, 0.0, 90.0, 0.0)):
            side = rotated((1.0, 0.0, 0.0), forearm, degrees)
            ahead = rotated((0.0, 0.0, 1.0), forearm, degrees)
            fired.append(detector.update(side, ahead, now=10.0 + i * 0.2))

        assert fired[-1] is True

    def test_a_hand_held_rolled_becomes_the_new_rest(self):
        detector = TwistDetector()
        detector.update(*_axes(0.0), now=10.0)
        detector.update(*_axes(70.0), now=10.2)

        # Held there for a while, then twisted twice from the new angle.
        detector.update(*_axes(70.0), now=12.0)
        fired = [
            detector.update(*_axes(roll), now=12.2 + i * 0.2)
            for i, roll in enumerate((140.0, 70.0, 140.0, 70.0))
        ]

        assert fired[-1] is True

    def test_a_slow_change_of_grip_moves_the_baseline_instead(self):
        detector = TwistDetector()

        for i in range(90):
            assert detector.update(*_axes(0.5 * i), now=10.0 + i * 0.05) is False

    def test_axes_are_read_from_the_pose_columns(self):
        rows = [[1.0, 2.0, 3.0, 9.0], [4.0, 5.0, 6.0, 9.0], [7.0, 8.0, 9.0, 9.0]]

        assert controller_axes(rows) == ((1.0, 4.0, 7.0), (3.0, 6.0, 9.0))


class TestSwitchDefaults:
    def test_the_gesture_defaults_on_and_autolaunch_off(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config: dict = {}
        _ensure_vrc_listen_config(config, loaded={})
        listen = config["vrc_listen"]

        assert listen["vr_gesture"] == {"enabled": True}
        assert listen["steamvr_autolaunch"] is False
        assert "vr_status" not in listen

    def test_the_players_choices_survive_and_the_retired_status_line_is_dropped(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config = {
            "vrc_listen": {
                "vr_status": {"enabled": False},
                "vr_gesture": {"enabled": "no"},
                "steamvr_autolaunch": "yes",
            }
        }
        _ensure_vrc_listen_config(config, loaded=config)
        listen = config["vrc_listen"]

        assert listen["vr_gesture"]["enabled"] is False
        assert listen["steamvr_autolaunch"] is True
        assert "vr_status" not in listen


class TestPanelAngles:
    def test_angles_and_positions_round_trip(self):
        from src.core.vr_geometry import angles_of, position_from_angles

        for yaw, pitch, radius in ((0.0, -12.0, 1.5), (35.0, 20.0, 2.5), (-80.0, -50.0, 0.8)):
            position = position_from_angles(yaw, pitch, radius)
            back = angles_of(position)

            assert back[0] == pytest.approx(yaw, abs=1e-6)
            assert back[1] == pytest.approx(pitch, abs=1e-6)
            assert back[2] == pytest.approx(radius, abs=1e-6)

    def test_straight_ahead_is_in_front_and_a_little_down(self):
        from src.core.vr_geometry import position_from_angles

        x, y, z = position_from_angles(0.0, -12.0, 1.5)

        assert x == pytest.approx(0.0, abs=1e-9)
        assert y < 0 and z < 0
