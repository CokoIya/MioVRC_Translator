# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""VRChat Integration Settings Tab."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QGroupBox,
    QScrollArea,
    QCheckBox,
    QLineEdit,
    QTextEdit,
)

from src.utils.i18n import tr as _base_tr


def tr(language: str | None, key: str, **kwargs) -> str:
    text = _base_tr(language, key, **kwargs)
    return "" if text == key else text

logger = logging.getLogger(__name__)


class VRChatIntegrationTab(QWidget):
    """VRChat Integration configuration tab."""

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
        """Initialize the VRChat Integration UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("vrchat_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # OSC Settings Section
        self._add_osc_section(layout)

        # Chatbox Format Section
        self._add_chatbox_section(layout)

        # Translation Output Section
        self._add_output_section(layout)

        # VRC Listen Section
        self._add_listen_section(layout)

        # Testing Section
        self._add_testing_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_osc_section(self, layout: QVBoxLayout) -> None:
        """Add OSC settings."""
        group = QGroupBox(self._t("osc_settings"))
        group_layout = QVBoxLayout(group)

        # Enable OSC
        self._osc_enable_check = QCheckBox(self._t("enable_osc"))
        self._osc_enable_check.setChecked(True)
        self._osc_enable_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._osc_enable_check)

        # Connection status
        status_layout = QHBoxLayout()
        status_label = QLabel(self._t("connection_status"))
        status_layout.addWidget(status_label)

        self._status_indicator = QLabel(self._t("osc_connected"))
        self._status_indicator.setStyleSheet("color: #4CAF50; font-weight: bold;")
        status_layout.addWidget(self._status_indicator)
        status_layout.addStretch()

        group_layout.addLayout(status_layout)

        # Guide button
        guide_btn = QPushButton("📖 " + self._t("osc_guide"))
        guide_btn.clicked.connect(self._on_open_guide)
        group_layout.addWidget(guide_btn)

        layout.addWidget(group)

    def _add_chatbox_section(self, layout: QVBoxLayout) -> None:
        """Add chatbox format settings."""
        group = QGroupBox(self._t("chatbox_format"))
        group_layout = QVBoxLayout(group)

        # Output format selection
        format_layout = QHBoxLayout()
        format_label = QLabel(self._t("output_format"))
        format_layout.addWidget(format_label)

        self._format_combo = QComboBox()
        self._format_combo.addItems([
            self._t("format_translation_only"),
            self._t("format_original_translation"),
            self._t("format_translation_original"),
            self._t("format_both_side"),
            self._t("format_custom_template"),
        ])
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        format_layout.addWidget(self._format_combo, 1)

        group_layout.addLayout(format_layout)

        # Custom template
        template_label = QLabel(self._t("custom_template"))
        group_layout.addWidget(template_label)

        self._template_edit = QTextEdit()
        self._template_edit.setPlaceholderText("{translation}\n{original}")
        self._template_edit.setMaximumHeight(80)
        self._template_edit.textChanged.connect(self._on_config_change)
        self._template_edit.setEnabled(False)
        group_layout.addWidget(self._template_edit)

        # Template help
        help_label = QLabel(self._t("chatbox_template_help"))
        help_label.setStyleSheet("font-size: 11px; color: #888;")
        group_layout.addWidget(help_label)

        layout.addWidget(group)

    def _add_output_section(self, layout: QVBoxLayout) -> None:
        """Add translation output settings."""
        group = QGroupBox(self._t("translation_output"))
        group_layout = QVBoxLayout(group)

        # Target language
        target_layout = QHBoxLayout()
        target_label = QLabel(self._t("target_language"))
        target_layout.addWidget(target_label)

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
        self._target_combo.currentIndexChanged.connect(self._on_config_change)
        target_layout.addWidget(self._target_combo, 1)

        group_layout.addLayout(target_layout)

        # Secondary target
        target2_layout = QHBoxLayout()
        target2_label = QLabel(self._t("secondary_target"))
        target2_layout.addWidget(target2_label)

        self._target2_combo = QComboBox()
        self._target2_combo.addItems([
            self._t("none_option"),
            self._t("lang_english"),
            self._t("lang_chinese_simplified"),
            self._t("lang_japanese"),
            self._t("lang_korean"),
        ])
        self._target2_combo.currentIndexChanged.connect(self._on_config_change)
        target2_layout.addWidget(self._target2_combo, 1)

        group_layout.addLayout(target2_layout)

        layout.addWidget(group)

    def _add_listen_section(self, layout: QVBoxLayout) -> None:
        """Add VRC Listen (desktop audio) settings."""
        group = QGroupBox(self._t("vrc_listen"))
        group_layout = QVBoxLayout(group)

        # Enable listen
        self._listen_enable_check = QCheckBox(
            self._t("enable_listen")
        )
        self._listen_enable_check.setChecked(False)
        self._listen_enable_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._listen_enable_check)

        # Listen target language
        listen_target_layout = QHBoxLayout()
        listen_label = QLabel(self._t("listen_target"))
        listen_target_layout.addWidget(listen_label)

        self._listen_target_combo = QComboBox()
        self._listen_target_combo.addItems([
            self._t("lang_english"),
            self._t("lang_chinese_simplified"),
            self._t("lang_japanese"),
            self._t("lang_korean"),
        ])
        self._listen_target_combo.currentIndexChanged.connect(self._on_config_change)
        listen_target_layout.addWidget(self._listen_target_combo, 1)

        group_layout.addLayout(listen_target_layout)

        # Show overlay
        self._overlay_check = QCheckBox(self._t("show_overlay"))
        self._overlay_check.setChecked(True)
        self._overlay_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._overlay_check)

        layout.addWidget(group)

    def _add_testing_section(self, layout: QVBoxLayout) -> None:
        """Add testing tools."""
        group = QGroupBox(self._t("testing"))
        group_layout = QVBoxLayout(group)

        # Test chatbox button
        test_btn = QPushButton("🧪 " + self._t("test_chatbox"))
        test_btn.clicked.connect(self._on_test_chatbox)
        group_layout.addWidget(test_btn)

        # Test OSC connection
        test_osc_btn = QPushButton("🔌 " + self._t("test_osc"))
        test_osc_btn.clicked.connect(self._on_test_osc)
        group_layout.addWidget(test_osc_btn)

        layout.addWidget(group)

    def _on_format_changed(self, index: int) -> None:
        """Handle format selection change."""
        # Enable custom template edit only when "Custom Template" is selected
        self._template_edit.setEnabled(index == 4)
        self.config_changed.emit()

    def _on_config_change(self) -> None:
        """Emit config changed signal."""
        self.config_changed.emit()

    def _on_open_guide(self) -> None:
        """Open OSC setup guide."""
        logger.info("Opening OSC guide...")
        # TODO: Implement guide

    def _on_test_chatbox(self) -> None:
        """Test chatbox output."""
        logger.info("Testing chatbox...")
        # TODO: Implement test

    def _on_test_osc(self) -> None:
        """Test OSC connection."""
        logger.info("Testing OSC connection...")
        # TODO: Implement test

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        format_map = {
            0: "translated",
            1: "original_with_translated",
            2: "translated_with_original",
            3: "both",
            4: "custom",
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
            "vrc": {
                "osc_enabled": self._osc_enable_check.isChecked(),
                "output_format": format_map.get(self._format_combo.currentIndex(), "translated"),
                "custom_template": self._template_edit.toPlainText().strip(),
            },
            "translation": {
                "target_language": target_map.get(self._target_combo.currentIndex(), "ja"),
                "target_language_2": target_map.get(self._target2_combo.currentIndex(), "en") if self._target2_combo.currentIndex() > 0 else "",
            },
            "vrc_listen": {
                "enabled": self._listen_enable_check.isChecked(),
                "target_language": target_map.get(self._listen_target_combo.currentIndex(), "zh-CN"),
                "show_overlay": self._overlay_check.isChecked(),
            },
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        vrc_cfg = config.get("vrc", {})
        trans_cfg = config.get("translation", {})
        listen_cfg = config.get("vrc_listen", {})

        # Load OSC settings
        self._osc_enable_check.setChecked(bool(vrc_cfg.get("osc_enabled", True)))

        # Load format
        format_reverse = {
            "translated": 0,
            "original_with_translated": 1,
            "translated_with_original": 2,
            "both": 3,
            "custom": 4,
        }
        output_format = vrc_cfg.get("output_format", "translated")
        self._format_combo.setCurrentIndex(format_reverse.get(output_format, 0))

        # Load template
        template = vrc_cfg.get("custom_template", "")
        if template:
            self._template_edit.setPlainText(template)

        # Load target languages
        target_reverse = {
            "en": 0,
            "zh-CN": 1,
            "ja": 2,
            "ko": 3,
            "es": 4,
            "fr": 5,
            "de": 6,
            "ru": 7,
        }

        target = trans_cfg.get("target_language", "ja")
        self._target_combo.setCurrentIndex(target_reverse.get(target, 2))

        target2 = trans_cfg.get("target_language_2", "")
        if target2:
            self._target2_combo.setCurrentIndex(target_reverse.get(target2, 0) + 1)
        else:
            self._target2_combo.setCurrentIndex(0)

        # Load listen settings
        self._listen_enable_check.setChecked(bool(listen_cfg.get("enabled", False)))

        listen_target = listen_cfg.get("target_language", "zh-CN")
        self._listen_target_combo.setCurrentIndex(target_reverse.get(listen_target, 1))

        self._overlay_check.setChecked(bool(listen_cfg.get("show_overlay", True)))
