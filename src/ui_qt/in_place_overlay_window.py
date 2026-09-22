# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""A click-through window that lays translated plates over the screen.

For players without a headset (or with VRChat on the desktop): the capture
knows where its frame sits on the desktop, so the plates can be drawn straight
over the signs they came from. The window takes no input at all - it must not
steal a click from the game underneath - and it is a tool window so it never
shows up in the taskbar.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from src.ui_qt.in_place_painter import (
    DEFAULT_PLATE_OPACITY,
    paint_plates,
    paint_selection_frame,
    plates_for,
)

logger = logging.getLogger(__name__)


class DesktopInPlaceOverlay(QWidget):
    """Plates over the desktop, positioned from a screenshot result."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._plates: list = []
        self._frame_region: tuple[float, float, float, float] | None = None
        self._frame_hint = ""
        self._show_frame = False
        self._plate_opacity = DEFAULT_PLATE_OPACITY

    # ------------------------------------------------------------ geometry
    @staticmethod
    def screen_rect_for(result) -> QRect:
        """Where the result's frame sits on the desktop, in screen pixels."""

        frame_w, frame_h = getattr(result, "frame_size", (0, 0))
        origin = getattr(result, "origin", (0, 0))
        try:
            scale = float(getattr(result, "scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        if scale <= 0:
            scale = 1.0
        width = int(round(frame_w / scale))
        height = int(round(frame_h / scale))
        return QRect(int(origin[0]), int(origin[1]), max(1, width), max(1, height))

    # ------------------------------------------------------------ content
    def show_result(self, result, *, plate_opacity: float = DEFAULT_PLATE_OPACITY) -> bool:
        lines = list(getattr(result, "lines", []) or [])
        frame_size = getattr(result, "frame_size", (0, 0))
        if not lines or not frame_size[0] or not frame_size[1]:
            return False
        rect = self.screen_rect_for(result)
        self._plate_opacity = plate_opacity
        self._plates = plates_for(lines, frame_size, (rect.width(), rect.height()))
        self._show_frame = False
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()
        return True

    def show_frame(
        self,
        rect: QRect,
        region: tuple[float, float, float, float] | None,
        *,
        hint: str = "",
    ) -> None:
        """Show the selection frame over a screen rectangle."""

        self._plates = []
        self._frame_region = region
        self._frame_hint = hint
        self._show_frame = True
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def update_frame(self, region: tuple[float, float, float, float] | None) -> None:
        self._frame_region = region
        self.update()

    def clear(self) -> None:
        self._plates = []
        self._frame_region = None
        self._show_frame = False
        self.hide()

    # ------------------------------------------------------------ painting
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            if self._show_frame:
                paint_selection_frame(
                    painter,
                    (self.width(), self.height()),
                    self._frame_region,
                    hint=self._frame_hint,
                )
            if self._plates:
                paint_plates(painter, self._plates, plate_opacity=self._plate_opacity)
        finally:
            painter.end()
