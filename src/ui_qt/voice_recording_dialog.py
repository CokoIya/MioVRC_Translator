"""Voice recording and import helper for XTTS-v2."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QLineEdit,
    QSizePolicy,
)

from src.tts.wav_utils import decode_wav_bytes, encode_pcm16_wav
from src.tts.xtts_engine import (
    XTTS_REFERENCE_MAX_CLIPPED_RATIO,
    XTTS_REFERENCE_MAX_DC_OFFSET,
    XTTS_REFERENCE_MIN_ACTIVE_SECONDS,
    XTTS_REFERENCE_MIN_DURATION_SECONDS,
    XTTS_REFERENCE_MIN_DYNAMIC_RANGE,
    XTTS_REFERENCE_MIN_PEAK,
    XTTS_REFERENCE_MIN_RMS,
    XTTSAudioDecodeError,
    XTTSAudioDecoderUnavailableError,
    analyze_xtts_reference_audio_bytes,
    normalize_xtts_reference_audio_file,
)
from src.ui_qt.qt_localization import configure_file_dialog
from src.utils.i18n import tr
from src.utils.localization import format_locale_number, format_locale_percent, normalize_ui_language

logger = logging.getLogger(__name__)


class VoiceRecordingDialog(QDialog):
    """Dialog for recording or importing voice samples for XTTS-v2."""

    voice_recorded = Signal(bytes, str)  # audio_data, voice_name

    def __init__(self, parent=None, input_device: int | None = None, ui_lang: str | None = None):
        super().__init__(parent)
        self._ui_lang = normalize_ui_language(
            ui_lang or str(getattr(parent, "_ui_lang", "") or "").strip() or "en",
            default="en",
        )
        self.setWindowTitle(self._t("voice_record_title"))
        self.setMinimumSize(500, 400)

        self._recording = False
        self._recorded_audio: Optional[bytes] = None
        self._audio_frames: list = []
        self._stream = None
        self._input_device = input_device
        self._record_sample_rate = 24000
        self._status_key = "voice_record_ready"
        self._status_kwargs: dict[str, object] = {}

        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # Title
        self._title_label = QLabel(self._t("voice_record_header"))
        self._title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setWordWrap(True)
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._title_label)

        # Instructions
        self._instructions_label = QLabel(self._t("voice_record_instructions"))
        self._instructions_label.setWordWrap(True)
        self._instructions_label.setStyleSheet("color: #666; padding: 10px; background: #f5f5f5; border-radius: 5px;")
        layout.addWidget(self._instructions_label)

        # Recording status
        self._status_label = QLabel(self._t("voice_record_ready"))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(self._status_label)

        # Progress bar for recording duration
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximum(10)  # 10 seconds
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat(self._t("voice_record_seconds_format"))
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Recording controls
        recording_layout = QHBoxLayout()

        self._record_btn = QPushButton("🎤 " + self._t("voice_record_start"))
        self._record_btn.setFixedHeight(50)
        self._record_btn.setStyleSheet(
            "QPushButton { font-size: 14px; background-color: #4CAF50; color: white; border-radius: 5px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        self._record_btn.clicked.connect(self._toggle_recording)
        recording_layout.addWidget(self._record_btn)

        self._stop_btn = QPushButton("⏹️ " + self._t("voice_record_stop"))
        self._stop_btn.setFixedHeight(50)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton { font-size: 14px; background-color: #f44336; color: white; border-radius: 5px; }"
            "QPushButton:hover { background-color: #da190b; }"
            "QPushButton:disabled { background-color: #cccccc; }"
        )
        self._stop_btn.clicked.connect(self._stop_recording)
        recording_layout.addWidget(self._stop_btn)

        layout.addLayout(recording_layout)

        # Play button
        self._play_btn = QPushButton("▶️ " + self._t("voice_record_play"))
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._play_recording)
        layout.addWidget(self._play_btn)

        # Divider
        divider = QLabel("─" * 50)
        divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
        divider.setStyleSheet("color: #ccc;")
        layout.addWidget(divider)

        # Import audio file button
        self._import_btn = QPushButton("📂 " + self._t("voice_record_import_file"))
        self._import_btn.setFixedHeight(40)
        self._import_btn.clicked.connect(self._import_audio)
        layout.addWidget(self._import_btn)

        # Voice name input
        name_layout = QHBoxLayout()
        self._name_label = QLabel(self._t("voice_record_name"))
        name_layout.addWidget(self._name_label)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText(self._t("voice_record_name_placeholder"))
        self._name_input.setText(self._t("voice_record_default_name"))
        name_layout.addWidget(self._name_input, 1)

        layout.addLayout(name_layout)

        layout.addStretch()

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._cancel_btn = QPushButton(tr(self._ui_lang, "cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton(self._t("voice_record_save"))
        self._save_btn.setEnabled(False)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_voice)
        button_layout.addWidget(self._save_btn)

        layout.addLayout(button_layout)

        # Timer for recording duration
        self._recording_timer = QTimer(self)
        self._recording_timer.timeout.connect(self._update_recording_duration)
        self._recording_seconds = 0

    def _normalize_reference_audio_path_to_bytes(self, source_path: str | Path) -> bytes:
        with tempfile.TemporaryDirectory(prefix="mio_xtts_ref_") as tmp_dir:
            output_path = Path(tmp_dir) / "reference.wav"
            normalize_xtts_reference_audio_file(source_path, output_path)
            return output_path.read_bytes()

    def _normalize_reference_audio_bytes(self, audio_data: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="mio_xtts_ref_") as tmp_dir:
            source_path = Path(tmp_dir) / "recording.wav"
            source_path.write_bytes(audio_data)
            return self._normalize_reference_audio_path_to_bytes(source_path)

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _number(self, value: float, decimals: int) -> str:
        return format_locale_number(value, self._ui_lang, decimals=decimals)

    def _set_status_text(self, key: str, **kwargs: object) -> None:
        self._status_key = key
        self._status_kwargs = dict(kwargs)
        self._status_label.setText(self._render_status_text())

    def _render_status_text(self) -> str:
        kwargs = dict(self._status_kwargs)
        if self._status_key in {
            "voice_record_needs_clearer",
            "voice_record_complete_status",
        } and isinstance(kwargs.get("duration"), (int, float)):
            kwargs["duration"] = self._number(float(kwargs["duration"]), 1)
        if self._status_key == "voice_record_imported_stats":
            if isinstance(kwargs.get("peak"), (int, float)):
                kwargs["peak"] = self._number(float(kwargs["peak"]), 2)
            if isinstance(kwargs.get("rms"), (int, float)):
                kwargs["rms"] = self._number(float(kwargs["rms"]), 3)
        return self._t(self._status_key, **kwargs)

    def _localized_quality_problem(self, stats) -> str | None:
        if stats.duration_seconds < XTTS_REFERENCE_MIN_DURATION_SECONDS:
            return self._t(
                "voice_record_quality_too_short",
                duration=self._number(stats.duration_seconds, 1),
            )
        if (
            stats.peak < XTTS_REFERENCE_MIN_PEAK
            or stats.rms < XTTS_REFERENCE_MIN_RMS
            or stats.active_duration_seconds < XTTS_REFERENCE_MIN_ACTIVE_SECONDS
            or stats.dynamic_range < XTTS_REFERENCE_MIN_DYNAMIC_RANGE
        ):
            return self._t(
                "voice_record_quality_quiet",
                peak=self._number(stats.peak, 3),
                rms=self._number(stats.rms, 4),
                active=self._number(stats.active_duration_seconds, 1),
            )
        if stats.dc_offset > XTTS_REFERENCE_MAX_DC_OFFSET:
            return self._t(
                "voice_record_quality_dc_offset",
                offset=self._number(stats.dc_offset, 3),
            )
        if stats.clipped_ratio > XTTS_REFERENCE_MAX_CLIPPED_RATIO:
            return self._t(
                "voice_record_quality_clipped",
                clipped=format_locale_percent(
                    stats.clipped_ratio * 100.0,
                    self._ui_lang,
                    decimals=1,
                ),
            )
        return None

    def _localized_import_error(self, error: BaseException, source_path: str | Path) -> str:
        if isinstance(error, XTTSAudioDecoderUnavailableError):
            return self._t("voice_record_import_decoder_missing")
        if isinstance(error, XTTSAudioDecodeError):
            return self._t("voice_record_import_decode_failed")
        path = Path(source_path)
        try:
            if path.suffix.lower() == ".wav" and path.stat().st_size <= 64 * 1024 * 1024:
                stats = analyze_xtts_reference_audio_bytes(path.read_bytes())
                quality_problem = self._localized_quality_problem(stats)
                if quality_problem:
                    return quality_problem
        except Exception:
            pass
        return self._t("voice_record_import_failed")

    def update_language(self, ui_language: str) -> None:
        old_default_name = self._t("voice_record_default_name")
        should_update_default_name = self._name_input.text() == old_default_name
        self._ui_lang = normalize_ui_language(ui_language, default="en")
        self.setWindowTitle(self._t("voice_record_title"))
        self._title_label.setText(self._t("voice_record_header"))
        self._instructions_label.setText(self._t("voice_record_instructions"))
        self._record_btn.setText(
            "🎤 "
            + self._t(
                "voice_record_recording" if self._recording else "voice_record_start"
            )
        )
        self._stop_btn.setText("⏹️ " + self._t("voice_record_stop"))
        self._play_btn.setText("▶️ " + self._t("voice_record_play"))
        self._import_btn.setText("📂 " + self._t("voice_record_import_file"))
        self._name_label.setText(self._t("voice_record_name"))
        self._name_input.setPlaceholderText(self._t("voice_record_name_placeholder"))
        if should_update_default_name:
            self._name_input.setText(self._t("voice_record_default_name"))
        self._cancel_btn.setText(self._t("cancel"))
        self._save_btn.setText(self._t("voice_record_save"))
        self._progress_bar.setFormat(self._t("voice_record_seconds_format"))
        self._status_label.setText(self._render_status_text())
        self.adjustSize()

    def _toggle_recording(self) -> None:
        """Toggle recording on/off."""
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        """Start recording audio."""
        try:
            import sounddevice as sd

            self._recording = True
            self._audio_frames = []
            self._recording_seconds = 0

            # Update UI
            self._record_btn.setText("🎤 " + self._t("voice_record_recording"))
            self._record_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._set_status_text("voice_record_in_progress")
            self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #f44336; padding: 10px;")
            self._progress_bar.setValue(0)
            self._progress_bar.setVisible(True)

            # Start timer
            self._recording_timer.start(1000)

            # Start audio recording at a rate the selected device natively supports.
            self._stream = self._open_input_stream(sd)
            self._stream.start()

            logger.info(
                "Started voice recording (device=%s, sample_rate=%s)",
                self._input_device if self._input_device is not None else "default",
                self._record_sample_rate,
            )

        except Exception as e:
            logger.error("Failed to start recording: %s", e)
            QMessageBox.critical(
                self,
                self._t("voice_recording_error_title"),
                self._t("voice_record_start_failed"),
            )
            self._release_audio_resources(clear_frames=True)
            self._reset_recording_ui()

    def _open_input_stream(self, sd):
        preferred_rate = 24000
        try:
            device_info = sd.query_devices(self._input_device, kind="input")
            preferred_rate = int(round(float(device_info.get("default_samplerate", 24000) or 24000)))
        except Exception as exc:
            logger.debug("Failed to query input device sample rate: %s", exc)

        candidates: list[int] = []
        for rate in (preferred_rate, 48000, 44100, 32000, 24000, 16000):
            if rate > 0 and rate not in candidates:
                candidates.append(rate)

        last_error: Exception | None = None
        for sample_rate in candidates:
            try:
                stream = sd.InputStream(
                    device=self._input_device,
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._audio_callback,
                )
                self._record_sample_rate = sample_rate
                return stream
            except Exception as exc:
                last_error = exc
                logger.debug("Input stream open failed at %s Hz: %s", sample_rate, exc)
        if last_error is not None:
            raise last_error
        raise RuntimeError(self._t("voice_record_no_usable_rate"))

    def _audio_callback(self, indata, frames, time_info, status):
        """Audio stream callback."""
        del frames, time_info
        if status:
            logger.warning("Audio callback status: %s", status)

        if self._recording:
            self._audio_frames.append(indata.copy())

    def _update_recording_duration(self) -> None:
        """Update recording duration display."""
        self._recording_seconds += 1
        self._progress_bar.setValue(self._recording_seconds)

        if self._recording_seconds >= 30:
            # Auto-stop after 30 seconds
            self._stop_recording()
            QMessageBox.information(
                self,
                self._t("voice_record_complete_title"),
                self._t("voice_record_max_duration"),
            )

    def _stop_recording(self) -> None:
        """Stop recording audio."""
        if not self._recording:
            return

        try:
            self._recording = False
            self._recording_timer.stop()

            # Stop audio stream
            stream = self._stream
            self._stream = None
            if stream:
                try:
                    stream.stop()
                finally:
                    stream.close()

            # Convert recorded frames to WAV
            if self._audio_frames:
                audio_data = np.concatenate(self._audio_frames, axis=0)
                audio_float = np.asarray(audio_data, dtype=np.float32).reshape(-1)
                raw_recorded_audio = encode_pcm16_wav(audio_float, self._record_sample_rate)

                duration = len(audio_float) / float(self._record_sample_rate)
                try:
                    self._recorded_audio = self._normalize_reference_audio_bytes(raw_recorded_audio)
                    stats = analyze_xtts_reference_audio_bytes(self._recorded_audio)
                    quality_problem = None
                except Exception:
                    self._recorded_audio = raw_recorded_audio
                    stats = analyze_xtts_reference_audio_bytes(raw_recorded_audio)
                    quality_problem = self._localized_quality_problem(stats) or self._t(
                        "voice_record_import_failed"
                    )
                logger.info(
                    "Recording stopped, duration: %.2f seconds, peak=%.4f, rms=%.5f, active=%.2fs",
                    duration,
                    stats.peak,
                    stats.rms,
                    stats.active_duration_seconds,
                )

                self._play_btn.setEnabled(True)
                if quality_problem:
                    self._set_status_text(
                        "voice_record_needs_clearer",
                        duration=duration,
                    )
                    self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #f44336; padding: 10px;")
                    self._save_btn.setEnabled(False)
                    QMessageBox.warning(
                        self,
                        self._t("voice_record_not_usable_title"),
                        quality_problem,
                    )
                else:
                    self._set_status_text(
                        "voice_record_complete_status",
                        duration=duration,
                    )
                    self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #4CAF50; padding: 10px;")
                    self._save_btn.setEnabled(True)
                return
            else:
                self._set_status_text("voice_record_no_audio")
                self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #f44336; padding: 10px;")

        except Exception as e:
            logger.error("Failed to stop recording: %s", e)
            QMessageBox.critical(
                self,
                self._t("voice_recording_error_title"),
                self._t("voice_record_stop_failed"),
            )

        finally:
            self._reset_recording_ui()

    def _reset_recording_ui(self) -> None:
        """Reset recording UI to initial state."""
        self._record_btn.setText("🎤 " + self._t("voice_record_start"))
        self._record_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress_bar.setVisible(False)

    def _release_audio_resources(self, *, clear_frames: bool = False) -> None:
        """Stop native capture without processing or displaying recording UI."""

        self._recording = False
        self._recording_timer.stop()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("Failed to stop voice recording stream", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("Failed to close voice recording stream", exc_info=True)
        if clear_frames:
            self._audio_frames.clear()

    def _play_recording(self) -> None:
        """Play the recorded audio."""
        if not self._recorded_audio:
            return

        try:
            import sounddevice as sd

            audio_array, sample_rate = decode_wav_bytes(self._recorded_audio)

            # Play audio
            sd.play(audio_array, sample_rate)

            logger.info("Playing recorded audio")

        except Exception as e:
            logger.error("Failed to play recording: %s", e)
            QMessageBox.critical(
                self,
                self._t("voice_record_playback_error_title"),
                self._t("voice_record_playback_failed"),
            )

    def _import_audio(self) -> None:
        """Import audio file."""
        file_dialog = QFileDialog(self)
        configure_file_dialog(
            file_dialog,
            self._ui_lang,
            title=self._t("voice_record_import_file"),
        )
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter(self._t("voice_record_audio_filter"))

        if file_dialog.exec():
            files = file_dialog.selectedFiles()
            if files:
                audio_path = files[0]
                try:
                    self._recorded_audio = self._normalize_reference_audio_path_to_bytes(audio_path)
                    stats = analyze_xtts_reference_audio_bytes(self._recorded_audio)

                    # Get filename as default voice name
                    voice_name = Path(audio_path).stem
                    self._name_input.setText(voice_name)

                    # Update UI
                    self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #4CAF50; padding: 10px;")
                    self._set_status_text(
                        "voice_record_imported_stats",
                        name=Path(audio_path).name,
                        peak=stats.peak,
                        rms=stats.rms,
                    )
                    self._play_btn.setEnabled(True)
                    self._save_btn.setEnabled(True)

                    logger.info("Imported audio file: %s", audio_path)

                except Exception as e:
                    self._recorded_audio = None
                    self._play_btn.setEnabled(False)
                    self._save_btn.setEnabled(False)
                    self._set_status_text("voice_record_import_unusable_status")
                    self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #f44336; padding: 10px;")
                    logger.error("Failed to import audio: %s", e)
                    QMessageBox.warning(
                        self,
                        self._t("voice_record_import_unusable_title"),
                        self._localized_import_error(e, audio_path),
                    )

    def _save_voice(self) -> None:
        """Save the recorded voice."""
        if not self._recorded_audio:
            QMessageBox.warning(
                self,
                self._t("voice_record_no_recording_title"),
                self._t("voice_record_no_recording_message"),
            )
            return

        voice_name = self._name_input.text().strip()
        if not voice_name:
            QMessageBox.warning(
                self,
                self._t("voice_record_no_name_title"),
                self._t("voice_record_no_name_message"),
            )
            return

        # Emit signal with audio data and name
        self.voice_recorded.emit(self._recorded_audio, voice_name)

        # Close dialog
        self.accept()

    def done(self, result: int) -> None:
        self._release_audio_resources(clear_frames=True)
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._release_audio_resources(clear_frames=True)
        super().closeEvent(event)
