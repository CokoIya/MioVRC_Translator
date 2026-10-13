from __future__ import annotations

import numpy as np
import pytest

from src.tts.wav_utils import (
    concat_pcm16_wavs,
    decode_wav_bytes,
    encode_pcm16_wav,
    time_stretch_pcm16_wav,
)


def _tone(seconds: float, sample_rate: int = 24000, frequency: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


@pytest.mark.parametrize("rate", [0.5, 0.8, 1.25, 2.0])
def test_time_stretch_changes_duration_but_keeps_sample_rate_and_pitch(rate):
    sample_rate = 24000
    original = _tone(1.0, sample_rate)
    stretched_bytes = time_stretch_pcm16_wav(encode_pcm16_wav(original, sample_rate), rate)

    stretched, out_rate = decode_wav_bytes(stretched_bytes)
    assert out_rate == sample_rate
    expected = len(original) / rate
    # WSOLA works in 30 ms frames and keeps a short un-stretched tail, so the
    # length is approximate; a 10 % band still separates every rate tested.
    assert abs(len(stretched) - expected) < 0.1 * expected + 0.08 * sample_rate
    # Pitch: the dominant frequency must stay at the tone frequency, which a
    # plain resample would have shifted by the same factor as the duration.
    spectrum = np.abs(np.fft.rfft(stretched[: len(stretched) // 2 * 2] * np.hanning(len(stretched) // 2 * 2)))
    peak = np.argmax(spectrum) * sample_rate / (len(stretched) // 2 * 2)
    assert abs(peak - 220.0) < 8.0
    assert np.max(np.abs(stretched)) <= 1.0


def test_time_stretch_is_a_no_op_near_unity_and_for_garbage_rates():
    sample_rate = 24000
    payload = encode_pcm16_wav(_tone(0.3, sample_rate), sample_rate)
    assert time_stretch_pcm16_wav(payload, 1.0) is payload
    assert time_stretch_pcm16_wav(payload, 1.004) is payload
    assert time_stretch_pcm16_wav(payload, "fast") is payload
    assert time_stretch_pcm16_wav(payload, float("nan")) is payload


def test_time_stretch_handles_stereo_and_very_short_clips():
    sample_rate = 16000
    mono = _tone(0.5, sample_rate)
    stereo = np.stack([mono, mono * 0.5], axis=1)
    stretched, out_rate = decode_wav_bytes(
        time_stretch_pcm16_wav(encode_pcm16_wav(stereo, sample_rate), 1.5)
    )
    assert out_rate == sample_rate
    assert stretched.ndim == 2 and stretched.shape[1] == 2

    tiny = encode_pcm16_wav(_tone(0.01, sample_rate), sample_rate)
    short, _ = decode_wav_bytes(time_stretch_pcm16_wav(tiny, 2.0))
    assert len(short) == int(0.01 * sample_rate)


def test_concat_joins_clips_and_resamples_odd_rates_to_the_first():
    first = encode_pcm16_wav(_tone(0.2, 24000), 24000)
    second = encode_pcm16_wav(_tone(0.2, 48000), 48000)
    joined, rate = decode_wav_bytes(concat_pcm16_wavs([first, second]))
    assert rate == 24000
    assert abs(len(joined) - int(0.4 * 24000)) <= 2

    with pytest.raises(RuntimeError):
        concat_pcm16_wavs([])
