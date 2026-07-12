# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""API & Models Settings Tab."""

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
    QGroupBox,
    QScrollArea,
    QCheckBox,
)

from src.utils.i18n import tr as _base_tr
from src.utils.ui_config import get_backend_model_options


def tr(language: str | None, key: str, **kwargs) -> str:
    text = _base_tr(language, key, **kwargs)
    return "" if text == key else text

logger = logging.getLogger(__name__)


class APIModelsTab(QWidget):
    """API & Models configuration tab."""

    config_changed = Signal()

    def __init__(
        self,
        config: dict,
        ui_language: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = ui_language

        self._init_ui()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_language, key, **kwargs)

    def _init_ui(self) -> None:
        """Initialize the API & Models UI."""
        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("api_models_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Translation Provider Section
        self._add_provider_section(layout)

        # OpenAI Section
        self._add_openai_section(layout)

        # Anthropic Section
        self._add_anthropic_section(layout)

        # DeepSeek Section
        self._add_deepseek_section(layout)

        # Gemini Section
        self._add_gemini_section(layout)

        # Qwen Section
        self._add_qwen_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_provider_section(self, layout: QVBoxLayout) -> None:
        """Add translation provider selection."""
        group = QGroupBox(self._t("translation_provider"))
        group_layout = QVBoxLayout(group)

        # Provider combo
        provider_layout = QHBoxLayout()
        provider_label = QLabel(self._t("select_provider"))
        provider_layout.addWidget(provider_label)

        self._provider_combo = QComboBox()
        self._provider_combo.addItems([
            self._t("provider_openai"),
            self._t("provider_anthropic"),
            self._t("provider_deepseek"),
            self._t("provider_gemini"),
            self._t("provider_qwen"),
        ])
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addWidget(self._provider_combo, 1)

        group_layout.addLayout(provider_layout)

        # Model selection
        model_layout = QHBoxLayout()
        model_label = QLabel(self._t("model"))
        model_layout.addWidget(model_label)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        model_layout.addWidget(self._model_combo, 1)

        group_layout.addLayout(model_layout)

        layout.addWidget(group)

    def _add_openai_section(self, layout: QVBoxLayout) -> None:
        """Add OpenAI configuration."""
        self._openai_group = QGroupBox(self._t("openai_config"))
        group_layout = QVBoxLayout(self._openai_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._openai_key_input = QLineEdit()
        self._openai_key_input.setPlaceholderText("sk-...")
        self._openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._openai_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._openai_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Base URL
        url_layout = QHBoxLayout()
        url_label = QLabel(self._t("base_url"))
        url_layout.addWidget(url_label)

        self._openai_url_input = QLineEdit()
        self._openai_url_input.setPlaceholderText("https://api.openai.com/v1")
        url_layout.addWidget(self._openai_url_input, 1)

        group_layout.addLayout(url_layout)

        # Test button
        test_btn = QPushButton("🧪 " + self._t("test_connection"))
        test_btn.clicked.connect(self._test_openai)
        group_layout.addWidget(test_btn)

        layout.addWidget(self._openai_group)

    def _add_anthropic_section(self, layout: QVBoxLayout) -> None:
        """Add Anthropic configuration."""
        self._anthropic_group = QGroupBox(self._t("anthropic_config"))
        group_layout = QVBoxLayout(self._anthropic_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._anthropic_key_input = QLineEdit()
        self._anthropic_key_input.setPlaceholderText("sk-ant-...")
        self._anthropic_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._anthropic_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._anthropic_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Test button
        test_btn = QPushButton("🧪 " + self._t("test_connection"))
        test_btn.clicked.connect(self._test_anthropic)
        group_layout.addWidget(test_btn)

        layout.addWidget(self._anthropic_group)

    def _add_deepseek_section(self, layout: QVBoxLayout) -> None:
        """Add DeepSeek configuration."""
        self._deepseek_group = QGroupBox(self._t("deepseek_config"))
        group_layout = QVBoxLayout(self._deepseek_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._deepseek_key_input = QLineEdit()
        self._deepseek_key_input.setPlaceholderText("sk-...")
        self._deepseek_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._deepseek_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._deepseek_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        layout.addWidget(self._deepseek_group)

    def _add_gemini_section(self, layout: QVBoxLayout) -> None:
        """Add Gemini configuration."""
        self._gemini_group = QGroupBox(self._t("gemini_config"))
        group_layout = QVBoxLayout(self._gemini_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._gemini_key_input = QLineEdit()
        self._gemini_key_input.setPlaceholderText("AI...")
        self._gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._gemini_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._gemini_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        layout.addWidget(self._gemini_group)

    def _add_qwen_section(self, layout: QVBoxLayout) -> None:
        """Add Qwen configuration."""
        self._qwen_group = QGroupBox(self._t("qwen_config"))
        group_layout = QVBoxLayout(self._qwen_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._qwen_key_input = QLineEdit()
        self._qwen_key_input.setPlaceholderText("sk-...")
        self._qwen_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._qwen_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._qwen_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Region
        region_layout = QHBoxLayout()
        region_label = QLabel(self._t("region"))
        region_layout.addWidget(region_label)

        self._qwen_region_combo = QComboBox()
        self._qwen_region_combo.addItems([self._t("region_china"), self._t("region_international")])
        region_layout.addWidget(self._qwen_region_combo, 1)

        group_layout.addLayout(region_layout)

        layout.addWidget(self._qwen_group)

    def _toggle_password(self, line_edit: QLineEdit) -> None:
        """Toggle password visibility."""
        if line_edit.echoMode() == QLineEdit.EchoMode.Password:
            line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_provider_changed(self, index: int) -> None:
        """Handle provider selection change."""
        # Show/hide relevant sections
        self._openai_group.setVisible(index == 0)
        self._anthropic_group.setVisible(index == 1)
        self._deepseek_group.setVisible(index == 2)
        self._gemini_group.setVisible(index == 3)
        self._qwen_group.setVisible(index == 4)

        # Update model options
        provider_ids = {
            0: "openai",
            1: "anthropic",
            2: "deepseek",
            3: "gemini",
            4: "qianwen",
        }
        backend = provider_ids.get(index)
        models = list(get_backend_model_options(backend)) if backend else []

        self._model_combo.clear()
        self._model_combo.addItems(models)

        self.config_changed.emit()

    def _test_openai(self) -> None:
        """Test OpenAI connection."""
        logger.info("Testing OpenAI connection...")
        # TODO: Implement actual API test

    def _test_anthropic(self) -> None:
        """Test Anthropic connection."""
        logger.info("Testing Anthropic connection...")
        # TODO: Implement actual API test

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        provider_map = {
            0: "openai",
            1: "anthropic",
            2: "deepseek",
            3: "gemini",
            4: "qwen",
        }

        config = {
            "translation": {
                "backend": provider_map.get(self._provider_combo.currentIndex(), "openai"),
                "openai": {
                    "api_key": self._openai_key_input.text().strip(),
                    "base_url": self._openai_url_input.text().strip(),
                },
                "anthropic": {
                    "api_key": self._anthropic_key_input.text().strip(),
                },
                "deepseek": {
                    "api_key": self._deepseek_key_input.text().strip(),
                },
                "gemini": {
                    "api_key": self._gemini_key_input.text().strip(),
                },
                "qwen": {
                    "api_key": self._qwen_key_input.text().strip(),
                },
            }
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        trans_cfg = config.get("translation", {})

        # Load API keys
        openai_cfg = trans_cfg.get("openai", {})
        self._openai_key_input.setText(openai_cfg.get("api_key", ""))
        self._openai_url_input.setText(openai_cfg.get("base_url", ""))

        anthropic_cfg = trans_cfg.get("anthropic", {})
        self._anthropic_key_input.setText(anthropic_cfg.get("api_key", ""))

        deepseek_cfg = trans_cfg.get("deepseek", {})
        self._deepseek_key_input.setText(deepseek_cfg.get("api_key", ""))

        gemini_cfg = trans_cfg.get("gemini", {})
        self._gemini_key_input.setText(gemini_cfg.get("api_key", ""))

        qwen_cfg = trans_cfg.get("qwen", {})
        self._qwen_key_input.setText(qwen_cfg.get("api_key", ""))

        # Set provider
        backend = trans_cfg.get("backend", "openai")
        provider_map_reverse = {
            "openai": 0,
            "anthropic": 1,
            "deepseek": 2,
            "gemini": 3,
            "qwen": 4,
        }
        self._provider_combo.setCurrentIndex(provider_map_reverse.get(backend, 0))
