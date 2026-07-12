# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 こみ_Mio and Mio RealTime Translator contributors

"""Shared localization support for the tabbed settings UI."""

from __future__ import annotations

from PySide6.QtWidgets import QLayout, QVBoxLayout, QWidget

from src.utils.i18n import tr
from src.utils.localization import SUPPORTED_UI_LANGUAGES, normalize_ui_language


SETTINGS_UI_LANGUAGES = SUPPORTED_UI_LANGUAGES


def normalize_settings_language(language: object) -> str:
    """Return a supported UI language, falling back to the application default."""

    return normalize_ui_language(language)


def clear_layout(layout: QLayout) -> None:
    """Remove all child layouts and widgets without replacing the root layout."""

    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        if child_layout is not None:
            clear_layout(child_layout)
            child_layout.deleteLater()
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()


class LocalizedSettingsTab(QWidget):
    """Base class that can rebuild a tab after a runtime language change."""

    _ui_language: str

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._ui_language, key, **kwargs)

    def _root_layout(self) -> QVBoxLayout:
        current = self.layout()
        if current is None:
            return QVBoxLayout(self)
        clear_layout(current)
        return current  # type: ignore[return-value]

    def set_ui_language(self, language: object) -> None:
        """Retranslate the tab while preserving all unsaved control values."""

        normalized = normalize_settings_language(language)
        if normalized == self._ui_language:
            return
        state = self.get_config()
        ui_state = self._capture_localization_state()
        if "ui_language" in state:
            state["ui_language"] = normalized
        was_blocked = self.blockSignals(True)
        try:
            self._ui_language = normalized
            self._init_ui()
            self.load_config(state)
            self._restore_localization_state(ui_state)
        finally:
            self.blockSignals(was_blocked)

    def _capture_localization_state(self) -> object:
        return None

    def _restore_localization_state(self, _state: object) -> None:
        return

    def get_config(self) -> dict:  # pragma: no cover - implemented by concrete tabs
        raise NotImplementedError

    def load_config(self, config: dict) -> None:  # pragma: no cover - implemented by concrete tabs
        raise NotImplementedError

    def _init_ui(self) -> None:  # pragma: no cover - implemented by concrete tabs
        raise NotImplementedError
