"""The subtitle panel lives on a sphere around the head and faces the player."""

from __future__ import annotations

import math

import pytest

from src.core.vr_geometry import (
    MAX_PITCH,
    MAX_RADIUS,
    MIN_RADIUS,
    facing_transform,
    pitch_of,
    position_on_sphere,
    radius_of,
    reach_of,
    snap_to_sphere,
    sphere_hit,
    yaw_of,
)


class TestSphericalCoordinates:
    def test_straight_ahead_is_yaw_and_pitch_zero(self):
        assert yaw_of((0.0, 0.0, -1.5)) == pytest.approx(0.0)
        assert pitch_of((0.0, 0.0, -1.5)) == pytest.approx(0.0)

    def test_right_is_positive_yaw_and_up_is_positive_pitch(self):
        assert yaw_of((1.5, 0.0, 0.0)) == pytest.approx(math.pi / 2)
        assert yaw_of((-1.5, 0.0, 0.0)) == pytest.approx(-math.pi / 2)
        assert pitch_of((0.0, 1.0, -1.0)) == pytest.approx(math.pi / 4)
        assert pitch_of((0.0, -1.0, -1.0)) == pytest.approx(-math.pi / 4)

    def test_position_round_trips(self):
        original = (0.9, -0.4, -1.2)
        rebuilt = position_on_sphere(yaw_of(original), pitch_of(original), radius_of(original))

        assert rebuilt == pytest.approx(original)

    def test_radius_and_pitch_are_clamped(self):
        far = position_on_sphere(0.0, 0.0, 100.0)
        assert reach_of(far) == pytest.approx(MAX_RADIUS)
        assert radius_of((0.01, 0.0, 0.0)) == MIN_RADIUS
        high = position_on_sphere(0.0, math.pi / 2, 1.0)
        assert pitch_of(high) == pytest.approx(MAX_PITCH)

    def test_snap_keeps_direction(self):
        snapped = snap_to_sphere((0.0, 0.5, 2.0))

        assert yaw_of(snapped) == pytest.approx(math.pi)
        assert pitch_of(snapped) == pytest.approx(pitch_of((0.0, 0.5, 2.0)))


class TestRayCast:
    def test_a_ray_from_the_head_exits_where_it_points(self):
        assert sphere_hit((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), 1.5) == pytest.approx((0.0, 0.0, -1.5))

    def test_a_hand_beside_the_head_still_hits_the_far_side(self):
        hit = sphere_hit((0.3, -0.4, -0.2), (-0.5, 0.2, -0.8), 1.5)

        assert hit is not None
        assert reach_of(hit) == pytest.approx(1.5)
        assert hit[2] < 0

    def test_pointing_up_hits_the_top_of_the_sphere(self):
        assert sphere_hit((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 1.5) == pytest.approx((0.0, 1.5, 0.0))

    def test_the_exit_is_ahead_of_the_hand_not_behind(self):
        assert sphere_hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 2.0) == pytest.approx((2.0, 0.0, 0.0))

    def test_a_zero_direction_has_no_answer(self):
        assert sphere_hit((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0) is None


class TestFacing:
    @staticmethod
    def _front(rows):
        return (rows[0][2], rows[1][2], rows[2][2])

    @staticmethod
    def _up(rows):
        return (rows[0][1], rows[1][1], rows[2][1])

    def test_a_panel_ahead_keeps_the_identity_rotation(self):
        rows = facing_transform((0.0, 0.0, -1.5))

        assert rows[0][:3] == pytest.approx([1.0, 0.0, 0.0])
        assert self._front(rows) == pytest.approx((0.0, 0.0, 1.0))
        assert (rows[0][3], rows[1][3], rows[2][3]) == (0.0, 0.0, -1.5)

    def test_a_panel_to_the_right_turns_to_face_the_head(self):
        rows = facing_transform((1.5, 0.0, 0.0))

        assert self._front(rows) == pytest.approx((-1.0, 0.0, 0.0), abs=1e-9)
        assert self._up(rows) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_a_panel_above_tilts_down_toward_the_eyes(self):
        position = (0.0, 1.0, -1.0)
        rows = facing_transform(position)

        # The front points from the panel back to the origin.
        norm = math.sqrt(2.0)
        assert self._front(rows) == pytest.approx((0.0, -1.0 / norm, 1.0 / norm), abs=1e-9)
        # Still upright: the panel's up has a positive world-y component.
        assert self._up(rows)[1] > 0

    def test_axes_stay_orthonormal_everywhere(self):
        for yaw in (-2.5, -1.0, 0.3, 2.0):
            for pitch in (-1.2, 0.0, 0.9):
                rows = facing_transform(position_on_sphere(yaw, pitch, 1.3))
                axes = [tuple(rows[r][c] for r in range(3)) for c in range(3)]
                for a in axes:
                    assert math.sqrt(sum(v * v for v in a)) == pytest.approx(1.0)
                for i in range(3):
                    for j in range(i + 1, 3):
                        assert sum(p * q for p, q in zip(axes[i], axes[j])) == pytest.approx(0.0, abs=1e-9)
