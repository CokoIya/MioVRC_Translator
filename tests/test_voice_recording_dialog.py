import io
import wave

import numpy as np

from src.tts.reference_audio import validate_reference_audio_file
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
    usable, reason, _stats = validate_reference_audio_file(saved)
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


def test_voice_recording_dialog_follows_the_app_theme(qtbot):
    """The dialog used to hardcode light colors inside the dark window."""
    dark = VoiceRecordingDialog(ui_lang="en", theme="dark")
    qtbot.addWidget(dark)
    light = VoiceRecordingDialog(ui_lang="en", theme="light")
    qtbot.addWidget(light)

    from src.ui_qt.theme import theme_tokens

    dark_sheet = dark.styleSheet()
    light_sheet = light.styleSheet()

    assert dark_sheet and light_sheet
    assert dark_sheet != light_sheet
    assert str(theme_tokens("dark")["SHELL_BG"]) in dark_sheet
    assert str(theme_tokens("light")["SHELL_BG"]) in light_sheet
    # No leftover hardcoded light-theme values.
    assert "#f5f5f5" not in dark_sheet
    assert "#666" not in dark_sheet


def test_voice_recording_dialog_refresh_theme_repaints(qtbot):
    dialog = VoiceRecordingDialog(ui_lang="en", theme="dark")
    qtbot.addWidget(dialog)
    before = dialog.styleSheet()

    dialog.refresh_theme("light")

    assert dialog.styleSheet() != before


def test_voice_recording_progress_tracks_the_service_duration_cap(qtbot):
    from src.tts.reference_audio import REFERENCE_MAX_DURATION_SECONDS

    dialog = VoiceRecordingDialog(ui_lang="en", theme="dark")
    qtbot.addWidget(dialog)

    assert dialog._progress_bar.maximum() == int(REFERENCE_MAX_DURATION_SECONDS)

    # Past the cap the hint must say the extra audio is trimmed, because
    # normalization silently discards it.
    dialog._recording_seconds = int(REFERENCE_MAX_DURATION_SECONDS) + 5
    dialog._refresh_duration_hint()
    assert dialog._duration_hint_label.text() == dialog._t("voice_record_too_long_hint")

    dialog._recording_seconds = 12
    dialog._refresh_duration_hint()
    assert dialog._duration_hint_label.text() == dialog._t("voice_record_target_hint")


def test_voice_recording_dialog_discloses_the_upload(qtbot):
    dialog = VoiceRecordingDialog(ui_lang="en", theme="dark")
    qtbot.addWidget(dialog)

    assert dialog._upload_notice_label.text() == dialog._t("voice_record_upload_notice")
    assert dialog._upload_notice_label.isVisible() or not dialog.isVisible()


def test_voice_recording_dialog_reads_the_opener_theme(qtbot):
    """A caller that omits `theme` must still get the window's palette."""

    class FakeWindow:
        def _current_active_theme(self):
            return "light"

    dialog = VoiceRecordingDialog(ui_lang="en", theme=None)
    qtbot.addWidget(dialog)
    assert dialog._theme == "dark"

    from src.ui_qt import voice_recording_dialog as module

    assert module._parent_theme(FakeWindow()) == "light"
    assert module._parent_theme(object()) == "dark"
