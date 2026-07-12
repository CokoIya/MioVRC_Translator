# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""API & Models Settings Tab."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
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
)

from src.utils.ui_config import (
    get_backend_model_options,
    normalize_backend,
    normalize_qwen_translation_region,
    qwen_translation_region_for_ui_language,
)

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


class APIModelsTab(LocalizedSettingsTab):
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
        self._ui_language = normalize_settings_language(ui_language)

        self._init_ui()

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

        main_layout = self._root_layout()
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
        for label_key, backend in (
            ("provider_openai", "openai"),
            ("provider_anthropic", "anthropic"),
            ("provider_deepseek", "deepseek"),
            ("provider_gemini", "gemini"),
            ("provider_qwen", "qianwen"),
        ):
            self._provider_combo.addItem(self._t(label_key), backend)
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
        self._qwen_region_combo.addItem(self._t("region_china"), "china_mainland")
        self._qwen_region_combo.addItem(self._t("region_international"), "singapore")
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
        backend = str(self._provider_combo.itemData(index) or "")
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
        backend = normalize_backend(self._provider_combo.currentData())
        selected_model = self._model_combo.currentText().strip()
        selected_region = normalize_qwen_translation_region(
            self._qwen_region_combo.currentData()
        )

        config = {
            "translation": {
                "backend": backend or "openai",
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
                "qianwen": {
                    "api_key": self._qwen_key_input.text().strip(),
                    "region": selected_region,
                },
            }
        }
        if selected_model:
            config["translation"].setdefault(backend, {})["model"] = selected_model

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

        qwen_cfg = trans_cfg.get("qianwen", trans_cfg.get("qwen", {}))
        self._qwen_key_input.setText(qwen_cfg.get("api_key", ""))
        region = normalize_qwen_translation_region(
            qwen_cfg.get("region")
            or qwen_translation_region_for_ui_language(self._ui_language)
        )
        region_index = self._qwen_region_combo.findData(region)
        self._qwen_region_combo.setCurrentIndex(max(0, region_index))

        # Set provider
        backend = trans_cfg.get("backend", "openai")
        provider_map_reverse = {
            "openai": 0,
            "anthropic": 1,
            "deepseek": 2,
            "gemini": 3,
            "qianwen": 4,
            "qwen": 4,
        }
        self._provider_combo.setCurrentIndex(provider_map_reverse.get(normalize_backend(backend), 0))
        selected_backend = normalize_backend(backend)
        backend_cfg = trans_cfg.get(selected_backend, {})
        selected_model = str(backend_cfg.get("model", "") or "").strip()
        if selected_model:
            model_index = self._model_combo.findText(selected_model)
            if model_index < 0:
                self._model_combo.insertItem(0, selected_model)
                model_index = 0
            self._model_combo.setCurrentIndex(model_index)
