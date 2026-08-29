# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.icon_utils import ui_icon
from src.ui_qt.styles import build_text_input_styles
from src.ui_qt.theme import icon_tint, theme_from_config, theme_tokens
from src.ui_qt.window_utils import set_window_topmost
from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language
from src.utils.ui_config import get_ui_language

logger = logging.getLogger(__name__)

# Bumped so the roomier saved geometry from the old layout is replaced by the
# composer-sized default instead of preserving all that empty space.
TEXT_INPUT_WINDOW_CONFIG_VERSION = 6
DEFAULT_GEOMETRY = "460x210"
DEFAULT_SIZE = (460, 210)
MIN_SIZE = (320, 150)
# A composer for 144 characters does not need to keep a third of the screen.
# The floor follows the display only part of the way so "minimum" stays small.
MIN_SIZE_SCALE_CAP = 1.12
INPUT_MIN_HEIGHT = 64
# One source of truth for the header/footer control squares.
HEADER_CONTROL_SIZE = 26
# VRChat truncates past the chatbox limit, so the counter warns before it bites.
COUNTER_WARN_RATIO = 0.85
DEFAULT_OPACITY = 0.88
MIN_OPACITY = 0.45
MAX_OPACITY = 1.0
MAIN_THEME_CONFIG_KEY = "main_window_theme"
BASE_DPI = 96.0
RESIZE_MARGIN = 14
TEXT_INPUT_CHAR_LIMIT = 144
MIN_UI_SCALE = 0.9
MAX_UI_SCALE = 1.55


def _as_opacity(value: object, default: float = DEFAULT_OPACITY) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(MIN_OPACITY, min(MAX_OPACITY, parsed))


def _system_theme() -> str:
    app = QApplication.instance()
    if app is None:
        return "dark"
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    try:
        window_color = app.palette().color(QPalette.ColorRole.Window)
        return "dark" if window_color.lightness() < 128 else "light"
    except Exception:
        return "dark"


def _parse_geometry(geo: str) -> tuple[int, int, int, int]:
    try:
        parts = geo.replace("x", "+").split("+")
        if len(parts) == 4:
            return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        if len(parts) == 2:
            return int(parts[0]), int(parts[1]), 0, 0
    except (TypeError, ValueError):
        pass
    return DEFAULT_SIZE[0], DEFAULT_SIZE[1], 0, 0


class TextInputWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        config: dict,
        initial_text: str = "",
        on_send: Callable[[str], bool] | None = None,
    ) -> None:
        # Keep this as an unowned top-level window so minimizing or raising the
        # main window does not drag the floating input along on Windows.
        super().__init__(None)
        self._config = config if isinstance(config, dict) else {}
        self._on_send = on_send
        self._ui_lang = get_ui_language(self._config)
        self._closed = False
        self._text_was_sent = False
        self._drag_position: QPoint | None = None
        self._positioned = False
        self._last_ui_scale = 0.0
        self._resize_mode: str | None = None
        self._resize_start_pos: QPoint | None = None
        self._resize_start_geometry: QRect | None = None

        window_cfg = self._config.get("text_input_window", {})
        if not isinstance(window_cfg, dict):
            window_cfg = {}
        self._topmost = bool(window_cfg.get("topmost", True))
        self._opacity = _as_opacity(window_cfg.get("opacity", DEFAULT_OPACITY))
        self._minimized = bool(window_cfg.get("minimized", False))

        if window_cfg.get("size_version") != TEXT_INPUT_WINDOW_CONFIG_VERSION:
            geo = DEFAULT_GEOMETRY
        else:
            geo = str(window_cfg.get("geometry") or DEFAULT_GEOMETRY)
        self._has_saved_position = "+" in geo

        w, h, x, y = _parse_geometry(geo)
        self.setWindowTitle(tr(self._ui_lang, "text_input_floating"))
        self.resize(max(MIN_SIZE[0], w), max(MIN_SIZE[1], h))
        self.setMinimumSize(*MIN_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if self._has_saved_position and (x or y):
            self.move(x, y)
            self._positioned = True

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        flags = self.windowFlags() | Qt.WindowType.FramelessWindowHint
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowOpacity(self._opacity)
        self.setMouseTracking(True)

        self._build_ui(initial_text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QTimer.singleShot(0, self._focus_input)

        if self._minimized:
            QTimer.singleShot(0, self.showMinimized)

        logger.info("TextInputWindow opened (topmost=%s opacity=%.2f)", self._topmost, self._opacity)

    def _build_ui(self, initial_text: str) -> None:
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(10, 10, 10, 10)
        self._root_layout.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("textInputShell")
        shell_shadow = QGraphicsDropShadowEffect(self._shell)
        shell_shadow.setBlurRadius(28)
        shell_shadow.setOffset(0, 12)
        shell_shadow.setColor(self._shadow_color())
        self._shell.setGraphicsEffect(shell_shadow)
        self._root_layout.addWidget(self._shell, 1)

        self._main_layout = QVBoxLayout(self._shell)
        self._main_layout.setContentsMargins(10, 10, 10, 10)
        self._main_layout.setSpacing(8)

        self._top_row = QHBoxLayout()
        self._top_row.setSpacing(6)

        # The window is frameless, so the header carries its name. Without it
        # the composer arrives on screen with nothing saying what it is.
        self._title_label = QLabel(tr(self._ui_lang, "text_input_floating"))
        self._title_label.setObjectName("textInputTitle")
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self._top_row.addWidget(
            self._title_label, 0, Qt.AlignmentFlag.AlignVCenter
        )
        self._top_row.addStretch(1)

        self._opacity_label = QLabel(f"{int(round(self._opacity * 100))}%")
        self._opacity_label.setObjectName("textInputCounter")
        self._opacity_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._opacity_label.setToolTip(self._opacity_label_text())
        self._top_row.addWidget(self._opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(int(MIN_OPACITY * 100), int(MAX_OPACITY * 100))
        self._opacity_slider.setValue(int(round(self._opacity * 100)))
        self._opacity_slider.setFixedWidth(64)
        self._opacity_slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._opacity_slider.setToolTip(self._opacity_label_text())
        self._opacity_slider.valueChanged.connect(self._on_opacity_change)
        self._top_row.addWidget(self._opacity_slider)

        self._pin_button = QPushButton("")
        self._pin_button.setObjectName("iconButton")
        self._pin_button.setFixedSize(HEADER_CONTROL_SIZE, HEADER_CONTROL_SIZE)
        self._pin_button.setIconSize(QSize(14, 14))
        self._pin_button.clicked.connect(self.toggle_topmost)
        self._top_row.addWidget(self._pin_button)

        self._close_btn = QPushButton("")
        self._close_btn.setObjectName("iconButton")
        self._close_btn.setFixedSize(HEADER_CONTROL_SIZE, HEADER_CONTROL_SIZE)
        self._close_btn.setIconSize(QSize(14, 14))
        self._close_btn.clicked.connect(self.close)
        self._top_row.addWidget(self._close_btn)
        self._main_layout.addLayout(self._top_row)

        self._input_edit = QTextEdit()
        self._input_edit.setObjectName("inputTextEdit")
        self._input_edit.setAcceptRichText(False)
        self._input_edit.setPlaceholderText(tr(self._ui_lang, "text_input_placeholder"))
        self._input_edit.setMinimumHeight(INPUT_MIN_HEIGHT)
        self._input_edit.setText(initial_text)
        self._input_edit.installEventFilter(self)
        self._input_edit.textChanged.connect(self._refresh_actions)
        self._main_layout.addWidget(self._input_edit, 1)

        self._bottom_row = QHBoxLayout()
        self._bottom_row.setSpacing(8)

        self._counter_label = QLabel("")
        self._counter_label.setObjectName("textInputCounter")
        self._bottom_row.addWidget(self._counter_label)

        # Enter sends and Shift+Enter breaks the line; nothing said so before.
        self._key_hint_label = QLabel(tr(self._ui_lang, "text_input_key_hint"))
        self._key_hint_label.setObjectName("textInputKeyHint")
        self._key_hint_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._bottom_row.addWidget(self._key_hint_label, 1)

        self._clear_btn = QPushButton("")
        self._clear_btn.setObjectName("iconButton")
        self._clear_btn.setFixedSize(HEADER_CONTROL_SIZE, HEADER_CONTROL_SIZE)
        self._clear_btn.setIconSize(QSize(14, 14))
        self._clear_btn.clicked.connect(self._on_clear)
        self._bottom_row.addWidget(self._clear_btn)

        self._send_btn = QPushButton(tr(self._ui_lang, "text_input_send"))
        self._send_btn.setObjectName("primaryButton")
        self._send_btn.setIconSize(QSize(14, 14))
        self._send_btn.clicked.connect(self._on_send_clicked)
        self._send_btn.setDefault(True)
        self._bottom_row.addWidget(self._send_btn)
        self._main_layout.addLayout(self._bottom_row)

        self._apply_style()
        self._refresh_icons()
        self._refresh_actions()
        self._install_interaction_filter(self)
        self._apply_scaled_layout(force=True)

    def _apply_topmost_flags(self) -> None:
        was_visible = self.isVisible()
        if set_window_topmost(self, self._topmost, frameless=True):
            return
        if was_visible and not self._closed:
            self.show()
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(0, self._focus_input)

    def _shadow_color(self) -> QColor:
        shadow = QColor.fromString(str(theme_tokens(self._theme())["SHADOW"]))
        if shadow.isValid():
            return shadow
        return QColor(2, 6, 23, 97) if self._theme() == "dark" else QColor(15, 23, 42, 26)

    def _apply_style(self) -> None:
        if hasattr(self, "_shell"):
            effect = self._shell.graphicsEffect()
            if isinstance(effect, QGraphicsDropShadowEffect):
                effect.setColor(self._shadow_color())
        self.setStyleSheet(build_text_input_styles(self._theme()) + self._text_input_scale_styles())
        self._refresh_icons()

    def _set_button_icon(
        self,
        button: QPushButton,
        filename: str,
        color: str,
        fallback_text: str = "",
    ) -> None:
        icon = ui_icon(filename, self._scaled(14), color)
        button.setIcon(icon)
        button.setText(fallback_text if icon.isNull() else "")

    def _refresh_icons(self) -> None:
        tokens = theme_tokens(self._theme())
        muted = icon_tint(self._theme())
        strong = icon_tint(self._theme(), strong=True)
        primary = str(tokens["ACCENT"])
        self._set_button_icon(
            self._pin_button,
            "pin.svg" if self._topmost else "pin-off.svg",
            primary if self._topmost else muted,
            self._pin_text(),
        )
        self._pin_button.setToolTip(self._pin_text())
        self._pin_button.setProperty("pinned", "true" if self._topmost else "false")
        self._pin_button.style().unpolish(self._pin_button)
        self._pin_button.style().polish(self._pin_button)
        self._set_button_icon(self._close_btn, "x.svg", strong, tr(self._ui_lang, "text_input_close"))
        self._close_btn.setToolTip(tr(self._ui_lang, "text_input_close"))
        self._set_button_icon(self._clear_btn, "trash.svg", strong, tr(self._ui_lang, "text_input_clear"))
        self._clear_btn.setToolTip(tr(self._ui_lang, "text_input_clear"))
        send_icon = ui_icon("send.svg", self._scaled(14), "#ffffff")
        self._send_btn.setIcon(send_icon if not send_icon.isNull() else QIcon())

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = normalize_ui_language(ui_language)
        self.setWindowTitle(tr(self._ui_lang, "text_input_floating"))
        self._input_edit.setPlaceholderText(tr(self._ui_lang, "text_input_placeholder"))
        self._send_btn.setText(tr(self._ui_lang, "text_input_send"))
        self._opacity_label.setToolTip(self._opacity_label_text())
        self._opacity_slider.setToolTip(self._opacity_label_text())
        self._apply_style()
        self._refresh_icons()
        self._refresh_actions()
        self._refresh_header_labels()

    def _opacity_label_text(self) -> str:
        return tr(self._ui_lang, "text_input_opacity", pct=int(round(self._opacity * 100)))

    def _pin_text(self) -> str:
        return tr(self._ui_lang, "text_input_pin_on" if self._topmost else "text_input_pin_off")

    def _refresh_header_labels(self) -> None:
        """Shrink the title before it can push the window controls around."""

        label = getattr(self, "_title_label", None)
        if label is None:
            return
        reserved = (
            self._scaled(64 + 26 + 26 + 48) + self._opacity_label.sizeHint().width()
        )
        available = self.width() - reserved
        title = tr(self._ui_lang, "text_input_floating")
        label.setToolTip(title)
        needed = QFontMetrics(label.font()).horizontalAdvance(title) + self._scaled(20)
        # A pill is a shape, not a sentence: half a word inside a bordered
        # capsule looks broken, so it steps aside instead of truncating.
        if available < needed:
            label.setVisible(False)
            return
        label.setVisible(True)
        label.setText(title)
        hint = getattr(self, "_key_hint_label", None)
        if hint is None:
            return
        hint_text = tr(self._ui_lang, "text_input_key_hint")
        hint.setToolTip(hint_text)
        # Every sibling in the footer plus the shell margins and the three
        # gaps between them; anything left over is what the hint may use.
        hint_room = (
            self.width()
            - self._send_btn.sizeHint().width()
            - self._clear_btn.sizeHint().width()
            - self._counter_label.sizeHint().width()
            - self._scaled(70)
        )
        # All or nothing: half a shortcut ("Enter 发送 · Shi…") teaches nobody
        # anything, and the room it takes is what pushes the row into overlap.
        needed = QFontMetrics(hint.font()).horizontalAdvance(hint_text)
        if hint_room < needed:
            hint.setVisible(False)
            return
        hint.setVisible(True)
        hint.setText(hint_text)

    def _on_opacity_change(self, value: int) -> None:
        self._opacity = _as_opacity(float(value) / 100.0)
        self.setWindowOpacity(self._opacity)
        self._opacity_label.setText(self._opacity_label_text())
        self._update_window_config()

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        self._apply_topmost_flags()
        self._refresh_icons()
        self._update_window_config()

    def _on_send_clicked(self) -> None:
        text = self._trim_input_to_limit().strip()
        if not text:
            self._focus_input()
            return
        self._text_was_sent = True
        if self._on_send:
            accepted = self._on_send(text)
            if accepted is False:
                self._text_was_sent = False
                return
        self._input_edit.clear()
        self._refresh_actions()
        self._focus_input()

    def _on_clear(self) -> None:
        self._input_edit.clear()
        self._refresh_actions()
        self._focus_input()

    def _refresh_actions(self) -> None:
        text = self._trim_input_to_limit()
        count = len(text.strip())
        self._counter_label.setText(tr(self._ui_lang, "char_count", count=count))
        # Past the limit VRChat silently truncates, so the count changes colour
        # while there is still room to shorten the message.
        near_limit = count >= int(TEXT_INPUT_CHAR_LIMIT * COUNTER_WARN_RATIO)
        at_limit = count >= TEXT_INPUT_CHAR_LIMIT
        state = "full" if at_limit else ("warn" if near_limit else "")
        if self._counter_label.property("limit") != state:
            self._counter_label.setProperty("limit", state)
            self._counter_label.style().unpolish(self._counter_label)
            self._counter_label.style().polish(self._counter_label)
        self._send_btn.setEnabled(count > 0)
        self._clear_btn.setEnabled(count > 0)

    def _trim_input_to_limit(self) -> str:
        text = self._input_edit.toPlainText()
        if len(text) <= TEXT_INPUT_CHAR_LIMIT:
            return text

        cursor_position = min(self._input_edit.textCursor().position(), TEXT_INPUT_CHAR_LIMIT)
        text = text[:TEXT_INPUT_CHAR_LIMIT]
        self._input_edit.blockSignals(True)
        self._input_edit.setPlainText(text)
        cursor = self._input_edit.textCursor()
        cursor.setPosition(cursor_position)
        self._input_edit.setTextCursor(cursor)
        self._input_edit.blockSignals(False)
        return text

    def _theme(self) -> str:
        return theme_from_config(self._config)

    def refresh_theme(self) -> None:
        self._apply_style()

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
        geometry = screen.availableGeometry()
        dpi_scale = max(0.8, float(screen.logicalDotsPerInch()) / BASE_DPI)
        resolution_scale = max(
            0.9,
            min(1.12, min(geometry.width() / 1366.0, geometry.height() / 768.0)),
        )
        return max(MIN_UI_SCALE, min(MAX_UI_SCALE, dpi_scale * resolution_scale))

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self._ui_scale())))

    def _input_font_px(self) -> int:
        width_adjust = 0
        if self.width() < DEFAULT_SIZE[0]:
            width_adjust = -1
        elif self.width() >= DEFAULT_SIZE[0] + 220:
            width_adjust = min(3, (self.width() - DEFAULT_SIZE[0]) // 220)
        return max(12, min(22, self._scaled(15) + int(width_adjust)))

    def _text_input_scale_styles(self) -> str:
        control = self._scaled(HEADER_CONTROL_SIZE)
        return f"""
        QLabel#textInputTitle {{
            font-size: {self._scaled(11)}px;
            min-height: {self._scaled(22)}px;
            max-height: {self._scaled(22)}px;
        }}
        QLabel#textInputCounter, QLabel#textInputKeyHint {{
            font-size: {self._scaled(11)}px;
        }}
        QTextEdit#inputTextEdit {{
            font-size: {self._input_font_px()}px;
            padding: {self._scaled(10)}px;
        }}
        QPushButton#primaryButton {{
            font-size: {self._scaled(13)}px;
            min-height: {control}px;
            max-height: {control}px;
            padding: 0 {self._scaled(12)}px;
        }}
        QPushButton#iconButton {{
            min-width: {control}px;
            max-width: {control}px;
            min-height: {control}px;
            max-height: {control}px;
            border-radius: {self._scaled(9)}px;
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
        self._main_layout.setContentsMargins(
            self._scaled(9),
            self._scaled(9),
            self._scaled(9),
            self._scaled(9),
        )
        self._main_layout.setSpacing(self._scaled(7))
        self._top_row.setSpacing(self._scaled(6))
        self._bottom_row.setSpacing(self._scaled(8))
        self._opacity_slider.setFixedWidth(self._scaled(64))
        self._input_edit.setMinimumHeight(self._scaled(INPUT_MIN_HEIGHT))
        control_size = self._scaled(HEADER_CONTROL_SIZE)
        icon_size = self._scaled(14)
        for button in (self._pin_button, self._close_btn, self._clear_btn):
            button.setFixedSize(control_size, control_size)
            button.setIconSize(QSize(icon_size, icon_size))
        self._send_btn.setIconSize(QSize(icon_size, icon_size))
        effect = self._shell.graphicsEffect()
        if isinstance(effect, QGraphicsDropShadowEffect):
            effect.setBlurRadius(self._scaled(28))
            effect.setOffset(0, self._scaled(12))
        self._apply_style()
        self._refresh_header_labels()
        return True

    def _install_interaction_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        widget.setMouseTracking(True)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
            child.setMouseTracking(True)
        viewport = getattr(self._input_edit, "viewport", None)
        if callable(viewport):
            edit_viewport = viewport()
            edit_viewport.installEventFilter(self)
            edit_viewport.setMouseTracking(True)

    def _focus_input(self) -> None:
        if self._closed or not hasattr(self, "_input_edit"):
            return
        self._input_edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        parent_geo = parent.frameGeometry()
        x = parent_geo.x() + max((parent_geo.width() - self.width()) // 2, 0)
        y = parent_geo.y() + max((parent_geo.height() - self.height()) // 2, 0)
        self.move(x, y)

    def showEvent(self, event) -> None:  # noqa: N802
        if not self._positioned and not self._has_saved_position:
            self._center_on_parent()
            self._positioned = True
        super().showEvent(event)
        QTimer.singleShot(0, self._focus_input)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_root_layout"):
            self._apply_scaled_layout()
        self._refresh_header_labels()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is getattr(self, "_input_edit", None) and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Escape:
                self.close()
                return True
            if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._on_send_clicked()
                return True
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
            drag_top = self._shell.mapTo(self, self._shell.rect().topLeft()).y()
            if pos.y() <= drag_top + self._scaled(46):
                if self._begin_drag(event):
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        self._update_resize_cursor(pos)
        if self._continue_resize(event):
            return
        if self._continue_drag(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._finish_resize_or_drag(event)
        super().mouseReleaseEvent(event)

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
        was_dragging = self._drag_position is not None
        self._drag_position = None
        self._resize_mode = None
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self.unsetCursor()
        if was_resizing or was_dragging:
            self._update_window_config()
            event.accept()
            return True
        return False

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
            getattr(self, "_counter_label", None),
        }:
            return True
        if obj is self:
            drag_top = self._shell.mapTo(self, self._shell.rect().topLeft()).y()
            return pos.y() <= drag_top + self._scaled(46)
        return False

    def _is_in_resize_area(self, pos: QPoint) -> bool:
        return bool(self._get_resize_mode(pos))

    def _get_resize_mode(self, pos: QPoint) -> str:
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
        if not self._closed:
            self._closed = True
            self._save_window_state()
            logger.info("TextInputWindow closed")
        super().closeEvent(event)

    def _update_window_config(self) -> None:
        if not isinstance(self._config, dict):
            return
        geo = self.geometry()
        window_cfg = self._config.setdefault("text_input_window", {})
        if not isinstance(window_cfg, dict):
            window_cfg = {}
            self._config["text_input_window"] = window_cfg
        window_cfg["geometry"] = f"{geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}"
        window_cfg["size_version"] = TEXT_INPUT_WINDOW_CONFIG_VERSION
        window_cfg["topmost"] = self._topmost
        window_cfg["opacity"] = self._opacity
        window_cfg["minimized"] = False

    def _save_window_state(self) -> None:
        self._update_window_config()
        from src.utils import config_manager
        try:
            config_manager.save_config(self._config)
        except Exception:
            logger.debug("Failed to save text input window state", exc_info=True)
