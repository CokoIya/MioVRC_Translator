# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import logging

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.icon_utils import ui_icon
from src.ui_qt.styles import build_floating_window_styles
from src.ui_qt.theme import icon_tint, theme_tokens
from src.ui_qt.window_utils import set_window_topmost
from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language

logger = logging.getLogger(__name__)

MAX_HISTORY = 15
DEFAULT_SIZE = (440, 300)
MIN_SIZE = (300, 170)
# The floor is what the window collapses to beside VRChat. Scaling it by the
# full UI scale put a "minimum" of 558x341 on a high-DPI screen, which is not
# a minimum at all, so the floor follows the display only part of the way.
MIN_SIZE_SCALE_CAP = 1.12
DEFAULT_OPACITY = 0.88
MIN_OPACITY = 0.45
MAX_OPACITY = 1.0
BUBBLE_MIN_WRAP = 150
BUBBLE_MAX_WRAP = 720
# A bubble that spans the full row reads as a paragraph, not as a message.
# Leaving a gutter on the far side is what makes the left/right split legible.
BUBBLE_WRAP_RATIO = 0.82
BASE_DPI = 96.0
RESIZE_MARGIN = 14
MIN_UI_SCALE = 0.9
MAX_UI_SCALE = 1.55

class FloatingWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        ui_language: str,
        on_resend: Callable[[str, str], None] | None = None,
        on_close: Callable[[], None] | None = None,
        theme: str = "dark",
    ) -> None:
        # Keep this as an unowned top-level window so minimizing or raising the
        # main window does not drag the reverse-translation overlay along.
        super().__init__(None)
        self._ui_lang = normalize_ui_language(ui_language)
        self._on_resend = on_resend
        self._on_close = on_close
        self._history: deque[dict[str, object]] = deque(maxlen=MAX_HISTORY)
        self._history_seq = 0
        self._selected_history_id: int | None = None
        self._drag_position: QPoint | None = None
        self._topmost = True
        self._opacity = DEFAULT_OPACITY
        self._visible = False
        self._theme = str(theme or "dark")
        self._history_widgets: dict[int, dict[str, object]] = {}
        self._last_layout_width = 0
        self._last_ui_scale = 0.0
        self._status_key = "floating_status_waiting"
        self._layout_refresh_timer = QTimer(self)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.timeout.connect(self._apply_layout_update)
        self._pending_scroll_timer = QTimer(self)
        self._pending_scroll_timer.setSingleShot(True)
        self._pending_scroll_timer.timeout.connect(self._scroll_to_bottom)

        self.setWindowTitle(tr(self._ui_lang, "floating_window_title"))
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MIN_SIZE)
        # Enable resizing - no maximum size restriction
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.move(24, 96)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        flags = self.windowFlags() | Qt.WindowType.FramelessWindowHint
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowOpacity(self._opacity)

        self.setMouseTracking(True)
        # Resize tracking for the frameless overlay. Mouse events often land on
        # child widgets, so the event filter handles hit-testing as well.
        self._resize_mode: str | None = None
        self._resize_start_pos: QPoint | None = None
        self._resize_start_geometry: QRect | None = None

        self._build_ui()
        self._refresh_history()
        self.hide()

    def _build_ui(self) -> None:
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(10, 10, 10, 10)
        self._root_layout.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("floatingShell")
        shell_shadow = QGraphicsDropShadowEffect(self._shell)
        shell_shadow.setBlurRadius(28)
        shell_shadow.setOffset(0, 12)
        shell_shadow.setColor(self._shadow_color())
        self._shell.setGraphicsEffect(shell_shadow)
        self._root_layout.addWidget(self._shell, 1)

        self._shell_layout = QVBoxLayout(self._shell)
        self._shell_layout.setContentsMargins(8, 8, 8, 8)
        self._shell_layout.setSpacing(6)

        # Header: what the overlay is doing on the left, window controls right.
        self._top_row = QHBoxLayout()
        self._top_row.setSpacing(6)

        self._status_label = QLabel(self._status_text())
        self._status_label.setObjectName("floatingStatus")
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self._status_label.setToolTip(self._status_label.text())
        self._top_row.addWidget(
            self._status_label, 0, Qt.AlignmentFlag.AlignVCenter
        )
        self._top_row.addStretch(1)

        self._opacity_label = QLabel("")
        self._opacity_label.setObjectName("floatingHint")
        self._opacity_label.setToolTip(self._opacity_label_text())
        self._top_row.addWidget(self._opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(int(MIN_OPACITY * 100), int(MAX_OPACITY * 100))
        self._opacity_slider.setValue(int(self._opacity * 100))
        self._opacity_slider.setFixedWidth(64)
        self._opacity_slider.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._opacity_slider.setToolTip(self._opacity_label_text())
        self._opacity_slider.valueChanged.connect(self._on_opacity_change)
        self._top_row.addWidget(self._opacity_slider)

        # Both header buttons share one object name so a single stylesheet
        # rule owns their geometry; the pinned look rides on a property.
        self._pin_button = QPushButton("")
        self._pin_button.setObjectName("floatingIconButton")
        self._pin_button.setFixedSize(26, 26)
        self._pin_button.setIconSize(QSize(14, 14))
        self._pin_button.clicked.connect(self.toggle_topmost)
        self._refresh_pin_button()
        self._top_row.addWidget(self._pin_button)

        self._close_button = QPushButton("")
        self._close_button.setObjectName("floatingIconButton")
        self._close_button.setFixedSize(26, 26)
        self._close_button.setIconSize(QSize(14, 14))
        self._close_button.clicked.connect(self.close)
        self._refresh_close_button()
        self._top_row.addWidget(self._close_button)
        self._shell_layout.addLayout(self._top_row)

        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("floatingTranscript")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("floatingScrollContent")
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(2, 2, 2, 2)
        self._scroll_layout.setSpacing(6)
        self._scroll_layout.addStretch(1)
        self._scroll_area.setWidget(self._scroll_content)
        self._shell_layout.addWidget(self._scroll_area, 1)

        # Footer: the send button stays visible even when nothing is selected,
        # so players learn it exists before they need it. The hint next to it
        # says how to arm it.
        self._footer_layout = QHBoxLayout()
        self._footer_layout.setSpacing(8)

        self._hint_label = QLabel(self._hint_text())
        self._hint_label.setObjectName("floatingHint")
        self._hint_label.setToolTip(self._hint_text())
        self._hint_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._footer_layout.addWidget(self._hint_label, 1)

        self._send_selected_button = QPushButton(tr(self._ui_lang, "send_to_vrc"))
        self._send_selected_button.setObjectName("floatingSendButton")
        self._send_selected_button.setIconSize(QSize(13, 13))
        self._send_selected_button.setToolTip(tr(self._ui_lang, "floating_send_hint"))
        self._send_selected_button.clicked.connect(self._send_selected_history)
        self._footer_layout.addWidget(self._send_selected_button)
        self._shell_layout.addLayout(self._footer_layout)

        self._apply_style()
        self._refresh_send_button()
        self._install_interaction_filter(self)
        self._apply_scaled_layout(force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = event.size().width()
        scale_changed = self._apply_scaled_layout()
        self._refresh_hint_label()
        if abs(width - self._last_layout_width) >= 12 or scale_changed:
            self._last_layout_width = width
            self._update_wraplengths()
            self._layout_refresh_timer.stop()
            self._layout_refresh_timer.start(40)

    def _bubble_wraplength(self) -> int:
        """Return the widest a bubble may grow, not the width it takes."""

        viewport = getattr(self, "_scroll_area", None)
        width = viewport.viewport().width() if viewport is not None else self.width()
        horizontal_padding = self._scaled(16)
        available = max(BUBBLE_MIN_WRAP, width - horizontal_padding)
        return max(
            self._scaled(BUBBLE_MIN_WRAP),
            min(int(available * BUBBLE_WRAP_RATIO), self._scaled(BUBBLE_MAX_WRAP)),
        )

    def _update_wraplengths(self) -> None:
        # Bubbles are sized by their content: a two-word reply must not occupy
        # the same slab as a paragraph, or the left/right split stops reading
        # as a conversation.
        limit = self._bubble_wraplength()
        for widgets in self._history_widgets.values():
            bubble = widgets.get("bubble")
            if bubble is not None:
                bubble.setMaximumWidth(limit)
            self._elide_original(widgets, limit)

    def _elide_original(self, widgets: dict[str, object], limit: int) -> None:
        """Trim the source line to one row, full text in the tooltip.

        The translation is what players read; letting the original wrap made a
        single message fill the whole overlay and hid the conversation.
        """

        label = widgets.get("original")
        if label is None:
            return
        text = str(widgets.get("original_text", "") or "")
        if not text:
            return
        available = max(self._scaled(60), limit - self._scaled(24))
        metrics = QFontMetrics(label.font())
        label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, available))

    def _apply_layout_update(self) -> None:
        self._update_wraplengths()
        for entry_id in list(self._history_widgets):
            self._style_history_entry(entry_id)

    def _screen(self):
        handle = self.windowHandle()
        if handle is not None and handle.screen() is not None:
            return handle.screen()
        app = QApplication.instance()
        if app is not None:
            return app.primaryScreen()
        return None

    def _ui_scale(self) -> float:
        screen = self._screen()
        if screen is None:
            return 1.0
        try:
            geometry = screen.availableGeometry()
            dpi_scale = max(0.8, float(screen.logicalDotsPerInch()) / BASE_DPI)
        except RuntimeError:
            # The overlay outlives a monitor being unplugged or reconfigured,
            # which leaves the cached QScreen wrapper pointing at freed memory.
            return 1.0
        resolution_scale = max(
            0.9,
            min(1.12, min(geometry.width() / 1366.0, geometry.height() / 768.0)),
        )
        return max(MIN_UI_SCALE, min(MAX_UI_SCALE, dpi_scale * resolution_scale))

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self._ui_scale())))

    def _history_font_px(self) -> int:
        width_bonus = max(0, min(3, (self.width() - DEFAULT_SIZE[0]) // 220))
        return max(12, min(22, self._scaled(13) + int(width_bonus)))

    def _original_font_px(self) -> int:
        return max(10, self._history_font_px() - 2)

    def _floating_scale_styles(self) -> str:
        control = self._scaled(26)
        return f"""
        QLabel#floatingStatus {{
            font-size: {self._scaled(11)}px;
            min-height: {self._scaled(22)}px;
            max-height: {self._scaled(22)}px;
        }}
        QLabel#floatingHint {{
            font-size: {self._scaled(11)}px;
        }}
        QLabel#bubbleSpeaker {{
            font-size: {max(9, self._scaled(10))}px;
        }}
        QPushButton#floatingIconButton {{
            min-width: {control}px;
            max-width: {control}px;
            min-height: {control}px;
            max-height: {control}px;
        }}
        QPushButton#floatingSendButton {{
            font-size: {self._scaled(12)}px;
            min-height: {control}px;
            padding: 0 {self._scaled(10)}px;
        }}
        """

    def _apply_scaled_layout(self, *, force: bool = False) -> bool:
        scale = self._ui_scale()
        if not force and abs(scale - self._last_ui_scale) < 0.03:
            return False
        self._last_ui_scale = scale
        floor_scale = min(scale, MIN_SIZE_SCALE_CAP)
        self.setMinimumSize(
            int(round(MIN_SIZE[0] * floor_scale)),
            int(round(MIN_SIZE[1] * floor_scale)),
        )
        self._root_layout.setContentsMargins(
            self._scaled(10),
            self._scaled(10),
            self._scaled(10),
            self._scaled(10),
        )
        self._shell_layout.setContentsMargins(
            self._scaled(8),
            self._scaled(8),
            self._scaled(8),
            self._scaled(8),
        )
        self._shell_layout.setSpacing(self._scaled(6))
        self._top_row.setSpacing(self._scaled(6))
        self._footer_layout.setSpacing(self._scaled(8))
        self._scroll_layout.setContentsMargins(
            self._scaled(2),
            self._scaled(2),
            self._scaled(2),
            self._scaled(2),
        )
        self._scroll_layout.setSpacing(self._scaled(6))
        self._opacity_slider.setFixedWidth(self._scaled(64))
        control_size = self._scaled(26)
        icon_size = self._scaled(14)
        for button in (self._pin_button, self._close_button):
            button.setFixedSize(control_size, control_size)
            button.setIconSize(QSize(icon_size, icon_size))
        self._send_selected_button.setIconSize(QSize(self._scaled(13), self._scaled(13)))
        self._apply_style()
        self._update_wraplengths()
        self._refresh_hint_label()
        return True

    def _is_near_bottom(self) -> bool:
        bar = self._scroll_area.verticalScrollBar()
        try:
            maximum = bar.maximum()
            if maximum <= 0:
                return True
            return bar.value() >= maximum - max(12, int(bar.pageStep() * 0.04))
        except Exception:
            return True

    def _schedule_scroll_to_bottom(self) -> None:
        self._pending_scroll_timer.stop()
        self._pending_scroll_timer.start(16)

    def _opacity_label_text(self) -> str:
        return tr(self._ui_lang, "text_input_opacity", pct=int(round(self._opacity * 100)))

    def _hint_text(self) -> str:
        return tr(self._ui_lang, "floating_send_hint")

    def _refresh_hint_label(self) -> None:
        """Fit the hint to whatever the send button leaves behind.

        Squeezed to the minimum width the footer has room for the button and
        little else, so the hint shortens and then steps aside rather than
        running underneath it.
        """

        label = getattr(self, "_hint_label", None)
        if label is None:
            return
        button = getattr(self, "_send_selected_button", None)
        reserved = (button.sizeHint().width() if button is not None else 0)
        available = self.width() - reserved - self._scaled(44)
        if available < self._scaled(64):
            label.setVisible(False)
            return
        label.setVisible(True)
        metrics = QFontMetrics(label.font())
        label.setText(
            metrics.elidedText(
                self._hint_text(), Qt.TextElideMode.ElideRight, available
            )
        )

    def _status_text(self) -> str:
        return tr(self._ui_lang, self._status_key)

    def _refresh_status_label(self) -> None:
        text = self._status_text()
        self._status_label.setText(text)
        self._status_label.setToolTip(text)

    def _install_interaction_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        widget.setMouseTracking(True)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
            child.setMouseTracking(True)
        viewport = getattr(self, "_scroll_area", None)
        if viewport is not None:
            self._scroll_area.viewport().installEventFilter(self)
            self._scroll_area.viewport().setMouseTracking(True)

    def _pin_text(self) -> str:
        return tr(self._ui_lang, "text_input_pin_on" if self._topmost else "text_input_pin_off")

    def _shadow_color(self) -> QColor:
        """获取阴影颜色"""
        tokens = theme_tokens(self._theme)
        shadow = QColor.fromString(str(tokens["SHADOW"]))
        if shadow.isValid():
            return shadow
        return QColor(2, 6, 23, 97) if self._theme == "dark" else QColor(15, 23, 42, 46)

    def _apply_style(self) -> None:
        # 更新阴影颜色
        if hasattr(self, "_shell"):
            effect = self._shell.graphicsEffect()
            if isinstance(effect, QGraphicsDropShadowEffect):
                effect.setColor(self._shadow_color())

        self.setStyleSheet(build_floating_window_styles(self._theme) + self._floating_scale_styles())
        self._refresh_pin_button()
        self._refresh_close_button()
        self._refresh_send_button()
        for entry_id in self._history_widgets:
            self._style_history_entry(entry_id)

    def _refresh_pin_button(self) -> None:
        tokens = theme_tokens(self._theme)
        icon = ui_icon(
            "pin.svg" if self._topmost else "pin-off.svg",
            self._scaled(16),
            str(tokens["ACCENT"]) if self._topmost else icon_tint(self._theme),
        )
        self._pin_button.setIcon(icon)
        self._pin_button.setText(self._pin_text() if icon.isNull() else "")
        self._pin_button.setToolTip(self._pin_text())
        self._pin_button.setProperty("pinned", "true" if self._topmost else "false")
        self._pin_button.style().unpolish(self._pin_button)
        self._pin_button.style().polish(self._pin_button)

    def _refresh_close_button(self) -> None:
        icon = ui_icon("x.svg", self._scaled(15), icon_tint(self._theme, strong=True))
        self._close_button.setIcon(icon)
        close_text = tr(self._ui_lang, "text_input_close")
        self._close_button.setText(close_text if icon.isNull() else "")
        self._close_button.setToolTip(close_text)

    def _on_opacity_change(self, value: int) -> None:
        self._opacity = max(MIN_OPACITY, min(MAX_OPACITY, float(value) / 100.0))
        self.setWindowOpacity(self._opacity)
        # The percentage lives in the tooltip; a permanent readout of a value
        # the player is already dragging only costs header width.
        text = self._opacity_label_text()
        self._opacity_label.setText(f"{int(round(self._opacity * 100))}%")
        self._opacity_label.setToolTip(text)
        self._opacity_slider.setToolTip(text)

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        was_visible = self._visible or self.isVisible()
        native_applied = set_window_topmost(self, self._topmost)
        self._refresh_pin_button()
        if was_visible and not native_applied:
            self.show()
            self._visible = True
            self.raise_()
            self.activateWindow()

    @staticmethod
    def _entry_payload(entry: dict[str, object]) -> str:
        return str(entry.get("payload", "") or "").strip()

    @staticmethod
    def _entry_source(entry: dict[str, object]) -> str:
        return str(entry.get("source", "listen"))

    @staticmethod
    def _entry_side(entry: dict[str, object]) -> str:
        return "right" if FloatingWindow._entry_source(entry) in {"manual", "mic"} else "left"

    @staticmethod
    def _entry_original(entry: dict[str, object]) -> str:
        return str(entry.get("original", "") or "").strip()

    def _starts_a_turn(self, entry: dict[str, object]) -> bool:
        """True when this entry is the first from its side in a row."""

        previous: dict[str, object] | None = None
        for candidate in self._history:
            if candidate.get("id") == entry.get("id"):
                break
            previous = candidate
        if previous is None:
            return True
        return self._entry_side(previous) != self._entry_side(entry)

    def _reveal_leading_speaker(self) -> None:
        """Keep a name on the oldest visible message after an eviction."""

        if not self._history:
            return
        widgets = self._history_widgets.get(int(self._history[0].get("id", 0)))
        if widgets is None:
            return
        label = widgets.get("speaker")
        if label is not None:
            label.setVisible(True)

    def _entry_speaker(self, entry: dict[str, object]) -> str:
        source = self._entry_source(entry)
        if source == "error":
            return ""
        key = (
            "floating_speaker_self"
            if source in {"manual", "mic"}
            else "floating_speaker_other"
        )
        return tr(self._ui_lang, key)

    def _bubble_colors(self, source: str, *, selected: bool) -> tuple[str, str, str, str]:
        """Return (background, border, text, secondary text) for one bubble.

        Side, not selection, drives the fill: what the player said is tinted
        with the accent and what they heard stays neutral, so a glance at the
        colour tells them who spoke. Selection only thickens the border, which
        keeps that cue readable on either side.
        """

        tokens = theme_tokens(self._theme)
        text = str(tokens["TEXT_PRIMARY"])
        secondary = str(tokens["TEXT_MUTED"])

        if source == "error":
            return (
                str(tokens["DANGER_SOFT"]),
                str(tokens["DANGER_BORDER"]),
                text,
                secondary,
            )

        if source in {"manual", "mic"}:
            border = (
                str(tokens["ACCENT"]) if selected else str(tokens["ACCENT_BORDER"])
            )
            return str(tokens["ACCENT_SOFT"]), border, text, secondary

        border = str(tokens["ACCENT"]) if selected else str(tokens["PANEL_BORDER"])
        return str(tokens["PANEL_BG"]), border, text, secondary

    def _entry_can_resend(self, entry: dict[str, object]) -> bool:
        return bool(self._entry_payload(entry)) and self._entry_source(entry) != "error"

    def _selected_entry(self) -> dict[str, object] | None:
        if self._selected_history_id is None:
            return None
        for entry in self._history:
            if entry.get("id") == self._selected_history_id:
                return entry
        self._selected_history_id = None
        return None

    def _clear_history_widgets(self) -> None:
        for widgets in self._history_widgets.values():
            lane = widgets.get("lane")
            if lane is not None:
                try:
                    lane.deleteLater()
                except Exception:
                    pass
        self._history_widgets.clear()

    def _remove_history_entry_ui(self, entry_id: int) -> None:
        widgets = self._history_widgets.pop(entry_id, None)
        if widgets is None:
            return
        lane = widgets.get("lane")
        if lane is not None:
            try:
                lane.deleteLater()
            except Exception:
                pass

    def _reindex_history_rows(self) -> None:
        for idx, entry in enumerate(self._history):
            entry_id = int(entry.get("id", 0))
            widgets = self._history_widgets.get(entry_id)
            if widgets is None:
                continue
            lane = widgets.get("lane")
            if lane is not None:
                lane.setProperty("row_index", idx)

    def _refresh_history(self) -> None:
        self._layout_refresh_timer.stop()
        self._update_wraplengths()
        should_scroll = (not self._visible) or self._is_near_bottom()
        self._clear_history_widgets()

        if self._selected_entry() is None:
            self._selected_history_id = None

        if not self._history:
            self._update_actions()
            return

        for entry in self._history:
            self._append_history_entry_ui(entry)

        self._update_actions()
        if should_scroll:
            self._schedule_scroll_to_bottom()

    def _append_history_entry_ui(self, entry: dict[str, object]) -> None:
        entry_id = int(entry.get("id", 0))
        source = self._entry_source(entry)
        can_resend = self._entry_can_resend(entry)
        side = self._entry_side(entry)

        lane = QFrame()
        lane.setObjectName("historyLane")
        lane_layout = QHBoxLayout(lane)
        lane_layout.setContentsMargins(0, 0, 0, 0)
        lane_layout.setSpacing(0)

        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(self._scaled(2))

        speaker = self._entry_speaker(entry)
        speaker_label: QLabel | None = None
        if speaker:
            speaker_label = QLabel(speaker)
            speaker_label.setObjectName("bubbleSpeaker")
            speaker_label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                if side == "right"
                else Qt.AlignmentFlag.AlignLeft
            )
            # One name per turn: repeating it above every line of a monologue
            # is what made the transcript feel padded.
            speaker_label.setVisible(self._starts_a_turn(entry))
            column_layout.addWidget(speaker_label)

        bubble = QFrame()
        bubble.setObjectName("historyBubble")
        bubble.setMaximumWidth(self._bubble_wraplength())
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(
            self._scaled(11),
            self._scaled(7),
            self._scaled(11),
            self._scaled(7),
        )
        bubble_layout.setSpacing(self._scaled(2))

        label = QLabel(str(entry.get("text", "")))
        label.setObjectName("bubbleText")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble_layout.addWidget(label)

        # The original is kept, but demoted: players read the translation and
        # only glance at the source line to check a name or a number.
        original_text = self._entry_original(entry)
        original_label: QLabel | None = None
        if original_text and original_text != str(entry.get("text", "")):
            original_label = QLabel(original_text)
            original_label.setObjectName("bubbleOriginal")
            original_label.setWordWrap(False)
            original_label.setToolTip(original_text)
            original_label.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
            )
            original_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            bubble_layout.addWidget(original_label)

        bubble_row = QHBoxLayout()
        bubble_row.setContentsMargins(0, 0, 0, 0)
        bubble_row.setSpacing(0)
        if side == "right":
            bubble_row.addStretch(1)
        bubble_row.addWidget(bubble, 0)
        if side == "left":
            bubble_row.addStretch(1)
        column_layout.addLayout(bubble_row)

        lane_layout.addWidget(column, 1)

        if can_resend:
            for widget in (lane, bubble, label, original_label):
                if widget is None:
                    continue
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
                widget.mousePressEvent = (  # type: ignore[method-assign]
                    lambda _event, eid=entry_id: self._select_history_entry(eid)
                )

        self._scroll_layout.insertWidget(max(0, self._scroll_layout.count() - 1), lane)
        self._history_widgets[entry_id] = {
            "lane": lane,
            "bubble": bubble,
            "label": label,
            "original": original_label,
            "original_text": original_text,
            "speaker": speaker_label,
            "source": source,
            "can_resend": can_resend,
        }
        self._install_interaction_filter(lane)
        self._style_history_entry(entry_id)

    def _style_history_entry(self, entry_id: int) -> None:
        widgets = self._history_widgets.get(entry_id)
        if widgets is None:
            return
        bubble = widgets.get("bubble")
        source = str(widgets.get("source", "listen"))
        is_selected = entry_id == self._selected_history_id
        if bubble is not None:
            bg, border, text, secondary = self._bubble_colors(
                source, selected=is_selected
            )
            radius = self._scaled(12)
            bubble.setStyleSheet(f"""
                #historyBubble {{
                    background: {bg};
                    border: {2 if is_selected else 1}px solid {border};
                    border-radius: {radius}px;
                }}
                #historyBubble QLabel#bubbleText {{
                    color: {text};
                    font-size: {self._history_font_px()}px;
                    background: transparent;
                }}
                #historyBubble QLabel#bubbleOriginal {{
                    color: {secondary};
                    font-size: {self._original_font_px()}px;
                    background: transparent;
                }}
            """)
            self._elide_original(widgets, self._bubble_wraplength())

    def _select_history_entry(self, entry_id: int) -> None:
        previous_id = self._selected_history_id
        if self._selected_history_id == entry_id:
            self._selected_history_id = None
        else:
            self._selected_history_id = entry_id
        self._update_selection_ui(previous_id, self._selected_history_id)

    def _update_selection_ui(self, previous_id: int | None, current_id: int | None) -> None:
        if previous_id is not None:
            self._style_history_entry(previous_id)
        if current_id is not None:
            self._style_history_entry(current_id)
        self._update_actions()

    def _refresh_send_button(self) -> None:
        """Keep the button legible in both states.

        It stays on screen while nothing is selected so players discover it,
        and only takes the accent tint once clicking it would do something.
        """

        button = getattr(self, "_send_selected_button", None)
        if button is None:
            return
        armed = button.isEnabled()
        tokens = theme_tokens(self._theme)
        icon = ui_icon(
            "send.svg",
            self._scaled(13),
            "#ffffff" if armed else icon_tint(self._theme),
        )
        if not icon.isNull():
            button.setIcon(icon)
        button.setProperty("armed", armed)
        button.setStyleSheet(
            f"""
            QPushButton#floatingSendButton {{
                background: {tokens["ACCENT"] if armed else tokens["PANEL_BG"]};
                color: {"#ffffff" if armed else tokens["TEXT_MUTED"]};
                border: 1px solid {tokens["ACCENT"] if armed else tokens["PANEL_BORDER"]};
            }}
            """
        )

    def _update_actions(self) -> None:
        entry = self._selected_entry()
        self._send_selected_button.setEnabled(entry is not None and self._entry_can_resend(entry))
        self._refresh_send_button()

    def _send_selected_history(self) -> None:
        entry = self._selected_entry()
        if entry is None or not self._entry_can_resend(entry):
            self._update_actions()
            return
        if self._on_resend is None:
            return
        self._on_resend(self._entry_payload(entry), self._entry_source(entry))

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = normalize_ui_language(ui_language)
        self.setWindowTitle(tr(self._ui_lang, "floating_window_title"))
        self._opacity_label.setToolTip(self._opacity_label_text())
        self._opacity_slider.setToolTip(self._opacity_label_text())
        self._hint_label.setToolTip(self._hint_text())
        self._refresh_hint_label()
        self._refresh_status_label()
        self._refresh_pin_button()
        self._refresh_close_button()
        self._send_selected_button.setText(tr(self._ui_lang, "send_to_vrc"))
        self._send_selected_button.setToolTip(tr(self._ui_lang, "floating_send_hint"))
        self._refresh_history()

    def refresh_theme(self, theme: str) -> None:
        self._theme = str(theme or "dark")
        self._apply_style()

    def show_translation(
        self,
        text: str,
        *,
        source: str = "listen",
        payload: str | None = None,
        original: str | None = None,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._last_text = message
        self.add_history_entry(
            message, source=source, payload=payload, original=original
        )
        if not self._visible:
            self.show()
            self._visible = True
            set_window_topmost(self, self._topmost)
            self.raise_()
            self.activateWindow()

    def show_message(self, message) -> bool:
        # Prefer the split fields: the bubble renders the translation and the
        # source line separately, so the combined "original（translated）"
        # display string would double the text up.
        translated = str(getattr(message, "translated_text", "") or "").strip()
        original = str(getattr(message, "original_text", "") or "").strip()
        display = str(getattr(message, "display_text", "") or "").strip()
        if not (translated or display):
            return False
        payload = str(getattr(message, "chatbox_text", "") or "").strip() or None
        source = str(getattr(message, "source", "listen") or "listen")
        self.show_translation(
            translated or display,
            source=source,
            payload=payload,
            original=original if translated else "",
        )
        return True

    def set_listen_status(self, listening: bool) -> None:
        self._status_key = "floating_status_listening" if listening else "floating_status_waiting"
        self._refresh_status_label()

    def add_history_entry(
        self,
        text: str,
        *,
        source: str = "listen",
        payload: str | None = None,
        original: str | None = None,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        should_scroll = (not self._visible) or self._is_near_bottom()
        evicted_id: int | None = None
        if len(self._history) == MAX_HISTORY and self._history:
            evicted_id = int(self._history[0].get("id", 0))
        self._history_seq += 1
        if payload is None:
            resolved_payload = "" if source == "error" else message
        else:
            resolved_payload = str(payload).strip()
        entry = {
            "id": self._history_seq,
            "text": message,
            "source": str(source or "listen"),
            "payload": resolved_payload,
            "original": str(original or "").strip(),
        }
        self._history.append(entry)
        if evicted_id is not None:
            self._remove_history_entry_ui(evicted_id)
            self._reindex_history_rows()
            self._reveal_leading_speaker()
            if self._selected_history_id == evicted_id:
                self._selected_history_id = None
        self._append_history_entry_ui(entry)
        if self._selected_entry() is None:
            self._selected_history_id = None
        self._update_actions()
        if should_scroll:
            self._schedule_scroll_to_bottom()

    def reveal(self) -> None:
        if not self._visible:
            self.show()
            self._visible = True
        set_window_topmost(self, self._topmost)
        self.raise_()
        self.activateWindow()

    def hide_from_service(self) -> None:
        original_on_close = self._on_close
        try:
            self._on_close = None
            self.hide()
        finally:
            self._on_close = original_on_close

    def hide(self) -> None:  # type: ignore[override]
        should_notify = self._visible or self.isVisible()
        if not should_notify:
            return
        self._visible = False
        super().hide()
        if self._on_close is not None:
            self._on_close()

    def _begin_drag(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()
        return True

    def _continue_drag(self, event) -> bool:
        if self._drag_position is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return False
        self.move(event.globalPosition().toPoint() - self._drag_position)
        event.accept()
        return True

    def _event_window_pos(self, obj, event) -> QPoint | None:
        if not hasattr(event, "position"):
            return None
        pos = event.position().toPoint()
        if obj is self:
            return pos
        if isinstance(obj, QWidget):
            return obj.mapTo(self, pos)
        return None

    @staticmethod
    def _event_global_pos(event) -> QPoint | None:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        if hasattr(event, "globalPos"):
            return event.globalPos()
        return None

    def _begin_resize(self, event, mode: str) -> bool:
        if not mode or event.button() != Qt.MouseButton.LeftButton:
            return False
        global_pos = self._event_global_pos(event)
        if global_pos is None:
            return False
        self._resize_mode = mode
        self._resize_start_pos = global_pos
        self._resize_start_geometry = self.geometry()
        self._drag_position = None
        event.accept()
        return True

    def _continue_resize(self, event) -> bool:
        if not (
            self._resize_mode
            and self._resize_start_pos is not None
            and self._resize_start_geometry is not None
        ):
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return False
        global_pos = self._event_global_pos(event)
        if global_pos is None:
            return False
        delta = global_pos - self._resize_start_pos
        self.setGeometry(
            self._geometry_for_resize_delta(
                self._resize_mode,
                self._resize_start_geometry,
                delta,
            )
        )
        event.accept()
        return True

    def _finish_resize_or_drag(self, event) -> bool:
        was_resizing = self._resize_mode is not None
        self._drag_position = None
        self._resize_mode = None
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self.unsetCursor()
        if was_resizing:
            event.accept()
        return was_resizing

    def _geometry_for_resize_delta(
        self,
        mode: str,
        start_geometry: QRect,
        delta: QPoint,
    ) -> QRect:
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()
        left = start_geometry.left()
        top = start_geometry.top()
        right = start_geometry.right()
        bottom = start_geometry.bottom()

        if "e" in mode:
            right = max(left + min_width - 1, right + delta.x())
        if "s" in mode:
            bottom = max(top + min_height - 1, bottom + delta.y())
        if "w" in mode:
            left = min(right - min_width + 1, left + delta.x())
        if "n" in mode:
            top = min(bottom - min_height + 1, top + delta.y())

        return QRect(QPoint(left, top), QPoint(right, bottom))

    def _cursor_for_resize_mode(self, mode: str) -> Qt.CursorShape | None:
        if mode in {"se", "nw"}:
            return Qt.CursorShape.SizeFDiagCursor
        if mode in {"ne", "sw"}:
            return Qt.CursorShape.SizeBDiagCursor
        if mode in {"e", "w"}:
            return Qt.CursorShape.SizeHorCursor
        if mode in {"n", "s"}:
            return Qt.CursorShape.SizeVerCursor
        return None

    def _update_resize_cursor(self, pos: QPoint) -> None:
        mode = self._get_resize_mode(pos)
        cursor = self._cursor_for_resize_mode(mode)
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)

    def _is_drag_region(self, obj, pos: QPoint) -> bool:
        if obj in {
            getattr(self, "_shell", None),
            getattr(self, "_opacity_label", None),
            getattr(self, "_status_label", None),
        }:
            return True
        if obj is self:
            drag_top = self._shell.mapTo(self, self._shell.rect().topLeft()).y()
            return pos.y() <= drag_top + self._scaled(46)
        return False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        }:
            pos = self._event_window_pos(obj, event)
            if pos is not None:
                if event_type == QEvent.Type.MouseButtonPress:
                    mode = self._get_resize_mode(pos)
                    if mode and self._begin_resize(event, mode):
                        return True
                    if self._is_drag_region(obj, pos):
                        return self._begin_drag(event)
                if event_type == QEvent.Type.MouseMove:
                    if self._continue_resize(event):
                        return True
                    self._update_resize_cursor(pos)
                    if self._continue_drag(event):
                        return True
                if event_type == QEvent.Type.MouseButtonRelease:
                    if self._finish_resize_or_drag(event):
                        return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()

            mode = self._get_resize_mode(pos)
            if mode and self._begin_resize(event, mode):
                return

            # Check if clicking in title bar for dragging
            drag_top = self._shell.mapTo(self, self._shell.rect().topLeft()).y()
            drag_bottom = drag_top + self._scaled(46)
            if pos.y() <= drag_bottom:
                if self._begin_drag(event):
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Update cursor based on position
        pos = event.position().toPoint()
        self._update_resize_cursor(pos)

        # Handle resize
        if self._continue_resize(event):
            return

        # Handle drag move
        if self._continue_drag(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._finish_resize_or_drag(event)
        super().mouseReleaseEvent(event)

    def _is_in_resize_area(self, pos: QPoint) -> bool:
        """Check if position is near any window edge/corner."""
        return bool(self._get_resize_mode(pos))

    def _get_resize_mode(self, pos: QPoint) -> str:
        """Get resize mode based on position."""
        w = self.width()
        h = self.height()
        margin = self._scaled(RESIZE_MARGIN)

        near_left = pos.x() <= margin
        near_right = pos.x() >= w - margin
        near_top = pos.y() <= margin
        near_bottom = pos.y() >= h - margin

        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        if near_left:
            return "w"
        if near_right:
            return "e"
        if near_top:
            return "n"
        if near_bottom:
            return "s"
        return ""

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hide()
        event.accept()
