# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Grab what the player is looking at, for screenshot translation.

Prefers VRChat's own window: in VR that window mirrors the headset view, so it
holds exactly the nameplates and signs the player is reading.

Three things here were settled by measurement against a live VR session:

* **The window must be read through PrintWindow, not the screen.** A screen
  copy of the window's rectangle returns whatever is stacked on top of it - and
  with the player in a headset, that is routinely an IDE or a browser. With
  ``PW_RENDERFULLCONTENT`` the compositor hands over the window's own surface
  even while it is covered.
* **Downscaling costs real text.** At 1600 px wide the recognizer dropped the
  one sentence on a world panel worth translating; at native width it kept it,
  for about 120 ms more. The cap is therefore generous.
* **Compressing the PNG is the slowest step by far.** The bytes never leave
  the process, so they are stored rather than deflated.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from PySide6.QtCore import QBuffer, QByteArray, QRect
from PySide6.QtGui import QGuiApplication, QImage

logger = logging.getLogger(__name__)

VRCHAT_WINDOW_CLASSES = ("UnityWndClass",)
VRCHAT_TITLE_HINTS = ("vrchat",)
# Wider than any single VR mirror in practice; only truly enormous captures
# (multi-monitor desktops) get scaled, and recognition keeps its small text.
MAX_CAPTURE_WIDTH = 2560
# A region smaller than this is a click, not a selection.
MIN_CROP_PX = 8

_PW_CLIENTONLY = 0x00000001
_PW_RENDERFULLCONTENT = 0x00000002
_SW_SHOWNOACTIVATE = 4
_SW_SHOWMINNOACTIVE = 7
# A minimised window has no surface to print. It is restored without focus,
# read as soon as the compositor has drawn it (measured: ~400 ms), and put
# back the way the player left it.
_RESTORE_WAIT_S = 1.2
_RESTORE_POLL_S = 0.05
# The PNG never leaves the process, so compression buys nothing and costs the
# most: a full VR mirror took ~800 ms to deflate against ~75 ms stored, and the
# recognizer decodes the stored form faster too.
_PNG_STORED_QUALITY = 100


@dataclass(frozen=True)
class Capture:
    """A captured frame plus where it came from and where it sits."""

    png: bytes
    width: int
    height: int
    source: str
    scale: float = 1.0
    # Top-left of the captured client area on the desktop, in screen pixels,
    # so a desktop overlay can be laid straight over it.
    origin: tuple[int, int] = (0, 0)
    # The whole frame this capture belongs to, in the same (scaled) pixel space
    # as ``width``/``height``, and where this capture starts inside it. A
    # whole-frame capture has ``offset`` (0, 0) and frame == its own size.
    frame_width: int = 0
    frame_height: int = 0
    offset: tuple[int, int] = (0, 0)
    hwnd: int = 0
    # For a frame read from the headset: the eye's raw projection tangents
    # (left, right, top, bottom), which map every pixel to a direction.
    tangents: tuple[float, float, float, float] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.png)

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self.frame_width or self.width, self.frame_height or self.height)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # Handles are pointer-sized; the ctypes default of a 32-bit int truncates
    # them on 64-bit Windows and the next GDI call faults.
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    return user32


def _gdi32():
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    return gdi32


def find_vrchat_window() -> int:
    """Return VRChat's top-level window handle, or 0 when it is not running."""

    try:
        user32 = _user32()
    except Exception:
        logger.debug("user32 unavailable", exc_info=True)
        return 0

    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.strip().lower()
            if not any(hint in title for hint in VRCHAT_TITLE_HINTS):
                return True
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            if class_buffer.value in VRCHAT_WINDOW_CLASSES:
                matches.append(int(hwnd))
        except Exception:
            logger.debug("Window enumeration step failed", exc_info=True)
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        logger.debug("EnumWindows failed", exc_info=True)
        return 0
    return matches[0] if matches else 0


def encode_image(image: QImage) -> bytes:
    """A stored (uncompressed) PNG of the image; empty on failure."""

    return _encode(image)


def encode_png(image: QImage) -> bytes:
    """PNG bytes of an image, or b"" when Qt cannot encode it."""

    return _encode(image)


def _encode(image: QImage) -> bytes:
    # The QBuffer only borrows the array; a temporary would be freed under it.
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer, "PNG", _PNG_STORED_QUALITY):
            return b""
    finally:
        buffer.close()
    return bytes(storage.data())


def _to_png(
    image: QImage,
    source: str,
    *,
    origin: tuple[int, int] = (0, 0),
    hwnd: int = 0,
) -> Capture:
    if image.isNull():
        return Capture(b"", 0, 0, source)

    scale = 1.0
    if image.width() > MAX_CAPTURE_WIDTH:
        scale = MAX_CAPTURE_WIDTH / float(image.width())
        image = image.scaledToWidth(MAX_CAPTURE_WIDTH)

    png = _encode(image)
    if not png:
        return Capture(b"", 0, 0, source)
    return Capture(
        png=png,
        width=image.width(),
        height=image.height(),
        source=source,
        scale=scale,
        origin=(int(origin[0]), int(origin[1])),
        frame_width=image.width(),
        frame_height=image.height(),
        hwnd=int(hwnd or 0),
    )


def _client_origin(user32, hwnd: int) -> tuple[int, int]:
    point = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        return (0, 0)
    return (int(point.x), int(point.y))


def _print_window_image(hwnd: int) -> QImage:
    """Read a window's own client surface, covered or not."""

    user32 = _user32()
    gdi32 = _gdi32()
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return QImage()
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return QImage()

    screen_dc = user32.GetDC(0)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(
            hwnd, memory_dc, _PW_CLIENTONLY | _PW_RENDERFULLCONTENT
        ):
            return QImage()
        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height  # top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0
        ):
            return QImage()
        # GDI hands back BGRA; QImage's ARGB32 has that byte order in memory.
        # QImage does not own the bytes it is handed, so keep them alive until
        # the copy below has its own storage.
        raw = pixels.raw
        image = QImage(raw, width, height, width * 4, QImage.Format.Format_ARGB32)
        owned = image.copy()
        del image
        return owned
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(0, screen_dc)


def _looks_drawn(image: QImage) -> bool:
    """A freshly restored window prints black until the compositor catches up."""

    if image.isNull():
        return False
    width, height = image.width(), image.height()
    seen = set()
    for i in range(1, 5):
        for j in range(1, 5):
            seen.add(image.pixel(width * i // 5, height * j // 5) & 0x00FFFFFF)
            if len(seen) > 1:
                return True
    return False


def _print_restored(user32, hwnd: int) -> QImage:
    """Print a minimised window by restoring it quietly for a moment."""

    user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    image = QImage()
    try:
        deadline = time.monotonic() + _RESTORE_WAIT_S
        while time.monotonic() < deadline:
            time.sleep(_RESTORE_POLL_S)
            image = _print_window_image(hwnd)
            if _looks_drawn(image):
                break
    finally:
        # Put it back the way the player had it, still without stealing focus.
        user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)
    return image


def capture_window(hwnd: int) -> Capture:
    if not hwnd:
        return Capture(b"", 0, 0, "window")
    origin = (0, 0)
    try:
        user32 = _user32()
        if user32.IsIconic(int(hwnd)):
            image = _print_restored(user32, int(hwnd))
        else:
            image = _print_window_image(int(hwnd))
        origin = _client_origin(user32, int(hwnd))
    except Exception:
        logger.debug("PrintWindow capture failed", exc_info=True)
        image = QImage()
    return _to_png(image, "vrchat", origin=origin, hwnd=int(hwnd))


def capture_primary_screen() -> Capture:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return Capture(b"", 0, 0, "screen")
    try:
        pixmap = screen.grabWindow(0)
        geometry = screen.geometry()
        origin = (int(geometry.x()), int(geometry.y()))
    except Exception:
        logger.debug("Screen capture failed", exc_info=True)
        return Capture(b"", 0, 0, "screen")
    return _to_png(pixmap.toImage(), "screen", origin=origin)


def capture_for_translation(
    prefer_vrchat: bool = True, *, allow_screen_fallback: bool = True
) -> Capture:
    """Capture VRChat if it is up, otherwise the primary screen.

    With ``allow_screen_fallback`` off (a player in a headset), a missing or
    unreadable VRChat window is an error rather than a desktop grab: the
    desktop is not what they are looking at, and reading it would put IDE
    text over their view.
    """

    if prefer_vrchat:
        hwnd = find_vrchat_window()
        if hwnd:
            capture = capture_window(hwnd)
            if capture.ok:
                return capture
            logger.debug("VRChat window found but capture was empty")
            if not allow_screen_fallback:
                return Capture(b"", 0, 0, "vrchat")
        elif not allow_screen_fallback:
            return Capture(b"", 0, 0, "vrchat_missing")
    return capture_primary_screen()


def crop_capture(capture: Capture, region: Sequence[float] | None) -> Capture:
    """Cut a normalised (u0, v0, u1, v1) region out of a capture.

    Coordinates are fractions of the *frame*, so a crop of a crop still lands
    in the right place. A region too small to hold text returns the capture
    unchanged: a click means "all of it".
    """

    if not capture.ok or not region or len(region) != 4:
        return capture
    frame_w, frame_h = capture.frame_size
    try:
        u0, v0, u1, v1 = (max(0.0, min(1.0, float(v))) for v in region)
    except (TypeError, ValueError):
        return capture
    x0 = int(round(u0 * frame_w)) - capture.offset[0]
    y0 = int(round(v0 * frame_h)) - capture.offset[1]
    x1 = int(round(u1 * frame_w)) - capture.offset[0]
    y1 = int(round(v1 * frame_h)) - capture.offset[1]
    x0, x1 = max(0, min(x0, x1)), min(capture.width, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(capture.height, max(y0, y1))
    if (x1 - x0) < MIN_CROP_PX or (y1 - y0) < MIN_CROP_PX:
        return capture

    image = QImage.fromData(capture.png, "PNG")
    if image.isNull():
        return capture
    cropped = image.copy(QRect(x0, y0, x1 - x0, y1 - y0))
    png = _encode(cropped)
    if not png:
        return capture
    return replace(
        capture,
        png=png,
        width=cropped.width(),
        height=cropped.height(),
        # The crop belongs to the same frame as the capture it came from; a
        # whole-frame capture that never named its frame names it now.
        frame_width=frame_w,
        frame_height=frame_h,
        offset=(capture.offset[0] + x0, capture.offset[1] + y0),
    )


def vrchat_frame_size() -> tuple[int, int]:
    """The size a capture would have, before it is taken.

    The selection frame must share the frame's aspect so a rectangle drawn on
    it maps onto the same fraction of the capture; this reads VRChat's client
    size (or the primary screen's) without capturing anything.
    """

    hwnd = find_vrchat_window()
    if hwnd:
        try:
            rect = wintypes.RECT()
            if (
                _user32().GetClientRect(hwnd, ctypes.byref(rect))
                and rect.right > 0
                and rect.bottom > 0
            ):
                return (int(rect.right), int(rect.bottom))
        except Exception:
            logger.debug("GetClientRect failed", exc_info=True)
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return (1920, 1080)
    geometry = screen.geometry()
    return (max(1, int(geometry.width())), max(1, int(geometry.height())))
