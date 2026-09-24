# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Hand pictures to SteamVR overlays without making them blink.

``setOverlayRaw`` builds a brand-new texture on every call, and while the
compositor swaps it in the overlay is drawn with nothing - a visible flash.
Live subtitles update several times a second while someone speaks, so the
panel strobed. The cure SteamVR itself recommends is a texture that is
updated in place: one OpenGL texture per overlay, re-filled with
``glTexSubImage``-style uploads and handed over with ``setOverlayTexture``,
which the compositor keeps sampling from the same GPU memory.

Qt provides the GL context and the texture object, so no extra dependency
is needed. If anything about GL is unavailable the uploader falls back to
the raw path, which still works - it just blinks.

Orientation was settled against the official OpenVR GL sample: SteamVR
treats a GL texture's first row as its bottom, so a top-down QImage must be
mirrored before upload.

One more thing SteamVR does, measured by reading overlays back with
``getOverlayImageData``: it keeps an overlay's GL texture at the size of the
FIRST ``setOverlayTexture`` for the life of the overlay handle. A later
texture of another size - same GL name or a new one, even after
``clearOverlayTexture`` - is copied into that first-sized store, cropped or
not at all, and a raw upload in between does not free it either. The
subtitle board grew with every line and so showed only its first, empty
picture; a card of a new size kept the previous card. So every overlay now
uploads pictures on one fixed-size canvas: the picture is drawn centred on
it, transparent elsewhere, and the owner scales the overlay's width by
:attr:`OverlayTextureUploader.scale` so the picture keeps the size it asked
for.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Any

logger = logging.getLogger(__name__)

_context: Any | None = None
_surface: Any | None = None
_context_failed = False


def _shared_context():
    """One offscreen GL context for the process, created on first use."""

    global _context, _surface, _context_failed
    if _context is not None:
        return _context, _surface
    if _context_failed:
        return None, None
    try:
        from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat

        if QGuiApplication.instance() is None:
            raise RuntimeError("no QGuiApplication")
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        context = QOpenGLContext()
        context.setFormat(fmt)
        if not context.create() or not surface.isValid():
            raise RuntimeError("GL context creation failed")
        if not context.makeCurrent(surface):
            raise RuntimeError("GL makeCurrent failed")
        context.doneCurrent()
    except Exception:
        logger.info("OpenGL texture path unavailable; overlays will use raw uploads", exc_info=True)
        _context_failed = True
        return None, None
    _context, _surface = context, surface
    return _context, _surface


def gl_available() -> bool:
    context, _surface = _shared_context()
    return context is not None


class OverlayTextureUploader:
    """Owns one GL texture for one overlay handle; falls back to raw bytes."""

    def __init__(
        self,
        openvr_module: Any,
        overlay: Any,
        handle: Any,
        *,
        canvas: tuple[int, int] | None = None,
    ) -> None:
        self._openvr = openvr_module
        self._overlay = overlay
        self._handle = handle
        self._texture: Any | None = None
        self._size: tuple[int, int] | None = None
        self._gl_broken = False
        self._last_path = ""
        # The fixed texture size (see the module note). None: the first
        # picture's size, which every later picture is then fitted into.
        self._canvas: tuple[int, int] | None = (
            (max(1, int(canvas[0])), max(1, int(canvas[1]))) if canvas else None
        )
        self._drawn: tuple[int, int] | None = None
        self._scale = 1.0

    @property
    def canvas(self) -> tuple[int, int] | None:
        return self._canvas

    @property
    def drawn_size(self) -> tuple[int, int] | None:
        """The picture's size inside the texture after the last upload."""

        return self._drawn

    @property
    def scale(self) -> float:
        """Overlay width over picture width after the last upload.

        The overlay is as wide as its whole texture, so a picture drawn on a
        wider canvas needs the overlay set to ``wanted_width * scale`` metres
        to appear ``wanted_width`` wide. 1.0 on the raw path and when the
        picture fills the canvas.
        """

        return self._scale

    def quad_width(self, picture_width_meters: float) -> float:
        """The overlay width that shows the last picture at the given width."""

        return max(0.01, float(picture_width_meters)) * self._scale

    def picture_fraction(self, u: float, v: float) -> tuple[float, float]:
        """Map a point on the texture to the same point on the last picture.

        ``u``/``v`` are texture fractions with ``v`` counting from the top
        (see ``mouse_y_is_top_down``). Pointer events and ray hits land on the
        whole texture; once a picture of another size was letterboxed onto the
        pinned canvas, the texture's margins are not picture, and using the
        raw fraction put selections and button hits off by the margin. A point
        in the margin maps outside 0..1.
        """

        canvas, drawn = self._canvas, self._drawn
        if self._last_path != "gl" or not canvas or not drawn or drawn == canvas:
            return (float(u), float(v))
        canvas_w, canvas_h = canvas
        drawn_w, drawn_h = max(1, drawn[0]), max(1, drawn[1])
        x = (float(u) * canvas_w - (canvas_w - drawn_w) // 2) / drawn_w
        y = (float(v) * canvas_h - (canvas_h - drawn_h) // 2) / drawn_h
        return (x, y)

    def _compose(self, image: Any) -> tuple[Any, tuple[int, int]]:
        """Fit the picture on the canvas, centred; returns (canvas, drawn size)."""

        width, height = image.width(), image.height()
        if self._canvas is None:
            self._canvas = (width, height)
        canvas_w, canvas_h = self._canvas
        if (width, height) == (canvas_w, canvas_h):
            return image, (width, height)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter

        scaled = image
        if width > canvas_w or height > canvas_h:
            scaled = image.scaled(
                canvas_w,
                canvas_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_RGBA8888)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        try:
            painter.drawImage(
                (canvas_w - scaled.width()) // 2, (canvas_h - scaled.height()) // 2, scaled
            )
        finally:
            painter.end()
        return canvas, (scaled.width(), scaled.height())

    @property
    def mouse_y_is_top_down(self) -> bool:
        """How SteamVR reports mouse y on this overlay.

        Mouse coordinates are in the texture's own space, first row = 0. The
        GL path uploads the picture mirrored (its first row is the bottom),
        so y counts down from the top of the picture; the raw path's first
        row is the top, so y counts up from the bottom. Measured live: the
        selection rectangle ran the wrong way until this was honoured.
        """

        return self._last_path == "gl"

    def release(self) -> None:
        texture, self._texture = self._texture, None
        self._size = None
        if texture is None:
            return
        context, surface = _shared_context()
        try:
            if context is not None and context.makeCurrent(surface):
                try:
                    texture.destroy()
                finally:
                    context.doneCurrent()
        except Exception:
            logger.debug("Failed to release an overlay texture", exc_info=True)

    # ------------------------------------------------------------ uploads
    def upload_image(self, image: Any) -> bool:
        """Upload a QImage; True when SteamVR accepted it by either path."""

        if image is None or image.isNull():
            return False
        if not self._gl_broken:
            try:
                composed, drawn = self._compose(image)
            except Exception:
                logger.debug("Could not compose the picture on the canvas", exc_info=True)
                composed, drawn = image, (image.width(), image.height())
            if self._upload_gl(composed):
                self._last_path = "gl"
                self._drawn = drawn
                self._scale = composed.width() / float(max(1, drawn[0]))
                return True
        if self._upload_raw_image(image):
            # The raw path makes a new texture of the picture's own size
            # every time, so nothing needs scaling.
            self._last_path = "raw"
            self._drawn = (image.width(), image.height())
            self._scale = 1.0
            return True
        return False

    def upload_raw(self, buffer: Any, width: int, height: int) -> bool:
        """The raw path for callers that only have bytes."""

        overlay, handle = self._overlay, self._handle
        try:
            overlay.setOverlayRaw(handle, buffer, int(width), int(height), 4)
        except Exception:
            logger.debug("setOverlayRaw failed", exc_info=True)
            return False
        self._last_path = "raw"
        self._drawn = (int(width), int(height))
        self._scale = 1.0
        return True

    def _upload_raw_image(self, image: Any) -> bool:
        try:
            from PySide6.QtGui import QImage

            converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
            raw = bytes(converted.constBits())
            buffer = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
            return self.upload_raw(buffer, converted.width(), converted.height())
        except Exception:
            logger.debug("Raw image upload failed", exc_info=True)
            return False

    def _upload_gl(self, image: Any) -> bool:
        context, surface = _shared_context()
        if context is None:
            self._gl_broken = True
            return False
        try:
            from PySide6.QtGui import QImage
            from PySide6.QtOpenGL import QOpenGLTexture
        except Exception:
            logger.debug("QtOpenGL unavailable", exc_info=True)
            self._gl_broken = True
            return False
        try:
            if not context.makeCurrent(surface):
                self._gl_broken = True
                return False
        except Exception:
            self._gl_broken = True
            return False
        try:
            # GL's first row is the bottom of the picture.
            flipped = image.convertToFormat(QImage.Format.Format_RGBA8888).flipped()
            size = (flipped.width(), flipped.height())
            if self._texture is None or self._size != size:
                if self._texture is not None:
                    self._texture.destroy()
                texture = QOpenGLTexture(flipped, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
                texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
                texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
                self._texture = texture
                self._size = size
            else:
                self._texture.setData(flipped, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
            # The runtime copies the texture from its own GL context as soon
            # as it is handed over. Wait for the upload to finish first, or
            # the copy can still read the previous picture.
            try:
                context.functions().glFinish()
            except Exception:
                logger.debug("glFinish unavailable", exc_info=True)
            openvr = self._openvr
            handle_struct = openvr.Texture_t()
            handle_struct.handle = ctypes.c_void_p(int(self._texture.textureId()))
            handle_struct.eType = openvr.TextureType_OpenGL
            handle_struct.eColorSpace = openvr.ColorSpace_Auto
            self._overlay.setOverlayTexture(self._handle, handle_struct)
            return True
        except Exception:
            logger.info("OpenGL overlay upload failed; falling back to raw uploads", exc_info=True)
            self._gl_broken = True
            return False
        finally:
            try:
                context.doneCurrent()
            except Exception:
                pass
