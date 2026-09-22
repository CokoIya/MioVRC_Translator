# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Read on-screen text with the recognizer Windows already ships.

Windows 10/11 carry an offline OCR engine with Chinese, Japanese and Korean
language packs. Using it keeps screenshot translation free, private and - the
part that matters for this project - adds about two megabytes to the installer
instead of the several hundred a bundled OCR model would cost.

Two things here were found by measurement rather than by reading docs:

* **Recognition has to run on its own thread.** Qt initialises COM as a
  single-threaded apartment and the WinRT async APIs want a multi-threaded one;
  calling them from the UI thread takes the process down with no traceback.
* **CJK results arrive with a space between every character.** Passing that
  straight to a translator wrecks the output, so spacing is repaired before the
  text leaves this module.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

OCR_TIMEOUT_SECONDS = 20.0

# Ranges where a space between two characters is an artefact of recognition
# rather than a word boundary: CJK ideographs, kana, Hangul and full-width
# punctuation.
_CJK_CHARACTER = re.compile(
    r"[　-〿぀-ヿ㐀-䶿一-鿿"
    r"豈-﫿＀-￯가-힯]"
)
# Punctuation never needs a space on the side facing it; the recognizers put
# one there anyway when the surrounding script is CJK.
_PUNCTUATION = re.compile(r"[!-/:-@\[-`{-~　-〿！-｠]")

_LANGUAGE_ALIASES = {
    "zh": "zh-Hans-CN",
    "zh-cn": "zh-Hans-CN",
    "zh-hans": "zh-Hans-CN",
    "cn": "zh-Hans-CN",
    "chinese": "zh-Hans-CN",
    "zh-tw": "zh-Hant-TW",
    "zh-hant": "zh-Hant-TW",
    "jp": "ja",
    "japanese": "ja",
    "korean": "ko",
    "english": "en-US",
    "en": "en-US",
}


@dataclass(frozen=True)
class OcrLine:
    """One recognised line and where it sat in the captured image.

    The box is oriented: ``(left, top)`` is its top-left corner, ``width``
    runs along the text's own direction, ``height`` across it, and ``angle``
    is that direction in degrees, clockwise on screen (positive when the line
    descends to the right). Signs in a headset are seen from the side far
    more often than head-on, and an upright box around a tilted line is
    taller than its letters by the line's whole rise - enough to size every
    plate wrong and to merge lines that never touched.
    """

    text: str
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


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    language: str = ""
    error: str = ""
    # The tilt the recognizer straightened out, in degrees; boxes are already
    # mapped back, this is kept for diagnostics.
    text_angle: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text)


def unrotate_rect(
    left: float,
    top: float,
    width: float,
    height: float,
    angle_degrees: float,
    center: tuple[float, float],
) -> tuple[float, float, float, float]:
    """Map a rectangle the recognizer reported back onto the source image.

    Windows OCR straightens tilted text before reading it and reports every
    bounding rectangle in that straightened frame: ``OcrResult.TextAngle`` is
    the rotation, about the image centre, that turns those rectangles back
    into image pixels. Text on a sign seen at an angle in VR is routinely
    tilted several degrees, which put every plate a whole line off until this
    was applied. The result is the axis-aligned box around the rotated corners.
    """

    if not angle_degrees:
        return (left, top, width, height)
    cx, cy = center
    cos_a = math.cos(math.radians(angle_degrees))
    sin_a = math.sin(math.radians(angle_degrees))
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((left, top), (left + width, top), (left, top + height), (left + width, top + height)):
        dx, dy = x - cx, y - cy
        xs.append(cx + dx * cos_a - dy * sin_a)
        ys.append(cy + dx * sin_a + dy * cos_a)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def oriented_from_deskewed(
    left: float,
    top: float,
    width: float,
    height: float,
    angle_degrees: float,
    center: tuple[float, float],
) -> tuple[float, float, float, float, float]:
    """The oriented box of a rectangle reported in the recognizer's straightened frame.

    Where :func:`unrotate_rect` wraps an upright box around the rotated
    corners, this keeps the rectangle's own size and records the tilt, so a
    tilted line keeps its true height. Returns (left, top, width, height,
    angle) with ``(left, top)`` the top-left corner in image pixels.
    """

    if not angle_degrees:
        return (left, top, width, height, 0.0)
    cx, cy = center
    cos_a = math.cos(math.radians(angle_degrees))
    sin_a = math.sin(math.radians(angle_degrees))
    dx, dy = left - cx, top - cy
    return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a, width, height, angle_degrees)


def oriented_box_from_quad(points) -> tuple[float, float, float, float, float]:
    """(left, top, width, height, angle) of a four-corner text box.

    ``points`` are (x, y) pairs in the order top-left, top-right, bottom-right,
    bottom-left - the order every PaddleOCR-family detector reports. Width is
    measured along the top and bottom edges and height across the left and
    right ones, so the box keeps the glyphs' real height whatever its tilt.
    """

    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = [(float(p[0]), float(p[1])) for p in points]
    ux = ((x1 - x0) + (x2 - x3)) / 2.0
    uy = ((y1 - y0) + (y2 - y3)) / 2.0
    width = math.hypot(ux, uy)
    height = (math.hypot(x3 - x0, y3 - y0) + math.hypot(x2 - x1, y2 - y1)) / 2.0
    angle = math.degrees(math.atan2(uy, ux)) if width > 0 else 0.0
    return (x0, y0, width, height, angle)


def normalize_recognized_text(text: str) -> str:
    """Undo the per-character spacing the CJK recognizers emit.

    ``こ ん に ち は`` is one word to a reader and five to a translator; leaving
    the spaces in place measurably degrades the translation.
    """

    collapsed = re.sub(r"[ \t　]+", " ", str(text or "")).strip()
    if not collapsed:
        return ""
    out: list[str] = []
    for index, character in enumerate(collapsed):
        if character != " ":
            out.append(character)
            continue
        previous = collapsed[index - 1] if index else ""
        following = collapsed[index + 1] if index + 1 < len(collapsed) else ""
        # A space is an artefact when it separates two CJK characters, or when
        # it sits beside punctuation ("こんにちは ! " and "20 : 00"). Two Latin
        # letters always keep theirs, so "Hello world" survives intact.
        both_cjk = bool(
            _CJK_CHARACTER.match(previous) and _CJK_CHARACTER.match(following)
        )
        touches_punctuation = bool(
            _PUNCTUATION.match(previous) or _PUNCTUATION.match(following)
        )
        if both_cjk or touches_punctuation:
            continue
        out.append(character)
    return "".join(out).strip()


def normalize_ocr_language(language: object) -> str:
    """Map a translation language code onto a Windows recognizer tag."""

    text = str(language or "").strip()
    if not text:
        return ""
    return _LANGUAGE_ALIASES.get(text.lower(), text)


def ocr_runtime_available() -> bool:
    """True when the WinRT OCR bindings are installed at all.

    Kept apart from the language list so the settings page can tell "the
    component is missing from this install" from "Windows has no language
    pack": the fixes are different and a player cannot guess which applies.
    """

    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except Exception:
        return False
    return True


def available_ocr_languages() -> list[str]:
    """Return the recognizer language tags installed on this machine."""

    try:
        from winrt.windows.media.ocr import OcrEngine
    except Exception:
        logger.debug("Windows OCR is unavailable", exc_info=True)
        return []
    try:
        return [
            str(language.language_tag)
            for language in OcrEngine.available_recognizer_languages
        ]
    except Exception:
        logger.debug("Failed to list OCR recognizer languages", exc_info=True)
        return []


def pick_ocr_language(preferred: object, installed: list[str] | None = None) -> str:
    """Choose the closest installed recognizer to what the player wants."""

    languages = installed if installed is not None else available_ocr_languages()
    if not languages:
        return ""
    wanted = normalize_ocr_language(preferred)
    if not wanted:
        return languages[0]
    lowered = {tag.lower(): tag for tag in languages}
    if wanted.lower() in lowered:
        return lowered[wanted.lower()]
    # "ja" should still match "ja-JP", and "zh-Hans-CN" should match "zh-Hans".
    prefix = wanted.split("-", 1)[0].lower()
    for tag in languages:
        if tag.lower().split("-", 1)[0] == prefix:
            return tag
    return languages[0]


async def _recognize_async(png: bytes, language_tag: str) -> OcrResult:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = OcrEngine.try_create_from_language(Language(language_tag))
    if engine is None:
        return OcrResult(error="language_unavailable", language=language_tag)

    recognized = await engine.recognize_async(bitmap)
    angle = getattr(recognized, "text_angle", None)
    try:
        angle = float(angle) if angle is not None else 0.0
    except (TypeError, ValueError):
        angle = 0.0
    center = (float(bitmap.pixel_width) / 2.0, float(bitmap.pixel_height) / 2.0)
    lines: list[OcrLine] = []
    for line in recognized.lines:
        words = list(line.words)
        if not words:
            continue
        text = normalize_recognized_text(line.text)
        if not text:
            continue
        # Word boxes are reported in the straightened frame, where their
        # union is exact; the line then carries the tilt as its angle.
        left = min(float(word.bounding_rect.x) for word in words)
        top = min(float(word.bounding_rect.y) for word in words)
        right = max(float(word.bounding_rect.x) + float(word.bounding_rect.width) for word in words)
        bottom = max(float(word.bounding_rect.y) + float(word.bounding_rect.height) for word in words)
        x, y, width, height, tilt = oriented_from_deskewed(
            left, top, right - left, bottom - top, angle, center
        )
        lines.append(
            OcrLine(
                text=text,
                left=float(x),
                top=float(y),
                width=float(width),
                height=float(height),
                angle=float(tilt),
            )
        )
    return OcrResult(lines=lines, language=language_tag, text_angle=angle)


def recognize_png(
    png: bytes,
    language_tag: str,
    *,
    timeout: float = OCR_TIMEOUT_SECONDS,
) -> OcrResult:
    """Recognise a PNG image. Blocking; call it off the UI thread.

    The work is handed to a dedicated thread even when the caller is already a
    worker, because the COM apartment the recognizer needs must not be the one
    Qt set up.
    """

    if not png:
        return OcrResult(error="empty_image", language=language_tag)
    if not language_tag:
        return OcrResult(error="language_unavailable")

    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["result"] = asyncio.run(_recognize_async(png, language_tag))
        except Exception as exc:
            logger.debug("Screen OCR failed", exc_info=True)
            box["result"] = OcrResult(
                error=f"ocr_failed:{type(exc).__name__}", language=language_tag
            )

    thread = threading.Thread(target=run, name="screen-ocr", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return OcrResult(error="ocr_timeout", language=language_tag)
    result = box.get("result")
    if isinstance(result, OcrResult):
        return result
    return OcrResult(error="ocr_failed", language=language_tag)


def recognize_png_async(
    png: bytes,
    language_tag: str,
    callback: Callable[[OcrResult], None],
    *,
    timeout: float = OCR_TIMEOUT_SECONDS,
) -> threading.Thread:
    """Recognise in the background and hand the result to ``callback``."""

    def run() -> None:
        result = recognize_png(png, language_tag, timeout=timeout)
        try:
            callback(result)
        except Exception:
            logger.debug("Screen OCR callback failed", exc_info=True)

    thread = threading.Thread(target=run, name="screen-ocr-dispatch", daemon=True)
    thread.start()
    return thread
