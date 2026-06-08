from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QTimer, Qt, QVariantAnimation
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget


def set_window_topmost(widget: QWidget, topmost: bool, *, frameless: bool = False) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = int(widget.winId())
            hwnd_insert_after = -1 if topmost else -2
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_noactivate = 0x0010
            swp_noownerzorder = 0x0200
            flags = swp_nosize | swp_nomove | swp_noactivate | swp_noownerzorder
            return bool(ctypes.windll.user32.SetWindowPos(hwnd, hwnd_insert_after, 0, 0, 0, 0, flags))
        except Exception:
            return False

    was_visible = widget.isVisible()
    flags = widget.windowFlags()
    if frameless:
        flags |= Qt.WindowType.FramelessWindowHint
    if topmost:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    else:
        flags &= ~Qt.WindowType.WindowStaysOnTopHint
    widget.setWindowFlags(flags)
    if was_visible:
        widget.show()
        widget.raise_()
    return True


def apply_window_chrome_theme(widget: QWidget, theme: str) -> bool:
    """Best-effort native title bar color sync on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hwnd = int(widget.winId())
        normalized = "light" if str(theme).lower() == "light" else "dark"
        use_dark = ctypes.c_int(1 if normalized == "dark" else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on modern Windows, 19 on older builds.
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(use_dark),
                ctypes.sizeof(use_dark),
            )
        caption = 0x0E0907 if normalized == "dark" else 0xF7FAFC
        border = 0x30271C if normalized == "dark" else 0xE5D8CC
        for attr, color in ((35, caption), (34, border)):
            value = ctypes.c_int(color)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        return True
    except Exception:
        return False


class _ThemeFadeOverlay(QWidget):
    def __init__(self, parent: QWidget, snapshot: QPixmap, start_opacity: float = 1.0) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._opacity = max(0.0, min(1.0, float(start_opacity)))
        self._animation: QVariantAnimation | None = None
        self._cleanup_timer: QTimer | None = None
        self._finished = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

    def start(self, duration_ms: int) -> None:
        duration = max(120, int(duration_ms))
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._opacity)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_opacity)
        animation.finished.connect(self._finish)
        self._animation = animation
        cleanup_timer = QTimer(self)
        cleanup_timer.setSingleShot(True)
        cleanup_timer.timeout.connect(self._finish)
        self._cleanup_timer = cleanup_timer
        cleanup_timer.start(duration + 180)
        animation.start()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.deleteLater()

    def _set_opacity(self, value: object) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._snapshot.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.drawPixmap(self.rect(), self._snapshot)


class _ThemeRevealOverlay(QWidget):
    def __init__(self, parent: QWidget, snapshot: QPixmap, origin: QPoint) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._origin = origin
        self._radius = 0.0
        self._animation: QVariantAnimation | None = None
        self._cleanup_timer: QTimer | None = None
        self._finished = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

    def _max_radius(self) -> float:
        corners = (
            self.rect().topLeft(),
            self.rect().topRight(),
            self.rect().bottomLeft(),
            self.rect().bottomRight(),
        )
        radius = 0.0
        for corner in corners:
            dx = self._origin.x() - corner.x()
            dy = self._origin.y() - corner.y()
            radius = max(radius, (dx * dx + dy * dy) ** 0.5)
        return max(1.0, radius + 2.0)

    def start(self, duration_ms: int) -> None:
        duration = max(160, int(duration_ms))
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(self._max_radius())
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_radius)
        animation.finished.connect(self._finish)
        self._animation = animation
        cleanup_timer = QTimer(self)
        cleanup_timer.setSingleShot(True)
        cleanup_timer.timeout.connect(self._finish)
        self._cleanup_timer = cleanup_timer
        cleanup_timer.start(duration + 180)
        animation.start()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.deleteLater()

    def _set_radius(self, value: object) -> None:
        self._radius = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._snapshot.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cover = QPainterPath()
        cover.addRect(0, 0, self.width(), self.height())
        circle = QPainterPath()
        circle.addEllipse(QPointF(self._origin), self._radius, self._radius)
        painter.setClipPath(cover.subtracted(circle))
        painter.drawPixmap(self.rect(), self._snapshot)


def play_theme_reveal(
    widget: QWidget,
    *,
    origin_widget: QWidget | None = None,
    duration_ms: int = 420,
) -> QWidget | None:
    if not widget.isVisible() or widget.width() < 2 or widget.height() < 2:
        return None
    snapshot = widget.grab()
    if snapshot.isNull():
        return None
    origin = widget.rect().center()
    if origin_widget is not None:
        try:
            origin = widget.mapFromGlobal(origin_widget.mapToGlobal(origin_widget.rect().center()))
        except Exception:
            origin = widget.rect().center()
    overlay = _ThemeRevealOverlay(widget, snapshot, origin)
    overlay.setGeometry(widget.rect())
    overlay.show()
    overlay.raise_()
    overlay.start(duration_ms)
    return overlay


def play_theme_fade(
    widget: QWidget,
    *,
    update=None,
    duration_ms: int = 180,
    start_opacity: float = 1.0,
) -> QWidget | None:
    if not widget.isVisible() or widget.width() < 2 or widget.height() < 2:
        if callable(update):
            update()
        return None
    snapshot = widget.grab()
    if snapshot.isNull():
        if callable(update):
            update()
        return None
    if callable(update):
        update()
    overlay = _ThemeFadeOverlay(widget, snapshot, start_opacity=start_opacity)
    overlay.setGeometry(widget.rect())
    overlay.show()
    overlay.raise_()
    overlay.start(duration_ms)
    return overlay
