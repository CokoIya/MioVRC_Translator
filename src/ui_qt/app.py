# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from src.ui_qt.font_config import (
    application_font,
    apply_application_font,
    cjk_latin_font_paths,
)
from src.ui_qt.main_window import MainWindow
from src.ui_qt.qt_localization import install_qt_translations
from src.ui_qt.styles import build_app_stylesheet
from src.ui_qt.theme import theme_from_config
from src.utils.app_paths import resource_base_dirs
from src.utils.ui_config import get_ui_language


def run_qt_app(config: dict) -> int:
    from src.ui_qt.update_window import (
        consume_update_install_result,
        load_deferred_update_info,
        show_update_install_result,
    )

    update_install_result = consume_update_install_result()
    deferred_update = load_deferred_update_info()
    _configure_rendering()
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName("Mio RealTime Translator")
    app.setOrganizationName("MioTranslator")
    install_qt_translations(app, get_ui_language(config))
    apply_application_font(app, config)
    app.setWindowIcon(_app_icon())
    _apply_style(app, config)

    window = MainWindow(config)
    window.setWindowIcon(_app_icon())
    if deferred_update is not None:
        window._handle_update_available(deferred_update)
    window.show()
    if update_install_result is not None:
        ui_language = get_ui_language(config)
        QTimer.singleShot(
            0,
            lambda: show_update_install_result(
                window,
                update_install_result,
                ui_language,
            ),
        )
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
    for attr_name in (
        "AA_UseHighDpiPixmaps",
        "AA_DontCreateNativeWidgetSiblings",
        "AA_DontUseNativeDialogs",
    ):
        attr = getattr(Qt.ApplicationAttribute, attr_name, None)
        if attr is None:
            continue
        try:
            QApplication.setAttribute(attr, True)
        except Exception:
            pass


def _app_font(config: object = None) -> QFont:
    return application_font(config)


def _cjk_latin_font_paths() -> list[Path]:
    return cjk_latin_font_paths()


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
