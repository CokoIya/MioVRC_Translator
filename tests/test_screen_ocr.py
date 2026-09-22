"""Screenshot OCR: text repair, language matching, and failure handling."""

from __future__ import annotations

import math

import pytest

from src.core.screen_ocr import (
    OcrLine,
    OcrResult,
    normalize_ocr_language,
    normalize_recognized_text,
    pick_ocr_language,
    recognize_png,
)


class TestRecognizedTextRepair:
    """The CJK recognizers space every character; a translator must not see it."""

    @pytest.mark.parametrize(
        ("recognized", "expected"),
        [
            ("こ ん に ち は", "こんにちは"),
            ("你 好 世 界", "你好世界"),
            ("안 녕 하 세 요", "안녕하세요"),
            ("こ ん に ち は ! は じ め ま し て", "こんにちは!はじめまして"),
            ("フ レ ン ド 申 請 し て も い い で す か ?", "フレンド申請してもいいですか?"),
            (
                "イ ベ ン ト は 今 週 の 土 曜 日 20 : 00 か ら",
                "イベントは今週の土曜日 20:00 から",
            ),
        ],
    )
    def test_character_spacing_is_removed(self, recognized, expected):
        assert normalize_recognized_text(recognized) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Hello world from VRChat",
            "Join the event at 8 pm",
        ],
    )
    def test_latin_words_keep_their_spaces(self, text):
        """Collapsing these would turn a sentence into one long word."""

        assert normalize_recognized_text(text) == text

    def test_a_latin_word_beside_kana_keeps_its_boundary(self):
        assert normalize_recognized_text("Mio さ ん こ ん に ち は") == "Mio さんこんにちは"

    @pytest.mark.parametrize("value", ["", "   ", "\t", None])
    def test_blank_input_yields_nothing(self, value):
        assert normalize_recognized_text(value) == ""

    def test_runs_of_whitespace_collapse(self):
        assert normalize_recognized_text("  Hello    world  ") == "Hello world"


class TestLanguageSelection:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("zh", "zh-Hans-CN"),
            ("zh-CN", "zh-Hans-CN"),
            ("jp", "ja"),
            ("japanese", "ja"),
            ("en", "en-US"),
            ("ja", "ja"),
        ],
    )
    def test_translation_codes_map_to_recognizer_tags(self, code, expected):
        assert normalize_ocr_language(code) == expected

    def test_an_exact_match_wins(self):
        assert pick_ocr_language("ja", ["en-US", "ja", "zh-Hans-CN"]) == "ja"

    def test_a_prefix_match_is_accepted(self):
        """"ja" should still find "ja-JP" when that is what is installed."""

        assert pick_ocr_language("ja", ["en-US", "ja-JP"]) == "ja-JP"

    def test_an_uninstalled_language_falls_back_rather_than_failing(self):
        assert pick_ocr_language("ko", ["ja", "zh-Hans-CN"]) == "ja"

    def test_no_recognizer_installed_returns_empty(self):
        assert pick_ocr_language("ja", []) == ""


class TestFailureHandling:
    """A screenshot that cannot be read must not raise into the caller."""

    def test_an_empty_image_is_reported_not_raised(self):
        result = recognize_png(b"", "ja")

        assert result.ok is False
        assert result.error == "empty_image"

    def test_a_missing_language_is_reported(self):
        result = recognize_png(b"not-a-png", "")

        assert result.ok is False
        assert result.error == "language_unavailable"

    def test_corrupt_image_data_is_reported(self):
        result = recognize_png(b"definitely not a png", "ja")

        assert result.ok is False
        assert result.error.startswith("ocr_failed")


class TestResultShape:
    def test_lines_join_into_translatable_text(self):
        result = OcrResult(
            lines=[
                OcrLine("こんにちは", 10.0, 20.0, 100.0, 30.0),
                OcrLine("はじめまして", 10.0, 60.0, 120.0, 30.0),
            ]
        )

        assert result.ok is True
        assert result.text == "こんにちは\nはじめまして"

    def test_a_line_knows_its_far_edges(self):
        line = OcrLine("x", 10.0, 20.0, 100.0, 30.0)

        assert line.right == 110.0
        assert line.bottom == 50.0


class TestActionManifest:
    """SteamVR will not bind a button without a well-formed manifest."""

    def test_the_manifest_and_its_bindings_are_valid_json(self):
        import json
        from pathlib import Path

        from src.core.vr_input import manifest_directory

        directory = Path(manifest_directory())
        manifest = json.loads((directory / "action_manifest.json").read_text("utf-8"))

        assert manifest["action_sets"][0]["name"] == "/actions/mio"
        actions = {action["name"] for action in manifest["actions"]}
        assert "/actions/mio/in/translate_screen" in actions

        for entry in manifest["default_bindings"]:
            binding_file = directory / entry["binding_url"]
            assert binding_file.is_file(), entry["binding_url"]
            binding = json.loads(binding_file.read_text("utf-8"))
            assert "/actions/mio" in binding["bindings"]

    def test_every_binding_targets_the_declared_action(self):
        """A binding pointing at a name the manifest lacks silently does nothing."""

        import json
        from pathlib import Path

        from src.core.vr_input import (
            HOLD_GRIP_ACTION,
            HOLD_TRIGGER_ACTION,
            TRANSLATE_ACTION,
            manifest_directory,
        )

        directory = Path(manifest_directory())
        manifest = json.loads((directory / "action_manifest.json").read_text("utf-8"))
        declared = {action["name"] for action in manifest["actions"]}
        assert {TRANSLATE_ACTION, HOLD_TRIGGER_ACTION, HOLD_GRIP_ACTION} <= declared
        for binding_file in directory.glob("binding_*.json"):
            binding = json.loads(binding_file.read_text("utf-8"))
            outputs = {
                entry["output"]
                for source in binding["bindings"]["/actions/mio"]["sources"]
                for entry in source["inputs"].values()
            }
            assert outputs <= declared, binding_file.name
            # The button itself must be bound everywhere; the chord halves
            # may be absent on a controller that lacks the input.
            assert TRANSLATE_ACTION in outputs, binding_file.name
            poses = {
                entry["output"] for entry in binding["bindings"]["/actions/mio"].get("poses", [])
            }
            assert poses <= declared, binding_file.name

    def test_the_manifest_ships_with_the_app(self):
        import os

        from src.core.vr_input import manifest_path

        assert os.path.isfile(manifest_path())


class TestUnrotate:
    """The recognizer reports boxes in a straightened frame; map them back."""

    def test_zero_angle_is_the_identity(self):
        from src.core.screen_ocr import unrotate_rect

        assert unrotate_rect(10, 20, 30, 40, 0.0, (100, 100)) == (10, 20, 30, 40)

    def test_a_tilt_moves_a_box_around_the_image_centre(self):
        from src.core.screen_ocr import unrotate_rect

        # A box far above-left of the centre, rotated clockwise by 90 degrees,
        # ends up above-right: (x, y) -> (cx - (y - cy), cy + (x - cx)).
        left, top, width, height = unrotate_rect(0, 0, 10, 10, 90.0, (100, 100))

        assert (round(left), round(top)) == (190, 0)
        assert (round(width), round(height)) == (10, 10)

    def test_a_small_tilt_shifts_boxes_the_way_the_live_mirror_did(self):
        """Measured on a live VR mirror: 5.9 degrees put boxes a line too low."""

        from src.core.screen_ocr import unrotate_rect

        left, top, _w, _h = unrotate_rect(555, 385, 167, 37, 5.9, (1280, 755.5))

        # The straightened box sat below-left of the glyphs; the mapped one
        # moves up and to the right, back onto them.
        assert left > 555
        assert top < 385

    def test_the_box_stays_axis_aligned_and_covers_the_rotated_corners(self):
        from src.core.screen_ocr import unrotate_rect

        left, top, width, height = unrotate_rect(100, 100, 200, 20, 45.0, (0, 0))

        assert width > 200 * 0.7
        assert height > 20
        assert width == pytest.approx((200 + 20) * math.cos(math.radians(45)), rel=0.01)


class TestOrientedBoxes:
    """Lines keep their own tilt instead of an upright box drawn around it."""

    def test_an_upright_quad_is_an_ordinary_rectangle(self):
        from src.core.screen_ocr import oriented_box_from_quad

        assert oriented_box_from_quad([(10, 20), (110, 20), (110, 50), (10, 50)]) == (10, 20, 100, 30, 0)

    def test_a_tilted_quad_keeps_its_true_height(self):
        from src.core.screen_ocr import oriented_box_from_quad

        # A 400 px line descending ten degrees to the right, 30 px tall.
        c, s = math.cos(math.radians(10)), math.sin(math.radians(10))
        quad = [(0, 0), (400 * c, 400 * s), (400 * c - 30 * s, 400 * s + 30 * c), (-30 * s, 30 * c)]

        left, top, width, height, angle = oriented_box_from_quad(quad)

        assert (left, top) == (0, 0)
        assert width == pytest.approx(400)
        assert height == pytest.approx(30)
        assert angle == pytest.approx(10)
        # The upright box around it would be taller by the line's whole rise.
        assert height < 400 * s + 30 * c

    def test_a_deskewed_rectangle_becomes_an_oriented_box(self):
        from src.core.screen_ocr import oriented_from_deskewed, unrotate_rect

        left, top, width, height, angle = oriented_from_deskewed(555, 385, 167, 37, 5.9, (1280, 755.5))
        upright = unrotate_rect(555, 385, 167, 37, 5.9, (1280, 755.5))

        # The same top corner as the upright mapping, but the box keeps its
        # own size and remembers the tilt.
        assert top == pytest.approx(upright[1])
        assert left > upright[0]
        assert (width, height, angle) == (167, 37, 5.9)

    def test_zero_tilt_is_the_identity(self):
        from src.core.screen_ocr import oriented_from_deskewed

        assert oriented_from_deskewed(1, 2, 3, 4, 0.0, (9, 9)) == (1, 2, 3, 4, 0.0)
