"""Voice recording and import helper for cloud voice cloning."""
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
    QFrame,
    QMessageBox,
    QProgressBar,
    QLineEdit,
    QSizePolicy,
)

from src.tts.wav_utils import decode_wav_bytes, encode_pcm16_wav
from src.tts.reference_audio import (
    REFERENCE_MAX_CLIPPED_RATIO,
    REFERENCE_MAX_DURATION_SECONDS,
    REFERENCE_MAX_DC_OFFSET,
    REFERENCE_MIN_ACTIVE_SECONDS,
    REFERENCE_MIN_DURATION_SECONDS,
    REFERENCE_MIN_DYNAMIC_RANGE,
    REFERENCE_MIN_PEAK,
    REFERENCE_MIN_RMS,
    ReferenceAudioDecodeError,
    ReferenceAudioDecoderUnavailableError,
    analyze_reference_audio_bytes,
    normalize_reference_audio_file,
)
from src.ui_qt.qt_localization import configure_file_dialog
from src.ui_qt.theme import resolve_theme, theme_tokens
from src.utils.i18n import tr
from src.utils.localization import format_locale_number, format_locale_percent, normalize_ui_language

logger = logging.getLogger(__name__)


def _parent_theme(parent: object) -> str:
    """Best-effort read of the opener's resolved theme."""
    accessor = getattr(parent, "_current_active_theme", None)
    if callable(accessor):
        try:
            resolved = str(accessor() or "").strip()
        except Exception:
            resolved = ""
        if resolved:
            return resolved
    for name in ("_active_theme", "_theme"):
        value = str(getattr(parent, name, "") or "").strip()
        if value:
            return value
    return "dark"


class VoiceRecordingDialog(QDialog):
    """Dialog for recording or importing a voice sample to clone."""

    voice_recorded = Signal(bytes, str)  # audio_data, voice_name

    def __init__(
        self,
        parent=None,
        input_device: int | None = None,
        ui_lang: str | None = None,
        theme: str | None = None,
    ):
        super().__init__(parent)
        self._ui_lang = normalize_ui_language(
            ui_lang or str(getattr(parent, "_ui_lang", "") or "").strip() or "en",
            default="en",
        )
        # Inherit the app theme; the dialog used to hardcode light colors and
        # rendered as a white panel inside the dark window.
        self._theme = resolve_theme(theme if theme is not None else _parent_theme(parent))
        self.setWindowTitle(self._t("voice_record_title"))
        self.setMinimumSize(560, 520)

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
        """Build the dialog. Styling is driven by the shared theme tokens."""
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 24)

        self._title_label = QLabel(self._t("voice_record_header"))
        self._title_label.setObjectName("voiceRecordTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setWordWrap(True)
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._title_label)

        self._instructions_label = QLabel(self._t("voice_record_instructions"))
        self._instructions_label.setObjectName("voiceRecordHint")
        self._instructions_label.setWordWrap(True)
        layout.addWidget(self._instructions_label)

        # The recording leaves the machine, so say so where the player is
        # actually about to record rather than only on the settings page.
        self._upload_notice_label = QLabel(self._t("voice_record_upload_notice"))
        self._upload_notice_label.setObjectName("voiceRecordNotice")
        self._upload_notice_label.setWordWrap(True)
        layout.addWidget(self._upload_notice_label)

        self._status_label = QLabel(self._t("voice_record_ready"))
        self._status_label.setObjectName("voiceRecordStatus")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # The bar tracks the recommended window rather than a flat 10 s cap, so
        # the player can see when the clip is long enough and when it is being
        # trimmed.
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("voiceRecordProgress")
        self._progress_bar.setMaximum(int(REFERENCE_MAX_DURATION_SECONDS))
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat(self._t("voice_record_seconds_format"))
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._duration_hint_label = QLabel(self._t("voice_record_target_hint"))
        self._duration_hint_label.setObjectName("voiceRecordSubtle")
        self._duration_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._duration_hint_label.setWordWrap(True)
        layout.addWidget(self._duration_hint_label)

        recording_layout = QHBoxLayout()
        recording_layout.setSpacing(10)

        self._record_btn = QPushButton("🎤 " + self._t("voice_record_start"))
        self._record_btn.setObjectName("voiceRecordPrimary")
        self._record_btn.setMinimumHeight(44)
        self._record_btn.clicked.connect(self._toggle_recording)
        recording_layout.addWidget(self._record_btn)

        self._stop_btn = QPushButton("⏹ " + self._t("voice_record_stop"))
        self._stop_btn.setObjectName("voiceRecordDanger")
        self._stop_btn.setMinimumHeight(44)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_recording)
        recording_layout.addWidget(self._stop_btn)

        layout.addLayout(recording_layout)

        self._play_btn = QPushButton("▶ " + self._t("voice_record_play"))
        self._play_btn.setObjectName("voiceRecordSecondary")
        self._play_btn.setMinimumHeight(38)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._play_recording)
        layout.addWidget(self._play_btn)

        divider = QFrame()
        divider.setObjectName("voiceRecordDivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        self._import_btn = QPushButton("📂 " + self._t("voice_record_import_file"))
        self._import_btn.setObjectName("voiceRecordSecondary")
        self._import_btn.setMinimumHeight(38)
        self._import_btn.clicked.connect(self._import_audio)
        layout.addWidget(self._import_btn)

        name_layout = QHBoxLayout()
        name_layout.setSpacing(10)
        self._name_label = QLabel(self._t("voice_record_name"))
        name_layout.addWidget(self._name_label)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("voiceRecordName")
        self._name_input.setMinimumHeight(36)
        self._name_input.setPlaceholderText(self._t("voice_record_name_placeholder"))
        self._name_input.setText(self._t("voice_record_default_name"))
        name_layout.addWidget(self._name_input, 1)

        layout.addLayout(name_layout)
        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        self._cancel_btn = QPushButton(tr(self._ui_lang, "cancel"))
        self._cancel_btn.setObjectName("voiceRecordSecondary")
        self._cancel_btn.setMinimumHeight(38)
        self._cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton(self._t("voice_record_save"))
        self._save_btn.setObjectName("voiceRecordPrimary")
        self._save_btn.setMinimumHeight(38)
        self._save_btn.setEnabled(False)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_voice)
        button_layout.addWidget(self._save_btn)

        layout.addLayout(button_layout)

        self._recording_timer = QTimer(self)
        self._recording_timer.timeout.connect(self._update_recording_duration)
        self._recording_seconds = 0

        self._apply_theme()

    def _apply_theme(self) -> None:
        """Paint the dialog from the shared theme tokens."""
        tokens = theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {tokens["SHELL_BG"]};
                color: {tokens["TEXT_PRIMARY"]};
            }}
            QLabel {{
                color: {tokens["TEXT_PRIMARY"]};
                background: transparent;
            }}
            QLabel#voiceRecordTitle {{
                font-size: 18px;
                font-weight: bold;
                color: {tokens["TEXT_PRIMARY"]};
            }}
            QLabel#voiceRecordHint {{
                color: {tokens["TEXT_SECONDARY"]};
                background: {tokens["PANEL_BG"]};
                border: 1px solid {tokens["PANEL_BORDER"]};
                border-radius: {tokens["RADIUS_M"]}px;
                padding: 10px 12px;
            }}
            QLabel#voiceRecordNotice {{
                color: {tokens["TEXT_SECONDARY"]};
                background: {tokens["WARNING_SOFT"]};
                border: 1px solid {tokens["WARNING_BORDER"]};
                border-radius: {tokens["RADIUS_M"]}px;
                padding: 10px 12px;
            }}
            QLabel#voiceRecordSubtle {{
                color: {tokens["TEXT_MUTED"]};
                background: transparent;
                font-size: 12px;
                padding: 0px;
            }}
            QLabel#voiceRecordStatus {{
                font-size: 14px;
                font-weight: bold;
                color: {tokens["TEXT_PRIMARY"]};
                padding: 6px;
            }}
            QFrame#voiceRecordDivider {{
                background: {tokens["PANEL_DIVIDER"]};
                border: none;
            }}
            QProgressBar#voiceRecordProgress {{
                background: {tokens["FIELD_BG"]};
                border: 1px solid {tokens["FIELD_BORDER"]};
                border-radius: {tokens["RADIUS_S"]}px;
                color: {tokens["TEXT_PRIMARY"]};
                text-align: center;
                min-height: 20px;
            }}
            QProgressBar#voiceRecordProgress::chunk {{
                background: {tokens["ACCENT"]};
                border-radius: {tokens["RADIUS_S"]}px;
            }}
            QLineEdit#voiceRecordName {{
                background: {tokens["FIELD_BG"]};
                border: 1px solid {tokens["FIELD_BORDER"]};
                border-radius: {tokens["RADIUS_S"]}px;
                color: {tokens["INPUT_TEXT"]};
                padding: 6px 10px;
            }}
            QLineEdit#voiceRecordName:focus {{
                border-color: {tokens["FIELD_FOCUS"]};
            }}
            QPushButton {{
                border-radius: {tokens["RADIUS_S"]}px;
                padding: 8px 16px;
                font-size: 14px;
            }}
            QPushButton#voiceRecordPrimary {{
                background: {tokens["ACCENT"]};
                color: {tokens["TEXT_INVERTED"]};
                border: 1px solid {tokens["ACCENT_BORDER"]};
            }}
            QPushButton#voiceRecordPrimary:hover:enabled {{
                background: {tokens["ACCENT_HOVER"]};
            }}
            QPushButton#voiceRecordDanger {{
                background: {tokens["DANGER"]};
                color: {tokens["TEXT_INVERTED"]};
                border: 1px solid {tokens["DANGER_BORDER"]};
            }}
            QPushButton#voiceRecordSecondary {{
                background: {tokens["PANEL_RAISED"]};
                color: {tokens["TEXT_PRIMARY"]};
                border: 1px solid {tokens["PANEL_BORDER"]};
            }}
            QPushButton#voiceRecordSecondary:hover:enabled {{
                background: {tokens["FIELD_HOVER"]};
            }}
            QPushButton:disabled {{
                background: {tokens["PANEL_ALT_BG"]};
                color: {tokens["TEXT_MUTED"]};
                border: 1px solid {tokens["PANEL_BORDER"]};
            }}
            """
        )

    def refresh_theme(self, theme: str) -> None:
        self._theme = resolve_theme(theme)
        self._apply_theme()

    def _refresh_duration_hint(self) -> None:
        """Tell the player whether the clip is short, on target, or trimmed."""
        seconds = float(self._recording_seconds)
        if seconds > REFERENCE_MAX_DURATION_SECONDS:
            self._duration_hint_label.setText(self._t("voice_record_too_long_hint"))
        else:
            self._duration_hint_label.setText(self._t("voice_record_target_hint"))

    def _normalize_reference_audio_path_to_bytes(self, source_path: str | Path) -> bytes:
        with tempfile.TemporaryDirectory(prefix="mio_voice_ref_") as tmp_dir:
            output_path = Path(tmp_dir) / "reference.wav"
            normalize_reference_audio_file(source_path, output_path)
            return output_path.read_bytes()

    def _normalize_reference_audio_bytes(self, audio_data: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="mio_voice_ref_") as tmp_dir:
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
        if stats.duration_seconds < REFERENCE_MIN_DURATION_SECONDS:
            return self._t(
                "voice_record_quality_too_short",
                duration=self._number(stats.duration_seconds, 1),
            )
        if (
            stats.peak < REFERENCE_MIN_PEAK
            or stats.rms < REFERENCE_MIN_RMS
            or stats.active_duration_seconds < REFERENCE_MIN_ACTIVE_SECONDS
            or stats.dynamic_range < REFERENCE_MIN_DYNAMIC_RANGE
        ):
            return self._t(
                "voice_record_quality_quiet",
                peak=self._number(stats.peak, 3),
                rms=self._number(stats.rms, 4),
                active=self._number(stats.active_duration_seconds, 1),
            )
        if stats.dc_offset > REFERENCE_MAX_DC_OFFSET:
            return self._t(
                "voice_record_quality_dc_offset",
                offset=self._number(stats.dc_offset, 3),
            )
        if stats.clipped_ratio > REFERENCE_MAX_CLIPPED_RATIO:
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
        if isinstance(error, ReferenceAudioDecoderUnavailableError):
            return self._t("voice_record_import_decoder_missing")
        if isinstance(error, ReferenceAudioDecodeError):
            return self._t("voice_record_import_decode_failed")
        path = Path(source_path)
        try:
            if path.suffix.lower() == ".wav" and path.stat().st_size <= 64 * 1024 * 1024:
                stats = analyze_reference_audio_bytes(path.read_bytes())
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
        self._stop_btn.setText("⏹ " + self._t("voice_record_stop"))
        self._play_btn.setText("▶ " + self._t("voice_record_play"))
        self._upload_notice_label.setText(self._t("voice_record_upload_notice"))
        self._refresh_duration_hint()
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
        self._refresh_duration_hint()

        if self._recording_seconds >= REFERENCE_MAX_DURATION_SECONDS:
            # Anything past the cap is trimmed during normalization, so stop
            # rather than let the player keep talking into discarded audio.
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
                    stats = analyze_reference_audio_bytes(self._recorded_audio)
                    quality_problem = None
                except Exception:
                    self._recorded_audio = raw_recorded_audio
                    stats = analyze_reference_audio_bytes(raw_recorded_audio)
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
        self._refresh_duration_hint()

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
                    stats = analyze_reference_audio_bytes(self._recorded_audio)

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
