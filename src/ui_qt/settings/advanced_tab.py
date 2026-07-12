# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Advanced Settings Tab."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
    QSpinBox,
)

from src.utils.logger import logs_dir

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


class AdvancedTab(LocalizedSettingsTab):
    """Advanced configuration tab."""

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
        """Initialize the Advanced UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("advanced_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Performance Section
        self._add_performance_section(layout)

        # Device Selection Section
        self._add_device_section(layout)

        # Cache Section
        self._add_cache_section(layout)

        # Update Section
        self._add_update_section(layout)

        # Logging Section
        self._add_logging_section(layout)

        # Experimental Section
        self._add_experimental_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = self._root_layout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_performance_section(self, layout: QVBoxLayout) -> None:
        """Add performance settings."""
        group = QGroupBox(self._t("performance"))
        group_layout = QVBoxLayout(group)

        # Performance profile
        profile_layout = QHBoxLayout()
        profile_label = QLabel(self._t("performance_profile"))
        profile_layout.addWidget(profile_label)

        self._profile_combo = QComboBox()
        self._profile_combo.addItems([
            self._t("performance_balanced"),
            self._t("performance_high"),
            self._t("performance_power_saving"),
        ])
        self._profile_combo.currentIndexChanged.connect(self._on_config_change)
        profile_layout.addWidget(self._profile_combo, 1)

        group_layout.addLayout(profile_layout)

        # Thread pool size
        thread_layout = QHBoxLayout()
        thread_label = QLabel(self._t("thread_pool"))
        thread_layout.addWidget(thread_label)

        self._thread_spin = QSpinBox()
        self._thread_spin.setMinimum(1)
        self._thread_spin.setMaximum(16)
        self._thread_spin.setValue(4)
        self._thread_spin.valueChanged.connect(self._on_config_change)
        thread_layout.addWidget(self._thread_spin)

        group_layout.addLayout(thread_layout)

        layout.addWidget(group)

    def _add_device_section(self, layout: QVBoxLayout) -> None:
        """Add device selection."""
        group = QGroupBox(self._t("device_selection"))
        group_layout = QVBoxLayout(group)

        # ASR device
        asr_layout = QHBoxLayout()
        asr_label = QLabel(self._t("asr_device"))
        asr_layout.addWidget(asr_label)

        self._asr_device_combo = QComboBox()
        self._asr_device_combo.addItems([
            self._t("device_cpu"),
            self._t("device_cuda"),
            self._t("device_auto"),
        ])
        self._asr_device_combo.currentIndexChanged.connect(self._on_config_change)
        asr_layout.addWidget(self._asr_device_combo, 1)

        group_layout.addLayout(asr_layout)

        # TTS device
        tts_layout = QHBoxLayout()
        tts_label = QLabel(self._t("tts_device"))
        tts_layout.addWidget(tts_label)

        self._tts_device_combo = QComboBox()
        self._tts_device_combo.addItems([
            self._t("device_cpu"),
            self._t("device_cuda"),
            self._t("device_auto"),
        ])
        self._tts_device_combo.currentIndexChanged.connect(self._on_config_change)
        tts_layout.addWidget(self._tts_device_combo, 1)

        group_layout.addLayout(tts_layout)

        # CUDA info
        cuda_btn = QPushButton("🔧 " + self._t("install_cuda"))
        cuda_btn.clicked.connect(self._on_install_cuda)
        group_layout.addWidget(cuda_btn)

        layout.addWidget(group)

    def _add_cache_section(self, layout: QVBoxLayout) -> None:
        """Add cache settings."""
        group = QGroupBox(self._t("cache_settings"))
        group_layout = QVBoxLayout(group)

        # TTS cache size
        cache_layout = QHBoxLayout()
        cache_label = QLabel(self._t("tts_cache_size"))
        cache_layout.addWidget(cache_label)

        self._cache_spin = QSpinBox()
        self._cache_spin.setMinimum(10)
        self._cache_spin.setMaximum(1000)
        self._cache_spin.setValue(100)
        self._cache_spin.setSuffix(self._t("settings_megabytes_suffix"))
        self._cache_spin.valueChanged.connect(self._on_config_change)
        cache_layout.addWidget(self._cache_spin)

        group_layout.addLayout(cache_layout)

        # Clear cache button
        clear_btn = QPushButton("🗑️ " + self._t("clear_cache"))
        clear_btn.clicked.connect(self._on_clear_cache)
        group_layout.addWidget(clear_btn)

        layout.addWidget(group)

    def _add_update_section(self, layout: QVBoxLayout) -> None:
        """Add update settings."""
        group = QGroupBox(self._t("updates"))
        group_layout = QVBoxLayout(group)

        # Auto-check updates
        self._auto_update_check = QCheckBox(
            self._t("check_updates_on_start")
        )
        self._auto_update_check.setChecked(True)
        self._auto_update_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._auto_update_check)

        # Check now button
        check_btn = QPushButton("🔄 " + self._t("check_now"))
        check_btn.clicked.connect(self._on_check_updates)
        group_layout.addWidget(check_btn)

        layout.addWidget(group)

    def _add_logging_section(self, layout: QVBoxLayout) -> None:
        """Add logging/debug settings."""
        group = QGroupBox(self._t("logging"))
        group_layout = QVBoxLayout(group)

        # Log level
        level_layout = QHBoxLayout()
        level_label = QLabel(self._t("log_level"))
        level_layout.addWidget(level_label)

        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems([
            self._t("log_error"),
            self._t("log_warning"),
            self._t("log_info"),
            self._t("log_debug"),
        ])
        self._log_level_combo.setCurrentIndex(2)
        self._log_level_combo.currentIndexChanged.connect(self._on_config_change)
        level_layout.addWidget(self._log_level_combo, 1)

        group_layout.addLayout(level_layout)

        log_path = QLabel(str(logs_dir() / "mio.log"))
        log_path.setWordWrap(True)
        log_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        group_layout.addWidget(log_path)

        # Open logs button
        logs_btn = QPushButton("📄 " + self._t("open_logs"))
        logs_btn.clicked.connect(self._on_open_logs)
        group_layout.addWidget(logs_btn)

        layout.addWidget(group)

    def _add_experimental_section(self, layout: QVBoxLayout) -> None:
        """Add experimental features."""
        group = QGroupBox("⚠️ " + self._t("experimental"))
        group_layout = QVBoxLayout(group)

        # Beta features
        self._beta_check = QCheckBox(
            self._t("enable_beta")
        )
        self._beta_check.setChecked(False)
        self._beta_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._beta_check)

        # Aggressive chunking
        self._chunking_check = QCheckBox(
            self._t("aggressive_chunking")
        )
        self._chunking_check.setChecked(False)
        self._chunking_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._chunking_check)

        layout.addWidget(group)

    def _on_config_change(self) -> None:
        """Emit config changed signal."""
        self.config_changed.emit()

    def _on_install_cuda(self) -> None:
        """Open CUDA installation dialog."""
        logger.info("Opening CUDA installation...")
        # TODO: Implement CUDA install

    def _on_clear_cache(self) -> None:
        """Clear TTS cache."""
        logger.info("Clearing cache...")
        # TODO: Implement cache clear

    def _on_check_updates(self) -> None:
        """Check for updates."""
        logger.info("Checking for updates...")
        # TODO: Implement update check

    def _on_open_logs(self) -> None:
        """Open log folder."""
        path = logs_dir()
        logger.info("Opening log folder: %s", path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        profile_map = {0: "balanced", 1: "high_performance", 2: "power_saving"}
        device_map = {0: "cpu", 1: "cuda", 2: "auto"}
        log_level_map = {0: "ERROR", 1: "WARNING", 2: "INFO", 3: "DEBUG"}

        config = {
            "performance": {
                "profile": profile_map.get(self._profile_combo.currentIndex(), "balanced"),
                "thread_pool_size": self._thread_spin.value(),
            },
            "asr": {
                "device": device_map.get(self._asr_device_combo.currentIndex(), "cpu"),
            },
            "tts": {
                "device": device_map.get(self._tts_device_combo.currentIndex(), "cpu"),
                "cache_size_mb": self._cache_spin.value(),
            },
            "updates": {
                "check_on_start": self._auto_update_check.isChecked(),
            },
            "logging": {
                "level": log_level_map.get(self._log_level_combo.currentIndex(), "INFO"),
            },
            "experimental": {
                "beta_features": self._beta_check.isChecked(),
                "aggressive_chunking": self._chunking_check.isChecked(),
            },
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        perf_cfg = config.get("performance", {})
        asr_cfg = config.get("asr", {})
        tts_cfg = config.get("tts", {})
        update_cfg = config.get("updates", {})
        log_cfg = config.get("logging", {})
        exp_cfg = config.get("experimental", {})

        # Load performance
        profile = perf_cfg.get("profile", "balanced")
        profile_map_reverse = {"balanced": 0, "high_performance": 1, "power_saving": 2}
        self._profile_combo.setCurrentIndex(profile_map_reverse.get(profile, 0))

        self._thread_spin.setValue(int(perf_cfg.get("thread_pool_size", 4)))

        # Load devices
        device_map_reverse = {"cpu": 0, "cuda": 1, "auto": 2}

        asr_device = asr_cfg.get("device", "cpu")
        self._asr_device_combo.setCurrentIndex(device_map_reverse.get(asr_device, 0))

        tts_device = tts_cfg.get("device", "cpu")
        self._tts_device_combo.setCurrentIndex(device_map_reverse.get(tts_device, 0))

        # Load cache
        cache_size = int(tts_cfg.get("cache_size_mb", 100))
        self._cache_spin.setValue(cache_size)

        # Load updates
        self._auto_update_check.setChecked(bool(update_cfg.get("check_on_start", True)))

        # Load logging
        log_level = log_cfg.get("level", "INFO")
        log_level_map_reverse = {"ERROR": 0, "WARNING": 1, "INFO": 2, "DEBUG": 3}
        self._log_level_combo.setCurrentIndex(log_level_map_reverse.get(log_level, 2))

        # Load experimental
        self._beta_check.setChecked(bool(exp_cfg.get("beta_features", False)))
        self._chunking_check.setChecked(bool(exp_cfg.get("aggressive_chunking", False)))
