# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QPalette
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
from src.utils.ui_config import get_ui_language

logger = logging.getLogger(__name__)

TEXT_INPUT_WINDOW_CONFIG_VERSION = 5
DEFAULT_GEOMETRY = "520x320"
DEFAULT_SIZE = (520, 320)
MIN_SIZE = (360, 220)
DEFAULT_OPACITY = 0.88
MIN_OPACITY = 0.45
MAX_OPACITY = 1.0
MAIN_THEME_CONFIG_KEY = "main_window_theme"


TEXT_INPUT_PALETTES = {
    "dark": {
        "shell": "rgba(9, 12, 17, 242)",
        "border": "rgba(148, 163, 184, 58)",
        "field": "rgba(248, 250, 252, 242)",
        "field_border": "rgba(148, 163, 184, 70)",
        "text": "#1d1d1f",
        "field_muted": "#6b7280",
        "chrome_text": "#e5e7eb",
        "muted": "rgba(203, 213, 225, 205)",
        "button": "rgba(30, 41, 59, 224)",
        "button_hover": "rgba(51, 65, 85, 236)",
        "primary": "#5b8cff",
        "primary_hover": "#6b99ff",
        "disabled": "rgba(51, 65, 85, 170)",
        "disabled_text": "#93a4bb",
        "slider_bg": "rgba(148, 163, 184, 82)",
        "shadow": QColor(2, 6, 23, 92),
    },
    "light": {
        "shell": "rgba(255, 255, 255, 246)",
        "border": "rgba(141, 151, 168, 70)",
        "field": "#fbfcff",
        "field_border": "#d7dde8",
        "text": "#111827",
        "field_muted": "#8a93a3",
        "chrome_text": "#111827",
        "muted": "#667085",
        "button": "#f3f6fb",
        "button_hover": "#e9eef7",
        "primary": "#0a84ff",
        "primary_hover": "#006dde",
        "disabled": "#dbe3ef",
        "disabled_text": "#7b8798",
        "slider_bg": "#dbe4ef",
        "shadow": QColor(15, 23, 42, 46),
    },
}


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

        self._build_ui(initial_text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QTimer.singleShot(0, self._focus_input)

        if self._minimized:
            QTimer.singleShot(0, self.showMinimized)

        logger.info("TextInputWindow opened (topmost=%s opacity=%.2f)", self._topmost, self._opacity)

    def _build_ui(self, initial_text: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("textInputShell")
        shell_shadow = QGraphicsDropShadowEffect(self._shell)
        shell_shadow.setBlurRadius(28)
        shell_shadow.setOffset(0, 12)
        shell_shadow.setColor(self._shadow_color())
        self._shell.setGraphicsEffect(shell_shadow)
        root.addWidget(self._shell, 1)

        main_layout = QVBoxLayout(self._shell)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._opacity_label = QLabel(self._opacity_label_text())
        self._opacity_label.setObjectName("opacityLabel")
        top_row.addWidget(self._opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(int(MIN_OPACITY * 100), int(MAX_OPACITY * 100))
        self._opacity_slider.setValue(int(round(self._opacity * 100)))
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
        top_row.addWidget(self._pin_button)

        self._close_btn = QPushButton("")
        self._close_btn.setObjectName("iconButton")
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setIconSize(QSize(15, 15))
        self._close_btn.clicked.connect(self.close)
        top_row.addWidget(self._close_btn)
        main_layout.addLayout(top_row)

        self._input_edit = QTextEdit()
        self._input_edit.setObjectName("inputTextEdit")
        self._input_edit.setAcceptRichText(False)
        self._input_edit.setPlaceholderText(tr(self._ui_lang, "text_input_placeholder"))
        self._input_edit.setMinimumHeight(132)
        self._input_edit.setText(initial_text)
        self._input_edit.installEventFilter(self)
        self._input_edit.textChanged.connect(self._refresh_actions)
        main_layout.addWidget(self._input_edit, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self._counter_label = QLabel("")
        self._counter_label.setObjectName("textInputCounter")
        bottom_row.addWidget(self._counter_label, 1)

        self._clear_btn = QPushButton("")
        self._clear_btn.setObjectName("iconButton")
        self._clear_btn.setFixedSize(30, 30)
        self._clear_btn.setIconSize(QSize(15, 15))
        self._clear_btn.clicked.connect(self._on_clear)
        bottom_row.addWidget(self._clear_btn)

        self._send_btn = QPushButton(tr(self._ui_lang, "text_input_send"))
        self._send_btn.setObjectName("primaryButton")
        self._send_btn.setIconSize(QSize(15, 15))
        self._send_btn.clicked.connect(self._on_send_clicked)
        self._send_btn.setDefault(True)
        bottom_row.addWidget(self._send_btn)
        main_layout.addLayout(bottom_row)

        self._apply_style()
        self._refresh_icons()
        self._refresh_actions()

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
        self.setStyleSheet(build_text_input_styles(self._theme()))
        self._refresh_icons()

    def _set_button_icon(
        self,
        button: QPushButton,
        filename: str,
        color: str,
        fallback_text: str = "",
    ) -> None:
        icon = ui_icon(filename, 16, color)
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
        self._set_button_icon(self._close_btn, "x.svg", strong, tr(self._ui_lang, "text_input_close"))
        self._close_btn.setToolTip(tr(self._ui_lang, "text_input_close"))
        self._set_button_icon(self._clear_btn, "trash.svg", strong, tr(self._ui_lang, "text_input_clear"))
        self._clear_btn.setToolTip(tr(self._ui_lang, "text_input_clear"))
        send_icon = ui_icon("send.svg", 16, "#ffffff")
        self._send_btn.setIcon(send_icon if not send_icon.isNull() else QIcon())

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = ui_language
        self.setWindowTitle(tr(self._ui_lang, "text_input_floating"))
        self._input_edit.setPlaceholderText(tr(self._ui_lang, "text_input_placeholder"))
        self._send_btn.setText(tr(self._ui_lang, "text_input_send"))
        self._opacity_label.setText(self._opacity_label_text())
        self._apply_style()
        self._refresh_icons()
        self._refresh_actions()

    def _opacity_label_text(self) -> str:
        return tr(self._ui_lang, "text_input_opacity", pct=int(round(self._opacity * 100)))

    def _pin_text(self) -> str:
        return tr(self._ui_lang, "text_input_pin_on" if self._topmost else "text_input_pin_off")

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
        text = self._input_edit.toPlainText().strip()
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
        text = self._input_edit.toPlainText()
        count = len(text.strip())
        self._counter_label.setText(tr(self._ui_lang, "char_count", count=count))
        self._send_btn.setEnabled(count > 0)
        self._clear_btn.setEnabled(count > 0)

    def _theme(self) -> str:
        return theme_from_config(self._config)

    def refresh_theme(self) -> None:
        self._apply_style()

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
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_position = None
        super().mouseReleaseEvent(event)

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
