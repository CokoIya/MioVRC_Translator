import io
import wave

import numpy as np

from src.tts.xtts_engine import validate_xtts_reference_audio_file
from src.ui_qt import voice_recording_dialog
from src.ui_qt.voice_recording_dialog import VoiceRecordingDialog


def _tone_wav_bytes(
    *,
    duration_s: float = 3.0,
    sample_rate: int = 24000,
    amplitude: float = 0.0005,
) -> bytes:
    samples = np.sin(
        np.linspace(
            0.0,
            np.pi * 2.0 * 220.0 * duration_s,
            int(sample_rate * duration_s),
            endpoint=False,
        )
    )
    samples = (samples * amplitude * 32767.0).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def _silent_wav_bytes(duration_s: float = 3.0, sample_rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * int(sample_rate * duration_s))
    return output.getvalue()


def _patch_file_dialog(monkeypatch, path: str) -> None:
    monkeypatch.setattr(
        voice_recording_dialog.QFileDialog,
        "exec",
        lambda _dialog: True,
    )
    monkeypatch.setattr(
        voice_recording_dialog.QFileDialog,
        "selectedFiles",
        lambda _dialog: [path],
    )


def test_voice_recording_dialog_import_normalizes_quiet_reference(qtbot, monkeypatch, tmp_path):
    source = tmp_path / "quiet voice.wav"
    source.write_bytes(_tone_wav_bytes())
    _patch_file_dialog(monkeypatch, str(source))

    dialog = VoiceRecordingDialog()
    qtbot.addWidget(dialog)

    dialog._import_audio()

    assert dialog._save_btn.isEnabled() is True
    assert dialog._recorded_audio is not None
    saved = tmp_path / "normalized.wav"
    saved.write_bytes(dialog._recorded_audio)
    usable, reason, _stats = validate_xtts_reference_audio_file(saved)
    assert usable is True
    assert reason == ""


def test_voice_recording_dialog_import_rejects_silent_reference(qtbot, monkeypatch, tmp_path):
    source = tmp_path / "silent.wav"
    source.write_bytes(_silent_wav_bytes())
    _patch_file_dialog(monkeypatch, str(source))
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        voice_recording_dialog.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    dialog = VoiceRecordingDialog()
    qtbot.addWidget(dialog)

    dialog._import_audio()

    assert dialog._save_btn.isEnabled() is False
    assert dialog._recorded_audio is None
    assert warnings
    assert warnings[0][0] == "Import Not Usable"


def test_voice_recording_dialog_reject_stops_native_capture(qtbot):
    class Stream:
        def __init__(self):
            self.stop_calls = 0
            self.close_calls = 0

        def stop(self):
            self.stop_calls += 1

        def close(self):
            self.close_calls += 1

    dialog = VoiceRecordingDialog()
    qtbot.addWidget(dialog)
    stream = Stream()
    dialog._stream = stream
    dialog._recording = True
    dialog._recording_timer.start(1000)

    dialog.reject()

    assert dialog._recording is False
    assert dialog._recording_timer.isActive() is False
    assert dialog._stream is None
    assert stream.stop_calls == 1
    assert stream.close_calls == 1
