# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

from src.ui_qt.styles import set_cjk_latin_font_family
from src.utils.app_paths import resource_base_dirs
from src.utils.ui_config import UI_FONT_SYSTEM, ui_font_preference_from_config

_BUNDLED_FONT_FILENAME = "851tegakizatsu.TTF"
_bundled_font_family: str | None = None


def cjk_latin_font_paths() -> list[Path]:
    paths: list[Path] = []
    for base_dir in resource_base_dirs():
        paths.append(base_dir / "assets" / "fonts" / _BUNDLED_FONT_FILENAME)
    paths.extend(
        [
            Path(r"C:\Windows\Fonts\851tegakizatsu.TTF"),
        ]
    )
    return paths


def application_font(config: object = None) -> QFont:
    """Return the selected application font and keep stylesheet fonts in sync."""

    preference = ui_font_preference_from_config(config)
    if preference != UI_FONT_SYSTEM:
        family = _load_bundled_font_family()
        if family:
            set_cjk_latin_font_family(family)
            font = QFont(family)
            font.setPointSizeF(10.0)
            _tune_font_rendering(font)
            return font

    font = _system_default_font()
    set_cjk_latin_font_family(font.family())
    _tune_font_rendering(font)
    return font


def apply_application_font(app: QApplication, config: object = None) -> QFont:
    font = application_font(config)
    app.setFont(font)
    return font


def _load_bundled_font_family() -> str | None:
    global _bundled_font_family
    if _bundled_font_family:
        return _bundled_font_family
    if QGuiApplication.instance() is None:
        return None
    for font_path in cjk_latin_font_paths():
        if not font_path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _bundled_font_family = families[0]
            return _bundled_font_family
    return None


def _system_default_font() -> QFont:
    try:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    except Exception:
        font = QFont()
    if font.family():
        return font
    return QFont("Segoe UI Variable Text")


def _tune_font_rendering(font: QFont) -> None:
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
