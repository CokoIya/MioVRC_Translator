# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""The Mio tab drawn for the SteamVR dashboard.

Painted directly rather than built from widgets: a dashboard tab is read from
two metres away and clicked with a laser, so every control is a large flat
button, and the whole thing is one hit-tested picture. Widgets would bring
popups and focus handling that cannot work inside an overlay texture.

The panel only knows facts handed to it in :meth:`set_state`; every button
reports an action id and the main window decides what it means. It has two
pages: the controls, and the reads kept this session (VRHandsFrame keeps its
captures on the wrist the same way), switched with ``page:main`` and
``page:reads``.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from src.utils.i18n import tr

# Tall enough for four sections plus the hint under the last one; the
# dashboard scales the tab, so only the aspect ratio shows.
PANEL_SIZE = (1280, 1120)
# Reads listed on the reads page; the rest wait in the history.
MAX_LISTED_READS = 7
CURSOR_RADIUS = 14
CURSOR_COLOR = QColor(255, 255, 255, 235)
CURSOR_RING = QColor(20, 24, 36, 200)
MARGIN = 36
GAP = 14
ROW_HEIGHT = 74
SECTION_TITLE_PX = 24
BUTTON_PX = 27
VALUE_PX = 25
HINT_PX = 20
RADIUS = 14

BACKGROUND = QColor(14, 16, 24, 242)
SECTION = QColor(255, 255, 255, 14)
BUTTON = QColor(255, 255, 255, 26)
BUTTON_HOVER = QColor(255, 255, 255, 52)
BUTTON_PRESSED = QColor(255, 255, 255, 80)
ACTIVE = QColor(80, 160, 255, 230)
ACTIVE_HOVER = QColor(110, 180, 255, 240)
TEXT = QColor(245, 247, 250)
TEXT_DIM = QColor(190, 196, 208)
DANGER = QColor(235, 90, 90, 230)
NOTE = QColor(230, 150, 40, 220)


@dataclass(frozen=True)
class Button:
    action: str
    label: str
    rect: QRect
    active: bool = False
    danger: bool = False


def _font(px: int, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(px)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.DemiBold)
    return font


class VRDashboardPanel:
    """Layout + painting + hit testing for the dashboard tab."""

    def __init__(self, ui_language: str = "zh-CN") -> None:
        self._lang = str(ui_language or "zh-CN")
        self._state: dict = {}
        self._buttons: list[Button] = []
        self._texts: list[tuple[QRect, str, int, QColor, bool]] = []
        self._notes: list[QRect] = []
        self._layout_dirty = True

    @property
    def size(self) -> tuple[int, int]:
        return PANEL_SIZE

    def set_language(self, ui_language: str) -> None:
        self._lang = str(ui_language or "zh-CN")
        self._layout_dirty = True

    def set_state(self, state: dict) -> bool:
        """Return True when the new facts differ from what is drawn."""

        merged = dict(self._state)
        merged.update(state or {})
        if merged == self._state:
            return False
        self._state = merged
        self._layout_dirty = True
        return True

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._lang, key, **kwargs)

    # ------------------------------------------------------------ layout
    def _layout(self) -> None:
        self._buttons = []
        self._texts = []
        self._notes = []
        width, _height = PANEL_SIZE
        s = self._state
        y = MARGIN

        # Header: title left, status right.
        self._texts.append((QRect(MARGIN, y, 600, 44), self._t("vr_dash_title"), 34, TEXT, True))
        status = str(s.get("status", "") or "")
        if status:
            self._texts.append(
                (QRect(width // 2, y + 6, width // 2 - MARGIN, 36), status, HINT_PX, TEXT_DIM, False)
            )
        y += 62
        note = str(s.get("note", "") or "")
        if note:
            # A one-off instruction (close this menu so the view can be read)
            # goes above everything, where the player is already looking.
            self._notes.append(QRect(MARGIN, y, width - 2 * MARGIN, 64))
            self._texts.append(
                (QRect(MARGIN + 18, y, width - 2 * MARGIN - 36, 64), note, BUTTON_PX, TEXT, True)
            )
            y += 64 + GAP

        def section(title_key: str) -> None:
            nonlocal y
            self._texts.append(
                (QRect(MARGIN, y, width - 2 * MARGIN, 32), self._t(title_key), SECTION_TITLE_PX, TEXT_DIM, True)
            )
            y += 40

        def row(items: list[tuple[str, str, bool, bool]], *, weights: list[int] | None = None) -> None:
            """Lay buttons across one row; weights share the width."""

            nonlocal y
            if not items:
                return
            weights = weights or [1] * len(items)
            total = float(sum(weights))
            inner = width - 2 * MARGIN - GAP * (len(items) - 1)
            x = MARGIN
            for (action, label, active, danger), weight in zip(items, weights):
                w = int(round(inner * weight / total))
                self._buttons.append(
                    Button(action, label, QRect(x, y, w, ROW_HEIGHT), active=active, danger=danger)
                )
                x += w + GAP
            y += ROW_HEIGHT + GAP

        if str(s.get("page", "main") or "main") == "reads":
            self._layout_reads(y, section, row)
            self._layout_dirty = False
            return

        # -------------------------------------------------- listening
        section("vr_dash_section_listen")
        listening = bool(s.get("listening", False))
        row(
            [
                (
                    "toggle_listen",
                    self._t("vr_dash_stop_listen" if listening else "vr_dash_start_listen"),
                    listening,
                    listening,
                ),
                ("toggle_listen_others", self._t("vr_dash_listen_others"), bool(s.get("listen_others", False)), False),
                ("toggle_tts", self._t("vr_dash_tts"), bool(s.get("tts", False)), False),
            ]
        )
        muted = bool(s.get("muted", False))
        row(
            [
                ("toggle_mic_chatbox", self._t("vr_dash_mic_chatbox"), bool(s.get("mic_chatbox", True)), False),
                ("toggle_listen_chatbox", self._t("vr_dash_listen_chatbox"), bool(s.get("listen_chatbox", True)), False),
                ("toggle_mute", self._t("vr_dash_mute"), muted, muted),
            ]
        )

        # -------------------------------------------------- services
        section("vr_dash_section_translation")
        row(
            [
                ("provider_prev", "◀", False, False),
                ("provider_next", self._t("vr_dash_provider", value=str(s.get("provider", "") or "—")), False, False),
                ("model_prev", "◀", False, False),
                ("model_next", self._t("vr_dash_model", value=str(s.get("model", "") or "—")), False, False),
            ],
            weights=[1, 7, 1, 7],
        )
        row(
            [
                ("output_format_next", self._t("vr_dash_output_format", value=str(s.get("output_format", "") or "—")), False, False),
                ("tts_engine_next", self._t("vr_dash_tts_engine", value=str(s.get("tts_engine", "") or "—")), False, False),
            ]
        )

        # -------------------------------------------------- headset display
        section("vr_dash_section_display")
        row(
            [
                ("toggle_vr_overlay", self._t("vr_dash_vr_overlay"), bool(s.get("vr_overlay", False)), False),
                ("toggle_desktop_overlay", self._t("vr_dash_desktop_overlay"), bool(s.get("desktop_overlay", False)), False),
            ]
        )
        size_key = str(s.get("vr_size", "medium") or "medium")
        row(
            [
                ("size:small", self._t("vr_dash_size_small"), size_key == "small", False),
                ("size:medium", self._t("vr_dash_size_medium"), size_key == "medium", False),
                ("size:large", self._t("vr_dash_size_large"), size_key == "large", False),
                ("size:xlarge", self._t("vr_dash_size_xlarge"), size_key == "xlarge", False),
            ]
        )
        editing = bool(s.get("edit_mode", False))
        locked = bool(s.get("locked", False))
        row(
            [
                ("move_panel", self._t("vr_dash_moving" if editing else "vr_dash_move"), editing, False),
                ("toggle_lock", self._t("vr_dash_locked" if locked else "vr_dash_lock"), locked, False),
                ("reset_position", self._t("vr_dash_reset_position"), False, False),
            ]
        )

        # -------------------------------------------------- screenshot
        section("vr_dash_section_screenshot")
        reads = list(s.get("reads", []) or [])
        row(
            [
                ("screenshot", self._t("vr_dash_screenshot"), False, False),
                ("toggle_frame_gesture", self._t("vr_dash_frame_gesture"), bool(s.get("frame_gesture", False)), False),
                ("page:reads", self._t("vr_dash_reads", count=len(reads)), False, False),
            ]
        )
        hint_key = (
            "vr_dash_screenshot_hint_bound"
            if bool(s.get("binding_active", False))
            else "vr_dash_screenshot_hint_unbound"
        )
        self._texts.append((QRect(MARGIN, y, width - 2 * MARGIN, 56), self._t(hint_key), HINT_PX, TEXT_DIM, False))
        self._layout_dirty = False

    def _layout_reads(self, y: int, section, row) -> None:
        """The second page: what the frame gesture does, and the kept reads."""

        width, _height = PANEL_SIZE
        s = self._state
        section("vr_dash_section_reads")
        row(
            [
                ("page:main", self._t("vr_dash_back"), False, False),
                ("screenshot", self._t("vr_dash_screenshot"), False, False),
                ("toggle_frame_gesture", self._t("vr_dash_frame_gesture"), bool(s.get("frame_gesture", False)), False),
            ]
        )
        panels = int(s.get("open_panels", 0) or 0)
        row(
            [
                ("gather_panels", self._t("vr_dash_gather"), False, False),
                ("close_panels", self._t("vr_dash_close_panels", count=panels), False, panels > 0),
                ("tutorial", self._t("vr_dash_tutorial"), False, False),
            ]
        )
        section("vr_dash_section_history")
        reads = [entry for entry in list(s.get("reads", []) or []) if isinstance(entry, dict)]
        if not reads:
            self._texts.append(
                (QRect(MARGIN, self._cursor_y(), width - 2 * MARGIN, 56), self._t("vr_dash_reads_empty"), HINT_PX, TEXT_DIM, False)
            )
            return
        for entry in reads[:MAX_LISTED_READS]:
            entry_id = entry.get("id")
            pinned = bool(entry.get("pinned", False))
            row(
                [
                    (f"recall:{entry_id}", str(entry.get("label", "") or "—"), bool(entry.get("open", False)), False),
                    (f"pin:{entry_id}", "★" if pinned else "☆", pinned, False),
                ],
                weights=[8, 1],
            )

    def _cursor_y(self) -> int:
        """Just below the last thing laid out."""

        bottoms = [button.rect.bottom() for button in self._buttons] + [rect.bottom() for rect, *_ in self._texts]
        return (max(bottoms) + GAP) if bottoms else MARGIN

    # ------------------------------------------------------------ hit test
    def hit_test(self, x: float, y: float) -> str | None:
        if self._layout_dirty:
            self._layout()
        point_x, point_y = int(x), int(y)
        for button in self._buttons:
            if button.rect.contains(point_x, point_y):
                return button.action
        return None

    # ------------------------------------------------------------ painting
    def render_rgba(
        self, *, hover: str | None = None, pressed: str | None = None
    ) -> tuple[ctypes.Array, int, int]:
        image = self.render_image(hover=hover, pressed=pressed)
        raw = bytes(image.constBits())
        # pyopenvr calls byref on the buffer, so it has to be a ctypes object.
        buffer = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        return buffer, image.width(), image.height()

    def render_image(
        self,
        *,
        hover: str | None = None,
        pressed: str | None = None,
        cursor: tuple[float, float] | None = None,
        dwell: float = 0.0,
    ) -> QImage:
        """The panel; ``cursor`` (pixels) draws the laser's dot on it, since
        SteamVR draws no laser for an overlay that took no input from it.
        ``dwell`` (0..1) draws the rest-to-click countdown as a ring filling
        around the dot."""

        if self._layout_dirty:
            self._layout()
        width, height = PANEL_SIZE
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(BACKGROUND)
            painter.drawRoundedRect(QRectF(0, 0, width, height), 28, 28)
            painter.setBrush(NOTE)
            for rect in self._notes:
                painter.drawRoundedRect(rect, RADIUS, RADIUS)

            for rect, text, px, color, bold in self._texts:
                painter.setFont(_font(px, bold=bold))
                painter.setPen(color)
                flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextWordWrap)
                painter.drawText(rect, flags, text)

            for button in self._buttons:
                if button.action == pressed:
                    fill = BUTTON_PRESSED
                elif button.active:
                    fill = ACTIVE_HOVER if button.action == hover else ACTIVE
                    if button.danger:
                        fill = DANGER
                elif button.action == hover:
                    fill = BUTTON_HOVER
                else:
                    fill = BUTTON
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(button.rect, RADIUS, RADIUS)
                if button.active:
                    pen = QPen(QColor(255, 255, 255, 120))
                    pen.setWidth(2)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(button.rect.adjusted(1, 1, -1, -1), RADIUS, RADIUS)
                painter.setPen(TEXT)
                font = _font(BUTTON_PX, bold=button.active)
                painter.setFont(font)
                label = self._elide(button.label, font, button.rect.width() - 24)
                painter.drawText(button.rect, int(Qt.AlignmentFlag.AlignCenter), label)

            if cursor is not None:
                cx, cy = float(cursor[0]), float(cursor[1])
                if 0 <= cx <= width and 0 <= cy <= height:
                    pen = QPen(CURSOR_RING)
                    pen.setWidth(4)
                    painter.setPen(pen)
                    painter.setBrush(CURSOR_COLOR)
                    painter.drawEllipse(QRectF(cx - CURSOR_RADIUS, cy - CURSOR_RADIUS, 2 * CURSOR_RADIUS, 2 * CURSOR_RADIUS))
                    if dwell > 0:
                        ring = QPen(CURSOR_COLOR)
                        ring.setWidth(6)
                        painter.setPen(ring)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        radius = CURSOR_RADIUS * 2.2
                        span = int(round(-360.0 * 16 * max(0.0, min(1.0, float(dwell)))))
                        painter.drawArc(
                            QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius), 90 * 16, span
                        )
        finally:
            painter.end()
        return image

    @staticmethod
    def _elide(text: str, font: QFont, max_width: int) -> str:
        metrics = QFontMetrics(font)
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(10, max_width))
