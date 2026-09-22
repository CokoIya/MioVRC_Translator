"""Plates land on their source text; the headset transform lands on the sign."""

from __future__ import annotations

import math

import pytest

from src.core.in_place_layout import (
    MAX_GROW_RATIO,
    MIN_FONT_PX,
    HeadAnchor,
    PlacedLine,
    clamp_depth,
    clamp_fov_scale,
    frame_region_from_uv,
    in_place_transform,
    layout_plates,
    region_is_a_click,
    wrap_text,
)


def _measure(text: str, font_px: int) -> tuple[float, float]:
    """A fixed-pitch stand-in for a font: every glyph is 0.6 em wide."""

    return (len(text) * font_px * 0.6, font_px * 1.2)


def _line(text: str, *, left=100.0, top=200.0, width=300.0, height=40.0) -> PlacedLine:
    return PlacedLine(original="src", translated=text, left=left, top=top, width=width, height=height)


class TestWrap:
    def test_short_text_stays_on_one_row(self):
        assert wrap_text("hello", 20, 1000, _measure) == ["hello"]

    def test_spaced_text_wraps_on_words(self):
        rows = wrap_text("one two three four", 10, 60, _measure)
        assert rows == ["one two", "three four"]
        assert all(_measure(row, 10)[0] <= 60 for row in rows)

    def test_cjk_text_wraps_on_characters(self):
        rows = wrap_text("こんにちは世界", 10, 24, _measure)
        assert rows == ["こんにち", "は世界"]

    def test_empty_text_gives_nothing(self):
        assert wrap_text("   ", 10, 100, _measure) == []


class TestPlates:
    def test_a_plate_sits_on_its_line_scaled_to_the_target(self):
        plates = layout_plates([_line("ok")], (1000, 500), (500, 250), _measure)

        assert len(plates) == 1
        plate = plates[0]
        # Line at (100, 200) in a 1000x500 frame -> (50, 100) at half size,
        # minus padding.
        assert plate.x < 50 <= plate.x + plate.width
        assert plate.y < 100 <= plate.y + plate.height
        assert plate.rows == ("ok",)

    def test_font_follows_line_height(self):
        tall = layout_plates([_line("ok", height=60)], (1000, 500), (1000, 500), _measure)[0]
        short = layout_plates([_line("ok", height=20)], (1000, 500), (1000, 500), _measure)[0]

        assert tall.font_px > short.font_px
        assert short.font_px >= MIN_FONT_PX

    def test_a_long_translation_shrinks_before_it_wraps(self):
        fits = layout_plates([_line("abc")], (1000, 500), (1000, 500), _measure)[0]
        longer = layout_plates([_line("abcdefghijklmnop")], (1000, 500), (1000, 500), _measure)[0]

        assert longer.font_px < fits.font_px
        assert longer.rows == ("abcdefghijklmnop",)

    def test_a_much_longer_translation_widens_then_wraps(self):
        text = " ".join(["word"] * 30)
        plate = layout_plates([_line(text, width=100)], (1000, 500), (1000, 500), _measure)[0]

        assert len(plate.rows) > 1
        # Never wider than the growth cap plus padding.
        assert plate.width <= 100 * MAX_GROW_RATIO + 2 * 20

    def test_plates_never_leave_the_frame(self):
        plate = layout_plates(
            [_line("x" * 40, left=950, top=480, width=40, height=15)],
            (1000, 500),
            (1000, 500),
            _measure,
        )[0]

        assert plate.x >= 0
        assert plate.y >= 0
        assert plate.x + plate.width <= 1000
        assert plate.y + plate.height <= 500

    def test_empty_lines_are_skipped(self):
        assert layout_plates([_line("  ")], (10, 10), (10, 10), _measure) == []


class TestTransform:
    def _anchor(self, pose=None):
        identity = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0))
        return HeadAnchor(
            pose=pose or identity,
            eye_offset=(-0.034, 0.0, 0.0),
            tangents=(-1.28, 1.28, -1.28, 1.28),
        )

    def test_symmetric_lenses_put_the_sheet_in_front_of_the_left_eye(self):
        rows, width = in_place_transform(self._anchor(), 2.0, 1.0)

        assert rows[0][3] == pytest.approx(-0.034)
        assert rows[1][3] == pytest.approx(0.0)
        assert rows[2][3] == pytest.approx(-2.0)
        # Full horizontal field of view at that depth.
        assert width == pytest.approx(2.56 * 2.0)

    def test_fov_scale_and_depth_scale_the_width(self):
        _rows, base = in_place_transform(self._anchor(), 2.0, 1.0)
        _rows, wider = in_place_transform(self._anchor(), 2.0, 1.2)
        _rows, farther = in_place_transform(self._anchor(), 3.0, 1.0)

        assert wider == pytest.approx(base * 1.2)
        assert farther == pytest.approx(base * 1.5)

    def test_asymmetric_lenses_shift_the_centre(self):
        anchor = HeadAnchor(
            pose=((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            eye_offset=(0.0, 0.0, 0.0),
            tangents=(-1.4, 1.2, -1.3, 1.3),
        )
        rows, _ = in_place_transform(anchor, 2.0, 1.0)

        assert rows[0][3] == pytest.approx((-1.4 + 1.2) / 2 * 2.0)
        assert rows[1][3] == pytest.approx(0.0)

    def test_a_view_that_reaches_farther_down_centres_the_sheet_lower(self):
        # OpenVR's raw "top" is the (negative) tangent of the frame's bottom
        # edge; a lens that sees more of the floor than the ceiling has a
        # frame whose centre sits below eye level.
        anchor = HeadAnchor(
            pose=((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            eye_offset=(0.0, 0.0, 0.0),
            tangents=(-1.0, 1.0, -1.4, 1.2),
        )
        rows, _ = in_place_transform(anchor, 2.0, 1.0)

        assert rows[1][3] == pytest.approx((-1.4 + 1.2) / 2 * 2.0)

    def test_the_head_pose_carries_the_sheet_into_the_world(self):
        # Head at (1, 1.6, 0), turned 90 degrees to the left (yaw): "forward"
        # (-z in head space) becomes -x in the world.
        c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
        pose = ((c, 0, s, 1.0), (0, 1, 0, 1.6), (-s, 0, c, 0.0))
        anchor = HeadAnchor(pose=pose, eye_offset=(0, 0, 0), tangents=(-1, 1, -1, 1))

        rows, _ = in_place_transform(anchor, 2.0, 1.0)

        assert rows[0][3] == pytest.approx(1.0 - 2.0)
        assert rows[1][3] == pytest.approx(1.6)
        assert rows[2][3] == pytest.approx(0.0, abs=1e-9)

    def test_clamps(self):
        assert clamp_depth("nope") == 1.5
        assert clamp_depth(0.1) == 0.8
        assert clamp_depth(50) == 5.0
        assert clamp_fov_scale(None) == 1.0
        assert clamp_fov_scale(0.1) == 0.5
        assert clamp_fov_scale(9) == 1.6


class TestRegions:
    def test_drag_corners_are_ordered_and_clamped(self):
        assert frame_region_from_uv((0.8, 0.9), (0.2, 0.1)) == (0.2, 0.1, 0.8, 0.9)
        assert frame_region_from_uv((-1, 2), (0.5, 0.5)) == (0.0, 0.5, 0.5, 1.0)

    def test_a_tiny_drag_is_a_click(self):
        assert region_is_a_click((0.5, 0.5, 0.505, 0.6))
        assert region_is_a_click(None)
        assert not region_is_a_click((0.2, 0.2, 0.6, 0.6))
