# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Paragraphs, not lines: group what the recognizer found and fit the answer.

A sign is read as a stack of lines, but it is written as paragraphs. Handing
each line to the translator on its own breaks sentences in half, and drawing
one plate per line puts a dozen overlapping boxes over a notice board. So
lines that continue one another are merged into a block, the block is
translated as one text, and the translation is drawn inside the block's own
rectangle with the largest font that fits - the way a camera translator
replaces a poster.

Merging has to be reluctant. A changelog is a column of one-line items at one
margin and spacing; a visitor board is a table; joining either into a single
paragraph turns a sign into a wall of text no plate can hold. So a line
continues the one above only when it looks like the wrapped remainder of the
same paragraph: same tilt and size, sitting right under a line that ran the
paragraph's full width, no wider than that width, aligned with it, not opening
with a bullet or a number, and with nothing standing beside either of them.

Every box is oriented. Text on a board seen from the side runs at an angle,
and an upright rectangle around such a line is taller than the letters by the
line's whole rise. Grouping and fitting therefore happen in each block's own
frame - the x axis along the text, the y axis across it - and the plate is
drawn rotated back onto the sign.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from src.core.in_place_layout import MAX_FONT_PX, Plate, wrap_text

Measure = Callable[[str, int], tuple[float, float]]

# A line continues the one above when the gap between them is at most this
# many line heights...
MAX_GAP_RATIO = 0.6
# ...or they overlap by at most this much (recognisers pad their boxes)...
MAX_OVERLAP_RATIO = 0.35
# ...their heights agree within this factor (one font, not a headline and
# its small print)...
MAX_HEIGHT_RATIO = 1.35
# ...and they run at the same tilt, within this many degrees.
MAX_ANGLE_DIFFERENCE = 4.0
# The line above must reach the paragraph's right edge within this many line
# heights to have wrapped at all, and the line below may not stick out past
# that edge by more than the same.
EDGE_TOLERANCE = 1.2
# Left edges must agree within this many line heights (a Chinese paragraph
# indents its first line by two), or, for centred text, the centres within
# one.
ALIGN_TOLERANCE = 2.2
CENTRE_TOLERANCE = 1.0
# Another line stands beside this one when their centres share a row within
# this fraction of a line height and they are separated sideways by between
# these many line heights: the cells of a table row, a label and its value.
SIDE_CENTRE_RATIO = 0.4
SIDE_GAP_RATIO = 0.3
SIDE_REACH_RATIO = 8.0
# The smallest text still readable in a headset at a normal sign distance.
MIN_BLOCK_FONT_PX = 11
# A block's plate may grow downward by this much of its own height before
# the text is cut short instead.
MAX_GROW_RATIO = 0.6
# Tilts below this are recogniser jitter; the box stays upright.
MIN_ANGLE = 0.75

_CJK = re.compile(r"[　-ヿ㐀-䶿一-鿿가-힯＀-￯]")
# A line that opens with a bullet, a dash or a number is an item of its own,
# never the continuation of the line above it.
_LIST_MARKER = re.compile(
    r"^\s*(?:[-–—―•・●○◎■□◆◇▶►▷▸➤➢✓✔☆★※→›»*+]"
    r"|[（(]?\d{1,3}[.)）、:：]"
    r"|[①-⑳⑴-⒇]"
    r"|[a-zA-Z][.)](?=\s|$))"
)


# ------------------------------------------------------------------ frames
def _direction(angle: float) -> tuple[float, float]:
    radians = math.radians(angle)
    return (math.cos(radians), math.sin(radians))


def to_frame(
    x: float, y: float, origin: tuple[float, float], angle: float
) -> tuple[float, float]:
    """An image point as (along, across) in the frame at ``origin`` tilted by ``angle``."""

    cos_a, sin_a = _direction(angle)
    dx, dy = x - origin[0], y - origin[1]
    return (dx * cos_a + dy * sin_a, -dx * sin_a + dy * cos_a)


def from_frame(
    along: float, across: float, origin: tuple[float, float], angle: float
) -> tuple[float, float]:
    """The image point of frame coordinates; the inverse of :func:`to_frame`."""

    cos_a, sin_a = _direction(angle)
    return (origin[0] + along * cos_a - across * sin_a, origin[1] + along * sin_a + across * cos_a)


def box_corners(
    left: float, top: float, width: float, height: float, angle: float
) -> tuple[tuple[float, float], ...]:
    """Image-space corners of an oriented box: top-left, top-right, bottom-right, bottom-left."""

    cos_a, sin_a = _direction(angle)
    return (
        (left, top),
        (left + width * cos_a, top + width * sin_a),
        (left + width * cos_a - height * sin_a, top + width * sin_a + height * cos_a),
        (left - height * sin_a, top + height * cos_a),
    )


def _at(line, name: str) -> float:
    try:
        return float(getattr(line, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _angle_of(line) -> float:
    angle = _at(line, "angle")
    return angle if abs(angle) >= MIN_ANGLE else 0.0


def _angle_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _extent(line, origin: tuple[float, float], angle: float) -> tuple[float, float, float, float]:
    """(along0, across0, along1, across1) of a line's box in another frame."""

    corners = box_corners(
        _at(line, "left"), _at(line, "top"), _at(line, "width"), _at(line, "height"), _angle_of(line)
    )
    points = [to_frame(x, y, origin, angle) for x, y in corners]
    return (
        min(a for a, _ in points),
        min(b for _, b in points),
        max(a for a, _ in points),
        max(b for _, b in points),
    )


# ------------------------------------------------------------------ blocks
@dataclass(frozen=True)
class TextBlock:
    """Lines that read as one paragraph, with the oriented box they cover.

    ``(left, top)`` is the box's top-left corner in image pixels; ``width``
    runs along the text at ``angle`` degrees and ``height`` across it.
    """

    lines: tuple
    left: float
    top: float
    width: float
    height: float
    angle: float = 0.0

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def line_height(self) -> float:
        heights = sorted(_at(line, "height") for line in self.lines)
        if not heights:
            return self.height
        return heights[len(heights) // 2]

    @property
    def text(self) -> str:
        """The paragraph, joined the way its script is written.

        Chinese and Japanese lines run on without a space; a space between
        them would split words. Latin lines get one.
        """

        pieces = [str(getattr(line, "text", "") or "").strip() for line in self.lines]
        pieces = [piece for piece in pieces if piece]
        if not pieces:
            return ""
        joined = pieces[0]
        for piece in pieces[1:]:
            if _CJK.search(joined[-1:]) and _CJK.search(piece[:1]):
                joined += piece
            else:
                joined += " " + piece
        return joined

    @property
    def area(self) -> float:
        return self.width * self.height


class _Draft:
    """A block being assembled, kept in the frame of its first line."""

    __slots__ = ("lines", "origin", "angle", "a0", "b0", "a1", "b1", "last", "heights", "beside")

    def __init__(self, line, beside: bool) -> None:
        self.lines = [line]
        self.origin = (_at(line, "left"), _at(line, "top"))
        self.angle = _angle_of(line)
        self.a0, self.b0, self.a1, self.b1 = _extent(line, self.origin, self.angle)
        self.last = (self.a0, self.b0, self.a1, self.b1)
        self.heights = [_at(line, "height")]
        self.beside = beside

    def median_height(self) -> float:
        heights = sorted(self.heights)
        return heights[len(heights) // 2] if heights else 0.0

    def add(self, line, extent: tuple[float, float, float, float], beside: bool) -> None:
        self.lines.append(line)
        a0, b0, a1, b1 = extent
        self.a0, self.b0 = min(self.a0, a0), min(self.b0, b0)
        self.a1, self.b1 = max(self.a1, a1), max(self.b1, b1)
        self.last = extent
        self.heights.append(_at(line, "height"))
        self.beside = beside

    def finish(self) -> TextBlock:
        left, top = from_frame(self.a0, self.b0, self.origin, self.angle)
        return TextBlock(
            lines=tuple(self.lines),
            left=left,
            top=top,
            width=self.a1 - self.a0,
            height=self.b1 - self.b0,
            angle=self.angle,
        )


def _has_side_neighbour(lines: Sequence, index: int) -> bool:
    """True when another line shares this line's row a little way off to the side."""

    line = lines[index]
    height, width = _at(line, "height"), _at(line, "width")
    if height <= 0 or width <= 0:
        return False
    origin = (_at(line, "left"), _at(line, "top"))
    angle = _angle_of(line)
    for other_index, other in enumerate(lines):
        if other_index == index:
            continue
        other_height = _at(other, "height")
        if other_height <= 0 or _at(other, "width") <= 0:
            continue
        a0, b0, a1, b1 = _extent(other, origin, angle)
        unit = min(height, other_height)
        if abs((b0 + b1) / 2.0 - height / 2.0) > SIDE_CENTRE_RATIO * unit:
            continue
        if a0 >= width:
            gap = a0 - width
        elif a1 <= 0:
            gap = -a1
        else:
            # Overlapping sideways means above or below, not beside.
            continue
        if SIDE_GAP_RATIO * unit <= gap <= SIDE_REACH_RATIO * unit:
            return True
    return False


def _continues(draft: _Draft, line, beside: bool) -> bool:
    """Does ``line`` read as the wrapped remainder of the paragraph in ``draft``?"""

    if beside or draft.beside:
        return False
    if _LIST_MARKER.match(str(getattr(line, "text", "") or "")):
        return False
    if _angle_difference(_angle_of(line), draft.angle) > MAX_ANGLE_DIFFERENCE:
        return False
    line_height = _at(line, "height")
    median = draft.median_height()
    if line_height <= 0 or median <= 0:
        return False
    if max(line_height, median) / min(line_height, median) > MAX_HEIGHT_RATIO:
        return False
    a0, b0, a1, _b1 = _extent(line, draft.origin, draft.angle)
    unit = max(line_height, median)
    gap = b0 - draft.last[3]
    if gap < -MAX_OVERLAP_RATIO * unit or gap > MAX_GAP_RATIO * unit:
        return False
    # The line above stopped short of the paragraph's edge: its paragraph
    # ended there, and whatever follows starts a new one.
    if draft.last[2] < draft.a1 - EDGE_TOLERANCE * unit:
        return False
    # Wider than the paragraph: a wrapped remainder never is.
    if a1 > draft.a1 + EDGE_TOLERANCE * unit:
        return False
    left_aligned = abs(a0 - draft.a0) <= ALIGN_TOLERANCE * unit
    centred = abs((a0 + a1) / 2.0 - (draft.a0 + draft.a1) / 2.0) <= CENTRE_TOLERANCE * unit
    return left_aligned or centred


def group_lines_into_blocks(lines: Iterable) -> list[TextBlock]:
    """Merge recognised lines into paragraph blocks, in reading order."""

    ordered = sorted(
        (line for line in lines if str(getattr(line, "text", "") or "").strip()),
        key=lambda line: (_at(line, "top"), _at(line, "left")),
    )
    beside = [_has_side_neighbour(ordered, index) for index in range(len(ordered))]
    drafts: list[_Draft] = []
    for index, line in enumerate(ordered):
        home = None
        # A line without a box (a recognizer that gives none) cannot sit
        # under anything; it stays a block of its own.
        if _at(line, "height") > 0 and _at(line, "width") > 0:
            # The block started most recently is the likeliest home.
            for draft in reversed(drafts):
                if _continues(draft, line, beside[index]):
                    home = draft
                    break
        if home is None:
            drafts.append(_Draft(line, beside[index]))
        else:
            home.add(line, _extent(line, home.origin, home.angle), beside[index])
    return [draft.finish() for draft in drafts]


# ------------------------------------------------------------------ fitting
def _rows_height(rows: Sequence[str], font_px: int, measure: Measure) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    measured = [measure(row, font_px) for row in rows]
    return max(w for w, _ in measured), sum(h for _, h in measured)


def fit_plate(
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    line_height: float,
    measure: Measure,
    *,
    max_height: float | None = None,
    sample: str = "",
) -> Plate | None:
    """Fit ``text`` into the rectangle with the largest font that works.

    The font starts at the source line height - capped so that ``sample``
    (the longest source line) would itself fit the width, since recognisers
    report the box of a button or a banner rather than of its glyphs - and
    shrinks until the wrapped text fits; if the floor is reached the plate
    grows downward, up to ``max_height`` (the room before the next block),
    and past that the last row is cut with an ellipsis rather than spilling
    over its neighbour. The plate is upright in the caller's frame.
    """

    text = str(text or "").strip()
    if not text:
        return None
    # The source rectangle hugs the glyphs; the plate wears a small margin
    # around it so the original is fully covered.
    padding = max(3, int(round(line_height * 0.12)))
    base_h = height + 2 * padding
    inner_w = max(20.0, width - 2 * padding)
    grow_room = height * MAX_GROW_RATIO
    if max_height is not None:
        grow_room = max(0.0, min(grow_room, max_height - height))
    ceiling = base_h + grow_room
    inner_h_limit = max(line_height, ceiling - 2 * padding)

    font_px = max(MIN_BLOCK_FONT_PX, min(MAX_FONT_PX, int(round(line_height * 0.82))))
    sample = str(sample or "").strip()
    if sample:
        # The original had to fit its own box; the translation should not
        # start bigger than that.
        while font_px > MIN_BLOCK_FONT_PX and measure(sample, font_px)[0] > inner_w:
            font_px -= 1
    rows: list[str] = [text]
    block_w = block_h = 0.0
    while True:
        rows = wrap_text(text, font_px, inner_w, measure)
        block_w, block_h = _rows_height(rows, font_px, measure)
        if block_h <= inner_h_limit or font_px <= MIN_BLOCK_FONT_PX:
            break
        font_px -= 1

    if block_h > inner_h_limit and rows:
        # Cut what does not fit; the reader gets the start of the paragraph
        # rather than a plate over the next one.
        kept: list[str] = []
        used = 0.0
        for row in rows:
            _w, h = measure(row, font_px)
            if used + h > inner_h_limit and kept:
                break
            kept.append(row)
            used += h
        if len(kept) < len(rows):
            last = kept[-1]
            while last and measure(last + "…", font_px)[0] > inner_w:
                last = last[:-1]
            kept[-1] = (last + "…") if last else "…"
        rows = kept
        block_w, block_h = _rows_height(rows, font_px, measure)

    plate_h = int(round(max(base_h, block_h + 2 * padding)))
    plate_h = min(plate_h, int(round(ceiling)))
    return Plate(
        x=int(round(x - padding)),
        y=int(round(y - padding)),
        width=int(round(width + 2 * padding)),
        height=max(1, plate_h),
        font_px=font_px,
        rows=tuple(rows),
    )


def layout_block_plates(
    lines: Iterable,
    frame_size: tuple[int, int],
    target_size: tuple[int, int],
    measure: Measure,
) -> list[Plate]:
    """One plate per placed block, scaled from frame pixels to the target.

    ``lines`` are PlacedLine-like objects whose ``text`` is the translation
    and whose oriented box is the block's; ``line_height`` (when present) is
    the source line height that sizes the font. Each plate is fitted in its
    block's own frame and comes back with the block's angle, its corner
    already rotated into place.
    """

    frame_w, frame_h = max(1, int(frame_size[0])), max(1, int(frame_size[1]))
    target_w, target_h = max(1, int(target_size[0])), max(1, int(target_size[1]))
    sx, sy = target_w / float(frame_w), target_h / float(frame_h)

    scaled = []
    for line in lines:
        text = str(getattr(line, "text", "") or "").strip()
        if not text:
            continue
        height = max(1.0, float(line.height) * sy)
        line_height = _at(line, "line_height") * sy or height
        angle = _angle_of(line)
        if angle and sx != sy:
            # Unequal scaling tilts the box a little differently.
            cos_a, sin_a = _direction(angle)
            angle = math.degrees(math.atan2(sin_a * sy, cos_a * sx))
        scaled.append(
            (
                text,
                float(line.left) * sx,
                float(line.top) * sy,
                max(1.0, float(line.width) * sx),
                height,
                max(1.0, min(height, line_height)),
                str(getattr(line, "source_sample", "") or ""),
                angle,
            )
        )
    scaled.sort(key=lambda item: (item[2], item[1]))

    plates: list[Plate] = []
    for index, (text, x, y, w, h, lh, sample, angle) in enumerate(scaled):
        # Room to grow: down to the next block that shares this column,
        # measured in this block's own frame.
        room = target_h - y
        for other_index, other in enumerate(scaled):
            if other_index == index:
                continue
            _t, ox, oy, ow, _oh, _olh, _os, _oa = other
            a0, b0 = to_frame(ox, oy, (x, y), angle)
            if b0 > 0 and min(w, a0 + ow) - max(0.0, a0) > 0:
                room = min(room, b0 - 4)
        plate = fit_plate(text, 0.0, 0.0, w, h, lh, measure, max_height=max(h, room), sample=sample)
        if plate is None:
            continue
        px, py = from_frame(plate.x, plate.y, (x, y), angle)
        px, py = int(round(px)), int(round(py))
        if not angle:
            # Keep an upright plate on the frame; a label off the edge is a
            # label lost. A tilted one is left where its line is.
            px = max(0, min(px, target_w - plate.width))
            py = max(0, min(py, target_h - plate.height))
        plates.append(Plate(px, py, plate.width, plate.height, plate.font_px, plate.rows, angle))
    return plates
