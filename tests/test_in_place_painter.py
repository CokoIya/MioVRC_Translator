"""The painter draws every plate where its line is, tilted ones included."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from src.core.in_place_layout import Plate
from src.ui_qt.in_place_painter import LABEL_FONT_FAMILIES, _font, paint_plates


def _paint(plates) -> QImage:
    image = QImage(400, 400, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_plates(painter, plates, plate_opacity=1.0)
    finally:
        painter.end()
    return image


class TestPainter:
    def test_an_upright_plate_covers_its_rectangle(self, qapp):
        image = _paint([Plate(100, 100, 200, 30, 16, ("abc",))])

        assert image.pixelColor(200, 115).alpha() > 200
        assert image.pixelColor(320, 115).alpha() == 0

    def test_a_tilted_plate_is_drawn_along_its_line(self, qapp):
        image = _paint([Plate(100, 100, 200, 30, 16, ("abc",), angle=45.0)])

        # A point 99 px along the plate's own x axis and 15 px down it lies
        # at (160, 181) on the screen once the plate is turned 45 degrees.
        assert image.pixelColor(160, 181).alpha() > 200
        # Where an upright plate would have been there is nothing.
        assert image.pixelColor(290, 110).alpha() == 0

    def test_the_label_font_is_a_plain_face_not_the_apps_handwriting(self, qapp):
        font = _font(20)

        assert font.families() == list(LABEL_FONT_FAMILIES)
        assert font.pixelSize() == 20
        assert LABEL_FONT_FAMILIES[0] == "SimSun"


class TestSelectionFrame:
    def test_the_pointer_box_is_drawn_in_green_when_nothing_is_dragged(self, qapp):
        from src.ui_qt.in_place_painter import POINTER_BOX_COLOR, render_selection_frame_image

        image = render_selection_frame_image((400, 400), None, box=(0.25, 0.25, 0.75, 0.75))

        # On the box's border: green. Well inside it: only the faint tints.
        edge = image.pixelColor(100, 200)
        assert edge.green() == POINTER_BOX_COLOR[1] and edge.red() == POINTER_BOX_COLOR[0]
        inside = image.pixelColor(200, 200)
        assert inside.alpha() < 120

    def test_a_dragged_region_hides_the_pointer_box(self, qapp):
        from src.ui_qt.in_place_painter import POINTER_BOX_COLOR, render_selection_frame_image

        image = render_selection_frame_image(
            (400, 400), (0.1, 0.1, 0.2, 0.2), box=(0.25, 0.25, 0.75, 0.75)
        )

        edge = image.pixelColor(100, 200)
        assert (edge.red(), edge.green()) != (POINTER_BOX_COLOR[0], POINTER_BOX_COLOR[1])
