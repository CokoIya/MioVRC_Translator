"""Cropping keeps frame coordinates; the frame size falls back to the screen."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtGui import QColor, QImage

from src.core import screen_capture
from src.core.screen_capture import Capture, crop_capture


def _png(width: int, height: int) -> bytes:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(20, 40, 60))
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(storage.data())


def _capture(width=400, height=200, **kwargs) -> Capture:
    return Capture(
        png=_png(width, height),
        width=width,
        height=height,
        source="vrchat",
        frame_width=width,
        frame_height=height,
        **kwargs,
    )


class TestCrop:
    def test_a_region_becomes_a_smaller_capture_with_an_offset(self, qapp):
        cropped = crop_capture(_capture(), (0.25, 0.5, 0.75, 1.0))

        assert cropped.width == 200
        assert cropped.height == 100
        assert cropped.offset == (100, 100)
        # The frame it belongs to is unchanged, so lines can be lifted back.
        assert cropped.frame_size == (400, 200)
        assert cropped.source == "vrchat"
        image = QImage.fromData(cropped.png, "PNG")
        assert (image.width(), image.height()) == (200, 100)

    def test_a_click_sized_region_means_the_whole_capture(self, qapp):
        capture = _capture()

        assert crop_capture(capture, (0.5, 0.5, 0.501, 0.501)) is capture

    def test_no_region_means_the_whole_capture(self, qapp):
        capture = _capture()

        assert crop_capture(capture, None) is capture
        assert crop_capture(capture, (0.1, 0.2)) is capture

    def test_a_crop_of_a_crop_is_measured_from_the_frame(self, qapp):
        first = crop_capture(_capture(), (0.5, 0.0, 1.0, 1.0))
        second = crop_capture(first, (0.75, 0.0, 1.0, 0.5))

        assert second.offset == (300, 0)
        assert second.width == 100
        assert second.height == 100

    def test_regions_are_clamped_to_the_frame(self, qapp):
        cropped = crop_capture(_capture(), (-1.0, -1.0, 0.5, 2.0))

        assert cropped.offset == (0, 0)
        assert cropped.width == 200
        assert cropped.height == 200

    def test_an_empty_capture_is_returned_untouched(self, qapp):
        empty = Capture(b"", 0, 0, "vrchat")

        assert crop_capture(empty, (0, 0, 1, 1)) is empty


class TestFrameSize:
    def test_without_vrchat_the_primary_screen_sets_the_aspect(self, qapp, monkeypatch):
        monkeypatch.setattr(screen_capture, "find_vrchat_window", lambda: 0)

        width, height = screen_capture.vrchat_frame_size()

        assert width > 0
        assert height > 0


def test_frame_size_defaults_to_the_capture_itself():
    capture = Capture(b"x", 640, 360, "screen")

    assert capture.frame_size == (640, 360)
    assert capture.ok
