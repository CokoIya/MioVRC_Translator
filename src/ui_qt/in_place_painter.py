# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Paint translated plates over the text they replace.

Black plate, white text, always: the plates sit on top of arbitrary world
content, and the one combination that stays readable over a bright sign, a
dark corridor and a nameplate alike is the maximum-contrast one. The app theme
does not apply here.

The same painter feeds two surfaces: the headset label sheet (an RGBA texture)
and the desktop overlay window (a widget's paint event). The frame for region
selection is drawn here too, so both surfaces show the same thing.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterable

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from src.core.in_place_layout import Plate, PlacedLine
from src.core.text_blocks import layout_block_plates

# Wider textures cost upload time for no legibility gain: 1600 px across the
# whole view still leaves a 30 px line four pixels per stroke.
MAX_SHEET_WIDTH = 1600
DEFAULT_PLATE_OPACITY = 0.88
PLATE_RADIUS = 6
PLATE_COLOR = (6, 8, 14)
TEXT_COLOR = (255, 255, 255)
FRAME_BORDER_PX = 4
FRAME_TINT_ALPHA = 40
SELECTION_FILL_ALPHA = 36
SELECTION_BORDER_PX = 3


# The app's own face is a handwriting font chosen for its charm; a label laid
# over a sign in a headset does not follow it. The player asked for a fixed
# Song/Ming face (宋体), with the usual system faces behind it for glyphs it
# lacks.
LABEL_FONT_FAMILIES = (
    "SimSun",
    "宋体",
    "NSimSun",
    "MS Mincho",
    "Noto Serif CJK SC",
    "Microsoft YaHei UI",
    "Yu Gothic UI",
    "Malgun Gothic",
    "Segoe UI",
)


def _font(px: int) -> QFont:
    font = QFont()
    font.setFamilies(list(LABEL_FONT_FAMILIES))
    font.setPixelSize(max(1, int(px)))
    # SimSun has no bold face; a synthesised one smears at small sizes, so
    # the plates rely on contrast rather than weight.
    font.setWeight(QFont.Weight.Medium)
    return font


def qt_measure(text: str, font_px: int) -> tuple[float, float]:
    """Width and height of one rendered row, for the layout to reason with."""

    metrics = QFontMetricsF(_font(font_px))
    return (float(metrics.horizontalAdvance(text)), float(metrics.height()))


def plates_for(
    lines: Iterable[PlacedLine],
    frame_size: tuple[int, int],
    target_size: tuple[int, int],
) -> list[Plate]:
    return layout_block_plates(lines, frame_size, target_size, qt_measure)


def sheet_size(frame_size: tuple[int, int], max_width: int = MAX_SHEET_WIDTH) -> tuple[int, int]:
    """The texture size for a frame: same aspect, capped width."""

    frame_w, frame_h = max(1, int(frame_size[0])), max(1, int(frame_size[1]))
    if frame_w <= max_width:
        return (frame_w, frame_h)
    scale = max_width / float(frame_w)
    return (max_width, max(1, int(round(frame_h * scale))))


def paint_plates(
    painter: QPainter,
    plates: Iterable[Plate],
    *,
    plate_opacity: float = DEFAULT_PLATE_OPACITY,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    alpha = int(round(max(0.0, min(1.0, float(plate_opacity))) * 255))
    plate_color = QColor(*PLATE_COLOR, alpha)
    text_color = QColor(*TEXT_COLOR)
    for plate in plates:
        angle = float(getattr(plate, "angle", 0.0) or 0.0)
        rect = QRect(0, 0, plate.width, plate.height)
        painter.save()
        try:
            # A plate lies along its line: its corner is where the text's
            # corner is, and it turns with the text's tilt.
            painter.translate(plate.x, plate.y)
            if angle:
                painter.rotate(angle)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(plate_color)
            painter.drawRoundedRect(rect, PLATE_RADIUS, PLATE_RADIUS)
            painter.setFont(_font(plate.font_px))
            painter.setPen(text_color)
            # Rows are already wrapped to the plate; a paragraph reads from
            # the left, like the sign it covers.
            inset = max(4, int(round(plate.font_px * 0.27)))
            painter.drawText(
                rect.adjusted(inset, 0, -inset, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                plate.text,
            )
        finally:
            painter.restore()


def _to_buffer(image: QImage) -> tuple[ctypes.Array, int, int]:
    raw = bytes(image.constBits())
    # pyopenvr calls byref on the buffer, so it has to be a ctypes object.
    buffer = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
    return buffer, image.width(), image.height()


def render_label_sheet_image(
    lines: Iterable[PlacedLine],
    frame_size: tuple[int, int],
    *,
    max_width: int = MAX_SHEET_WIDTH,
    plate_opacity: float = DEFAULT_PLATE_OPACITY,
) -> QImage:
    """The whole-frame RGBA picture with every plate on it, for the headset."""

    width, height = sheet_size(frame_size, max_width)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_plates(
            painter,
            plates_for(lines, frame_size, (width, height)),
            plate_opacity=plate_opacity,
        )
    finally:
        painter.end()
    return image


def render_label_sheet(
    lines: Iterable[PlacedLine],
    frame_size: tuple[int, int],
    *,
    max_width: int = MAX_SHEET_WIDTH,
    plate_opacity: float = DEFAULT_PLATE_OPACITY,
) -> tuple[ctypes.Array, int, int]:
    """Raw-buffer form of :func:`render_label_sheet_image`."""

    return _to_buffer(
        render_label_sheet_image(
            lines, frame_size, max_width=max_width, plate_opacity=plate_opacity
        )
    )


# The card: the picture that was read, with the plates on it. Scaled up so a
# small sign's text is at least this tall, never wider than the cap.
CARD_MAX_WIDTH = 1600
CARD_MIN_WIDTH = 480
CARD_MIN_LINE_PX = 24
CARD_BORDER_PX = 4
CARD_RADIUS = 18


def _shifted(line, offset: tuple[float, float]) -> PlacedLine:
    """A copy of ``line`` in picture pixels rather than frame pixels."""

    return PlacedLine(
        original=str(getattr(line, "original", "") or ""),
        translated=str(getattr(line, "translated", "") or getattr(line, "text", "") or ""),
        left=float(line.left) - float(offset[0]),
        top=float(line.top) - float(offset[1]),
        width=float(line.width),
        height=float(line.height),
        line_height=float(getattr(line, "line_height", 0.0) or 0.0),
        source_sample=str(getattr(line, "source_sample", "") or ""),
        angle=float(getattr(line, "angle", 0.0) or 0.0),
    )


def card_scale(picture_size: tuple[int, int], lines, *, max_width: int = CARD_MAX_WIDTH) -> float:
    """How much to enlarge the picture so its text reads comfortably."""

    width = max(1, int(picture_size[0]))
    heights = sorted(
        float(getattr(line, "line_height", 0.0) or 0.0) or float(getattr(line, "height", 0.0) or 0.0)
        for line in lines
    )
    heights = [h for h in heights if h > 0]
    scale = 1.0
    if heights:
        scale = max(scale, CARD_MIN_LINE_PX / heights[len(heights) // 2])
    scale = max(scale, CARD_MIN_WIDTH / float(width))
    return min(scale, max_width / float(width))


def render_translation_card(
    picture: QImage,
    lines: Iterable[PlacedLine],
    offset: tuple[float, float] = (0.0, 0.0),
    *,
    plate_opacity: float = DEFAULT_PLATE_OPACITY,
    max_width: int = CARD_MAX_WIDTH,
) -> QImage:
    """The captured picture with the translated plates laid on it.

    ``lines`` are in frame pixels; ``offset`` is where the picture's top-left
    sits in the frame. This is what the headset shows on a card, the way
    VRHandsFrame shows a crop: the labels and the text they cover are one
    picture, so nothing depends on where the head is now.
    """

    lines = list(lines)
    src_w, src_h = max(1, picture.width()), max(1, picture.height())
    scale = card_scale((src_w, src_h), lines, max_width=max_width)
    width = max(1, int(round(src_w * scale)))
    height = max(1, int(round(src_h * scale)))
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, width, height), CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(clip)
        painter.drawImage(QRect(0, 0, width, height), picture)
        plates = layout_block_plates(
            [_shifted(line, offset) for line in lines], (src_w, src_h), (width, height), qt_measure
        )
        paint_plates(painter, plates, plate_opacity=plate_opacity)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 150), CARD_BORDER_PX))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = CARD_BORDER_PX / 2.0
        painter.drawRoundedRect(
            QRectF(half, half, width - CARD_BORDER_PX, height - CARD_BORDER_PX), CARD_RADIUS, CARD_RADIUS
        )
    finally:
        painter.end()
    return image


def render_toast_image(
    frame_size: tuple[int, int],
    lines: "Iterable[str] | str",
    *,
    max_width: int = 1280,
) -> QImage:
    """A few centred lines on an otherwise empty sheet: progress, an error,
    or a short result list. Drawn on the label sheet itself so that no extra
    panel ever appears for screenshot translation.
    """

    rows = [str(lines)] if isinstance(lines, str) else [str(line) for line in lines if str(line).strip()]
    width, height = sheet_size(frame_size, max_width)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    if not rows:
        return image
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font_px = max(18, int(round(height * 0.045)))
        font = _font(font_px)
        metrics = QFontMetricsF(font)
        max_text_w = width * 0.8
        text_w = min(max_text_w, max(metrics.horizontalAdvance(row) for row in rows) + font_px * 1.5)
        row_h = metrics.height() * 1.15
        text_h = row_h * len(rows) + font_px * 0.9
        box = QRectF((width - text_w) / 2.0, height * 0.55 - text_h / 2.0, text_w, text_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*PLATE_COLOR, 220))
        painter.drawRoundedRect(box, PLATE_RADIUS * 2, PLATE_RADIUS * 2)
        painter.setFont(font)
        painter.setPen(QColor(*TEXT_COLOR))
        y = box.top() + font_px * 0.45
        for row in rows:
            rect = QRectF(box.left() + font_px * 0.6, y, box.width() - font_px * 1.2, row_h)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), metrics.elidedText(row, Qt.TextElideMode.ElideRight, int(rect.width())))
            y += row_h
    finally:
        painter.end()
    return image


POINTER_BOX_COLOR = (70, 225, 110)
POINTER_BOX_BORDER_PX = 3
POINTER_BOX_FILL_ALPHA = 22


def paint_selection_frame(
    painter: QPainter,
    size: tuple[int, int],
    region: tuple[float, float, float, float] | None,
    *,
    hint: str = "",
    box: tuple[float, float, float, float] | None = None,
) -> None:
    """The frame border, the tint, the rectangle being dragged, and - when
    nothing is being dragged - the green box that follows the laser."""

    width, height = max(1, int(size[0])), max(1, int(size[1]))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # A faint tint so the player can see where the frame ends.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, FRAME_TINT_ALPHA))
    painter.drawRect(0, 0, width, height)
    pen = QPen(QColor(255, 255, 255, 220))
    pen.setWidth(FRAME_BORDER_PX)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    half = FRAME_BORDER_PX / 2.0
    painter.drawRect(QRectF(half, half, width - FRAME_BORDER_PX, height - FRAME_BORDER_PX))

    if region:
        u0, v0, u1, v1 = region
        rect = QRectF(u0 * width, v0 * height, (u1 - u0) * width, (v1 - v0) * height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(90, 170, 255, SELECTION_FILL_ALPHA))
        painter.drawRect(rect)
        pen = QPen(QColor(90, 170, 255, 255))
        pen.setWidth(SELECTION_BORDER_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
    elif box:
        u0, v0, u1, v1 = box
        rect = QRectF(u0 * width, v0 * height, (u1 - u0) * width, (v1 - v0) * height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*POINTER_BOX_COLOR, POINTER_BOX_FILL_ALPHA))
        painter.drawRect(rect)
        pen = QPen(QColor(*POINTER_BOX_COLOR, 255))
        pen.setWidth(POINTER_BOX_BORDER_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    if hint:
        font_px = max(14, int(round(height * 0.035)))
        painter.setFont(_font(font_px))
        metrics = QFontMetricsF(_font(font_px))
        text_w = metrics.horizontalAdvance(hint) + font_px * 1.5
        text_h = metrics.height() + font_px * 0.8
        box = QRectF((width - text_w) / 2.0, height * 0.06, text_w, text_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*PLATE_COLOR, 200))
        painter.drawRoundedRect(box, PLATE_RADIUS, PLATE_RADIUS)
        painter.setPen(QColor(*TEXT_COLOR))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), hint)


def render_selection_frame_image(
    frame_size: tuple[int, int],
    region: tuple[float, float, float, float] | None,
    *,
    hint: str = "",
    box: tuple[float, float, float, float] | None = None,
    max_width: int = 1280,
) -> QImage:
    """The frame picture for the headset; re-rendered as the laser moves."""

    width, height = sheet_size(frame_size, max_width)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_selection_frame(painter, (width, height), region, hint=hint, box=box)
    finally:
        painter.end()
    return image


def render_selection_frame(
    frame_size: tuple[int, int],
    region: tuple[float, float, float, float] | None,
    *,
    hint: str = "",
    box: tuple[float, float, float, float] | None = None,
    max_width: int = 1280,
) -> tuple[ctypes.Array, int, int]:
    """Raw-buffer form of :func:`render_selection_frame_image`."""

    return _to_buffer(
        render_selection_frame_image(frame_size, region, hint=hint, box=box, max_width=max_width)
    )
