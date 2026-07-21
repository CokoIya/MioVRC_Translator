# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Quick Setup Tab - Get users running in 5 simple steps."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QFrame,
)

from src.utils.ui_config import UI_LANGUAGE_OPTIONS
from src.ui_qt.credential_prompt import show_missing_credential_prompt
from src.utils.credential_validation import (
    MissingCredential,
    first_missing_required_credential,
)
from src.utils.provider_settings import PROVIDER_CHOICES, preserve_provider_id

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


class QuickSetupTab(LocalizedSettingsTab):
    """Quick Setup tab for first-time configuration."""

    config_changed = Signal()
    language_changed = Signal(str)

    def __init__(
        self,
        config: dict,
        ui_language: str,
        on_test_microphone: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        *,
        on_open_api_settings: Callable[[MissingCredential], None] | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = normalize_settings_language(ui_language)
        self._on_test_microphone = on_test_microphone
        self._on_open_api_settings = on_open_api_settings
        self._updating_language = False
        self._loading_config = False
        translation_cfg = (
            config.get("translation", {}) if isinstance(config, dict) else {}
        )
        self._provider_api_keys = {}
        provider_ids = {backend for _label_key, backend in PROVIDER_CHOICES}
        provider_ids.add(preserve_provider_id(translation_cfg.get("backend")))
        for provider in provider_ids:
            provider_cfg = translation_cfg.get(provider, {})
            if provider == "qianwen" and not isinstance(provider_cfg, dict):
                provider_cfg = translation_cfg.get("qwen", {})
            if provider == "qianwen" and not provider_cfg:
                provider_cfg = translation_cfg.get("qwen", {})
            self._provider_api_keys[provider] = str(
                provider_cfg.get("api_key", "") if isinstance(provider_cfg, dict) else ""
            )
        self._current_provider = preserve_provider_id(
            translation_cfg.get("backend", "openai")
        )

        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the Quick Setup UI."""
        layout = self._root_layout()
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("quick_setup_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel(
            self._t("quick_setup_subtitle")
        )
        subtitle.setStyleSheet("font-size: 14px; color: #888;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        language_row = QHBoxLayout()
        language_label = QLabel(self._t("settings_app_language"))
        language_row.addWidget(language_label)
        self._language_combo = QComboBox()
        for label, code in UI_LANGUAGE_OPTIONS:
            self._language_combo.addItem(label, code)
        language_index = self._language_combo.findData(self._ui_language)
        self._language_combo.setCurrentIndex(max(0, language_index))
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_row.addWidget(self._language_combo, 1)
        layout.addLayout(language_row)

        layout.addSpacing(20)

        # Step 1: Translation Direction
        self._add_step(
            layout,
            "1",
            self._t("quick_setup_step1"),
            self._create_translation_direction_widget(),
        )

        # Step 2: Translation Provider
        self._add_step(
            layout,
            "2",
            self._t("quick_setup_step2"),
            self._create_provider_widget(),
        )

        # Step 3: API Key
        self._add_step(
            layout,
            "3",
            self._t("quick_setup_step3"),
            self._create_api_key_widget(),
        )

        # Step 4: Microphone Test
        self._add_step(
            layout,
            "4",
            self._t("quick_setup_step4"),
            self._create_microphone_test_widget(),
        )

        # Step 5: Ready
        self._add_step(
            layout,
            "5",
            self._t("quick_setup_step5"),
            self._create_ready_widget(),
        )

        layout.addStretch()

    def _add_step(
        self,
        layout: QVBoxLayout,
        number: str,
        title: str,
        widget: QWidget
    ) -> None:
        """Add a numbered step to the layout."""
        step_frame = QFrame()
        step_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        step_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 20px;
            }
        """)

        step_layout = QVBoxLayout(step_frame)

        # Step header
        header_layout = QHBoxLayout()

        # Step number badge
        badge = QLabel(number)
        badge.setFixedSize(32, 32)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("""
            QLabel {
                background: #2196F3;
                color: white;
                border-radius: 16px;
                font-weight: bold;
                font-size: 16px;
            }
        """)
        header_layout.addWidget(badge)

        # Step title
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_label.setWordWrap(True)
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        step_layout.addLayout(header_layout)
        step_layout.addSpacing(15)
        step_layout.addWidget(widget)

        layout.addWidget(step_frame)

    def _create_translation_direction_widget(self) -> QWidget:
        """Create translation direction selection widget."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Source language
        source_label = QLabel(self._t("source_language"))
        layout.addWidget(source_label)

        self._source_combo = QComboBox()
        self._source_combo.addItems([
            self._t("lang_auto_detect"),
            self._t("lang_english"),
            self._t("lang_chinese_simplified"),
            self._t("lang_japanese"),
            self._t("lang_korean"),
            self._t("lang_spanish"),
            self._t("lang_french"),
            self._t("lang_german"),
            self._t("lang_russian"),
        ])
        self._source_combo.currentIndexChanged.connect(self._on_config_change)
        layout.addWidget(self._source_combo, 1)

        # Arrow
        arrow_label = QLabel("→")
        arrow_label.setStyleSheet("font-size: 20px;")
        layout.addWidget(arrow_label)

        # Target language
        target_label = QLabel(self._t("target_language"))
        layout.addWidget(target_label)

        self._target_combo = QComboBox()
        self._target_combo.addItems([
            self._t("lang_english"),
            self._t("lang_chinese_simplified"),
            self._t("lang_japanese"),
            self._t("lang_korean"),
            self._t("lang_spanish"),
            self._t("lang_french"),
            self._t("lang_german"),
            self._t("lang_russian"),
        ])
        self._target_combo.setCurrentIndex(1)  # Default to Chinese
        self._target_combo.currentIndexChanged.connect(self._on_config_change)
        layout.addWidget(self._target_combo, 1)

        return widget

    def _create_provider_widget(self) -> QWidget:
        """Create translation provider selection widget."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(self._t("select_provider"))
        layout.addWidget(label)

        self._provider_combo = QComboBox()
        for label_key, backend in PROVIDER_CHOICES:
            self._provider_combo.addItem(self._t(label_key), backend)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_change)
        layout.addWidget(self._provider_combo, 1)

        return widget

    def _create_api_key_widget(self) -> QWidget:
        """Create API key input widget."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # API key input
        key_layout = QHBoxLayout()
        label = QLabel(self._t("api_key"))
        key_layout.addWidget(label)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText("sk-...")
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.textChanged.connect(self._on_config_change)
        key_layout.addWidget(self._api_key_input, 1)

        # Show/Hide button
        self._show_key_btn = QPushButton("👁")
        self._show_key_btn.setFixedWidth(40)
        self._show_key_btn.clicked.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._show_key_btn)

        layout.addLayout(key_layout)

        # Help text
        help_text = QLabel(
            self._t("api_key_help")
        )
        help_text.setStyleSheet("font-size: 12px; color: #888;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        return widget

    def _create_microphone_test_widget(self) -> QWidget:
        """Create microphone test widget."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(
            self._t("microphone_test_info")
        )
        label.setWordWrap(True)
        layout.addWidget(label, 1)

        test_btn = QPushButton(
            "🎤 " + self._t("test_microphone")
        )
        test_btn.setMinimumWidth(150)
        if self._on_test_microphone:
            test_btn.clicked.connect(self._on_test_microphone)
        layout.addWidget(test_btn)

        return widget

    def _create_ready_widget(self) -> QWidget:
        """Create ready confirmation widget."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        ready_label = QLabel("✅ " + (
            self._t("quick_setup_complete")
        ))
        ready_label.setStyleSheet("font-size: 14px; color: #4CAF50;")
        ready_label.setWordWrap(True)
        layout.addWidget(ready_label)

        tip_label = QLabel(
            self._t("quick_setup_tip")
        )
        tip_label.setStyleSheet("font-size: 12px; color: #888; margin-top: 10px;")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)

        return widget

    def _toggle_key_visibility(self) -> None:
        """Toggle API key visibility."""
        if self._api_key_input.echoMode() == QLineEdit.EchoMode.Password:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("🙈")
        else:
            self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("👁")

    def _on_provider_change(self) -> None:
        """Handle provider selection change."""
        provider = preserve_provider_id(self._provider_combo.currentData())
        if not self._loading_config:
            self._provider_api_keys[self._current_provider] = self._api_key_input.text()
        self._current_provider = provider
        provider_key = self._provider_api_keys.get(provider, "")
        if self._api_key_input.text() != provider_key:
            self._api_key_input.setText(provider_key)

        # Update API key placeholder based on provider
        placeholders = {
            "openai": "sk-...",
            "openai_compatible": "sk-...",
            "anthropic": "sk-ant-...",
            "anthropic_compatible": "sk-ant-...",
            "deepseek": "sk-...",
            "gemini": "AI...",
            "qianwen": "sk-...",
            "xai": "xai-...",
            "grok_compatible": "xai-...",
        }
        self._api_key_input.setPlaceholderText(placeholders.get(provider, ""))
        self._on_config_change()
        if not self._loading_config:
            self._prompt_for_missing_credential()

    def _prompt_for_missing_credential(self) -> bool:
        provider = preserve_provider_id(self._provider_combo.currentData())
        if provider not in {backend for _label_key, backend in PROVIDER_CHOICES}:
            return False
        config = {
            "translation": {
                "backend": provider,
                provider: {"api_key": self._api_key_input.text().strip()},
            }
        }
        missing = first_missing_required_credential(
            config,
            scopes=("translation",),
            ui_language=self._ui_language,
            active_only=False,
        )
        if missing is None:
            return False

        def open_api_settings() -> None:
            if callable(self._on_open_api_settings):
                self._on_open_api_settings(missing)
            else:
                self._api_key_input.setFocus()

        show_missing_credential_prompt(
            self,
            missing,
            ui_language=self._ui_language,
            open_settings=open_api_settings,
        )
        return True

    def _on_language_changed(self, _index: int) -> None:
        if self._updating_language:
            return
        code = str(self._language_combo.currentData() or "")
        if code:
            self.language_changed.emit(code)
        self._on_config_change()

    def _on_config_change(self) -> None:
        """Emit config changed signal."""
        self.config_changed.emit()

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        # Map UI selections to config format
        source_map = {
            0: "auto",
            1: "en",
            2: "zh-CN",
            3: "ja",
            4: "ko",
            5: "es",
            6: "fr",
            7: "de",
            8: "ru",
        }

        target_map = {
            0: "en",
            1: "zh-CN",
            2: "ja",
            3: "ko",
            4: "es",
            5: "fr",
            6: "de",
            7: "ru",
        }

        config = {
            "ui_language": str(self._language_combo.currentData() or self._ui_language),
            "source_language": source_map.get(self._source_combo.currentIndex(), "auto"),
            "target_language": target_map.get(self._target_combo.currentIndex(), "zh-CN"),
            "translation_provider": preserve_provider_id(
                self._provider_combo.currentData()
            ),
            "api_key": self._api_key_input.text().strip(),
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        self._loading_config = True
        try:
            self._load_config_values(config)
        finally:
            self._loading_config = False

    def _load_config_values(self, config: dict) -> None:
        ui_language = normalize_settings_language(config.get("ui_language", self._ui_language))
        self._updating_language = True
        try:
            index = self._language_combo.findData(ui_language)
            self._language_combo.setCurrentIndex(max(0, index))
        finally:
            self._updating_language = False

        # Reverse mapping from config to UI
        source_reverse = {"auto": 0, "en": 1, "zh-CN": 2, "ja": 3, "ko": 4, "es": 5, "fr": 6, "de": 7, "ru": 8}
        target_reverse = {"en": 0, "zh-CN": 1, "ja": 2, "ko": 3, "es": 4, "fr": 5, "de": 6, "ru": 7}
        source = config.get("source_language", "auto")
        self._source_combo.setCurrentIndex(source_reverse.get(source, 0))

        target = config.get("target_language", "zh-CN")
        self._target_combo.setCurrentIndex(target_reverse.get(target, 1))

        provider = preserve_provider_id(config.get("translation_provider", "openai"))
        api_key = str(config.get("api_key", "") or "")
        self._provider_api_keys[provider] = api_key
        provider_index = self._provider_combo.findData(provider)
        if provider_index < 0:
            self._provider_combo.addItem(
                self._t("current_provider_unavailable", provider=provider),
                provider,
            )
            provider_index = self._provider_combo.count() - 1
        self._provider_combo.setCurrentIndex(provider_index)

        self._api_key_input.setText(api_key)
