# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""TTS & Voice Settings Tab."""

from __future__ import annotations

import logging
from pathlib import Path
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
    QCheckBox,
)

from src.utils.i18n import tr as _base_tr
from src.tts.xtts_engine import (
    XTTS_REFERENCE_AUDIO_NAME_FILTER,
    normalize_xtts_reference_audio_file,
    safe_xtts_voice_name,
    xtts_reference_audio_dir,
)


def tr(language: str | None, key: str, **kwargs) -> str:
    text = _base_tr(language, key, **kwargs)
    return "" if text == key else text

logger = logging.getLogger(__name__)


class TTSVoiceTab(QWidget):
    """TTS & Voice configuration tab."""

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
        """Initialize the TTS & Voice UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("tts_voice_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # TTS Engine Section
        self._add_engine_section(layout)

        # Voice Selection Section
        self._add_voice_section(layout)

        # Voice Parameters Section
        self._add_parameters_section(layout)

        # Output Device Section
        self._add_output_section(layout)

        # Voice Management Section
        self._add_management_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_engine_section(self, layout: QVBoxLayout) -> None:
        """Add TTS engine selection."""
        group = QGroupBox(self._t("tts_engine"))
        group_layout = QVBoxLayout(group)

        # Engine selection
        engine_layout = QHBoxLayout()
        engine_label = QLabel(self._t("select_engine"))
        engine_layout.addWidget(engine_label)

        self._engine_combo = QComboBox()
        self._engine_combo.addItems([
            self._t("engine_edge_label"),
            self._t("engine_gtts_label"),
            self._t("engine_xtts_label"),
            self._t("engine_style_bert_label"),
            self._t("engine_voicevox_label"),
            self._t("engine_pyttsx3_label"),
        ])
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_layout.addWidget(self._engine_combo, 1)

        group_layout.addLayout(engine_layout)

        # Engine info
        self._engine_info = QLabel(self._t("engine_edge_info"))
        self._engine_info.setStyleSheet("color: #4CAF50; font-size: 12px;")
        group_layout.addWidget(self._engine_info)

        layout.addWidget(group)

    def _add_voice_section(self, layout: QVBoxLayout) -> None:
        """Add voice selection."""
        group = QGroupBox(self._t("voice_selection"))
        group_layout = QVBoxLayout(group)

        # Voice combo
        voice_layout = QHBoxLayout()
        voice_label = QLabel(self._t("voice"))
        voice_layout.addWidget(voice_label)

        self._voice_combo = QComboBox()
        self._voice_combo.addItems([
            self._t("voice_xiaoxiao"),
            self._t("voice_xiaoyi"),
            self._t("voice_yunyang"),
            self._t("voice_nanami"),
            self._t("voice_aoi"),
            self._t("voice_jenny"),
            self._t("voice_aria"),
        ])
        self._voice_combo.currentIndexChanged.connect(self._on_config_change)
        voice_layout.addWidget(self._voice_combo, 1)

        group_layout.addLayout(voice_layout)

        # Preview button
        preview_btn = QPushButton("▶️ " + self._t("preview_voice"))
        preview_btn.clicked.connect(self._on_preview)
        group_layout.addWidget(preview_btn)

        layout.addWidget(group)

    def _add_parameters_section(self, layout: QVBoxLayout) -> None:
        """Add voice parameters."""
        group = QGroupBox(self._t("voice_parameters"))
        group_layout = QVBoxLayout(group)

        # Speed slider
        speed_layout = QVBoxLayout()
        speed_label = QLabel(self._t("speed"))
        speed_layout.addWidget(speed_label)

        speed_slider_layout = QHBoxLayout()
        speed_slider_layout.addWidget(QLabel("0.5x"))

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setMinimum(50)
        self._speed_slider.setMaximum(200)
        self._speed_slider.setValue(100)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.setTickInterval(25)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_slider_layout.addWidget(self._speed_slider, 1)

        speed_slider_layout.addWidget(QLabel("2.0x"))

        self._speed_value_label = QLabel("1.0x")
        self._speed_value_label.setStyleSheet("font-weight: bold;")
        speed_slider_layout.addWidget(self._speed_value_label)

        speed_layout.addLayout(speed_slider_layout)
        group_layout.addLayout(speed_layout)

        # Volume slider
        volume_layout = QVBoxLayout()
        volume_label = QLabel(self._t("volume"))
        volume_layout.addWidget(volume_label)

        volume_slider_layout = QHBoxLayout()
        volume_slider_layout.addWidget(QLabel("0%"))

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setMinimum(0)
        self._volume_slider.setMaximum(100)
        self._volume_slider.setValue(100)
        self._volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._volume_slider.setTickInterval(10)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_slider_layout.addWidget(self._volume_slider, 1)

        volume_slider_layout.addWidget(QLabel("100%"))

        self._volume_value_label = QLabel("100%")
        self._volume_value_label.setStyleSheet("font-weight: bold;")
        volume_slider_layout.addWidget(self._volume_value_label)

        volume_layout.addLayout(volume_slider_layout)
        group_layout.addLayout(volume_layout)

        # Pitch (for supported engines)
        pitch_layout = QHBoxLayout()
        pitch_label = QLabel(self._t("pitch"))
        pitch_layout.addWidget(pitch_label)

        self._pitch_spin = QDoubleSpinBox()
        self._pitch_spin.setMinimum(-12.0)
        self._pitch_spin.setMaximum(12.0)
        self._pitch_spin.setSingleStep(0.5)
        self._pitch_spin.setValue(0.0)
        self._pitch_spin.setDecimals(1)
        self._pitch_spin.setSuffix(self._t("pitch_suffix"))
        self._pitch_spin.valueChanged.connect(self._on_config_change)
        pitch_layout.addWidget(self._pitch_spin, 1)

        group_layout.addLayout(pitch_layout)

        layout.addWidget(group)

    def _add_output_section(self, layout: QVBoxLayout) -> None:
        """Add output device selection."""
        group = QGroupBox(self._t("output_device"))
        group_layout = QVBoxLayout(group)

        # Device selection
        device_layout = QHBoxLayout()
        device_label = QLabel(self._t("playback_device"))
        device_layout.addWidget(device_label)

        self._output_combo = QComboBox()
        self._output_combo.addItems([
            self._t("default_speaker"),
            self._t("virtual_cable"),
            self._t("device_system_default"),
            self._t("device_custom"),
        ])
        self._output_combo.currentIndexChanged.connect(self._on_config_change)
        device_layout.addWidget(self._output_combo, 1)

        group_layout.addLayout(device_layout)

        # Monitor output checkbox
        self._monitor_check = QCheckBox(self._t("monitor_output"))
        self._monitor_check.setChecked(False)
        self._monitor_check.stateChanged.connect(self._on_config_change)
        group_layout.addWidget(self._monitor_check)

        layout.addWidget(group)

    def _add_management_section(self, layout: QVBoxLayout) -> None:
        """Add voice model management."""
        group = QGroupBox(self._t("voice_management"))
        group_layout = QVBoxLayout(group)

        # XTTS-v2 Voice Recording Section
        xtts_info = QLabel("🎤 " + self._t("xtts_management_hint"))
        xtts_info.setStyleSheet("color: #4CAF50; font-size: 12px; padding: 5px;")
        group_layout.addWidget(xtts_info)

        xtts_btn_layout = QHBoxLayout()

        record_btn = QPushButton("🎤 " + self._t("record_voice"))
        record_btn.clicked.connect(self._on_record_voice)
        xtts_btn_layout.addWidget(record_btn)

        import_voice_btn = QPushButton("📂 " + self._t("import_voice"))
        import_voice_btn.clicked.connect(self._on_import_voice)
        xtts_btn_layout.addWidget(import_voice_btn)

        group_layout.addLayout(xtts_btn_layout)

        # Divider
        divider = QLabel("─" * 40)
        divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
        divider.setStyleSheet("color: #ccc; padding: 5px;")
        group_layout.addWidget(divider)

        # Download models button (for other engines)
        download_btn = QPushButton("📥 " + self._t("download_models"))
        download_btn.clicked.connect(self._on_download_models)
        group_layout.addWidget(download_btn)

        # Manage voices button
        manage_btn = QPushButton("⚙️ " + self._t("manage_voices"))
        manage_btn.clicked.connect(self._on_manage_voices)
        group_layout.addWidget(manage_btn)

        layout.addWidget(group)

    def _on_engine_changed(self, index: int) -> None:
        """Handle engine selection change."""
        engine_info = {
            0: self._t("engine_edge_info"),
            1: self._t("engine_gtts_info"),
            2: self._t("engine_xtts_info"),
            3: self._t("engine_style_bert_info"),
            4: self._t("engine_voicevox_info"),
            5: self._t("engine_pyttsx3_info"),
        }
        self._engine_info.setText(engine_info.get(index, ""))
        self.config_changed.emit()

    def _on_speed_changed(self, value: int) -> None:
        """Handle speed slider change."""
        speed = value / 100.0
        self._speed_value_label.setText(f"{speed:.1f}x")
        self.config_changed.emit()

    def _on_volume_changed(self, value: int) -> None:
        """Handle volume slider change."""
        self._volume_value_label.setText(f"{value}%")
        self.config_changed.emit()

    def _on_config_change(self) -> None:
        """Emit config changed signal."""
        self.config_changed.emit()

    def _on_preview(self) -> None:
        """Preview selected voice."""
        logger.info("Previewing voice...")
        # TODO: Implement preview
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            self._t("preview_title"),
            self._t("preview_not_ready"),
        )

    def _on_record_voice(self) -> None:
        """Open voice recording dialog for XTTS-v2."""
        logger.info("Opening voice recording dialog...")
        try:
            from ..voice_recording_dialog import VoiceRecordingDialog

            dialog = VoiceRecordingDialog(self, ui_lang=self._ui_language)
            dialog.voice_recorded.connect(self._on_voice_recorded)
            dialog.exec()
        except Exception as e:
            logger.error("Failed to open recording dialog: %s", e)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                self._t("error"),
                self._t("recording_dialog_open_failed", error=e),
            )

    def _on_voice_recorded(self, audio_data: bytes, voice_name: str) -> None:
        """Handle recorded voice data."""
        logger.info("Voice recorded: %s (%d bytes)", voice_name, len(audio_data))

        try:
            # Save to reference audio directory for XTTS
            ref_audio_dir = xtts_reference_audio_dir()
            ref_audio_dir.mkdir(parents=True, exist_ok=True)

            output_path = ref_audio_dir / f"{safe_xtts_voice_name(voice_name)}.wav"
            tmp_path = output_path.with_suffix(".tmp.wav")
            with open(tmp_path, 'wb') as f:
                f.write(audio_data)
            try:
                normalize_xtts_reference_audio_file(tmp_path, output_path)
            finally:
                tmp_path.unlink(missing_ok=True)

            logger.info("Saved reference audio: %s", output_path)

            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                self._t("voice_saved_title"),
                self._t("voice_saved_message", voice=voice_name),
            )

        except Exception as e:
            logger.error("Failed to save voice: %s", e)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                self._t("voice_save_error_title"),
                self._t("voice_save_failed", error=e),
            )

    def _on_download_models(self) -> None:
        """Open model download dialog."""
        logger.info("Opening model download...")
        # TODO: Implement download

    def _on_import_voice(self) -> None:
        """Import custom voice."""
        logger.info("Importing custom voice...")

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        file_dialog = QFileDialog(self)
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter(XTTS_REFERENCE_AUDIO_NAME_FILTER)

        if file_dialog.exec():
            files = file_dialog.selectedFiles()
            if files:
                audio_path = files[0]
                try:
                    # Get filename as voice name
                    voice_name = Path(audio_path).stem

                    # Convert to reference audio directory
                    ref_audio_dir = xtts_reference_audio_dir()
                    ref_audio_dir.mkdir(parents=True, exist_ok=True)

                    output_path = ref_audio_dir / f"{safe_xtts_voice_name(voice_name)}.wav"
                    normalize_xtts_reference_audio_file(audio_path, output_path)

                    logger.info("Imported voice: %s", output_path)

                    QMessageBox.information(
                        self,
                        self._t("voice_imported_title"),
                        self._t("voice_imported_message", voice=voice_name),
                    )

                except Exception as e:
                    logger.error("Failed to import voice: %s", e)
                    QMessageBox.critical(
                        self,
                        self._t("voice_import_error_title"),
                        self._t("voice_import_failed", error=e),
                    )

    def _on_manage_voices(self) -> None:
        """Manage installed voices."""
        logger.info("Managing voices...")
        # TODO: Implement management

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        engine_map = {
            0: "edge",
            1: "gtts",
            2: "xtts",
            3: "style_bert_vits2",
            4: "voicevox",
            5: "pyttsx3",
        }

        config = {
            "tts": {
                "engine": engine_map.get(self._engine_combo.currentIndex(), "edge"),
                "voice": self._voice_combo.currentText(),
                "speed": self._speed_slider.value() / 100.0,
                "volume": self._volume_slider.value() / 100.0,
                "pitch": self._pitch_spin.value(),
                "output_device": self._output_combo.currentText(),
                "monitor_output": self._monitor_check.isChecked(),
            }
        }

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        tts_cfg = config.get("tts", {})

        # Load engine
        engine = tts_cfg.get("engine", "edge")
        engine_map_reverse = {
            "edge": 0,
            "gtts": 1,
            "xtts": 2,
            "style_bert_vits2": 3,
            "voicevox": 4,
            "pyttsx3": 5,
        }
        self._engine_combo.setCurrentIndex(engine_map_reverse.get(engine, 0))

        # Load voice
        voice = tts_cfg.get("voice", "")
        if voice:
            index = self._voice_combo.findText(voice)
            if index >= 0:
                self._voice_combo.setCurrentIndex(index)

        # Load parameters
        speed = float(tts_cfg.get("speed", 1.0))
        self._speed_slider.setValue(int(speed * 100))

        volume = float(tts_cfg.get("volume", 1.0))
        self._volume_slider.setValue(int(volume * 100))

        pitch = float(tts_cfg.get("pitch", 0.0))
        self._pitch_spin.setValue(pitch)

        # Load output device
        output = tts_cfg.get("output_device", "")
        if output:
            index = self._output_combo.findText(output)
            if index >= 0:
                self._output_combo.setCurrentIndex(index)

        # Load monitor setting
        self._monitor_check.setChecked(bool(tts_cfg.get("monitor_output", False)))
