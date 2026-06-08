from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from src.ui_qt.main_window import MainWindow
from src.ui_qt.styles import build_app_stylesheet
from src.ui_qt.theme import theme_from_config
from src.utils.app_paths import resource_base_dirs


def run_qt_app(config: dict) -> int:
    _configure_rendering()
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName("Mio RealTime Translator")
    app.setOrganizationName("MioTranslator")
    app.setFont(_app_font())
    app.setWindowIcon(_app_icon())
    _apply_style(app, config)

    window = MainWindow(config)
    window.setWindowIcon(_app_icon())
    window.show()
    return app.exec()


def _apply_style(app: QApplication, config: dict) -> None:
    app.setStyleSheet(build_app_stylesheet(theme_from_config(config)))


def _configure_rendering() -> None:
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    for attr_name in ("AA_UseHighDpiPixmaps", "AA_DontCreateNativeWidgetSiblings"):
        attr = getattr(Qt.ApplicationAttribute, attr_name, None)
        if attr is None:
            continue
        try:
            QApplication.setAttribute(attr, True)
        except Exception:
            pass


def _app_font() -> QFont:
    font = QFont("Segoe UI Variable Text")
    font.setPointSizeF(10.0)
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font


def _app_icon() -> QIcon:
    for base_dir in resource_base_dirs():
        for relative in (
            Path("assets") / "icons" / "app_icon_mio.ico",
            Path("assets") / "icons" / "app_icon_mio.png",
        ):
            path = base_dir / relative
            if path.is_file():
                return QIcon(str(path))
    return QIcon()
