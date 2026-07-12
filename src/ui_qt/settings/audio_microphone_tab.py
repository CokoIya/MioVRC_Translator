# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Audio & Microphone Settings Tab."""

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
    QSlider,
    QGroupBox,
    QScrollArea,
    QDoubleSpinBox,
)

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


class AudioMicrophoneTab(LocalizedSettingsTab):
    """Audio & Microphone configuration tab."""

    config_changed = Signal()

    def __init__(
        self,
        config: dict,
        ui_language: str,
        on_test_microphone: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        on_audio_diagnostics_requested: Callable[[str], None] | None = None,
        on_vad_calibration_requested: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = normalize_settings_language(ui_language)
        self._on_test_microphone = on_test_microphone
        self._on_audio_diagnostics_requested = on_audio_diagnostics_requested
        self._on_vad_calibration_requested = on_vad_calibration_requested

        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the Audio & Microphone UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("audio_mic_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Microphone Section
        self._add_microphone_section(layout)

        # VAD Settings Section
        self._add_vad_section(layout)

        # Audio Processing Section
        self._add_processing_section(layout)

        # Diagnostics Section
        self._add_diagnostics_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = self._root_layout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_microphone_section(self, layout: QVBoxLayout) -> None:
        """Add microphone selection."""
        group = QGroupBox(self._t("microphone"))
        group_layout = QVBoxLayout(group)

        # Device selection
        device_layout = QHBoxLayout()
        device_label = QLabel(self._t("input_device"))
        device_layout.addWidget(device_label)

        self._device_combo = QComboBox()
        self._device_combo.addItem(self._t("device_default_microphone"), "auto")
        self._device_combo.addItem(self._t("device_system_default"), "system")
        self._device_combo.addItem(self._t("device_custom"), "fixed")
        self._device_combo.currentIndexChanged.connect(self._on_config_change)
        device_layout.addWidget(self._device_combo, 1)

        group_layout.addLayout(device_layout)

        # Test button
        test_layout = QHBoxLayout()
        test_label = QLabel(self._t("microphone_test_info"))
        test_label.setWordWrap(True)
        test_layout.addWidget(test_label, 1)

        test_btn = QPushButton("🎤 " + self._t("test_microphone"))
        test_btn.setMinimumWidth(150)
        if self._on_test_microphone:
            test_btn.clicked.connect(self._on_test_microphone)
        test_layout.addWidget(test_btn)

        group_layout.addLayout(test_layout)

        layout.addWidget(group)

    def _add_vad_section(self, layout: QVBoxLayout) -> None:
        """Add VAD (Voice Activity Detection) settings."""
        group = QGroupBox(self._t("vad_settings"))
        group_layout = QVBoxLayout(group)

        # Sensitivity
        sens_layout = QVBoxLayout()
        sens_label = QLabel(self._t("vad_sensitivity"))
        sens_layout.addWidget(sens_label)

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel(self._t("sensitivity_low")))

        self._vad_sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self._vad_sensitivity_slider.setMinimum(0)
        self._vad_sensitivity_slider.setMaximum(3)
        self._vad_sensitivity_slider.setValue(2)
        self._vad_sensitivity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._vad_sensitivity_slider.setTickInterval(1)
        self._vad_sensitivity_slider.valueChanged.connect(self._on_config_change)
        slider_layout.addWidget(self._vad_sensitivity_slider, 1)

        slider_layout.addWidget(QLabel(self._t("sensitivity_high")))
        sens_layout.addLayout(slider_layout)

        group_layout.addLayout(sens_layout)

        # Speech threshold
        threshold_layout = QHBoxLayout()
        threshold_label = QLabel(self._t("speech_threshold"))
        threshold_layout.addWidget(threshold_label)

        self._speech_threshold_spin = QDoubleSpinBox()
        self._speech_threshold_spin.setMinimum(0.0)
        self._speech_threshold_spin.setMaximum(1.0)
        self._speech_threshold_spin.setSingleStep(0.05)
        self._speech_threshold_spin.setValue(0.5)
        self._speech_threshold_spin.setDecimals(2)
        self._speech_threshold_spin.valueChanged.connect(self._on_config_change)
        threshold_layout.addWidget(self._speech_threshold_spin)

        group_layout.addLayout(threshold_layout)

        # Silence duration
        silence_layout = QHBoxLayout()
        silence_label = QLabel(self._t("silence_duration"))
        silence_layout.addWidget(silence_label)

        self._silence_spin = QDoubleSpinBox()
        self._silence_spin.setMinimum(0.1)
        self._silence_spin.setMaximum(5.0)
        self._silence_spin.setSingleStep(0.1)
        self._silence_spin.setValue(0.65)
        self._silence_spin.setDecimals(2)
        self._silence_spin.valueChanged.connect(self._on_config_change)
        silence_layout.addWidget(self._silence_spin)

        group_layout.addLayout(silence_layout)

        # Min RMS
        rms_layout = QHBoxLayout()
        rms_label = QLabel(self._t("min_rms"))
        rms_layout.addWidget(rms_label)

        self._min_rms_spin = QDoubleSpinBox()
        self._min_rms_spin.setMinimum(0.0)
        self._min_rms_spin.setMaximum(1.0)
        self._min_rms_spin.setSingleStep(0.001)
        self._min_rms_spin.setValue(0.012)
        self._min_rms_spin.setDecimals(3)
        self._min_rms_spin.valueChanged.connect(self._on_config_change)
        rms_layout.addWidget(self._min_rms_spin)

        group_layout.addLayout(rms_layout)

        layout.addWidget(group)

    def _add_processing_section(self, layout: QVBoxLayout) -> None:
        """Add audio processing settings."""
        group = QGroupBox(self._t("audio_processing"))
        group_layout = QVBoxLayout(group)

        # Noise reduction
        noise_layout = QHBoxLayout()
        noise_label = QLabel(self._t("noise_reduction"))
        noise_layout.addWidget(noise_label)

        self._noise_combo = QComboBox()
        self._noise_combo.addItems([
            self._t("noise_off"),
            self._t("noise_light"),
            self._t("noise_medium"),
            self._t("noise_strong"),
        ])
        self._noise_combo.setCurrentIndex(1)  # Default: Light
        self._noise_combo.currentIndexChanged.connect(self._on_config_change)
        noise_layout.addWidget(self._noise_combo, 1)

        group_layout.addLayout(noise_layout)

        # Sample rate
        rate_layout = QHBoxLayout()
        rate_label = QLabel(self._t("sample_rate"))
        rate_layout.addWidget(rate_label)

        self._sample_rate_combo = QComboBox()
        self._sample_rate_combo.addItems(["16000 Hz", "24000 Hz", "48000 Hz"])
        self._sample_rate_combo.setCurrentIndex(0)
        self._sample_rate_combo.currentIndexChanged.connect(self._on_config_change)
        rate_layout.addWidget(self._sample_rate_combo, 1)

        group_layout.addLayout(rate_layout)

        layout.addWidget(group)

    def _add_diagnostics_section(self, layout: QVBoxLayout) -> None:
        """Add diagnostics tools."""
        group = QGroupBox(self._t("diagnostics"))
        group_layout = QVBoxLayout(group)

        # Calibration button
        calibrate_btn = QPushButton("🔧 " + self._t("calibrate_vad"))
        calibrate_btn.clicked.connect(self._on_calibrate)
        group_layout.addWidget(calibrate_btn)

        # Audio diagnostics button
        diag_btn = QPushButton("📊 " + self._t("audio_diagnostics"))
        diag_btn.clicked.connect(self._on_diagnostics)
        group_layout.addWidget(diag_btn)

        layout.addWidget(group)

    def _on_config_change(self) -> None:
        """Emit config changed signal."""
        self.config_changed.emit()

    def _on_calibrate(self) -> None:
        """Open VAD calibration window."""
        if callable(self._on_vad_calibration_requested):
            self._on_vad_calibration_requested("mic")
            return
        logger.info("Opening VAD calibration...")

    def _on_diagnostics(self) -> None:
        """Open audio diagnostics window."""
        if callable(self._on_audio_diagnostics_requested):
            self._on_audio_diagnostics_requested("mic")
            return
        logger.info("Opening audio diagnostics...")

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        noise_map = {0: 0.0, 1: 0.35, 2: 0.6, 3: 0.85}
        sample_map = {0: 16000, 1: 24000, 2: 48000}

        selection = str(self._device_combo.currentData() or "auto")
        existing_audio = self._config.get("audio", {})
        input_mode = "fixed" if selection == "fixed" else "auto"
        input_device = str(existing_audio.get("input_device", "") or "") if input_mode == "fixed" else ""
        config = {
            "audio": {
                "input_device_mode": input_mode,
                "input_device": input_device,
                "vad_sensitivity": self._vad_sensitivity_slider.value(),
                "vad_speech_ratio": self._speech_threshold_spin.value(),
                "vad_silence_threshold": self._silence_spin.value(),
                "vad_min_rms": self._min_rms_spin.value(),
                "denoise_strength": noise_map.get(self._noise_combo.currentIndex(), 0.35),
                "sample_rate": sample_map.get(self._sample_rate_combo.currentIndex(), 16000),
            }
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        audio_cfg = config.get("audio", {})

        # Load device
        mode = str(audio_cfg.get("input_device_mode", "") or "").strip().lower()
        if not mode:
            mode = "fixed" if audio_cfg.get("input_device") else "auto"
        index = self._device_combo.findData("fixed" if mode == "fixed" else "auto")
        self._device_combo.setCurrentIndex(max(0, index))

        # Load VAD settings
        self._vad_sensitivity_slider.setValue(int(audio_cfg.get("vad_sensitivity", 2)))
        self._speech_threshold_spin.setValue(float(audio_cfg.get("vad_speech_ratio", 0.5)))
        self._silence_spin.setValue(float(audio_cfg.get("vad_silence_threshold", 0.65)))
        self._min_rms_spin.setValue(float(audio_cfg.get("vad_min_rms", 0.012)))

        # Load denoise
        denoise = float(audio_cfg.get("denoise_strength", 0.35))
        if denoise == 0.0:
            self._noise_combo.setCurrentIndex(0)
        elif denoise <= 0.35:
            self._noise_combo.setCurrentIndex(1)
        elif denoise <= 0.6:
            self._noise_combo.setCurrentIndex(2)
        else:
            self._noise_combo.setCurrentIndex(3)

        # Load sample rate
        rate = int(audio_cfg.get("sample_rate", 16000))
        rate_map = {16000: 0, 24000: 1, 48000: 2}
        self._sample_rate_combo.setCurrentIndex(rate_map.get(rate, 0))

    def _capture_localization_state(self) -> object:
        return self._device_combo.currentIndex()

    def _restore_localization_state(self, state: object) -> None:
        if isinstance(state, int) and 0 <= state < self._device_combo.count():
            self._device_combo.setCurrentIndex(state)
