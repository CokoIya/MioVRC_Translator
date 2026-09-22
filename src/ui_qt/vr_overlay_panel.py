# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Off-screen subtitle panel rendered for a SteamVR overlay.

This is deliberately not the desktop floating window. That window carries a
title pill, an opacity slider, a pin, a close button and a send button - none
of which a player can use through a headset, and all of which would eat the
panel. What belongs in VR is the conversation and nothing else, at a size that
survives being read from a metre and a half away.

The plate is drawn here rather than by a stylesheet so its alpha lands exactly
once. See ``render_rgba`` for why that matters.
"""

from __future__ import annotations

import ctypes
import logging
from collections import deque

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.ui_qt.theme import theme_tokens

logger = logging.getLogger(__name__)

MAX_VR_LINES = 4
# Wide and short, the way OVR Overlay Translator's board reads: a row is a
# sentence, not a chat bubble.
DEFAULT_PANEL_SIZE = (1200, 400)
# The panel grows to fit wrapped lines and shrinks back; a fixed height
# squeezed rows into half-visible strips once a line wrapped.
MIN_PANEL_HEIGHT = 140
MAX_PANEL_HEIGHT = 1100
DEFAULT_PLATE_OPACITY = 0.50
MIN_PLATE_OPACITY = 0.0
MAX_PLATE_OPACITY = 0.95
PLATE_RADIUS = 12
PANEL_MARGIN_X = 36
PANEL_MARGIN_Y = 22
# Text is read across a room, not across a desk. What was said sits above
# its translation and a touch larger, as on OVR's board.
BASE_TRANSLATION_PX = 30
BASE_ORIGINAL_PX = 32
# The headset panel does not follow the app font: the player asked for a
# fixed Song/Ming face (宋体) in the headset, and for plain rows without
# bubbles behind them.
PANEL_FONT_FAMILIES = '"SimSun", "宋体", "NSimSun", "MS Mincho", "Noto Serif CJK SC", "Microsoft YaHei UI"'
# White for what was said; amber for the translation of others and a light
# blue for the player's own line, so the two sides tell apart at a glance.
ORIGINAL_TEXT_COLOR = "#ffffff"
OTHER_TRANSLATION_COLOR = "#ffc857"
SELF_TRANSLATION_COLOR = "#5fd0ff"
SEPARATOR_COLOR = "rgba(255, 255, 255, 45)"


def clamp_plate_opacity(value: object, default: float = DEFAULT_PLATE_OPACITY) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return max(MIN_PLATE_OPACITY, min(MAX_PLATE_OPACITY, parsed))


class VROverlayPanel(QFrame):
    """Renders recent subtitles into an RGBA buffer for SetOverlayRaw."""

    def __init__(
        self,
        *,
        theme: str = "dark",
        size: tuple[int, int] = DEFAULT_PANEL_SIZE,
        plate_opacity: float = DEFAULT_PLATE_OPACITY,
    ) -> None:
        super().__init__(None)
        self._theme = str(theme or "dark")
        self._plate_opacity = clamp_plate_opacity(plate_opacity)
        self._entries: deque[tuple[str, str, str]] = deque(maxlen=MAX_VR_LINES)
        self._rows: list[tuple[QLabel, QLabel | None, QLabel | None]] = []

        self.setObjectName("vrPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(PANEL_MARGIN_X, PANEL_MARGIN_Y, PANEL_MARGIN_X, PANEL_MARGIN_Y)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.resize(*size)
        self._apply_style()

    # ------------------------------------------------------------------ state
    def set_theme(self, theme: str) -> None:
        self._theme = str(theme or "dark")
        self._apply_style()

    def set_plate_opacity(self, value: float) -> None:
        self._plate_opacity = clamp_plate_opacity(value)

    @property
    def plate_opacity(self) -> float:
        return self._plate_opacity

    def set_panel_size(self, width: int, height: int) -> None:
        self.resize(max(240, int(width)), max(120, int(height)))
        self._rebuild_rows()

    def clear(self) -> None:
        self._entries.clear()
        self._rebuild_rows()

    def add_message(
        self,
        *,
        translated: str,
        original: str = "",
        source: str = "listen",
    ) -> bool:
        text = str(translated or "").strip()
        if not text:
            return False
        self._entries.append((text, str(original or "").strip(), str(source or "listen")))
        self._rebuild_rows()
        return True

    # ------------------------------------------------------------------ render
    def render_rgba(self) -> tuple[ctypes.Array, int, int]:
        """Return (ctypes buffer, width, height) with per-pixel alpha.

        pyopenvr calls ``byref`` on the buffer, so it has to be a ctypes
        object rather than bytes. This is the raw-upload form; the flicker-free
        path takes :meth:`render_image` instead.
        """

        image = self.render_image()
        raw = bytes(image.constBits())
        buffer = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        return buffer, image.width(), image.height()

    def render_image(self) -> QImage:
        """The panel as an RGBA image with per-pixel alpha.

        The plate is painted here and only the child widgets are rendered on
        top. Letting ``QWidget.render`` draw the background instead composites
        the stylesheet colour over the window background, which doubles the
        alpha, and fills the rounded corners square.
        """

        image = QImage(self.size(), QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._plate_color())
            painter.drawRoundedRect(
                image.rect().adjusted(0, 0, -1, -1), PLATE_RADIUS, PLATE_RADIUS
            )
            self.render(
                painter,
                QPoint(0, 0),
                renderFlags=QWidget.RenderFlag.DrawChildren,
            )
        finally:
            painter.end()
        return image

    # ------------------------------------------------------------------ internals
    def _plate_color(self) -> QColor:
        # A near-black plate keeps white text legible over any world.
        alpha = int(round(self._plate_opacity * 255))
        return QColor(6, 8, 14, alpha)

    def _apply_style(self) -> None:
        # The theme only names the plate; the text colours are fixed so they
        # read over any world.
        theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QFrame#vrPanel {{ background: transparent; }}
            QLabel {{ background: transparent; font-family: {PANEL_FONT_FAMILIES}; }}
            QLabel#vrTranslation {{
                color: {OTHER_TRANSLATION_COLOR};
                font-size: {BASE_TRANSLATION_PX}px;
                font-weight: 700;
            }}
            QLabel#vrTranslationSelf {{
                color: {SELF_TRANSLATION_COLOR};
                font-size: {BASE_TRANSLATION_PX}px;
                font-weight: 700;
            }}
            QLabel#vrOriginal {{
                color: {ORIGINAL_TEXT_COLOR};
                font-size: {BASE_ORIGINAL_PX}px;
                font-weight: 700;
            }}
            QFrame#vrSeparator {{
                background: {SEPARATOR_COLOR};
                border: none;
            }}
            """
        )

    def _clear_rows(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

    def _rebuild_rows(self) -> None:
        """Rows the way OVR's board reads: what was said in white, its
        translation under it in colour, everything centred, a hairline
        between messages. No bubbles and no speaker tags - the colour of the
        translation already says whose line it is."""

        self._build_rows()
        # The newest line is the point of the board. When the rows outgrow
        # the tallest panel allowed, the oldest lines go: a board that kept
        # them clipped the newest line off its bottom and looked stuck.
        while len(self._entries) > 1 and self._needed_height() > MAX_PANEL_HEIGHT:
            self._entries.popleft()
            self._build_rows()
        self._fit_height()

    def _build_rows(self) -> None:
        self._clear_rows()
        entries = list(self._entries)
        for index, (translated, original, source) in enumerate(entries):
            is_self = source in {"manual", "mic"}
            row = QWidget(self)
            row.setObjectName("vrRow")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 4, 0, 4)
            row_layout.setSpacing(2)

            original_label: QLabel | None = None
            if original and original != translated:
                original_label = QLabel(original, row)
                original_label.setObjectName("vrOriginal")
                original_label.setWordWrap(True)
                original_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                row_layout.addWidget(original_label)

            translation = QLabel(translated, row)
            translation.setObjectName("vrTranslationSelf" if is_self else "vrTranslation")
            translation.setWordWrap(True)
            translation.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(translation)

            if index < len(entries) - 1:
                separator = QFrame(row)
                separator.setObjectName("vrSeparator")
                separator.setFixedHeight(1)
                row_layout.addSpacing(6)
                row_layout.addWidget(separator)

            self._layout.insertWidget(index, row)
            self._rows.append((translation, original_label, None))
        self._layout.activate()

    def _fit_height(self) -> None:
        """Resize to what the rows need, at the panel's fixed width."""

        height = max(MIN_PANEL_HEIGHT, min(MAX_PANEL_HEIGHT, self._needed_height()))
        if height != self.height():
            self.resize(self.width(), height)
            self._layout.activate()

    def _needed_height(self) -> int:
        """The height the rows need at the panel's width, wrapping included."""

        width = self.width()
        text_width = max(200, width - 2 * PANEL_MARGIN_X)
        # Summed by hand: a wrapping label's size hint guesses at a width of
        # its own and left a band of empty plate under the last row.
        needed = 2 * PANEL_MARGIN_Y
        for index, (translation, original_label, _speaker) in enumerate(self._rows):
            row_height = 8
            for label in (translation, original_label):
                if label is not None:
                    label.setFixedWidth(text_width)
                    row_height += max(label.heightForWidth(text_width), label.sizeHint().height() if not label.wordWrap() else 0)
            if original_label is not None:
                row_height += 2
            if index < len(self._rows) - 1:
                row_height += 7 + self._layout.spacing()
            needed += row_height
        if not self._rows:
            needed = DEFAULT_PANEL_SIZE[1]
        return int(needed)

    def _elide(self, text: str, label: QLabel) -> str:
        available = max(80, self.width() - 76)
        return QFontMetrics(label.font()).elidedText(
            text, Qt.TextElideMode.ElideRight, available
        )


MAX_SCREENSHOT_LINES = 6
SCREENSHOT_PANEL_SIZE = (720, 420)


class VRScreenshotPanel(VROverlayPanel):
    """Shows what a screenshot said, translated, on the player's hand.

    A separate class rather than a mode flag on the subtitle panel: the two
    show different things at different moments, and a screenshot result must
    not be pushed out of view by the next thing someone says.
    """

    def __init__(
        self,
        *,
        theme: str = "dark",
        plate_opacity: float = 0.55,
        size: tuple[int, int] = SCREENSHOT_PANEL_SIZE,
    ) -> None:
        # A denser plate than the subtitle panel: this one is read closely and
        # is dismissed straight after, so contrast beats see-through.
        super().__init__(theme=theme, size=size, plate_opacity=plate_opacity)
        self._status_label: QLabel | None = None

    def show_status(self, text: str) -> None:
        """Replace the contents with a single status line."""

        self._entries.clear()
        self._clear_rows()
        label = QLabel(str(text or ""), self)
        label.setObjectName("vrTranslation")
        label.setWordWrap(True)
        self._layout.insertWidget(0, label)
        self._status_label = label
        self._layout.activate()
        label.setFixedWidth(max(80, self.width() - 60))
        needed = max(MIN_PANEL_HEIGHT, min(MAX_PANEL_HEIGHT, self._layout.sizeHint().height()))
        if needed != self.height():
            self.resize(self.width(), needed)
            self._layout.activate()

    def show_lines(self, pairs: list[tuple[str, str]]) -> None:
        """Show up to a screenful of (original, translated) pairs."""

        self._entries.clear()
        for original, translated in pairs[:MAX_SCREENSHOT_LINES]:
            text = str(translated or "").strip()
            if not text:
                continue
            self._entries.append((text, str(original or "").strip(), "listen"))
        self._rebuild_rows()

    def show_picture(self, image: QImage) -> bool:
        """Replace the contents with a picture, scaled to the panel's width.

        The picture is the read region with its translations on it, so the
        hand shows the same card the view would.
        """

        if image is None or image.isNull():
            return False
        self._entries.clear()
        self._clear_rows()
        inner_w = max(80, self.width() - 60)
        inner_h = max(80, MAX_PANEL_HEIGHT - 48)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False
        pixmap = pixmap.scaled(
            inner_w, inner_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        label = QLabel(self)
        label.setObjectName("vrPicture")
        label.setPixmap(pixmap)
        label.setFixedSize(pixmap.size())
        self._layout.insertWidget(0, label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._layout.activate()
        needed = max(MIN_PANEL_HEIGHT, min(MAX_PANEL_HEIGHT, self._layout.sizeHint().height()))
        if needed != self.height():
            self.resize(self.width(), needed)
            self._layout.activate()
        return True
