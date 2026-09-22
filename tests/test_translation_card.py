"""The translation card: where it hangs, what it shows, and that Mio never
launches SteamVR to show it."""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest
from PySide6.QtGui import QColor, QImage

from src.core.in_place_layout import (
    MIN_CARD_WIDTH_METERS,
    HeadAnchor,
    PlacedLine,
    card_transform,
    card_width_meters,
    text_extent,
)


def _anchor() -> HeadAnchor:
    return HeadAnchor(
        pose=((1, 0, 0, 0), (0, 1, 0, 1.6), (0, 0, 1, 0)),
        eye_offset=(-0.034, 0.0, 0.0),
        tangents=(-1.28, 1.28, -1.28, 1.28),
    )


class TestCardGeometry:
    def test_the_centre_of_the_frame_hangs_straight_ahead(self):
        rows = card_transform(_anchor(), (0.5, 0.5), 2.0)

        assert rows[0][3] == pytest.approx(-0.034)
        assert rows[1][3] == pytest.approx(1.6)
        assert rows[2][3] == pytest.approx(-2.0)

    def test_the_top_left_of_the_frame_hangs_up_and_to_the_left(self):
        rows = card_transform(_anchor(), (0.0, 0.0), 1.0)

        assert rows[0][3] == pytest.approx(-0.034 - 1.28)
        # The frame's top edge is the tangent OpenVR names "bottom": up.
        assert rows[1][3] == pytest.approx(1.6 + 1.28)

    def test_the_head_pose_carries_the_card(self):
        # Turned a quarter to the left: "ahead" is -x in the world.
        pose = ((0.0, 0, 1.0, 1.0), (0, 1, 0, 1.6), (-1.0, 0, 0.0, 0.0))
        anchor = HeadAnchor(pose=pose, eye_offset=(0, 0, 0), tangents=(-1, 1, -1, 1))

        rows = card_transform(anchor, (0.5, 0.5), 2.0)

        assert rows[0][3] == pytest.approx(1.0 - 2.0)
        assert rows[2][3] == pytest.approx(0.0, abs=1e-9)

    def test_the_card_is_a_little_larger_than_life(self):
        # Half the frame at 2 m: the view is 2.56 * 2 = 5.12 m wide there.
        assert card_width_meters(_anchor(), 0.5, 2.0) == pytest.approx(5.12 * 0.5 * 1.25)

    def test_a_tiny_region_still_gets_a_readable_card(self):
        assert card_width_meters(_anchor(), 0.01, 2.0) == MIN_CARD_WIDTH_METERS

    def test_the_card_never_fills_the_whole_view(self):
        assert card_width_meters(_anchor(), 1.0, 2.0) == pytest.approx(5.12 * 0.85)


class TestPointerBox:
    def test_the_box_is_centred_on_the_laser(self):
        from src.core.in_place_layout import (
            POINTER_BOX_HEIGHT_FRACTION,
            POINTER_BOX_WIDTH_FRACTION,
            pointer_box,
        )

        u0, v0, u1, v1 = pointer_box((0.5, 0.5))

        assert (u0 + u1) / 2 == pytest.approx(0.5)
        assert (v0 + v1) / 2 == pytest.approx(0.5)
        assert u1 - u0 == pytest.approx(POINTER_BOX_WIDTH_FRACTION)
        assert v1 - v0 == pytest.approx(POINTER_BOX_HEIGHT_FRACTION)

    def test_the_box_stays_inside_the_frame_at_the_edges(self):
        from src.core.in_place_layout import pointer_box

        assert pointer_box((0.0, 0.0))[:2] == (0.0, 0.0)
        assert pointer_box((1.0, 1.0))[2:] == (1.0, 1.0)


class TestTextExtent:
    def test_the_extent_wraps_the_lines_with_a_margin(self):
        lines = [PlacedLine("a", "b", 100, 100, 200, 20), PlacedLine("c", "d", 120, 160, 100, 20)]

        x, y, w, h = text_extent(lines, (1000, 1000))

        margin = 20 * 0.8 + 12
        assert (x, y) == (int(100 - margin), int(100 - margin))
        assert x + w == round(300 + margin)
        assert y + h == round(180 + margin)

    def test_the_extent_is_clamped_to_the_picture(self):
        assert text_extent([PlacedLine("a", "b", 5, 5, 990, 990)], (1000, 1000)) == (0, 0, 1000, 1000)

    def test_lines_are_in_frame_pixels_and_the_picture_may_be_offset(self):
        x, y, _w, _h = text_extent([PlacedLine("a", "b", 500, 500, 100, 20)], (400, 400), offset=(400, 400))

        assert (x, y) == (int(100 - 28), int(100 - 28))

    def test_no_lines_means_the_whole_picture(self):
        assert text_extent([], (640, 480)) == (0, 0, 640, 480)


class TestCardPainter:
    def test_the_card_carries_the_picture_and_its_plates(self, qapp):
        from src.ui_qt.in_place_painter import CARD_MIN_WIDTH, render_translation_card

        picture = QImage(200, 100, QImage.Format.Format_RGBA8888)
        picture.fill(QColor(20, 120, 220))
        # The picture sits at (300, 400) in the frame; the line is in frame pixels.
        lines = [PlacedLine("hello", "你好", 320, 430, 80, 20, line_height=20)]

        card = render_translation_card(picture, lines, (300, 400))

        # Enlarged to the readable minimum width, keeping the aspect.
        assert card.width() == CARD_MIN_WIDTH
        assert card.height() == CARD_MIN_WIDTH // 2
        scale = card.width() / 200
        # The picture shows through away from the plate...
        clear = card.pixelColor(int(150 * scale), int(80 * scale))
        assert (clear.red(), clear.green(), clear.blue()) == (20, 120, 220)
        # ...and the plate sits over the line.
        plate = card.pixelColor(int(60 * scale), int(40 * scale))
        assert plate.red() < 40 and plate.green() < 40


class TestHandPicture:
    def test_the_hand_panel_shows_the_picture(self, qapp):
        from src.ui_qt.vr_overlay_panel import VRScreenshotPanel

        panel = VRScreenshotPanel()
        image = QImage(800, 200, QImage.Format.Format_RGBA8888)
        image.fill(QColor(255, 0, 0))

        assert panel.show_picture(image) is True

        rendered = panel.render_image()
        column = rendered.width() // 2
        reds = [rendered.pixelColor(column, y) for y in range(rendered.height())]
        assert any(c.red() > 200 and c.green() < 50 for c in reds)

    def test_a_null_picture_is_refused(self, qapp):
        from src.ui_qt.vr_overlay_panel import VRScreenshotPanel

        assert VRScreenshotPanel().show_picture(QImage()) is False


class _Overlay:
    def __init__(self):
        self.calls: list[tuple] = []
        self.handles = 0

    def createOverlay(self, key, name):
        self.handles += 1
        self.calls.append(("create", key))
        return self.handles

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
            return 0

        return record


class _Matrix:
    def __init__(self):
        self.rows = [[0.0] * 4 for _ in range(3)]

    def __getitem__(self, r):
        return self.rows[r]


class _FakeOpenVR(types.ModuleType):
    TrackingUniverseStanding = 1
    HmdMatrix34_t = _Matrix

    def __init__(self):
        super().__init__("openvr")
        self.overlay = _Overlay()

    def IVROverlay(self):
        return self.overlay


class TestShowCard:
    def test_the_card_is_placed_sized_and_uploaded(self, monkeypatch):
        module = _FakeOpenVR()
        monkeypatch.setitem(sys.modules, "openvr", module)
        from src.core.steamvr_inplace import SteamVRLabelSheet

        sheet = SteamVRLabelSheet()

        assert sheet.show_card(b"rgba", 4, 4, _anchor(), (0.5, 0.5), 0.6, depth=2.0) is True

        calls = module.overlay.calls
        names = [call[0] for call in calls]
        for expected in ("setOverlayTransformAbsolute", "setOverlayWidthInMeters", "setOverlayRaw", "showOverlay"):
            assert expected in names
        width_call = next(call for call in calls if call[0] == "setOverlayWidthInMeters")
        assert width_call[2] == pytest.approx(0.6)
        transform = next(call for call in calls if call[0] == "setOverlayTransformAbsolute")
        assert transform[3].rows[2][3] == pytest.approx(-2.0)
        assert sheet.visible


class TestPlacementConfig:
    def test_the_default_placement_is_the_card_and_legacy_values_survive(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config = {"vrc_listen": {"screenshot_translation": {}}}
        _ensure_vrc_listen_config(config)
        assert config["vrc_listen"]["screenshot_translation"]["placement"] == "card"

        # The world-pinned labels were given up; their old value becomes the card.
        for value, expected in (("hand", "hand"), ("in_place", "card"), ("nonsense", "card")):
            config = {"vrc_listen": {"screenshot_translation": {"placement": value}}}
            _ensure_vrc_listen_config(config)
            assert config["vrc_listen"]["screenshot_translation"]["placement"] == expected


class TestNeverLaunchingSteamVR:
    """Opening Mio must not open SteamVR: PC players leave the headset switch on."""

    def test_when_steamvr_is_not_running_no_overlay_init_is_attempted(self):
        from tests.test_steamvr_overlay import _backend, _fake_openvr

        recorder: list = []
        module = _fake_openvr(recorder)
        module.VRApplication_Background = 3

        def init(kind):
            recorder.append(("init", kind))
            if kind == 3:
                raise RuntimeError("VRInitError_Init_NoServerForBackgroundApp")

        module.init = init
        backend = _backend(recorder)
        with patch.dict(sys.modules, {"openvr": module}):
            assert backend.start() is False

        assert backend.unavailable_reason == "steamvr_not_running"
        assert [call for call in recorder if call[0] == "init"] == [("init", 3)]

    def test_when_steamvr_is_running_the_probe_is_closed_before_the_overlay_opens(self):
        from tests.test_steamvr_overlay import _backend, _fake_openvr

        recorder: list = []
        module = _fake_openvr(recorder)
        module.VRApplication_Background = 3
        backend = _backend(recorder)
        with patch.dict(sys.modules, {"openvr": module}):
            assert backend.start() is True

        steps = [call for call in recorder if call[0] in {"init", "shutdown"}]
        assert steps[:3] == [("init", 3), ("shutdown",), ("init", 20)]
