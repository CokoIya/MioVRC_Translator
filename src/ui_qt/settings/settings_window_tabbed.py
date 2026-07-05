# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Main Settings Window with Tabbed Interface."""

from __future__ import annotations

import copy
import logging
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QPushButton,
    QWidget,
    QMessageBox,
)

from .quick_setup_tab import QuickSetupTab
from .api_models_tab import APIModelsTab
from .audio_microphone_tab import AudioMicrophoneTab
from .tts_voice_tab import TTSVoiceTab
from .vrchat_integration_tab import VRChatIntegrationTab
from .advanced_tab import AdvancedTab
from src.utils import config_manager
from src.utils.i18n import tr as _base_tr


def tr(language: str | None, key: str, **kwargs) -> str:
    text = _base_tr(language, key, **kwargs)
    return "" if text == key else text

logger = logging.getLogger(__name__)


class _ValueVar:
    def __init__(self, value: str = ""):
        self._value = value

    def set(self, value: object) -> None:
        self._value = str(value)

    def value(self) -> str:
        return self._value


class SettingsWindowTabbed(QDialog):
    """Tabbed settings window with reorganized categories.

    The production app opens settings through MainWindow, whose surrounding code
    was originally written for the monolithic SettingsWindow.  This class keeps
    the tabbed UI while exposing the small compatibility surface MainWindow
    needs: save/close callbacks, language signal, VRC-listen synchronization,
    page selection, and theme sync.
    """

    config_changed = Signal(dict)
    saved = Signal()
    language_changed = Signal(str)

    _PAGE_TO_TAB = {
        "common": 0,
        "quick_setup": 0,
        "translation": 1,
        "api": 1,
        "api_models": 1,
        "voice": 2,
        "audio": 2,
        "microphone": 2,
        "tts": 3,
        "vrc": 4,
        "vrchat": 4,
        "vrc_listen": 4,
        "advanced": 5,
    }

    def __init__(
        self,
        config: dict,
        ui_language: str | None = None,
        on_test_microphone: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        on_save: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        on_listen_state_changed: Callable[[bool | None, bool | None, bool | None], None] | None = None,
        on_theme_changed: Callable[[str], None] | None = None,
        on_audio_diagnostics_requested: Callable[[str], None] | None = None,
        on_vad_calibration_requested: Callable[[str], None] | None = None,
        on_mode_wizard_requested: Callable[[], None] | None = None,
        preload: bool = False,
        defer_initial_page: bool = False,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = ui_language or str(config.get("ui", {}).get("language", "en") or "en")
        self._on_test_microphone = on_test_microphone
        self._on_save = on_save
        self._on_close = on_close
        self._on_listen_state_changed = on_listen_state_changed
        self._on_theme_changed = on_theme_changed
        self._on_audio_diagnostics_requested = on_audio_diagnostics_requested
        self._on_vad_calibration_requested = on_vad_calibration_requested
        self._on_mode_wizard_requested = on_mode_wizard_requested
        self._preloaded = preload
        self._defer_initial_page = defer_initial_page
        self._closing = False
        self._saving = False
        self._active_theme = str(
            config.get("ui", {}).get("main_window_theme")
            or config.get("ui", {}).get("theme")
            or "dark"
        )
        self._theme_var = _ValueVar(self._theme_labels().get(self._active_theme, self._theme_labels()["dark"]))

        self.setWindowTitle(tr(self._ui_language, "settings_title") or "Settings")
        self.setMinimumSize(1000, 700)

        self._init_ui()
        self.load_config(self._config)

    def _init_ui(self) -> None:
        """Initialize the tabbed UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._quick_setup_tab = QuickSetupTab(
            self._config,
            self._ui_language,
            self._on_test_microphone,
        )
        self._quick_setup_tab.config_changed.connect(self._on_tab_config_changed)
        self._tabs.addTab(
            self._quick_setup_tab,
            "🎯 " + (tr(self._ui_language, "quick_setup_tab") or "Quick Setup"),
        )

        self._api_models_tab = APIModelsTab(
            self._config,
            self._ui_language,
        )
        self._api_models_tab.config_changed.connect(self._on_tab_config_changed)
        self._tabs.addTab(
            self._api_models_tab,
            "🔑 " + (tr(self._ui_language, "api_models_tab") or "API & Models"),
        )

        self._audio_tab = AudioMicrophoneTab(
            self._config,
            self._ui_language,
            self._on_test_microphone,
            on_audio_diagnostics_requested=self._on_audio_diagnostics_requested,
            on_vad_calibration_requested=self._on_vad_calibration_requested,
        )
        self._audio_tab.config_changed.connect(self._on_tab_config_changed)
        self._tabs.addTab(
            self._audio_tab,
            "🎤 " + (tr(self._ui_language, "audio_tab") or "Audio & Microphone"),
        )

        self._tts_tab = TTSVoiceTab(
            self._config,
            self._ui_language,
        )
        self._tts_tab.config_changed.connect(self._on_tab_config_changed)
        self._tabs.addTab(
            self._tts_tab,
            "🔊 " + (tr(self._ui_language, "tts_tab") or "TTS & Voice"),
        )

        self._vrchat_tab = VRChatIntegrationTab(
            self._config,
            self._ui_language,
        )
        self._vrchat_tab.config_changed.connect(self._on_vrchat_config_changed)
        self._tabs.addTab(
            self._vrchat_tab,
            "🎮 " + (tr(self._ui_language, "vrchat_tab") or "VRChat"),
        )

        self._advanced_tab = AdvancedTab(
            self._config,
            self._ui_language,
        )
        self._advanced_tab.config_changed.connect(self._on_tab_config_changed)
        self._tabs.addTab(
            self._advanced_tab,
            "⚙️ " + (tr(self._ui_language, "advanced_tab") or "Advanced"),
        )

        layout.addWidget(self._tabs)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(20, 10, 20, 10)
        button_layout.addStretch()

        cancel_btn = QPushButton(tr(self._ui_language, "cancel") or "Cancel")
        cancel_btn.clicked.connect(self._on_cancel)
        button_layout.addWidget(cancel_btn)
        self._cancel_btn = cancel_btn

        save_btn = QPushButton(tr(self._ui_language, "save") or "Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save_clicked)
        button_layout.addWidget(save_btn)
        self._save_btn = save_btn

        layout.addLayout(button_layout)

    def _on_tab_config_changed(self) -> None:
        """Handle configuration change from any tab."""
        logger.debug("Tabbed settings config changed")

    def _on_vrchat_config_changed(self) -> None:
        self._on_tab_config_changed()
        if callable(self._on_listen_state_changed):
            listen_cfg = self._vrchat_tab.get_config().get("vrc_listen", {})
            self._on_listen_state_changed(
                listen_cfg.get("enabled"),
                listen_cfg.get("show_overlay"),
                listen_cfg.get("send_to_chatbox"),
            )

    def _on_save_clicked(self) -> None:
        """Save configuration and close."""
        if self._saving:
            return
        self._saving = True
        self._set_save_controls_enabled(False)
        previous = copy.deepcopy(self._config)
        try:
            new_config = self._collect_config()
            self._config.clear()
            self._config.update(new_config)
            config_manager.save_config(self._config)
        except Exception as exc:
            logger.exception("Failed to save tabbed settings")
            self._config.clear()
            self._config.update(previous)
            self._saving = False
            self._set_save_controls_enabled(True)
            QMessageBox.critical(
                self,
                tr(self._ui_language, "save_failed") or "Save failed",
                str(exc),
            )
            return

        self._saving = False
        self._set_save_controls_enabled(True)
        self.config_changed.emit(self._config)
        self.saved.emit()
        if callable(self._on_save):
            self._on_save()
        self.accept()
        if callable(self._on_close):
            self._on_close()

    def _collect_config(self) -> dict:
        """Collect and merge configuration from all tabs."""
        cfg = copy.deepcopy(self._config)

        for tab_config in (
            self._api_models_tab.get_config(),
            self._audio_tab.get_config(),
            self._tts_tab.get_config(),
            self._vrchat_tab.get_config(),
            self._advanced_tab.get_config(),
        ):
            self._merge_dict(cfg, tab_config)

        quick_config = self._quick_setup_tab.get_config()
        quick_translation = cfg.setdefault("translation", {})
        if "source_language" in quick_config:
            quick_translation["source_language"] = quick_config["source_language"]
        if "target_language" in quick_config:
            quick_translation["target_language"] = quick_config["target_language"]
        provider = quick_config.get("translation_provider")
        if provider:
            quick_translation["backend"] = provider
            api_key = quick_config.get("api_key")
            if api_key:
                quick_translation.setdefault(provider, {})["api_key"] = api_key

        return cfg

    def _merge_dict(self, target: dict, source: dict) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._merge_dict(target[key], value)
            else:
                target[key] = value

    def _set_save_controls_enabled(self, enabled: bool) -> None:
        self._save_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)

    def get_config(self) -> dict:
        """Get current configuration."""
        return copy.deepcopy(self._config)

    def load_config(self, config: dict) -> None:
        """Load configuration into all tabs."""
        translation_cfg = config.get("translation", {}) if isinstance(config, dict) else {}
        quick_config = {
            "source_language": translation_cfg.get("source_language", "auto"),
            "target_language": translation_cfg.get("target_language", "zh-CN"),
            "translation_provider": translation_cfg.get("backend", "openai"),
        }
        provider = quick_config["translation_provider"]
        provider_cfg = translation_cfg.get(provider, {}) if isinstance(translation_cfg.get(provider), dict) else {}
        quick_config["api_key"] = provider_cfg.get("api_key", "")

        loaders = (
            (self._quick_setup_tab, quick_config),
            (self._api_models_tab, config),
            (self._audio_tab, config),
            (self._tts_tab, config),
            (self._vrchat_tab, config),
            (self._advanced_tab, config),
        )
        for tab, data in loaders:
            load = getattr(tab, "load_config", None)
            if callable(load):
                try:
                    load(data)
                except Exception:
                    logger.debug("Failed to load config into %s", type(tab).__name__, exc_info=True)

    def select_page(self, page_id: str) -> None:
        """Select a tab using legacy SettingsWindow page IDs."""
        index = self._PAGE_TO_TAB.get(str(page_id or ""), 0)
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def sync_vrc_listen_state(
        self,
        *,
        enabled: bool | None = None,
        show_overlay: bool | None = None,
        send_to_chatbox: bool | None = None,
    ) -> None:
        """Synchronize listen controls from MainWindow state."""
        listen_cfg = self._config.setdefault("vrc_listen", {})
        if enabled is not None:
            listen_cfg["enabled"] = bool(enabled)
        if show_overlay is not None:
            listen_cfg["show_overlay"] = bool(show_overlay)
        if send_to_chatbox is not None:
            listen_cfg["send_to_chatbox"] = bool(send_to_chatbox)
        load = getattr(self._vrchat_tab, "load_config", None)
        if callable(load):
            load(self._config)

    def _theme_labels(self) -> dict[str, str]:
        return {
            "dark": "Dark",
            "light": "Light",
            "system": "System",
        }

    def _theme_code_from_label(self, label: object) -> str:
        label_text = str(label or "")
        for code, text in self._theme_labels().items():
            if label_text == text:
                return code
        return label_text if label_text in self._theme_labels() else "dark"

    def _on_theme_toggle(self) -> None:
        next_theme = "light" if self._active_theme == "dark" else "dark"
        self._active_theme = next_theme
        self._theme_var.set(self._theme_labels()[next_theme])
        self._config.setdefault("ui", {})["main_window_theme"] = next_theme
        if callable(self._on_theme_changed):
            self._on_theme_changed(next_theme)

    def sync_theme(self, theme_preference: object, *, smooth: bool = False) -> None:
        """Synchronize visual theme from MainWindow."""
        del smooth
        preference = self._theme_code_from_label(theme_preference)
        self._active_theme = preference
        self._theme_var.set(self._theme_labels().get(preference, str(theme_preference or "system")))
        self._config.setdefault("ui", {})["main_window_theme"] = preference
        parent = self.parent()
        stylesheet_provider = getattr(parent, "_base_stylesheet", None)
        if callable(stylesheet_provider):
            self.setStyleSheet(stylesheet_provider())

    def _on_cancel(self) -> None:
        if callable(self._on_close):
            self._on_close()
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        if callable(self._on_close):
            self._on_close()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._closing = False
        super().showEvent(event)
