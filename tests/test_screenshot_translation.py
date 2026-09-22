"""One button press: capture, read, translate, show - and its failure paths."""

from __future__ import annotations

import threading
import time
import types

import pytest

from src.core.screenshot_translation import (
    MIN_LINE_CHARACTERS,
    ScreenshotTranslation,
    ScreenshotTranslator,
    padded_region,
    worthwhile_lines,
)


def _line(text):
    return types.SimpleNamespace(text=text)


def _wait(results, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if results:
            return results[0]
        time.sleep(0.02)
    raise AssertionError("the pipeline never reported a result")


def _translator(**overrides):
    defaults = dict(
        capture=lambda: types.SimpleNamespace(png=b"png-bytes", source="vrchat"),
        recognize=lambda png, language: types.SimpleNamespace(
            lines=[_line("こんにちは"), _line("ありがとう")], error=""
        ),
        translate=lambda text: {"こんにちは": "你好", "ありがとう": "谢谢"}.get(text, ""),
        ocr_language=lambda: "ja",
    )
    defaults.update(overrides)
    return ScreenshotTranslator(**defaults)


class TestLineSelection:
    """Whole-screen recognition returns dozens of fragments; most are noise."""

    def test_stray_glyphs_are_dropped(self):
        lines = [_line("a"), _line(""), _line("こんにちは")]

        assert worthwhile_lines(lines) == ["こんにちは"]

    def test_duplicates_are_not_translated_twice(self):
        lines = [_line("こんにちは"), _line("こんにちは")]

        assert worthwhile_lines(lines) == ["こんにちは"]

    def test_the_longest_lines_win_the_budget(self):
        lines = [_line("短い"), _line("これはとても長い文章です"), _line("中くらいの文")]

        assert worthwhile_lines(lines, limit=2) == [
            "これはとても長い文章です",
            "中くらいの文",
        ]

    def test_the_limit_is_honoured(self):
        lines = [_line(f"文章{index}0000") for index in range(20)]

        assert len(worthwhile_lines(lines, limit=3)) == 3

    def test_the_minimum_length_is_a_real_threshold(self):
        assert worthwhile_lines([_line("x" * (MIN_LINE_CHARACTERS - 1))]) == []
        assert worthwhile_lines([_line("x" * MIN_LINE_CHARACTERS)]) != []


class TestPipeline:
    def test_a_press_runs_every_stage_in_order(self):
        results: list[ScreenshotTranslation] = []
        status: list[str] = []
        translator = _translator(on_status=status.append, on_result=results.append)

        assert translator.trigger() is True
        result = _wait(results)

        assert status == ["capturing", "reading", "translating"]
        assert result.ok is True
        assert result.pairs == [("こんにちは", "你好"), ("ありがとう", "谢谢")]
        assert result.source == "vrchat"

    def test_a_line_that_will_not_translate_still_shows_its_original(self):
        """Dropping it silently would look like the OCR missed the text."""

        results: list[ScreenshotTranslation] = []
        translator = _translator(
            translate=lambda text: "" if text == "ありがとう" else "你好",
            on_result=results.append,
        )
        translator.trigger()
        result = _wait(results)

        assert ("ありがとう", "ありがとう") in result.pairs

    def test_a_translator_that_raises_is_reported_not_papered_over(self):
        """Showing the original as if it were a translation misled players;
        a failed provider is now an error with a reason."""

        results: list[ScreenshotTranslation] = []

        def explode(text):
            raise RuntimeError("provider down")

        translator = _translator(translate=explode, on_result=results.append)
        translator.trigger()
        result = _wait(results)

        assert result.ok is False
        assert result.error == "translation_failed"
        assert result.error_kind == "unavailable"
        assert result.pairs == []

    def test_an_empty_capture_is_reported(self):
        results: list[ScreenshotTranslation] = []
        translator = _translator(
            capture=lambda: types.SimpleNamespace(png=b"", source="screen"),
            on_result=results.append,
        )
        translator.trigger()

        assert _wait(results).error == "capture_failed"

    def test_a_capture_that_raises_is_reported(self):
        results: list[ScreenshotTranslation] = []

        def explode():
            raise RuntimeError("no screen")

        translator = _translator(capture=explode, on_result=results.append)
        translator.trigger()

        assert _wait(results).error == "capture_failed"

    def test_an_ocr_error_is_passed_through(self):
        results: list[ScreenshotTranslation] = []
        translator = _translator(
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[], error="language_unavailable"
            ),
            on_result=results.append,
        )
        translator.trigger()

        assert _wait(results).error == "language_unavailable"

    def test_a_screen_with_no_readable_text_says_so(self):
        results: list[ScreenshotTranslation] = []
        translator = _translator(
            recognize=lambda png, language: types.SimpleNamespace(lines=[], error=""),
            on_result=results.append,
        )
        translator.trigger()

        assert _wait(results).error == "no_text_found"

    def test_a_failure_releases_the_lock_for_the_next_press(self):
        results: list[ScreenshotTranslation] = []
        translator = _translator(
            capture=lambda: types.SimpleNamespace(png=b"", source="screen"),
            on_result=results.append,
        )

        assert translator.trigger() is True
        _wait(results)
        results.clear()
        assert translator.trigger() is True
        assert _wait(results).error == "capture_failed"


class TestConcurrency:
    def test_a_second_press_mid_run_is_ignored_not_queued(self):
        """Holding the button must not build a backlog to watch drain."""

        started = threading.Event()
        release = threading.Event()
        results: list[ScreenshotTranslation] = []

        def slow_recognize(png, language):
            started.set()
            release.wait(5.0)
            return types.SimpleNamespace(lines=[_line("こんにちは")], error="")

        translator = _translator(recognize=slow_recognize, on_result=results.append)

        assert translator.trigger() is True
        assert started.wait(5.0)
        assert translator.trigger() is False

        release.set()
        _wait(results)
        assert len(results) == 1

    def test_the_lock_is_free_again_once_a_run_finishes(self):
        results: list[ScreenshotTranslation] = []
        translator = _translator(on_result=results.append)

        translator.trigger()
        _wait(results)

        assert translator.running is False
        assert translator.trigger() is True


@pytest.mark.parametrize(
    ("pairs", "error", "expected"),
    [
        ([("a", "b")], "", True),
        ([], "", False),
        ([("a", "b")], "boom", False),
    ],
)
def test_result_ok_requires_content_and_no_error(pairs, error, expected):
    assert ScreenshotTranslation(pairs=pairs, error=error).ok is expected


class TestGeometry:
    """Every line keeps its box, lifted into whole-frame coordinates."""

    def _boxed(self, text, left, top, width=120.0, height=30.0):
        return types.SimpleNamespace(text=text, left=left, top=top, width=width, height=height)

    def _capture(self, **overrides):
        fields = dict(
            png=b"png-bytes",
            source="vrchat",
            frame_size=(1000, 500),
            origin=(10, 20),
            offset=(0, 0),
            scale=1.0,
        )
        fields.update(overrides)
        return types.SimpleNamespace(**fields)

    def test_lines_carry_their_boxes_and_the_frame(self):
        results = []
        translator = _translator(
            capture=lambda: self._capture(),
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("こんにちは", 100, 200)], error=""
            ),
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.placeable
        assert result.frame_size == (1000, 500)
        assert result.origin == (10, 20)
        line = result.lines[0]
        assert (line.original, line.translated) == ("こんにちは", "你好")
        assert (line.left, line.top, line.width, line.height) == (100, 200, 120, 30)

    def test_a_cropped_capture_lifts_lines_back_into_the_frame(self):
        results = []
        crops = []

        def crop(capture, region):
            crops.append(region)
            return self._capture(offset=(300, 150))

        translator = _translator(
            capture=lambda: self._capture(),
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("ありがとう", 10, 20)], error=""
            ),
            crop=crop,
            on_result=results.append,
        )

        translator.trigger(region=(0.3, 0.3, 0.8, 0.8))
        result = _wait(results)

        # The box is read with padding so its edges slice no line; the
        # result still names the box the player drew.
        assert crops == [pytest.approx(padded_region((0.3, 0.3, 0.8, 0.8)))]
        assert result.region == (0.3, 0.3, 0.8, 0.8)
        assert (result.lines[0].left, result.lines[0].top) == (310, 170)

    def test_the_head_pose_is_taken_with_the_capture(self):
        results = []
        anchor = object()
        translator = _translator(
            capture=lambda: self._capture(),
            snapshot_anchor=lambda: anchor,
            on_result=results.append,
        )

        translator.trigger()

        assert _wait(results).anchor is anchor

    def test_the_head_pose_is_read_before_the_frame(self):
        # The compositor's frame matches the pose the head has now; reading
        # and encoding the frame takes long enough that a pose taken after
        # it would belong to a head that has already turned away.
        results = []
        order = []

        def capture():
            order.append("capture")
            return self._capture()

        def snapshot():
            order.append("pose")
            return object()

        translator = _translator(capture=capture, snapshot_anchor=snapshot, on_result=results.append)

        translator.trigger()

        _wait(results)
        assert order == ["pose", "capture"]

    def test_a_pose_that_cannot_be_read_does_not_stop_the_run(self):
        results = []

        def boom():
            raise RuntimeError("no headset")

        translator = _translator(
            capture=lambda: self._capture(),
            snapshot_anchor=boom,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.ok
        assert result.anchor is None

    def test_a_selection_gets_a_bigger_line_budget(self):
        from src.core.screenshot_translation import MAX_REGION_LINES, MAX_TRANSLATED_LINES

        # Far enough apart that each line is its own paragraph, and more of
        # them than either budget allows.
        lines = [self._boxed("line %02d" % i, 0, i * 120) for i in range(MAX_REGION_LINES + 10)]
        counts = []
        for region in (None, (0.1, 0.1, 0.9, 0.9)):
            results = []
            translator = _translator(
                capture=lambda: self._capture(),
                recognize=lambda png, language: types.SimpleNamespace(lines=lines, error=""),
                translate=lambda text: text.upper(),
                crop=lambda capture, region: capture,
                on_result=results.append,
            )
            translator.trigger(region=region)
            counts.append(len(_wait(results).lines))

        assert counts == [MAX_TRANSLATED_LINES, MAX_REGION_LINES]

    def test_a_capture_without_geometry_is_not_placeable(self):
        results = []
        translator = _translator(on_result=results.append)

        translator.trigger()
        result = _wait(results)

        assert result.ok
        # The stub capture has no frame; pairs still come through for the
        # hand panel, but nothing can claim a position on screen.
        assert result.frame_size == (0, 0)
        assert not result.placeable


class TestShapeNoise:
    def test_icon_shapes_read_as_characters_are_dropped(self):
        assert worthwhile_lines([_line("口口"), _line("ーー"), _line("| |")]) == []

    def test_real_words_containing_those_characters_survive(self):
        assert worthwhile_lines([_line("入口"), _line("ロボット")]) == ["ロボット", "入口"]


class TestTranslationFailures:
    """A failed provider call is reported in plain words, once per run."""

    def _boxed(self, text, top):
        return types.SimpleNamespace(text=text, left=10.0, top=top, width=100.0, height=20.0)

    def _capture(self):
        return types.SimpleNamespace(png=b"png", source="vrchat", frame_size=(1000, 500), origin=(0, 0), offset=(0, 0), scale=1.0)

    def test_every_line_failing_is_one_error_with_a_kind(self):
        from src.core.screenshot_translation import TranslationUnavailable

        results = []
        calls = []

        def translate(text):
            calls.append(text)
            raise TranslationUnavailable("network", "connection reset")

        translator = _translator(
            capture=lambda: self._capture(),
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("こんにちは", 0), self._boxed("ありがとう", 40)], error=""
            ),
            translate=translate,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.error == "translation_failed"
        assert result.error_kind == "network"
        assert result.pairs == []
        assert not result.ok

    def test_a_partial_failure_keeps_the_good_lines_and_notes_the_kind(self):
        results = []

        def translate(text):
            if text == "ありがとう":
                raise RuntimeError("HTTP 429 Too Many Requests")
            return "你好"

        translator = _translator(
            capture=lambda: self._capture(),
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("こんにちは", 0), self._boxed("ありがとう", 40)], error=""
            ),
            translate=translate,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.ok
        assert result.pairs == [("こんにちは", "你好")]
        assert result.error_kind == "quota"

    def test_an_empty_answer_still_shows_the_original(self):
        results = []
        translator = _translator(
            capture=lambda: self._capture(),
            recognize=lambda png, language: types.SimpleNamespace(lines=[self._boxed("Front", 0)], error=""),
            translate=lambda text: "",
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.pairs == [("Front", "Front")]
        assert result.error_kind == ""

    @pytest.mark.parametrize(
        ("message", "kind"),
        [
            ("ConnectTimeout: timed out", "network"),
            ("Name or service not known (dns)", "network"),
            ("401 Unauthorized: invalid api key", "auth"),
            ("HTTP 429 rate limit exceeded", "quota"),
            ("insufficient balance", "quota"),
            ("something odd happened", "unavailable"),
        ],
    )
    def test_provider_errors_are_classified_by_their_wording(self, message, kind):
        from src.core.screenshot_translation import classify_translation_error

        assert classify_translation_error(RuntimeError(message)) == kind


class TestBlocksAndBatches:
    """Blocks are translated together, repeats once, and every copy keeps its plate."""

    @staticmethod
    def _boxed(text, left, top, width=120.0, height=20.0):
        return types.SimpleNamespace(
            text=text, left=float(left), top=float(top), width=float(width), height=float(height), angle=0.0
        )

    @staticmethod
    def _capture():
        return types.SimpleNamespace(png=b"png", source="vr_eye", frame_size=(1000, 2000), offset=(0, 0))

    def test_the_same_words_in_two_places_get_two_blocks(self):
        from src.core.screenshot_translation import worthwhile_blocks

        blocks = worthwhile_blocks([self._boxed("PC/VR", 100, 100), self._boxed("PC/VR", 100, 400)], 10)

        assert [(block.text, block.top) for block in blocks] == [("PC/VR", 100), ("PC/VR", 400)]

    def test_a_second_reading_of_the_same_box_is_one_block(self):
        from src.core.screenshot_translation import worthwhile_blocks

        blocks = worthwhile_blocks([self._boxed("PC/VR", 100, 100), self._boxed("PC/VR", 101, 101)], 10)

        assert len(blocks) == 1

    def test_repeated_text_is_translated_once_and_shown_everywhere(self):
        results = []
        calls = []

        def translate(text):
            calls.append(text)
            return "PC/VR 模式"

        translator = _translator(
            capture=self._capture,
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("PC/VR", 100, 100), self._boxed("PC/VR", 100, 400)], error=""
            ),
            translate=translate,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert calls == ["PC/VR"]
        assert [line.top for line in result.lines] == [100, 400]
        assert result.pairs == [("PC/VR", "PC/VR 模式")] * 2

    def test_blocks_are_translated_a_few_at_a_time_in_order(self):
        from src.core.screenshot_translation import TRANSLATION_WORKERS

        results = []
        gate = threading.Barrier(TRANSLATION_WORKERS, timeout=5.0)

        def translate(text):
            # Every worker must arrive before any leaves: only true when the
            # calls really run side by side.
            gate.wait()
            return f"<{text}>"

        lines = [self._boxed(f"Line {index}", 100, 100 + 200 * index) for index in range(TRANSLATION_WORKERS)]
        translator = _translator(
            capture=self._capture,
            recognize=lambda png, language: types.SimpleNamespace(lines=lines, error=""),
            translate=translate,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.pairs == [(f"Line {index}", f"<Line {index}>") for index in range(TRANSLATION_WORKERS)]

    def test_the_first_failure_on_the_sign_is_the_one_reported(self):
        results = []

        def translate(text):
            if text == "Line 0":
                raise RuntimeError("401 unauthorized")
            if text == "Line 1":
                raise RuntimeError("timed out")
            return "ok"

        lines = [self._boxed(f"Line {index}", 100, 100 + 200 * index) for index in range(3)]
        translator = _translator(
            capture=self._capture,
            recognize=lambda png, language: types.SimpleNamespace(lines=lines, error=""),
            translate=translate,
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.error_kind == "auth"
        assert result.pairs == [("Line 2", "ok")]

    def test_the_result_carries_the_picture_it_read(self):
        results = []
        translator = _translator(
            capture=lambda: types.SimpleNamespace(
                png=b"png-bytes",
                source="vr_eye",
                frame_size=(1000, 2000),
                offset=(40, 60),
                width=300,
                height=200,
            ),
            recognize=lambda png, language: types.SimpleNamespace(
                lines=[self._boxed("Line 0", 100, 100)], error=""
            ),
            translate=lambda text: "ok",
            on_result=results.append,
        )

        translator.trigger()
        result = _wait(results)

        assert result.png == b"png-bytes"
        assert result.offset == (40, 60)
        assert result.crop_size == (300, 200)
