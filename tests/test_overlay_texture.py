"""Overlay pictures go on one fixed-size canvas.

SteamVR keeps an overlay's GL texture at the size of the first upload for the
overlay's whole life (measured with getOverlayImageData): a later picture of
another size was copied into the old store, cropped, or not at all. The
subtitle board grew with every line and so showed only its first, empty
picture. So the uploader draws every picture centred on a canvas that never
changes size and tells its owner how much wider the overlay must be.
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtGui import QColor, QImage

from src.core import overlay_texture
from src.core.overlay_texture import OverlayTextureUploader


class _Overlay:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))

        return record


def _picture(width: int, height: int, color=(255, 0, 0, 255)) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*color))
    return image


@pytest.fixture
def uploader():
    return OverlayTextureUploader(types.SimpleNamespace(), _Overlay(), 7, canvas=(200, 100))


class TestCompose:
    def test_a_smaller_picture_is_centred_on_the_canvas(self, qapp, uploader):
        canvas, drawn = uploader._compose(_picture(100, 50))

        assert (canvas.width(), canvas.height()) == (200, 100)
        assert drawn == (100, 50)
        assert canvas.pixelColor(100, 50).alpha() == 255
        assert canvas.pixelColor(10, 10).alpha() == 0
        assert canvas.pixelColor(49, 50).alpha() == 0
        assert canvas.pixelColor(50, 50).alpha() == 255

    def test_a_picture_that_fills_the_canvas_is_used_as_it_is(self, qapp, uploader):
        picture = _picture(200, 100)

        canvas, drawn = uploader._compose(picture)

        assert canvas is picture
        assert drawn == (200, 100)

    def test_a_bigger_picture_is_scaled_down_to_fit(self, qapp, uploader):
        canvas, drawn = uploader._compose(_picture(400, 100))

        assert (canvas.width(), canvas.height()) == (200, 100)
        assert drawn == (200, 50)

    def test_without_a_canvas_the_first_picture_sets_it(self, qapp):
        up = OverlayTextureUploader(types.SimpleNamespace(), _Overlay(), 7)

        up._compose(_picture(64, 32))
        canvas, drawn = up._compose(_picture(32, 32))

        assert up.canvas == (64, 32)
        assert (canvas.width(), canvas.height()) == (64, 32)
        assert drawn == (32, 32)


class TestUpload:
    def test_the_gl_path_reports_the_width_scale(self, qapp, uploader, monkeypatch):
        uploaded: list = []
        monkeypatch.setattr(uploader, "_upload_gl", lambda image: uploaded.append(image) or True)

        assert uploader.upload_image(_picture(100, 50)) is True

        assert (uploaded[0].width(), uploaded[0].height()) == (200, 100)
        assert uploader.scale == pytest.approx(2.0)
        assert uploader.drawn_size == (100, 50)
        assert uploader.quad_width(0.5) == pytest.approx(1.0)
        assert uploader.mouse_y_is_top_down is True

    def test_the_raw_path_needs_no_scaling(self, qapp, uploader, monkeypatch):
        monkeypatch.setattr(overlay_texture, "_shared_context", lambda: (None, None))

        assert uploader.upload_image(_picture(100, 50)) is True

        names = [c[0] for c in uploader._overlay.calls]
        assert names == ["setOverlayRaw"]
        assert uploader._overlay.calls[0][3:5] == (100, 50)
        assert uploader.scale == pytest.approx(1.0)
        assert uploader.quad_width(0.5) == pytest.approx(0.5)
        assert uploader.mouse_y_is_top_down is False


class TestCanvasSizes:
    def test_the_board_and_hand_panels_fit_their_canvases(self):
        from src.core.steamvr_overlay import HAND_CANVAS, PANEL_CANVAS
        from src.ui_qt.vr_overlay_panel import DEFAULT_PANEL_SIZE, MAX_PANEL_HEIGHT, SCREENSHOT_PANEL_SIZE

        assert DEFAULT_PANEL_SIZE[0] <= PANEL_CANVAS[0]
        assert MAX_PANEL_HEIGHT <= PANEL_CANVAS[1]
        assert SCREENSHOT_PANEL_SIZE[0] <= HAND_CANVAS[0]
        assert MAX_PANEL_HEIGHT <= HAND_CANVAS[1]
