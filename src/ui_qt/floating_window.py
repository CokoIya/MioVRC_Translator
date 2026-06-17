# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import logging

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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

logger = logging.getLogger(__name__)

MAX_HISTORY = 15
DEFAULT_SIZE = (520, 320)
MIN_SIZE = (360, 220)
DEFAULT_OPACITY = 0.88
MIN_OPACITY = 0.45
MAX_OPACITY = 1.0
BUBBLE_MIN_WRAP = 160
BUBBLE_MAX_WRAP = 440
BUBBLE_WRAP_RATIO = 0.72

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
        self._ui_lang = ui_language
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
        self.move(24, 96)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        flags = self.windowFlags() | Qt.WindowType.FramelessWindowHint
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowOpacity(self._opacity)

        self._build_ui()
        self._refresh_history()
        self.hide()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("textInputShell")
        self._shell.installEventFilter(self)
        shell_shadow = QGraphicsDropShadowEffect(self._shell)
        shell_shadow.setBlurRadius(28)
        shell_shadow.setOffset(0, 12)
        shell_shadow.setColor(self._shadow_color())
        self._shell.setGraphicsEffect(shell_shadow)
        root.addWidget(self._shell, 1)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(10, 10, 10, 10)
        shell_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._opacity_label = QLabel(self._opacity_label_text())
        self._opacity_label.setObjectName("opacityLabel")
        self._opacity_label.installEventFilter(self)
        top_row.addWidget(self._opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(int(MIN_OPACITY * 100), int(MAX_OPACITY * 100))
        self._opacity_slider.setValue(int(self._opacity * 100))
        self._opacity_slider.setFixedWidth(86)
        self._opacity_slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._opacity_slider.valueChanged.connect(self._on_opacity_change)
        top_row.addWidget(self._opacity_slider)
        top_row.addStretch(1)

        self._pin_button = QPushButton("")
        self._pin_button.setObjectName("pinButton")
        self._pin_button.setFixedSize(30, 30)
        self._pin_button.setIconSize(QSize(15, 15))
        self._pin_button.clicked.connect(self.toggle_topmost)
        self._refresh_pin_button()
        top_row.addWidget(self._pin_button)

        self._close_button = QPushButton("")
        self._close_button.setObjectName("iconButton")
        self._close_button.setFixedSize(30, 30)
        self._close_button.setIconSize(QSize(15, 15))
        self._close_button.clicked.connect(self.close)
        self._refresh_close_button()
        top_row.addWidget(self._close_button)
        shell_layout.addLayout(top_row)

        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("inputTextEdit")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("floatingScrollContent")
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(6, 6, 6, 5)
        self._scroll_layout.setSpacing(8)
        self._scroll_layout.addStretch(1)
        self._scroll_area.setWidget(self._scroll_content)
        shell_layout.addWidget(self._scroll_area, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._status_label = QLabel(self._status_text())
        self._status_label.setObjectName("textInputCounter")
        self._status_label.setToolTip(self._status_label.text())
        self._status_label.installEventFilter(self)
        footer.addWidget(self._status_label, 1)

        self._send_selected_button = QPushButton(tr(self._ui_lang, "send_to_vrc"))
        self._send_selected_button.setObjectName("primaryButton")
        send_icon = ui_icon("send.svg", 15, "#ffffff")
        if not send_icon.isNull():
            self._send_selected_button.setIcon(send_icon)
            self._send_selected_button.setIconSize(QSize(15, 15))
        self._send_selected_button.clicked.connect(self._send_selected_history)
        footer.addWidget(self._send_selected_button)
        shell_layout.addLayout(footer)

        self._apply_style()

    def resizeEvent(self, event) -> None:  # noqa: N802
        width = event.size().width()
        if abs(width - self._last_layout_width) < 24:
            return
        self._last_layout_width = width
        self._update_wraplengths()
        self._layout_refresh_timer.stop()
        self._layout_refresh_timer.start(60)
        super().resizeEvent(event)

    def _bubble_wraplength(self) -> int:
        width = self.width()
        return max(BUBBLE_MIN_WRAP, min(int(width * BUBBLE_WRAP_RATIO), BUBBLE_MAX_WRAP))

    def _update_wraplengths(self) -> None:
        wraplength = self._bubble_wraplength()
        for entry_id, widgets in self._history_widgets.items():
            bubble = widgets.get("bubble")
            if bubble is not None:
                bubble.setFixedWidth(wraplength)

    def _apply_layout_update(self) -> None:
        self._update_wraplengths()

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

    def _status_text(self) -> str:
        return tr(self._ui_lang, self._status_key)

    def _refresh_status_label(self) -> None:
        text = self._status_text()
        self._status_label.setText(text)
        self._status_label.setToolTip(text)

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

        self.setStyleSheet(build_floating_window_styles(self._theme))
        self._refresh_pin_button()
        self._refresh_close_button()
        for entry_id in self._history_widgets:
            self._style_history_entry(entry_id)

    def _refresh_pin_button(self) -> None:
        tokens = theme_tokens(self._theme)
        icon = ui_icon(
            "pin.svg" if self._topmost else "pin-off.svg",
            16,
            str(tokens["ACCENT"]) if self._topmost else icon_tint(self._theme),
        )
        self._pin_button.setIcon(icon)
        self._pin_button.setText(self._pin_text() if icon.isNull() else "")
        self._pin_button.setToolTip(self._pin_text())

    def _refresh_close_button(self) -> None:
        icon = ui_icon("x.svg", 15, icon_tint(self._theme, strong=True))
        self._close_button.setIcon(icon)
        close_text = tr(self._ui_lang, "text_input_close")
        self._close_button.setText(close_text if icon.isNull() else "")
        self._close_button.setToolTip(close_text)

    def _on_opacity_change(self, value: int) -> None:
        self._opacity = max(MIN_OPACITY, min(MAX_OPACITY, float(value) / 100.0))
        self.setWindowOpacity(self._opacity)
        self._opacity_label.setText(self._opacity_label_text())

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

    def _bubble_colors(self, source: str, *, selected: bool) -> tuple[str, str, str, str]:
        """返回 (背景, 边框, 文本, 阴影) 四元组"""
        tokens = theme_tokens(self._theme)
        text = str(tokens["TEXT_PRIMARY"])
        is_dark = self._theme == "dark"

        if selected:
            bg = str(tokens["ACCENT_SOFT"])
            border = str(tokens["ACCENT_BORDER"])
            shadow = f"rgba(47, 111, 255, 0.28)" if is_dark else f"rgba(0, 152, 199, 0.24)"
            return bg, border, text, shadow

        if source in {"manual", "mic"}:
            bg = "rgba(28, 34, 50, 0.82)" if is_dark else "rgba(232, 241, 249, 0.88)"
            border = "rgba(120, 154, 200, 0.38)" if is_dark else "rgba(93, 115, 145, 0.42)"
            shadow = f"rgba(0, 0, 0, 0.12)" if is_dark else f"rgba(0, 0, 0, 0.08)"
            return bg, border, text, shadow

        if source == "error":
            bg = str(tokens["DANGER_SOFT"])
            border = str(tokens["DANGER_BORDER"])
            shadow = f"rgba(220, 38, 38, 0.18)"
            return bg, border, text, shadow

        # 默认 incoming 消息
        bg = "rgba(22, 27, 40, 0.78)" if is_dark else "rgba(248, 251, 255, 0.92)"
        border = "rgba(148, 163, 184, 0.32)" if is_dark else "rgba(141, 151, 168, 0.38)"
        shadow = f"rgba(0, 0, 0, 0.10)" if is_dark else f"rgba(0, 0, 0, 0.06)"
        return bg, border, text, shadow

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
        wraplength = self._bubble_wraplength()

        lane = QFrame()
        lane.setObjectName("historyLane")
        lane_layout = QHBoxLayout(lane)
        lane_layout.setContentsMargins(0, 0, 0, 0)
        lane_layout.setSpacing(0)
        if side == "right":
            lane_layout.addStretch(1)

        bubble = QFrame()
        bubble.setObjectName("historyBubble")
        bubble.setFixedWidth(wraplength)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 10, 12, 10)

        label = QLabel(str(entry.get("text", "")))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble_layout.addWidget(label)
        lane_layout.addWidget(bubble, 0)
        if side == "left":
            lane_layout.addStretch(1)

        if can_resend:
            lane.setCursor(Qt.CursorShape.PointingHandCursor)
            bubble.setCursor(Qt.CursorShape.PointingHandCursor)
            lane.mousePressEvent = lambda _event, eid=entry_id: self._select_history_entry(eid)  # type: ignore[method-assign]
            bubble.mousePressEvent = lambda _event, eid=entry_id: self._select_history_entry(eid)  # type: ignore[method-assign]
            label.mousePressEvent = lambda _event, eid=entry_id: self._select_history_entry(eid)  # type: ignore[method-assign]

        self._scroll_layout.insertWidget(max(0, self._scroll_layout.count() - 1), lane)
        self._history_widgets[entry_id] = {
            "lane": lane,
            "bubble": bubble,
            "label": label,
            "source": source,
            "can_resend": can_resend,
        }
        self._style_history_entry(entry_id)

    def _style_history_entry(self, entry_id: int) -> None:
        widgets = self._history_widgets.get(entry_id)
        if widgets is None:
            return
        bubble = widgets.get("bubble")
        source = str(widgets.get("source", "listen"))
        is_selected = entry_id == self._selected_history_id
        if bubble is not None:
            bg, border, text, shadow = self._bubble_colors(source, selected=is_selected)
            bubble.setStyleSheet(f"""
                #historyBubble {{
                    background: {bg};
                    border: 1px solid {border};
                    border-radius: 16px;
                    padding: 12px 14px;
                }}
                #historyBubble QLabel {{
                    color: {text};
                    font-size: 13px;
                    line-height: 1.5;
                }}
            """)

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

    def _update_actions(self) -> None:
        entry = self._selected_entry()
        self._send_selected_button.setEnabled(entry is not None and self._entry_can_resend(entry))

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
        self._ui_lang = ui_language
        self.setWindowTitle(tr(self._ui_lang, "floating_window_title"))
        self._opacity_label.setText(self._opacity_label_text())
        self._refresh_status_label()
        self._refresh_pin_button()
        self._refresh_close_button()
        self._send_selected_button.setText(tr(self._ui_lang, "send_to_vrc"))
        self._refresh_history()

    def refresh_theme(self, theme: str) -> None:
        self._theme = str(theme or "dark")
        self._apply_style()

    def show_translation(self, text: str, *, source: str = "listen", payload: str | None = None) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._last_text = message
        self.add_history_entry(message, source=source, payload=payload)
        if not self._visible:
            self.show()
            self._visible = True
            set_window_topmost(self, self._topmost)
            self.raise_()
            self.activateWindow()

    def show_message(self, message) -> bool:
        display = str(getattr(message, "display_text", "") or "").strip()
        if not display:
            return False
        payload = str(getattr(message, "chatbox_text", "") or "").strip() or None
        source = str(getattr(message, "source", "listen") or "listen")
        self.show_translation(display, source=source, payload=payload)
        return True

    def set_listen_status(self, listening: bool) -> None:
        self._status_key = "floating_status_listening" if listening else "floating_status_waiting"
        self._refresh_status_label()

    def add_history_entry(self, text: str, *, source: str = "listen", payload: str | None = None) -> None:
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
        }
        self._history.append(entry)
        if evicted_id is not None:
            self._remove_history_entry_ui(evicted_id)
            self._reindex_history_rows()
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

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj in {
            getattr(self, "_shell", None),
            getattr(self, "_opacity_label", None),
            getattr(self, "_status_label", None),
        }:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress:
                return self._begin_drag(event)
            if event_type == QEvent.Type.MouseMove:
                return self._continue_drag(event)
            if event_type == QEvent.Type.MouseButtonRelease:
                self._drag_position = None
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            drag_top = self._shell.mapTo(self, self._shell.rect().topLeft()).y()
            drag_bottom = drag_top + 46
            if pos.y() <= drag_bottom:
                if self._begin_drag(event):
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._continue_drag(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hide()
        event.accept()
