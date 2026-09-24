# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Where translated text goes so that it covers the text it came from.

Two separate pieces of geometry live here, both pure so they can be tested
without a headset or a font:

* **Plates.** Each recognised line becomes a black plate with white text laid
  over the line's own box. Translations are rarely the same length as their
  source, so a plate may shrink its font a little, then widen, then wrap - in
  that order, because a reader forgives a slightly smaller line far more than
  one that runs off the sign.
* **The headset transform.** The captured frame is the compositor's own
  left-eye picture, drawn with the projection SteamVR reports for that eye. A
  pixel in it corresponds to a direction from that eye, so a flat overlay that
  spans the eye's field of view at some depth, placed where the head was at
  capture time, puts every plate on top of its sign. The overlay is anchored
  to the world rather than to the head, so turning away does not drag the
  labels along.

The projection is known exactly, so ``fov_scale`` is only a trim the player
can turn if a headset reports its lenses wrongly.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

# A flat sheet can only match the sign at one depth; head movement shows
# the mismatch as drift. Signs in VRChat are usually read from one to two
# metres, so this is the compromise between them.
DEFAULT_DEPTH_METERS = 1.5
MIN_DEPTH_METERS = 0.8
MAX_DEPTH_METERS = 5.0
DEFAULT_FOV_SCALE = 1.0
MIN_FOV_SCALE = 0.5
MAX_FOV_SCALE = 1.6

MIN_FONT_PX = 13
MAX_FONT_PX = 72
# The plate's text height relative to the recognised line's height. Slightly
# under 1: recognisers report the ink bounds, and a font's em box is taller.
FONT_TO_LINE_RATIO = 0.82
# How far the font may shrink before the plate starts to widen instead.
MIN_SHRINK_RATIO = 0.72
# How much wider than its source a plate may grow before it wraps.
MAX_GROW_RATIO = 1.8
PADDING_RATIO = 0.22
MIN_PADDING_PX = 4

Measure = Callable[[str, int], tuple[float, float]]


def clamp_depth(value: object, default: float = DEFAULT_DEPTH_METERS) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return max(MIN_DEPTH_METERS, min(MAX_DEPTH_METERS, parsed))


def clamp_fov_scale(value: object, default: float = DEFAULT_FOV_SCALE) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return max(MIN_FOV_SCALE, min(MAX_FOV_SCALE, parsed))


@dataclass(frozen=True)
class PlacedLine:
    """One translated line and the box its source occupied, in frame pixels."""

    original: str
    translated: str
    left: float
    top: float
    width: float
    height: float
    # For a paragraph block: the height of one source line, which sizes the
    # font. Zero means "the rectangle is a single line".
    line_height: float = 0.0
    # The longest source line, so the translation is never drawn larger than
    # the original managed to fit in the same box.
    source_sample: str = ""
    # The box's tilt in degrees, clockwise on screen: ``width`` runs along
    # the text at this angle from ``(left, top)``. Zero is an upright box.
    angle: float = 0.0

    @property
    def text(self) -> str:
        return self.translated or self.original


@dataclass(frozen=True)
class Plate:
    """A drawable label: its rectangle, font size and already-wrapped rows.

    ``(x, y)`` is the top-left corner; the plate is drawn rotated by
    ``angle`` degrees (clockwise) about that corner, so it lies along the
    tilted line it covers.
    """

    x: int
    y: int
    width: int
    height: int
    font_px: int
    rows: tuple[str, ...]
    angle: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(self.rows)


# ------------------------------------------------------------------ wrapping
_HAS_SPACES = re.compile(r"\s")


def _tokens(text: str) -> tuple[list[str], str]:
    """Break text into wrap units: words for spaced scripts, characters otherwise."""

    if _HAS_SPACES.search(text):
        return [token for token in text.split() if token], " "
    return list(text), ""


def wrap_text(text: str, font_px: int, max_width: float, measure: Measure) -> list[str]:
    """Greedy wrap. Never returns an empty list for non-empty text."""

    text = str(text or "").strip()
    if not text:
        return []
    width, _height = measure(text, font_px)
    if width <= max_width:
        return [text]
    tokens, joiner = _tokens(text)
    rows: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current}{joiner}{token}" if current else token
        if current and measure(candidate, font_px)[0] > max_width:
            rows.append(current)
            current = token
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows


# ------------------------------------------------------------------ plates
def layout_plates(
    lines: Iterable[PlacedLine],
    frame_size: tuple[int, int],
    target_size: tuple[int, int],
    measure: Measure,
) -> list[Plate]:
    """Lay one plate over each line, scaled from frame pixels to the target.

    ``measure(text, font_px)`` returns the rendered (width, height) of a single
    row; it is injected so the layout stays independent of any toolkit.
    """

    frame_w, frame_h = (max(1, int(frame_size[0])), max(1, int(frame_size[1])))
    target_w, target_h = (max(1, int(target_size[0])), max(1, int(target_size[1])))
    sx = target_w / float(frame_w)
    sy = target_h / float(frame_h)

    plates: list[Plate] = []
    for line in lines:
        text = str(line.text or "").strip()
        if not text:
            continue
        x = line.left * sx
        y = line.top * sy
        box_w = max(1.0, line.width * sx)
        box_h = max(1.0, line.height * sy)

        base_font = int(round(box_h * FONT_TO_LINE_RATIO))
        font_px = max(MIN_FONT_PX, min(MAX_FONT_PX, base_font))
        padding = max(MIN_PADDING_PX, int(round(box_h * PADDING_RATIO)))

        # 1. Shrink a little.
        floor = max(MIN_FONT_PX, int(round(font_px * MIN_SHRINK_RATIO)))
        text_w, _ = measure(text, font_px)
        while text_w > box_w and font_px > floor:
            font_px -= 1
            text_w, _ = measure(text, font_px)

        # 2. Then widen, as far as the frame allows.
        available = box_w
        if text_w > available:
            room_right = target_w - x - padding * 2
            available = max(box_w, min(box_w * MAX_GROW_RATIO, room_right))

        # 3. Then wrap.
        rows = wrap_text(text, font_px, available, measure)
        if not rows:
            continue
        measured = [measure(row, font_px) for row in rows]
        block_w = max(width for width, _ in measured)
        row_h = max(height for _, height in measured)
        block_h = row_h * len(rows)

        plate_w = int(round(block_w + padding * 2))
        plate_h = int(round(block_h + padding * 2))
        plate_x = int(round(x - padding))
        plate_y = int(round(y - padding))
        # Keep the plate on the frame; a label off the edge is a label lost.
        plate_x = max(0, min(plate_x, target_w - plate_w))
        plate_y = max(0, min(plate_y, target_h - plate_h))
        plates.append(
            Plate(
                x=plate_x,
                y=plate_y,
                width=max(1, plate_w),
                height=max(1, plate_h),
                font_px=font_px,
                rows=tuple(rows),
            )
        )
    return plates


# ------------------------------------------------------------------ headset
Matrix34 = tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True)
class HeadAnchor:
    """Where the head was and how the left eye sees, at capture time."""

    pose: Matrix34
    eye_offset: tuple[float, float, float]
    # Raw projection tangents: left, right, top, bottom.
    tangents: tuple[float, float, float, float]


def _multiply(pose: Matrix34, local: Matrix34) -> list[list[float]]:
    out: list[list[float]] = []
    for r in range(3):
        row: list[float] = []
        for c in range(4):
            total = sum(pose[r][k] * local[k][c] for k in range(3))
            if c == 3:
                total += pose[r][3]
            row.append(total)
        out.append(row)
    return out


def in_place_transform(
    anchor: HeadAnchor, depth: float, fov_scale: float
) -> tuple[list[list[float]], float]:
    """Return (3x4 absolute transform, width in metres) for the overlay.

    The overlay is a plane facing the head as it was at capture time, one
    ``depth`` in front of the left eye, wide enough to span that eye's
    horizontal field of view. Its height follows the texture aspect, which is
    the frame's, so no vertical size is needed.
    """

    depth = clamp_depth(depth)
    fov_scale = clamp_fov_scale(fov_scale)
    left, right, top, bottom = anchor.tangents
    span = max(0.1, (right - left)) * depth * fov_scale
    ex, ey, ez = anchor.eye_offset
    # Asymmetric lenses (Index, Quest) look slightly outward and a little
    # down: the frame's centre is where the projection's centre is, not
    # straight ahead. OpenVR's raw values put the frame's top edge at the
    # tangent it calls ``bottom`` (positive, up) and the bottom edge at
    # ``top`` (negative, down), so their mean is the centre's height.
    cx = ex + (left + right) / 2.0 * depth * fov_scale
    cy = ey + (top + bottom) / 2.0 * depth * fov_scale
    cz = ez - depth
    local: Matrix34 = (
        (1.0, 0.0, 0.0, cx),
        (0.0, 1.0, 0.0, cy),
        (0.0, 0.0, 1.0, cz),
    )
    return _multiply(anchor.pose, local), span


def frame_region_from_uv(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Normalise two drag corners into (u0, v0, u1, v1) inside the unit square."""

    u0 = max(0.0, min(1.0, float(min(start[0], end[0]))))
    u1 = max(0.0, min(1.0, float(max(start[0], end[0]))))
    v0 = max(0.0, min(1.0, float(min(start[1], end[1]))))
    v1 = max(0.0, min(1.0, float(max(start[1], end[1]))))
    return (u0, v0, u1, v1)


def region_is_a_click(
    region: Sequence[float] | None, minimum: float = 0.02
) -> bool:
    """True when a drag was too small to be a drawn region: a click."""

    if not region or len(region) != 4:
        return True
    u0, v0, u1, v1 = (float(v) for v in region)
    return (u1 - u0) < minimum or (v1 - v0) < minimum


# The box that follows the laser on the selection frame, the way OVR
# Toolkit's translate tool frames what the controller points at: a click
# reads what is inside it, so most signs never need a drag. Sized as a share
# of the view; on a square eye frame this is about a third across and a
# sixth down, roughly the OVR box.
POINTER_BOX_WIDTH_FRACTION = 0.32
POINTER_BOX_HEIGHT_FRACTION = 0.16


def pointer_box(
    point: Sequence[float],
    *,
    width: float = POINTER_BOX_WIDTH_FRACTION,
    height: float = POINTER_BOX_HEIGHT_FRACTION,
) -> tuple[float, float, float, float]:
    """The pointer box centred on a frame point (fractions), kept inside the frame."""

    w = max(0.02, min(1.0, float(width)))
    h = max(0.02, min(1.0, float(height)))
    u = max(w / 2.0, min(1.0 - w / 2.0, float(point[0])))
    v = max(h / 2.0, min(1.0 - h / 2.0, float(point[1])))
    return (u - w / 2.0, v - h / 2.0, u + w / 2.0, v + h / 2.0)


# ------------------------------------------------------------------ card
# A flat sheet pinned at one depth only matches the sign at that depth: the
# moment the head moves, the labels part from their text. A card sidesteps
# that entirely - the picture and its labels travel together - so it is what
# the headset shows. It hangs along the direction the text was seen in, a
# little larger than life.
CARD_SCALE = 1.25
MIN_CARD_WIDTH_METERS = 0.3
# Never wider than this share of the eye's view at the card's depth.
MAX_CARD_SPAN_FRACTION = 0.85


def card_transform(
    anchor: HeadAnchor, centre_uv: tuple[float, float], depth: float
) -> list[list[float]]:
    """The 3x4 transform of a card facing the head, ``depth`` metres out along
    the direction frame point ``centre_uv`` (fractions, top-left origin) was
    seen in."""

    depth = clamp_depth(depth)
    left, right, top, bottom = anchor.tangents
    u = max(0.0, min(1.0, float(centre_uv[0])))
    v = max(0.0, min(1.0, float(centre_uv[1])))
    x_tan = left + u * (right - left)
    # The frame's top edge is at the tangent OpenVR names ``bottom``.
    y_tan = bottom + v * (top - bottom)
    ex, ey, ez = anchor.eye_offset
    local: Matrix34 = (
        (1.0, 0.0, 0.0, ex + x_tan * depth),
        (0.0, 1.0, 0.0, ey + y_tan * depth),
        (0.0, 0.0, 1.0, ez - depth),
    )
    return _multiply(anchor.pose, local)


def view_fraction(
    anchor: HeadAnchor, point: Sequence[float]
) -> tuple[float, float, float] | None:
    """Where a world point appears in the left eye's frame: (u, v, depth).

    ``u``/``v`` are fractions of the frame (top-left origin, may lie outside
    0..1 for a point outside the view); ``depth`` is the distance in front of
    the eye along its view axis. None for a point behind or at the eye.
    The inverse of :func:`card_transform`'s direction for a frame point.
    """

    pose = anchor.pose
    try:
        px, py, pz = (float(point[0]), float(point[1]), float(point[2]))
    except (TypeError, ValueError, IndexError):
        return None
    # World -> head: the pose's rotation is orthonormal, so its inverse is
    # its transpose applied to the offset from the head's origin.
    dx, dy, dz = px - pose[0][3], py - pose[1][3], pz - pose[2][3]
    hx = pose[0][0] * dx + pose[1][0] * dy + pose[2][0] * dz
    hy = pose[0][1] * dx + pose[1][1] * dy + pose[2][1] * dz
    hz = pose[0][2] * dx + pose[1][2] * dy + pose[2][2] * dz
    ex, ey, ez = anchor.eye_offset
    x, y, z = hx - ex, hy - ey, hz - ez
    depth = -z
    if depth <= 1e-3:
        return None
    left, right, top, bottom = anchor.tangents
    x_tan = x / depth
    y_tan = y / depth
    u = (x_tan - left) / (right - left) if right != left else 0.5
    # The frame's top edge is at the tangent OpenVR names ``bottom``.
    v = (y_tan - bottom) / (top - bottom) if top != bottom else 0.5
    return (u, v, depth)


def region_quad(
    anchor: HeadAnchor, region: Sequence[float], depth: float
) -> tuple[list[list[float]], float, float]:
    """(3x4 transform, width m, height m) of a quad that covers ``region``
    (u0, v0, u1, v1 of the frame) exactly, ``depth`` metres in front of the
    eye and facing the head - so it lines up with what that region shows."""

    u0, v0, u1, v1 = (float(value) for value in region)
    depth = max(0.05, float(depth))
    left, right, top, bottom = anchor.tangents
    width = abs(u1 - u0) * abs(right - left) * depth
    height = abs(v1 - v0) * abs(bottom - top) * depth
    centre = ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    x_tan = left + centre[0] * (right - left)
    y_tan = bottom + centre[1] * (top - bottom)
    ex, ey, ez = anchor.eye_offset
    local: Matrix34 = (
        (1.0, 0.0, 0.0, ex + x_tan * depth),
        (0.0, 1.0, 0.0, ey + y_tan * depth),
        (0.0, 0.0, 1.0, ez - depth),
    )
    return _multiply(anchor.pose, local), max(0.01, width), max(0.01, height)


def card_width_meters(
    anchor: HeadAnchor, width_fraction: float, depth: float, *, scale: float = CARD_SCALE
) -> float:
    """How wide a card showing ``width_fraction`` of the frame should be.

    Life size at the card's depth times ``scale``, never smaller than a
    readable minimum and never wider than most of the view.
    """

    depth = clamp_depth(depth)
    left, right, _top, _bottom = anchor.tangents
    span = max(0.1, right - left) * depth
    fraction = max(0.0, min(1.0, float(width_fraction)))
    return max(MIN_CARD_WIDTH_METERS, min(span * MAX_CARD_SPAN_FRACTION, span * fraction * scale))


def text_extent(
    lines: Iterable[PlacedLine],
    picture_size: tuple[int, int],
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int, int, int]:
    """The part of a picture that holds the lines, with a margin, as (x, y, w, h).

    Lines are in frame pixels; ``offset`` is where the picture's top-left
    sits in the frame. A whole-view read shows this part, not the whole
    frame: the player wants the sign, not the floor around it.
    """

    width, height = max(1, int(picture_size[0])), max(1, int(picture_size[1]))
    boxes = []
    for line in lines:
        try:
            x = float(line.left) - float(offset[0])
            y = float(line.top) - float(offset[1])
            w, h = float(line.width), float(line.height)
        except (AttributeError, TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            boxes.append((x, y, w, h))
    if not boxes:
        return (0, 0, width, height)
    heights = sorted(h for _x, _y, _w, h in boxes)
    margin = heights[len(heights) // 2] * 0.8 + 12.0
    x0 = min(x for x, _y, _w, _h in boxes) - margin
    y0 = min(y for _x, y, _w, _h in boxes) - margin
    x1 = max(x + w for x, _y, w, _h in boxes) + margin
    y1 = max(y + h for _x, y, _w, h in boxes) + margin
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(round(x1))), min(height, int(round(y1)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return (0, 0, width, height)
    return (x0, y0, x1 - x0, y1 - y0)
