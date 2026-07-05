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
    QGroupBox,
)

from src.utils.i18n import tr as _base_tr


def tr(language: str | None, key: str, **kwargs) -> str:
    text = _base_tr(language, key, **kwargs)
    return "" if text == key else text

logger = logging.getLogger(__name__)


class QuickSetupTab(QWidget):
    """Quick Setup tab for first-time configuration."""

    config_changed = Signal()

    def __init__(
        self,
        config: dict,
        ui_language: str,
        on_test_microphone: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = ui_language
        self._on_test_microphone = on_test_microphone

        self._init_ui()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_language, key, **kwargs)

    def _init_ui(self) -> None:
        """Initialize the Quick Setup UI."""
        layout = QVBoxLayout(self)
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
        layout.addWidget(subtitle)

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
        self._provider_combo.addItems([
            self._t("provider_openai_recommended"),
            self._t("provider_anthropic"),
            self._t("provider_deepseek"),
            self._t("provider_gemini"),
            self._t("provider_qwen"),
        ])
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
        layout.addWidget(ready_label)

        tip_label = QLabel(
            self._t("quick_setup_tip")
        )
        tip_label.setStyleSheet("font-size: 12px; color: #888; margin-top: 10px;")
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
        # Update API key placeholder based on provider
        provider_index = self._provider_combo.currentIndex()
        placeholders = {
            0: "sk-...",  # OpenAI
            1: "sk-ant-...",  # Anthropic
            2: "sk-...",  # DeepSeek
            3: "AI...",  # Gemini
            4: "sk-...",  # Qwen
        }
        self._api_key_input.setPlaceholderText(placeholders.get(provider_index, ""))
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

        provider_map = {
            0: "openai",
            1: "anthropic",
            2: "deepseek",
            3: "gemini",
            4: "qwen",
        }

        config = {
            "source_language": source_map.get(self._source_combo.currentIndex(), "auto"),
            "target_language": target_map.get(self._target_combo.currentIndex(), "zh-CN"),
            "translation_provider": provider_map.get(self._provider_combo.currentIndex(), "openai"),
            "api_key": self._api_key_input.text().strip(),
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        # Reverse mapping from config to UI
        source_reverse = {"auto": 0, "en": 1, "zh-CN": 2, "ja": 3, "ko": 4, "es": 5, "fr": 6, "de": 7, "ru": 8}
        target_reverse = {"en": 0, "zh-CN": 1, "ja": 2, "ko": 3, "es": 4, "fr": 5, "de": 6, "ru": 7}
        provider_reverse = {"openai": 0, "anthropic": 1, "deepseek": 2, "gemini": 3, "qwen": 4}

        source = config.get("source_language", "auto")
        self._source_combo.setCurrentIndex(source_reverse.get(source, 0))

        target = config.get("target_language", "zh-CN")
        self._target_combo.setCurrentIndex(target_reverse.get(target, 1))

        provider = config.get("translation_provider", "openai")
        self._provider_combo.setCurrentIndex(provider_reverse.get(provider, 0))

        api_key = config.get("api_key", "")
        self._api_key_input.setText(api_key)
