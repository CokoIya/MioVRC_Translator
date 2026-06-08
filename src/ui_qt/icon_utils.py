from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from src.utils.app_paths import resource_base_dirs


def find_ui_icon(filename: str) -> Path | None:
    for base_dir in resource_base_dirs():
        for rel in (
            Path("assets") / "icons" / "ui" / filename,
            Path("docs") / "assets" / "icons" / "ui" / filename,
        ):
            path = base_dir / rel
            if path.is_file():
                return path
    return None


def ui_icon_url(filename: str) -> str:
    path = find_ui_icon(filename)
    if path is None:
        return "none"
    return f'url("{path.as_posix()}")'


def ui_icon(filename: str, size: int = 16, color: str | None = None) -> QIcon:
    path = find_ui_icon(filename)
    if path is None:
        return QIcon()

    if not color:
        return QIcon(str(path))

    source = QPixmap(str(path))
    if source.isNull():
        return QIcon(str(path))

    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()

    return QIcon(pixmap)
