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
import math
from collections.abc import Iterable, Sequence

import numpy as np

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
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


# ------------------------------------------------------------------ colours
# VRHandsFrame's "estimate original colours": a plate in the sign's own
# background and ink colours reads as part of the sign, where white on black
# reads as a sticker. A matched plate is nearly opaque, since the original
# text under it must not show through.
MATCHED_PLATE_OPACITY = 0.96
# Below this contrast (WCAG ratio) the estimated ink is not trusted and the
# text goes white or black, whichever stands out more on the background.
MIN_TEXT_CONTRAST = 3.0
# Fewer ink pixels than this share of the samples: no ink was found.
MIN_INK_SHARE = 0.03
# Samples per plate along each side, at most.
COLOR_SAMPLES = 48

Rgb = tuple[int, int, int]


def picture_array(image: QImage) -> np.ndarray | None:
    """The picture as an (h, w, 3) uint8 RGB array, or None."""

    if image is None or image.isNull():
        return None
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    if width <= 0 or height <= 0:
        return None
    data = np.frombuffer(rgba.constBits(), dtype=np.uint8, count=rgba.sizeInBytes())
    rows = data.reshape(height, rgba.bytesPerLine())[:, : width * 4]
    return rows.reshape(height, width, 4)[:, :, :3].copy()


def _relative_luminance(color: Sequence[float]) -> float:
    def channel(value: float) -> float:
        c = max(0.0, min(255.0, float(value))) / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Sequence[float], b: Sequence[float]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def _readable_on(background: Rgb) -> Rgb:
    white, black = (255, 255, 255), (0, 0, 0)
    return white if contrast_ratio(white, background) >= contrast_ratio(black, background) else black


def _otsu(values: np.ndarray) -> float:
    """The luminance threshold that best splits values into two groups."""

    histogram, edges = np.histogram(values, bins=64, range=(0.0, 255.0))
    total = histogram.sum()
    if total == 0:
        return 127.5
    centres = (edges[:-1] + edges[1:]) / 2.0
    weight = np.cumsum(histogram)
    mean = np.cumsum(histogram * centres)
    overall = mean[-1]
    best, threshold = -1.0, 127.5
    for i in range(len(histogram) - 1):
        w0 = weight[i]
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0 = mean[i] / w0
        m1 = (overall - mean[i]) / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, threshold = between, edges[i + 1]
    return float(threshold)


def estimate_colors(pixels: np.ndarray) -> tuple[Rgb, Rgb] | None:
    """(background, ink) of a patch of sign, from its (n, 3) RGB samples.

    The larger of the two luminance groups is the background, the smaller
    one the letters. Ink that barely stands out is replaced with white or
    black so the translation always reads.
    """

    if pixels is None or len(pixels) < 4:
        return None
    samples = pixels.reshape(-1, 3).astype(np.float64)
    luminance = samples @ np.array([0.299, 0.587, 0.114])
    threshold = _otsu(luminance)
    dark = luminance <= threshold
    light = ~dark
    if dark.sum() >= light.sum():
        background_mask, ink_mask = dark, light
    else:
        background_mask, ink_mask = light, dark
    background = tuple(int(v) for v in np.median(samples[background_mask], axis=0))
    if ink_mask.sum() < max(1, MIN_INK_SHARE * len(samples)):
        return background, _readable_on(background)  # type: ignore[arg-type]
    ink = tuple(int(v) for v in np.median(samples[ink_mask], axis=0))
    if contrast_ratio(ink, background) < MIN_TEXT_CONTRAST:
        ink = _readable_on(background)  # type: ignore[arg-type]
    return background, ink  # type: ignore[return-value]


def plate_colors(array: np.ndarray | None, plate: Plate, scale: float = 1.0) -> tuple[Rgb, Rgb] | None:
    """The colours of the picture under ``plate`` (plate pixels = picture
    pixels x ``scale``)."""

    if array is None:
        return None
    height, width = array.shape[:2]
    angle = math.radians(float(getattr(plate, "angle", 0.0) or 0.0))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = [
        (plate.x + dx * cos_a - dy * sin_a, plate.y + dx * sin_a + dy * cos_a)
        for dx, dy in ((0, 0), (plate.width, 0), (0, plate.height), (plate.width, plate.height))
    ]
    factor = 1.0 / max(1e-6, float(scale))
    x0 = int(max(0, math.floor(min(x for x, _ in corners) * factor)))
    y0 = int(max(0, math.floor(min(y for _, y in corners) * factor)))
    x1 = int(min(width, math.ceil(max(x for x, _ in corners) * factor)))
    y1 = int(min(height, math.ceil(max(y for _, y in corners) * factor)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    step_x = max(1, (x1 - x0) // COLOR_SAMPLES)
    step_y = max(1, (y1 - y0) // COLOR_SAMPLES)
    return estimate_colors(array[y0:y1:step_y, x0:x1:step_x])


def paint_plates(
    painter: QPainter,
    plates: Iterable[Plate],
    *,
    plate_opacity: float = DEFAULT_PLATE_OPACITY,
    colors: Sequence[tuple[Rgb, Rgb] | None] | None = None,
) -> None:
    """Draw the plates; ``colors`` (one per plate, None for the default
    white on black) paints a plate in the sign's own colours."""

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    alpha = int(round(max(0.0, min(1.0, float(plate_opacity))) * 255))
    matched_alpha = int(round(MATCHED_PLATE_OPACITY * 255))
    default_plate = QColor(*PLATE_COLOR, alpha)
    default_text = QColor(*TEXT_COLOR)
    for index, plate in enumerate(plates):
        matched = colors[index] if colors is not None and index < len(colors) else None
        if matched is not None:
            plate_color = QColor(*matched[0], matched_alpha)
            text_color = QColor(*matched[1])
        else:
            plate_color, text_color = default_plate, default_text
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
    match_colors: bool = False,
) -> QImage:
    """The captured picture with the translated plates laid on it.

    ``lines`` are in frame pixels; ``offset`` is where the picture's top-left
    sits in the frame. This is what the headset shows on a card, the way
    VRHandsFrame shows a crop: the labels and the text they cover are one
    picture, so nothing depends on where the head is now. ``match_colors``
    paints each plate in the colours of the sign under it.
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
        colors = None
        if match_colors:
            array = picture_array(picture)
            factor = width / float(src_w)
            colors = [plate_colors(array, plate, factor) for plate in plates]
        paint_plates(painter, plates, plate_opacity=plate_opacity, colors=colors)
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


# ------------------------------------------------------------------ hand frame
# The frame between the player's hands (VRHandsFrame's gesture): white while
# framing, cyan while reading, green once a reading covers it.
HAND_FRAME_MAX_SIDE = 1024
HAND_FRAME_BORDER_PX = 6
HAND_FRAME_CORNER_PX = 14
HAND_FRAME_RADIUS = 16
HAND_FRAME_COLORS = {
    "ready": (255, 255, 255, 235),
    "reading": (64, 224, 255, 245),
    "shown": (80, 224, 128, 245),
}


def hand_frame_size(width_m: float, height_m: float, max_side: int = HAND_FRAME_MAX_SIDE) -> tuple[int, int]:
    """Texture size with the frame's aspect, longest side ``max_side``."""

    width_m, height_m = max(1e-3, float(width_m)), max(1e-3, float(height_m))
    if width_m >= height_m:
        return max_side, max(48, int(round(max_side * height_m / width_m)))
    return max(48, int(round(max_side * width_m / height_m))), max_side


def render_hand_frame_image(
    size: tuple[int, int],
    *,
    phase: str = "ready",
    still_fraction: float = 0.0,
    hint: str = "",
    picture: QImage | None = None,
    picture_rect: tuple[float, float, float, float] | None = None,
) -> QImage:
    """The frame's texture.

    ``picture`` (the read crop with its translated plates) is drawn at
    ``picture_rect`` - fractions of the frame, which may reach past its
    edges - so the plates land over the text they translate while the hands
    hold still.
    """

    width, height = max(1, int(size[0])), max(1, int(size[1]))
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, width, height), HAND_FRAME_RADIUS, HAND_FRAME_RADIUS)
        if picture is not None and not picture.isNull():
            x0, y0, x1, y1 = picture_rect or (0.0, 0.0, 1.0, 1.0)
            target = QRectF(x0 * width, y0 * height, (x1 - x0) * width, (y1 - y0) * height)
            painter.setClipPath(clip)
            painter.drawImage(target, picture)
            painter.setClipping(False)
        color = QColor(*HAND_FRAME_COLORS.get(phase, HAND_FRAME_COLORS["ready"]))
        half = HAND_FRAME_BORDER_PX / 2.0
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, HAND_FRAME_BORDER_PX))
        painter.drawRoundedRect(
            QRectF(half, half, width - HAND_FRAME_BORDER_PX, height - HAND_FRAME_BORDER_PX),
            HAND_FRAME_RADIUS,
            HAND_FRAME_RADIUS,
        )
        # Corner brackets, where the hands are: the frame reads as "held".
        arm = max(24.0, min(width, height) * 0.16)
        pen = QPen(color, HAND_FRAME_CORNER_PX)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = HAND_FRAME_CORNER_PX / 2.0
        for cx, cy, dx, dy in (
            (inset, inset, 1, 1),
            (width - inset, inset, -1, 1),
            (inset, height - inset, 1, -1),
            (width - inset, height - inset, -1, -1),
        ):
            painter.drawLine(QPointF(cx, cy), QPointF(cx + dx * arm, cy))
            painter.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * arm))
        if phase == "ready" and still_fraction > 0.0:
            # How close the stillness is to reading, along the bottom edge.
            bar_h = max(6.0, height * 0.012)
            inner = width - 2 * (HAND_FRAME_CORNER_PX + arm)
            if inner > 20:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(64, 224, 255, 220))
                painter.drawRoundedRect(
                    QRectF(
                        HAND_FRAME_CORNER_PX + arm,
                        height - bar_h - HAND_FRAME_BORDER_PX * 2,
                        inner * max(0.0, min(1.0, float(still_fraction))),
                        bar_h,
                    ),
                    bar_h / 2.0,
                    bar_h / 2.0,
                )
        if hint and picture is None:
            font_px = max(16, int(round(min(width, height) * 0.06)))
            font = _font(font_px)
            metrics = QFontMetricsF(font)
            text = metrics.elidedText(hint, Qt.TextElideMode.ElideRight, int(width * 0.8))
            text_w = metrics.horizontalAdvance(text) + font_px
            box = QRectF((width - text_w) / 2.0, HAND_FRAME_CORNER_PX + font_px * 0.4, text_w, metrics.height() + font_px * 0.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*PLATE_COLOR, 200))
            painter.drawRoundedRect(box, PLATE_RADIUS * 2, PLATE_RADIUS * 2)
            painter.setFont(font)
            painter.setPen(QColor(*TEXT_COLOR))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)
    finally:
        painter.end()
    return image
