# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""One button press: capture the view, read it, translate it, show it.

The stages have very different costs and very different threading rules, so
they are kept apart deliberately:

* capture must run on the UI thread, because Qt owns the screen handles;
* recognition must not, because the Windows recognizer needs its own COM
  apartment and takes a few hundred milliseconds on a full screen;
* translation is network-bound and belongs nowhere near either.

A press while a run is already going is ignored rather than queued. Two
screenshots of the same moment are not worth the second round trip, and a
player holding the button should not build a backlog they then have to watch
drain.

Every translated line keeps the box it was read from, in whole-frame pixels,
and the run records where the head was when the frame was taken. That is what
lets the result be drawn back over the sign it came from.
"""

from __future__ import annotations

import copy
import logging
import math
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from src.core.in_place_layout import PlacedLine
from src.core.text_blocks import box_corners, group_lines_into_blocks

logger = logging.getLogger(__name__)

# The bundled recognizer reads a whole game UI at once; capping too low left
# players with half a screen translated. Each block is still one provider
# call, but a notice board is a block per item, so the budgets are generous
# and repeated texts share a call.
MAX_TRANSLATED_LINES = 24
# A deliberate selection is small and everything in it was asked for.
MAX_REGION_LINES = 40
# Blocks are translated a few at a time: a notice board is a dozen round
# trips, and waiting for them one after another is what made a press feel
# slow. Kept small so metered providers are not hammered.
TRANSLATION_WORKERS = 3
# Ignore anything shorter than this: single stray glyphs are recognition noise
# from textures and UI edges, not something a player asked to have translated.
MIN_LINE_CHARACTERS = 2
# Characters the recognizer produces for boxes, bars and dots drawn in a
# world: a line made only of these is an icon, not a word. Seen live: a bed
# icon's two rounded rectangles came back as "口口".
_SHAPE_CHARACTERS = frozenset("口ロ囗一ー二三丨｜|lI1・·.-—_=~〇○●□■◇◆△▽")

# Why a translation call failed, in words a player can act on. The provider's
# own error is kept for the log; the player sees one of these.
TRANSLATION_ERROR_KINDS = ("network", "auth", "quota", "unavailable")
_NETWORK_HINTS = (
    "timeout", "timed out", "connection", "network", "resolve", "dns",
    "unreachable", "ssl", "proxy", "refused", "reset by peer", "eof",
)
_AUTH_HINTS = (
    "401", "403", "unauthorized", "forbidden", "api key", "apikey", "api_key",
    "invalid key", "authentication", "credential", "permission",
)
_QUOTA_HINTS = (
    "429", "quota", "rate limit", "ratelimit", "too many", "insufficient",
    "exceeded", "balance",
)


class TranslationUnavailable(Exception):
    """A translation call could not be made; ``kind`` says why in broad terms."""

    def __init__(self, kind: str = "unavailable", detail: str = "") -> None:
        self.kind = kind if kind in TRANSLATION_ERROR_KINDS else "unavailable"
        self.detail = str(detail or "")
        super().__init__(self.detail or self.kind)


def classify_translation_error(exc: BaseException) -> str:
    """Map a provider exception onto one of :data:`TRANSLATION_ERROR_KINDS`."""

    if isinstance(exc, TranslationUnavailable):
        return exc.kind
    text = f"{type(exc).__name__} {exc}".lower()
    if any(hint in text for hint in _AUTH_HINTS):
        return "auth"
    if any(hint in text for hint in _QUOTA_HINTS):
        return "quota"
    if any(hint in text for hint in _NETWORK_HINTS):
        return "network"
    return "unavailable"


@dataclass
class ScreenshotTranslation:
    """What one press produced."""

    pairs: list[tuple[str, str]] = field(default_factory=list)
    lines: list[PlacedLine] = field(default_factory=list)
    source: str = ""
    error: str = ""
    # Set when at least one line's translation call failed: one of
    # TRANSLATION_ERROR_KINDS. With ``error == "translation_failed"`` nothing
    # translated at all; otherwise the surviving lines are still shown.
    error_kind: str = ""
    ocr_ms: float = 0.0
    translate_ms: float = 0.0
    # Geometry of the frame the lines are placed in: its size in capture
    # pixels, where it sits on the desktop, and capture-to-screen scale.
    frame_size: tuple[int, int] = (0, 0)
    origin: tuple[int, int] = (0, 0)
    scale: float = 1.0
    # Head pose at capture time (a HeadAnchor) when a headset was available.
    anchor: object | None = None
    region: tuple[float, float, float, float] | None = None
    # The picture that was read (PNG), where its top-left sits in the frame
    # and its size, so the result can be shown as a card: that picture with
    # the translations laid on it.
    png: bytes = b""
    offset: tuple[int, int] = (0, 0)
    crop_size: tuple[int, int] = (0, 0)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.pairs)

    @property
    def placeable(self) -> bool:
        """True when every line knows where it belongs on the frame."""

        return bool(self.lines) and self.frame_size[0] > 0 and self.frame_size[1] > 0


def _coordinate(line, name: str) -> float:
    try:
        return float(getattr(line, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# A box read (the pointer box or a drawn rectangle) is padded before
# recognition, by these fractions of its own size, so that a line the box
# edge slices through is read whole; afterwards the picture shrinks to the
# lines whose middle lay inside the box, with a little air around them.
REGION_PAD_WIDTH = 0.08
REGION_PAD_HEIGHT = 0.25
SNAP_MARGIN_LINE_HEIGHTS = 0.35


def padded_region(region: Sequence[float]) -> tuple[float, float, float, float]:
    """The region to actually read for a box: the box plus its padding, clamped."""

    u0, v0, u1, v1 = (max(0.0, min(1.0, float(value))) for value in region)
    u0, u1 = min(u0, u1), max(u0, u1)
    v0, v1 = min(v0, v1), max(v0, v1)
    pad_w = (u1 - u0) * REGION_PAD_WIDTH
    pad_h = (v1 - v0) * REGION_PAD_HEIGHT
    return (max(0.0, u0 - pad_w), max(0.0, v0 - pad_h), min(1.0, u1 + pad_w), min(1.0, v1 + pad_h))


def _line_box(line) -> tuple[float, float, float, float] | None:
    """An upright box around a line, or None for a line that carries no box."""

    width = _coordinate(line, "width")
    height = _coordinate(line, "height")
    if width <= 0 or height <= 0:
        return None
    left = _coordinate(line, "left")
    top = _coordinate(line, "top")
    angle = _coordinate(line, "angle")
    if abs(angle) < 0.01:
        return (left, top, left + width, top + height)
    corners = box_corners(left, top, width, height, angle)
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def lines_in_target(lines, target: Sequence[float]) -> list:
    """The lines whose middle lies inside ``target`` (x0, y0, x1, y1, crop pixels).

    A line without a box cannot be judged and is kept.
    """

    x0, y0, x1, y1 = (float(value) for value in target)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    kept = []
    for line in lines:
        box = _line_box(line)
        if box is None:
            kept.append(line)
            continue
        centre_x = (box[0] + box[2]) / 2.0
        centre_y = (box[1] + box[3]) / 2.0
        if x0 <= centre_x <= x1 and y0 <= centre_y <= y1:
            kept.append(line)
    return kept


def lines_extent(lines, bounds: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """The pixel rectangle holding every boxed line plus a margin, clamped to ``bounds``."""

    boxes = [box for box in (_line_box(line) for line in lines) if box is not None]
    if not boxes:
        return None
    margin = SNAP_MARGIN_LINE_HEIGHTS * statistics.median(box[3] - box[1] for box in boxes)
    width, height = int(bounds[0]), int(bounds[1])
    x0 = max(0, int(math.floor(min(box[0] for box in boxes) - margin)))
    y0 = max(0, int(math.floor(min(box[1] for box in boxes) - margin)))
    x1 = min(width, int(math.ceil(max(box[2] for box in boxes) + margin)))
    y1 = min(height, int(math.ceil(max(box[3] for box in boxes) + margin)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1, y1)


def _shifted(line, dx: float, dy: float):
    """The same line with its box moved by (-dx, -dy)."""

    if _line_box(line) is None:
        return line
    try:
        return replace(line, left=float(line.left) - dx, top=float(line.top) - dy)
    except (TypeError, AttributeError):
        clone = copy.copy(line)
        clone.left = float(line.left) - dx
        clone.top = float(line.top) - dy
        return clone


def worthwhile_line_objects(
    lines, limit: int | None = MAX_TRANSLATED_LINES, *, keep_repeats: bool = False
) -> list:
    """Keep the recognised lines worth spending a translation call on.

    Recognition over a whole screen returns dozens of fragments; translating
    all of them is slow, costs money on metered providers, and buries the two
    lines the player actually looked at. With ``keep_repeats`` the same words
    in two places are two lines (a table full of "PC/VR" needs a plate on
    each); only a second reading of the very same box is dropped.
    """

    kept: list = []
    seen: set = set()
    for line in lines:
        text = str(getattr(line, "text", line) or "").strip()
        if len(text) < MIN_LINE_CHARACTERS:
            continue
        if all(character in _SHAPE_CHARACTERS or character.isspace() for character in text):
            continue
        key = text
        if keep_repeats:
            key = (text, int(_coordinate(line, "left") // 4), int(_coordinate(line, "top") // 4))
        if key in seen:
            continue
        seen.add(key)
        kept.append(line)
    # Longest first: a sign or a sentence beats a stray label.
    kept.sort(key=lambda item: len(str(getattr(item, "text", item) or "")), reverse=True)
    return kept if limit is None else kept[:limit]


def worthwhile_blocks(lines, limit: int):
    """Paragraph blocks worth a translation call, biggest first, then in reading order.

    Lines are first cleaned the way single lines are (noise and duplicates
    dropped), then grouped; the largest blocks win the budget because they
    are the sign the player is looking at, and the survivors are returned in
    reading order so the results read naturally.
    """

    cleaned = worthwhile_line_objects(lines, None, keep_repeats=True)
    blocks = group_lines_into_blocks(cleaned)
    chosen = sorted(blocks, key=lambda block: block.area, reverse=True)[:limit]
    return sorted(chosen, key=lambda block: (block.top, block.left))


def worthwhile_lines(lines, limit: int = MAX_TRANSLATED_LINES) -> list[str]:
    """The texts of :func:`worthwhile_line_objects`, in the same order."""

    return [
        str(getattr(line, "text", line) or "").strip()
        for line in worthwhile_line_objects(lines, limit)
    ]


def _placed(line, translated: str, offset: tuple[int, int]) -> PlacedLine:
    """Lift a recognised line or block into frame coordinates."""

    original = str(getattr(line, "text", line) or "").strip()
    source_lines = getattr(line, "lines", None) or ()
    sample = original
    if source_lines:
        sample = max(
            (str(getattr(item, "text", "") or "").strip() for item in source_lines),
            key=len,
            default=original,
        )
    return PlacedLine(
        original=original,
        translated=translated,
        left=float(getattr(line, "left", 0.0) or 0.0) + offset[0],
        top=float(getattr(line, "top", 0.0) or 0.0) + offset[1],
        width=float(getattr(line, "width", 0.0) or 0.0),
        height=float(getattr(line, "height", 0.0) or 0.0),
        line_height=float(getattr(line, "line_height", 0.0) or 0.0),
        source_sample=sample,
        angle=_coordinate(line, "angle"),
    )


@dataclass(frozen=True)
class _Frame:
    """What the worker needs to know about the capture, without the pixels."""

    source: str = ""
    frame_size: tuple[int, int] = (0, 0)
    origin: tuple[int, int] = (0, 0)
    offset: tuple[int, int] = (0, 0)
    scale: float = 1.0
    anchor: object | None = None
    region: tuple[float, float, float, float] | None = None
    crop_size: tuple[int, int] = (0, 0)
    # The box the player asked for, in the (padded) crop's pixels.
    target: tuple[float, float, float, float] | None = None

    @classmethod
    def of(cls, capture, anchor, region) -> "_Frame":
        size = getattr(capture, "frame_size", None)
        if not isinstance(size, tuple):
            size = (
                int(getattr(capture, "width", 0) or 0),
                int(getattr(capture, "height", 0) or 0),
            )
        offset = getattr(capture, "offset", (0, 0))
        origin = getattr(capture, "origin", (0, 0))
        try:
            scale = float(getattr(capture, "scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        target = None
        if region and len(region) == 4:
            try:
                u0, v0, u1, v1 = (max(0.0, min(1.0, float(value))) for value in region)
                target = (
                    min(u0, u1) * int(size[0]) - int(offset[0]),
                    min(v0, v1) * int(size[1]) - int(offset[1]),
                    max(u0, u1) * int(size[0]) - int(offset[0]),
                    max(v0, v1) * int(size[1]) - int(offset[1]),
                )
            except (TypeError, ValueError):
                target = None
        return cls(
            source=str(getattr(capture, "source", "") or ""),
            frame_size=(int(size[0]), int(size[1])),
            origin=(int(origin[0]), int(origin[1])),
            offset=(int(offset[0]), int(offset[1])),
            scale=scale,
            anchor=anchor,
            region=tuple(region) if region else None,
            crop_size=(
                int(getattr(capture, "width", 0) or 0),
                int(getattr(capture, "height", 0) or 0),
            ),
            target=target,
        )

    def result(self, **kwargs) -> ScreenshotTranslation:
        return ScreenshotTranslation(
            source=self.source,
            frame_size=self.frame_size,
            origin=self.origin,
            scale=self.scale,
            anchor=self.anchor,
            region=self.region,
            offset=self.offset,
            crop_size=self.crop_size,
            **kwargs,
        )


class ScreenshotTranslator:
    """Runs the capture -> OCR -> translate pipeline off the UI thread."""

    def __init__(
        self,
        *,
        capture: Callable[[], object],
        recognize: Callable[[bytes, str], object],
        translate: Callable[[str], str],
        ocr_language: Callable[[], str],
        on_status: Callable[[str], None] | None = None,
        on_result: Callable[[ScreenshotTranslation], None] | None = None,
        crop: Callable[[object, Sequence[float]], object] | None = None,
        snapshot_anchor: Callable[[], object] | None = None,
    ) -> None:
        self._capture = capture
        self._recognize = recognize
        self._translate = translate
        self._ocr_language = ocr_language
        self._on_status = on_status
        self._on_result = on_result
        self._crop = crop
        self._snapshot_anchor = snapshot_anchor
        self._busy = threading.Lock()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _status(self, key: str) -> None:
        if callable(self._on_status):
            try:
                self._on_status(key)
            except Exception:
                logger.debug("Screenshot status callback failed", exc_info=True)

    def _finish(self, result: ScreenshotTranslation) -> None:
        if callable(self._on_result):
            try:
                self._on_result(result)
            except Exception:
                logger.debug("Screenshot result callback failed", exc_info=True)

    def _anchor(self):
        if not callable(self._snapshot_anchor):
            return None
        try:
            return self._snapshot_anchor()
        except Exception:
            logger.debug("Head anchor snapshot failed", exc_info=True)
            return None

    def trigger(self, region: Sequence[float] | None = None) -> bool:
        """Start a run. Returns False when one is already in flight.

        ``region`` is an optional (u0, v0, u1, v1) fraction of the frame to
        read; without it the whole view is read.
        """

        if not self._busy.acquire(blocking=False):
            logger.debug("Screenshot translation already running; press ignored")
            return False
        self._running = True
        self._status("capturing")

        # Capture here: the caller is the UI thread, which is the only place
        # Qt will hand over the screen. The head pose is read first: the
        # compositor's frame was drawn for the pose the head has *now*, and
        # reading the frame plus encoding it takes long enough for a turning
        # head to move the labels off their sign if the pose came after.
        try:
            anchor = self._anchor()
            capture = self._capture()
            if region and callable(self._crop) and getattr(capture, "png", b""):
                # Read a little beyond the box so a sliced line comes out
                # whole; the picture shrinks back to the lines afterwards.
                capture = self._crop(capture, padded_region(region))
        except Exception:
            logger.exception("Screen capture failed")
            self._running = False
            self._busy.release()
            self._finish(ScreenshotTranslation(error="capture_failed"))
            return True

        png = bytes(getattr(capture, "png", b"") or b"")
        frame = _Frame.of(capture, anchor, region)
        if not png:
            self._running = False
            self._busy.release()
            self._finish(frame.result(error="capture_failed"))
            return True

        thread = threading.Thread(
            target=self._run,
            args=(png, frame),
            name="screenshot-translate",
            daemon=True,
        )
        thread.start()
        return True

    def _translate_texts(self, texts: Sequence[str]) -> dict[str, tuple[str, Exception | None]]:
        """Translate each distinct text once, a few at a time.

        Returns, per text, the translation and the exception that stopped it
        (one of the two is empty). Order of failures is the order of the
        texts, so the first failure reported is the first block on the sign.
        """

        unique: list[str] = []
        for text in texts:
            if text and text not in unique:
                unique.append(text)
        if not unique:
            return {}

        def one(text: str) -> tuple[str, Exception | None]:
            try:
                return (str(self._translate(text) or "").strip(), None)
            except Exception as exc:
                return ("", exc)

        workers = max(1, min(TRANSLATION_WORKERS, len(unique)))
        if workers == 1:
            return {text: one(text) for text in unique}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="screenshot-translate") as pool:
            return dict(zip(unique, pool.map(one, unique)))

    def _snap_to_lines(self, png: bytes, frame: _Frame, lines: list) -> tuple[bytes, _Frame, list]:
        """Keep the lines the box was on and cut the picture down to them.

        The box was read with padding so that no line is sliced; a line
        whose middle lay outside the box was only caught by the padding and
        goes. If the aim missed every line, the padding's lines stand.
        """

        target = frame.target
        if target is None:
            return png, frame, lines
        inside = lines_in_target(lines, target)
        extent = lines_extent(inside, frame.crop_size) if inside else None
        logger.info(
            "Box read: crop %dx%d at %s, box (%d,%d)-(%d,%d) in it, lines %d of %d inside, cut to %s",
            frame.crop_size[0],
            frame.crop_size[1],
            frame.offset,
            int(target[0]),
            int(target[1]),
            int(target[2]),
            int(target[3]),
            len(inside),
            len(lines),
            extent if extent else "nothing",
        )
        if not inside:
            return png, frame, lines
        if extent is None or extent == (0, 0, frame.crop_size[0], frame.crop_size[1]):
            return png, frame, inside
        x0, y0, x1, y1 = extent
        try:
            from PySide6.QtCore import QRect
            from PySide6.QtGui import QImage

            from src.core.screen_capture import encode_png

            image = QImage.fromData(png, "PNG")
            if image.isNull():
                return png, frame, inside
            cropped = image.copy(QRect(x0, y0, x1 - x0, y1 - y0))
            encoded = encode_png(cropped)
            if not encoded:
                return png, frame, inside
        except Exception:
            logger.debug("Could not cut the picture down to its lines", exc_info=True)
            return png, frame, inside
        shifted = [_shifted(line, x0, y0) for line in inside]
        snapped = replace(
            frame,
            offset=(frame.offset[0] + x0, frame.offset[1] + y0),
            crop_size=(cropped.width(), cropped.height()),
        )
        return encoded, snapped, shifted

    def _run(self, png: bytes, frame: _Frame) -> None:
        try:
            self._status("reading")
            language = ""
            try:
                language = str(self._ocr_language() or "")
            except Exception:
                logger.debug("Failed to resolve the OCR language", exc_info=True)
            started = time.perf_counter()
            recognized = self._recognize(png, language)
            ocr_ms = (time.perf_counter() - started) * 1000.0

            error = str(getattr(recognized, "error", "") or "")
            if error:
                self._finish(frame.result(error=error, ocr_ms=ocr_ms))
                return

            lines = list(getattr(recognized, "lines", []) or [])
            if frame.target is not None:
                png, frame, lines = self._snap_to_lines(png, frame, lines)
            limit = MAX_REGION_LINES if frame.region else MAX_TRANSLATED_LINES
            originals = worthwhile_blocks(lines, limit)
            if not originals:
                self._finish(frame.result(error="no_text_found", ocr_ms=ocr_ms))
                return

            self._status("translating")
            started = time.perf_counter()
            texts = [str(getattr(line, "text", line) or "").strip() for line in originals]
            answers = self._translate_texts(texts)
            pairs: list[tuple[str, str]] = []
            placed: list[PlacedLine] = []
            failure_kind = ""
            for line, original in zip(originals, texts):
                translated, failure = answers.get(original, ("", None))
                if failure is not None:
                    # One kind per run: the player hears about the first
                    # failure once, not once per line, and again only when
                    # they press the button again.
                    if not failure_kind:
                        failure_kind = classify_translation_error(failure)
                        logger.info(
                            "Screenshot translation failed (%s): %s", failure_kind, failure
                        )
                    continue
                if not translated:
                    # The provider answered with nothing; the original still
                    # carries information, so show it rather than drop it.
                    translated = original
                pairs.append((original, translated))
                placed.append(_placed(line, translated, frame.offset))
            translate_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "Screenshot: source=%s frame=%dx%d region=%s lines=%d blocks=%d "
                "translated=%d ocr=%.0fms translate=%.0fms",
                frame.source,
                frame.frame_size[0],
                frame.frame_size[1],
                "yes" if frame.region else "no",
                len(lines),
                len(originals),
                len(pairs),
                ocr_ms,
                translate_ms,
            )

            if not pairs and failure_kind:
                self._finish(
                    frame.result(
                        error="translation_failed",
                        error_kind=failure_kind,
                        ocr_ms=ocr_ms,
                        translate_ms=translate_ms,
                    )
                )
                return
            self._finish(
                frame.result(
                    pairs=pairs,
                    lines=placed,
                    error_kind=failure_kind,
                    ocr_ms=ocr_ms,
                    translate_ms=translate_ms,
                    png=png,
                )
            )
        except Exception:
            logger.exception("Screenshot translation failed")
            self._finish(frame.result(error="failed"))
        finally:
            self._running = False
            self._busy.release()
