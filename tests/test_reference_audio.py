"""Reference-audio preparation shared by the voice cloning flows."""

from __future__ import annotations

import io
import sys
import wave

import numpy as np
import pytest

import src.tts.reference_audio as reference_audio
from src.tts.qwen_voice_enrollment import (
    MAX_REFERENCE_SECONDS,
    MIN_REFERENCE_SAMPLE_RATE,
    MIN_REFERENCE_SECONDS,
)
from src.tts.reference_audio import (
    REFERENCE_MAX_DURATION_SECONDS,
    REFERENCE_MIN_DURATION_SECONDS,
    REFERENCE_SAMPLE_RATE,
    ReferenceAudioDecoderUnavailableError,
    analyze_reference_audio_bytes,
    normalize_reference_audio_file,
    reference_quality_problem,
    repair_reference_audio_file,
    validate_reference_audio_file,
)


# Fixtures run past the 5-second voice-enrollment minimum so each test fails
# (or passes) for the reason it names rather than for being too short.
def _silent_wav_bytes(duration_s: float = 6.0) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(REFERENCE_SAMPLE_RATE)
        wav.writeframes(b"\x00\x00" * int(REFERENCE_SAMPLE_RATE * duration_s))
    return output.getvalue()


def _tone_wav_bytes(
    *,
    duration_s: float = 6.0,
    sample_rate: int = 24000,
    channels: int = 1,
    amplitude: float = 0.25,
) -> bytes:
    samples = np.sin(
        np.linspace(
            0.0,
            np.pi * 2.0 * 220.0 * duration_s,
            int(sample_rate * duration_s),
            endpoint=False,
        )
    )
    pcm = (samples * amplitude * 32767.0).astype("<i2")
    if channels > 1:
        pcm = np.repeat(pcm[:, None], channels, axis=1).reshape(-1)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def test_reference_audio_is_normalized_to_mono_pcm_at_the_enrollment_rate(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_tone_wav_bytes(channels=2, sample_rate=16000))

    normalize_reference_audio_file(source, output)

    with wave.open(str(output), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        # 24 kHz mono is the rate voice-enrollment recommends (16 kHz minimum).
        assert wav.getframerate() == REFERENCE_SAMPLE_RATE
    usable, reason, stats = validate_reference_audio_file(output)
    assert usable is True
    assert reason == ""
    assert stats is not None and stats.peak > 0.1


def test_reference_audio_rejects_silence_before_it_can_be_uploaded(tmp_path):
    source = tmp_path / "silent.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_silent_wav_bytes())

    with pytest.raises(RuntimeError, match="too quiet|mostly silent"):
        normalize_reference_audio_file(source, output)

    assert not output.exists()


def test_reference_audio_rejects_clips_shorter_than_the_service_minimum():
    stats = analyze_reference_audio_bytes(_tone_wav_bytes(duration_s=1.5))
    problem = reference_quality_problem(stats)
    assert problem is not None
    assert "too short" in problem


def test_reference_audio_rejects_a_four_second_clip(tmp_path):
    """Four seconds passed the old 3-second check; voice-enrollment needs five."""

    clip = _tone_wav_bytes(duration_s=4.0)
    problem = reference_quality_problem(analyze_reference_audio_bytes(clip))
    assert problem is not None
    assert "too short" in problem

    source = tmp_path / "short.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(clip)
    with pytest.raises(RuntimeError, match="too short"):
        normalize_reference_audio_file(source, output)
    assert not output.exists()


def test_reference_limits_match_the_voice_enrollment_service():
    assert REFERENCE_MIN_DURATION_SECONDS == MIN_REFERENCE_SECONDS == 5.0
    assert REFERENCE_SAMPLE_RATE >= MIN_REFERENCE_SAMPLE_RATE
    assert REFERENCE_MAX_DURATION_SECONDS <= MAX_REFERENCE_SECONDS


def test_reference_audio_normalization_recovers_a_quiet_recording(tmp_path):
    source = tmp_path / "quiet.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_tone_wav_bytes(amplitude=0.0005))

    usable_before, reason_before, _stats = validate_reference_audio_file(source)
    assert usable_before is False
    assert "too quiet" in reason_before

    normalize_reference_audio_file(source, output)

    usable, reason, stats = validate_reference_audio_file(output)
    assert usable is True
    assert reason == ""
    assert stats is not None
    assert stats.peak >= 0.015
    assert stats.active_duration_seconds >= 1.0


def test_reference_audio_repair_normalizes_an_existing_quiet_file(tmp_path):
    reference = tmp_path / "quiet_saved.wav"
    reference.write_bytes(_tone_wav_bytes(amplitude=0.0005))

    repaired, reason, stats = repair_reference_audio_file(reference)

    assert repaired is True
    assert reason == ""
    assert stats is not None
    usable, reason_after, _stats_after = validate_reference_audio_file(reference)
    assert usable is True
    assert reason_after == ""


def test_reference_import_reports_a_missing_bundled_decoder(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp3"
    source.write_bytes(b"not a wav or mp3 stream")
    output = tmp_path / "reference.wav"

    monkeypatch.setitem(sys.modules, "av", None)

    with pytest.raises(ReferenceAudioDecoderUnavailableError, match="MP3"):
        normalize_reference_audio_file(source, output)


def test_reference_audio_caps_overlong_recordings(tmp_path):
    source = tmp_path / "long.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_tone_wav_bytes(duration_s=90.0))

    normalize_reference_audio_file(source, output)

    _usable, _reason, stats = validate_reference_audio_file(output)
    assert stats is not None
    # Staying well inside the 60 s service ceiling keeps the upload small.
    assert stats.duration_seconds <= 30.5


def test_reference_audio_rejects_oversized_files(monkeypatch, tmp_path):
    source = tmp_path / "huge.wav"
    source.write_bytes(_tone_wav_bytes(duration_s=1.0))
    output = tmp_path / "voice.wav"

    monkeypatch.setattr(
        reference_audio,
        "secure_file_size",
        lambda _path: 512 * 1024 * 1024,
    )

    with pytest.raises(ValueError, match="256 MiB"):
        normalize_reference_audio_file(source, output)
