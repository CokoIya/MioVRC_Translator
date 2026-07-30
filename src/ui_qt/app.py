# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import sys
import threading
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
from src.utils.startup_timing import record_startup_stage, startup_stage
from src.utils.ui_config import get_ui_language


logger = logging.getLogger(__name__)
_UPDATE_RESULT_ARG_PREFIX = "--mio-update-result="


def run_qt_app(config: dict) -> int:
    update_result_args = _detach_update_result_arguments(sys.argv)
    with startup_stage("ui.rendering_configuration"):
        _configure_rendering()
    with startup_stage("ui.application_create"):
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

    with startup_stage("ui.application_metadata"):
        app.setApplicationName("Mio RealTime Translator")
        app.setOrganizationName("MioTranslator")
        install_qt_translations(app, get_ui_language(config))
        app_icon = _app_icon()
        app.setWindowIcon(app_icon)

    with startup_stage("ui.main_window_create"):
        window = MainWindow(config)
        window.setWindowIcon(app_icon)
    with startup_stage("ui.main_window_show"):
        window.show()
    record_startup_stage("ui.show_requested")
    QTimer.singleShot(
        0,
        lambda: QTimer.singleShot(
            0,
            lambda: _run_post_show_app_initialization(
                app,
                window,
                config,
                update_result_args,
            ),
        )
    )
    return app.exec()


def _detach_update_result_arguments(argv: list[str]) -> list[str]:
    detached: list[str] = []
    retained: list[str] = []
    for argument in argv:
        if str(argument).startswith(_UPDATE_RESULT_ARG_PREFIX):
            detached.append(argument)
        else:
            retained.append(argument)
    if detached:
        argv[:] = retained
    return detached


def _run_post_show_app_initialization(
    app: QApplication,
    window: MainWindow,
    config: dict,
    update_result_args: list[str],
) -> None:
    record_startup_stage("ui.first_event_loop_turn")

    try:
        from src.utils.startup_tasks import schedule_post_ui_startup_tasks

        schedule_post_ui_startup_tasks()
    except Exception:
        logger.exception("Could not schedule post-window startup maintenance")

    start_background_initialization = getattr(
        window,
        "start_background_initialization",
        None,
    )
    if callable(start_background_initialization):
        try:
            start_background_initialization()
        except Exception:
            logger.exception("Could not schedule provider background initialization")

    _start_deferred_update_loading(window, config, update_result_args)

    try:
        with startup_stage("ui.deferred_font_apply"):
            apply_application_font(app, config)
            _apply_style(app, config)
    except Exception:
        logger.exception("Could not apply the configured application font")


def _start_deferred_update_loading(
    window: MainWindow,
    config: dict,
    update_result_args: list[str],
) -> None:
    def run() -> None:
        try:
            with startup_stage("background.update_state_load"):
                from src.ui_qt.update_window import (
                    consume_update_install_result,
                    load_deferred_update_info,
                    show_update_install_result,
                )

                update_install_result = consume_update_install_result(
                    argv=update_result_args,
                )
                deferred_update = load_deferred_update_info()
        except Exception:
            logger.exception("Could not load deferred update state")
            return

        def deliver() -> None:
            if deferred_update is not None:
                window._handle_update_available(deferred_update)
            if update_install_result is not None:
                show_update_install_result(
                    window,
                    update_install_result,
                    get_ui_language(config),
                )

        dispatch = getattr(window, "_call_in_ui", None)
        if callable(dispatch):
            dispatch(deliver, priority=True)
        else:
            deliver()

    threading.Thread(
        target=run,
        daemon=True,
        name="qt-update-state-loader",
    ).start()


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
