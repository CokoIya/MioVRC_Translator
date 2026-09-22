"""Lines become paragraphs only when they read as one; plates follow the tilt."""

from __future__ import annotations

import types

import pytest

from src.core.in_place_layout import PlacedLine
from src.core.text_blocks import (
    MIN_BLOCK_FONT_PX,
    box_corners,
    fit_plate,
    from_frame,
    group_lines_into_blocks,
    layout_block_plates,
    to_frame,
)


def _line(text, left, top, width=200.0, height=20.0, angle=0.0):
    return types.SimpleNamespace(
        text=text,
        left=float(left),
        top=float(top),
        width=float(width),
        height=float(height),
        angle=float(angle),
    )


def _measure(text: str, font_px: int) -> tuple[float, float]:
    return (len(text) * font_px * 0.6, font_px * 1.2)


class TestGrouping:
    def test_a_wrapped_paragraph_is_one_block(self):
        # Three lines at one margin: two full width, the last one short.
        blocks = group_lines_into_blocks(
            [
                _line("第一行文字比较长一些", 100, 100, width=300),
                _line("第二行文字也一样长的", 100, 124, width=298),
                _line("最后一行", 100, 148, width=120),
            ]
        )

        assert len(blocks) == 1
        block = blocks[0]
        assert (block.left, block.top) == (100, 100)
        assert block.bottom == pytest.approx(168)
        assert block.width == pytest.approx(300)
        assert block.text == "第一行文字比较长一些第二行文字也一样长的最后一行"

    def test_a_short_line_ends_its_paragraph(self):
        # The line above stopped well short of the paragraph's edge, so the
        # full-width line under it opens a new paragraph.
        blocks = group_lines_into_blocks(
            [
                _line("第一段第一行很长很长", 100, 100, width=300),
                _line("第一段结束", 100, 124, width=100),
                _line("第二段第一行也很长的", 100, 148, width=300),
                _line("第二段结束", 100, 172, width=100),
            ]
        )

        assert [b.text for b in blocks] == [
            "第一段第一行很长很长第一段结束",
            "第二段第一行也很长的第二段结束",
        ]

    def test_a_wide_gap_starts_a_new_block(self):
        blocks = group_lines_into_blocks([_line("标题", 100, 100), _line("正文", 100, 200)])

        assert len(blocks) == 2

    def test_list_items_stay_apart(self):
        # A changelog: one margin, one spacing, but every line is an item of
        # its own, and only a wrapped remainder joins the line above.
        blocks = group_lines_into_blocks(
            [
                _line("- Fixed leaderboard user name display", 100, 100, width=300),
                _line("issue", 100, 124, width=40),
                _line("- Increased XP for unboxing cases after", 100, 148, width=300),
                _line("the second chest", 100, 172, width=130),
                _line("・新增了付费功能", 100, 196, width=300),
            ]
        )

        assert [b.text for b in blocks] == [
            "- Fixed leaderboard user name display issue",
            "- Increased XP for unboxing cases after the second chest",
            "・新增了付费功能",
        ]

    @pytest.mark.parametrize("marker", ["-", "•", "・", "1.", "(2)", "③", "A.", "※"])
    def test_every_kind_of_marker_starts_an_item(self, marker):
        blocks = group_lines_into_blocks(
            [
                _line("第一项内容比较长一点点", 100, 100, width=300),
                _line(f"{marker} 第二项", 100, 124, width=100),
            ]
        )

        assert len(blocks) == 2

    def test_a_line_wider_than_the_paragraph_is_not_its_remainder(self):
        blocks = group_lines_into_blocks(
            [
                _line("Visitors Information Board", 100, 100, width=250),
                _line("インスタンス作成日時：2026-09-10 02:17:00", 100, 124, width=400),
            ]
        )

        assert len(blocks) == 2

    def test_a_headline_and_its_small_print_stay_apart(self):
        blocks = group_lines_into_blocks(
            [
                _line("大标题", 100, 100, width=300, height=40),
                _line("小字说明", 100, 142, width=300, height=20),
            ]
        )

        assert len(blocks) == 2

    def test_table_cells_stay_apart(self):
        # Two rows of three cells: nothing merges, across or down.
        cells = []
        for row, top in enumerate((100, 130)):
            cells += [
                _line(f"名前{row}", 100, top, width=80),
                _line(f"PC/VR{row}", 220, top, width=80),
                _line(f"SteamVR{row}", 340, top, width=100),
            ]

        blocks = group_lines_into_blocks(cells)

        assert len(blocks) == 6

    def test_a_line_on_another_board_is_not_a_neighbour(self):
        # Something at the same height but fifteen line heights away does
        # not stop a paragraph from wrapping.
        blocks = group_lines_into_blocks(
            [
                _line("第一行文字比较长一些", 100, 100, width=300),
                _line("第二行", 100, 124, width=60),
                _line("别的板子", 700, 104, width=80),
            ]
        )

        assert len(blocks) == 2

    def test_side_by_side_columns_stay_apart(self):
        blocks = group_lines_into_blocks(
            [_line("左栏", 100, 100, width=120), _line("右栏", 400, 122, width=120)]
        )

        assert len(blocks) == 2

    def test_latin_lines_are_joined_with_spaces(self):
        blocks = group_lines_into_blocks([_line("Press the", 10, 10), _line("trigger", 10, 32, width=80)])

        assert blocks[0].text == "Press the trigger"

    def test_mixed_scripts_join_sensibly(self):
        blocks = group_lines_into_blocks([_line("按下", 10, 10), _line("B 键", 10, 32, width=60)])

        assert blocks[0].text == "按下 B 键"

    def test_blocks_come_back_in_reading_order(self):
        blocks = group_lines_into_blocks([_line("下", 10, 300), _line("上", 10, 10)])

        assert [b.text for b in blocks] == ["上", "下"]

    def test_line_height_is_the_median_source_line(self):
        block = group_lines_into_blocks(
            [_line("a", 0, 0, height=18), _line("b", 0, 20, height=22), _line("c", 0, 44, height=24)]
        )[0]

        assert block.line_height == 22

    def test_lines_without_a_box_stay_alone(self):
        blocks = group_lines_into_blocks(
            [types.SimpleNamespace(text="一"), types.SimpleNamespace(text="二")]
        )

        assert [b.text for b in blocks] == ["一", "二"]


class TestTilt:
    def test_a_tilted_paragraph_keeps_its_lines_together(self):
        # Two lines on a board seen from the side, both descending eight
        # degrees to the right; the second starts one line down the first's
        # own vertical, not the screen's.
        angle = 8.0
        origin = (100.0, 100.0)
        second = from_frame(0.0, 24.0, origin, angle)
        blocks = group_lines_into_blocks(
            [
                _line("第一行文字比较长一些", origin[0], origin[1], width=300, height=20, angle=angle),
                _line("第二行", second[0], second[1], width=100, height=20, angle=angle),
            ]
        )

        assert len(blocks) == 1
        block = blocks[0]
        assert block.angle == pytest.approx(angle)
        assert block.height == pytest.approx(44)
        assert block.width == pytest.approx(300)
        assert (block.left, block.top) == pytest.approx(origin)

    def test_tilts_that_disagree_do_not_merge(self):
        blocks = group_lines_into_blocks(
            [
                _line("第一行文字比较长一些", 100, 100, width=300, angle=8.0),
                _line("第二行", 100, 124, width=100, angle=0.0),
            ]
        )

        assert len(blocks) == 2

    def test_a_jittery_tilt_counts_as_upright(self):
        block = group_lines_into_blocks([_line("一行", 10, 10, angle=0.4)])[0]

        assert block.angle == 0.0

    def test_frame_round_trip(self):
        origin = (50.0, 80.0)
        for x, y in ((0.0, 0.0), (120.0, -30.0), (-7.5, 42.0)):
            along, across = to_frame(x, y, origin, 17.0)

            assert from_frame(along, across, origin, 17.0) == pytest.approx((x, y))

    def test_box_corners_follow_the_tilt(self):
        corners = box_corners(0.0, 0.0, 100.0, 10.0, 90.0)

        # A quarter turn clockwise: the width runs straight down the screen.
        assert corners[1] == pytest.approx((0.0, 100.0), abs=1e-9)
        assert corners[3] == pytest.approx((-10.0, 0.0), abs=1e-9)


class TestFit:
    def test_short_text_keeps_the_source_line_size(self):
        plate = fit_plate("你好", 10, 10, 300, 30, 30, _measure)

        padding = max(3, int(round(30 * 0.12)))
        assert plate.font_px == int(round(30 * 0.82))
        assert plate.rows == ("你好",)
        # The plate wears a small margin around the source rectangle.
        assert (plate.x, plate.y) == (10 - padding, 10 - padding)
        assert plate.height == 30 + 2 * padding

    def test_long_text_shrinks_to_fit_the_block(self):
        text = "这是一段很长的译文，比原文长出许多，需要缩小字号才能放进原来的方框里面去。" * 3
        # No room below the block, so the text has to shrink rather than grow.
        plate = fit_plate(text, 0, 0, 300, 60, 30, _measure, max_height=60)

        padding = max(3, int(round(30 * 0.12)))
        assert plate.font_px < int(round(30 * 0.82))
        assert plate.height <= 60 + 2 * padding

    def test_text_that_cannot_fit_is_cut_with_an_ellipsis(self):
        text = "字" * 400
        plate = fit_plate(text, 0, 0, 120, 30, 30, _measure, max_height=40)

        assert plate.font_px == MIN_BLOCK_FONT_PX
        assert plate.rows[-1].endswith("…")
        assert plate.height <= 48

    def test_empty_text_gives_no_plate(self):
        assert fit_plate("  ", 0, 0, 10, 10, 10, _measure) is None


class TestLayout:
    def test_plates_sit_on_their_blocks_and_never_overlap_the_next(self):
        lines = [
            PlacedLine("a", "第一段译文，稍微长一些以便看到换行的效果", 100, 100, 300, 40, line_height=20),
            PlacedLine("b", "第二段", 100, 150, 300, 20, line_height=20),
        ]

        plates = layout_block_plates(lines, (1000, 500), (1000, 500), _measure)

        assert len(plates) == 2
        first, second = plates
        padding = max(3, int(round(20 * 0.12)))
        assert first.y == 100 - padding and second.y == 150 - padding
        assert first.y + first.height <= second.y
        assert first.angle == 0.0

    def test_scaling_from_frame_to_target(self):
        lines = [PlacedLine("a", "译文", 200, 100, 200, 40, line_height=40)]

        plate = layout_block_plates(lines, (2000, 1000), (1000, 500), _measure)[0]

        padding = max(3, int(round(20 * 0.12)))
        assert (plate.x, plate.y, plate.width) == (100 - padding, 50 - padding, 100 + 2 * padding)
        assert plate.font_px == int(round(20 * 0.82))

    def test_a_tilted_block_gets_a_tilted_plate_anchored_at_its_corner(self):
        lines = [PlacedLine("a", "译文", 200, 100, 200, 40, line_height=40, angle=10.0)]

        plate = layout_block_plates(lines, (1000, 500), (1000, 500), _measure)[0]

        assert plate.angle == pytest.approx(10.0)
        padding = max(3, int(round(40 * 0.12)))
        expected = from_frame(-padding, -padding, (200.0, 100.0), 10.0)
        assert (plate.x, plate.y) == (int(round(expected[0])), int(round(expected[1])))
        assert plate.width == 200 + 2 * padding
