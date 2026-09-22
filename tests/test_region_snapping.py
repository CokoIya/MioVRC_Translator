"""A box read comes out as whole lines.

The pointer box and a drawn rectangle are placed by the laser, not by the
text, so their edges slice through lines: the player saw half a line at
the top of the card and garbage at the bottom. The box is now read with
padding and the picture cut back to the lines whose middle lay inside it.
"""

from __future__ import annotations

import time
import types

import pytest
from PySide6.QtGui import QColor, QImage

from src.core.screen_capture import Capture, crop_capture, encode_png
from src.core.screen_ocr import OcrLine
from src.core.screenshot_translation import (
    ScreenshotTranslator,
    lines_extent,
    lines_in_target,
    padded_region,
)


def _line(text, left, top, width, height, angle=0.0):
    return OcrLine(text=text, left=left, top=top, width=width, height=height, angle=angle)


class TestPadding:
    def test_the_box_grows_by_a_share_of_its_own_size(self):
        assert padded_region((0.25, 0.25, 0.75, 0.75)) == pytest.approx((0.21, 0.125, 0.79, 0.875))

    def test_padding_stops_at_the_frame_edge(self):
        u0, v0, u1, v1 = padded_region((0.0, 0.9, 0.5, 1.0))

        assert (u0, v1) == (0.0, 1.0)
        assert v0 == pytest.approx(0.875)

    def test_a_backwards_box_is_the_same_box(self):
        assert padded_region((0.75, 0.75, 0.25, 0.25)) == padded_region((0.25, 0.25, 0.75, 0.75))


class TestLineChoice:
    def test_lines_are_kept_by_where_their_middle_is(self):
        inside = _line("こんにちは世界", 20, 60, 150, 20)
        sliced = _line("さようなら世界", 20, 200, 150, 20)

        kept = lines_in_target([inside, sliced], (16, 37, 216, 187))

        assert kept == [inside]

    def test_a_line_without_a_box_is_kept(self):
        bare = types.SimpleNamespace(text="こんにちは")

        assert lines_in_target([bare], (0, 0, 10, 10)) == [bare]

    def test_the_extent_wraps_the_lines_with_a_little_air(self):
        lines = [_line("一行目です", 20, 60, 150, 20), _line("二行目です", 30, 90, 100, 20)]

        assert lines_extent(lines, (232, 224)) == (13, 53, 177, 117)

    def test_the_extent_is_clamped_to_the_picture(self):
        assert lines_extent([_line("端の行です", 0, 0, 150, 20)], (100, 10)) == (0, 0, 100, 10)

    def test_no_boxes_means_no_extent(self):
        assert lines_extent([types.SimpleNamespace(text="x")], (100, 100)) is None

    def test_a_tilted_line_is_judged_by_its_upright_box(self):
        tilted = _line("斜めの行です", 100, 100, 100, 20, angle=45.0)

        assert lines_in_target([tilted], (100, 100, 200, 200)) == [tilted]


def _wait(results, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if results:
            return results[0]
        time.sleep(0.02)
    raise AssertionError("the pipeline never reported a result")


class TestBoxRead:
    def test_the_picture_shrinks_to_the_lines_the_box_was_on(self, qapp):
        frame = QImage(400, 300, QImage.Format.Format_RGBA8888)
        frame.fill(QColor(255, 255, 255))
        capture = Capture(png=encode_png(frame), width=400, height=300, source="test")
        seen: list = []

        def recognize(png, language):
            image = QImage.fromData(png, "PNG")
            seen.append((image.width(), image.height()))
            return types.SimpleNamespace(
                lines=[
                    _line("こんにちは世界", 20, 60, 150, 20),
                    _line("さようなら世界", 20, 200, 150, 20),
                ],
                error="",
            )

        results: list = []
        translator = ScreenshotTranslator(
            capture=lambda: capture,
            recognize=recognize,
            translate=lambda text: "hello world",
            ocr_language=lambda: "ja",
            crop=crop_capture,
            on_result=results.append,
        )

        assert translator.trigger((0.25, 0.25, 0.75, 0.75)) is True
        result = _wait(results)

        # Read with padding: the box was 200x150, the crop 232x224.
        assert seen == [(232, 224)]
        assert result.ok
        assert [original for original, _ in result.pairs] == ["こんにちは世界"]
        # Cut back to the one line plus its air, placed in frame pixels.
        assert result.crop_size == (164, 34)
        assert result.offset == (97, 91)
        picture = QImage.fromData(result.png, "PNG")
        assert (picture.width(), picture.height()) == (164, 34)
        assert result.lines[0].left == pytest.approx(104)
        assert result.lines[0].top == pytest.approx(98)

    def test_a_box_that_missed_every_line_keeps_what_the_padding_caught(self, qapp):
        frame = QImage(400, 300, QImage.Format.Format_RGBA8888)
        frame.fill(QColor(255, 255, 255))
        capture = Capture(png=encode_png(frame), width=400, height=300, source="test")
        results: list = []
        translator = ScreenshotTranslator(
            capture=lambda: capture,
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[_line("さようなら世界", 20, 200, 150, 20)], error=""
            ),
            translate=lambda text: "goodbye world",
            ocr_language=lambda: "ja",
            crop=crop_capture,
            on_result=results.append,
        )

        translator.trigger((0.25, 0.25, 0.75, 0.75))
        result = _wait(results)

        assert result.ok
        assert result.crop_size == (232, 224)
        assert result.offset == (84, 38)
