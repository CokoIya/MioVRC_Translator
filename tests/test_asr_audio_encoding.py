from __future__ import annotations

import base64
import struct

import numpy as np

from src.asr.audio_encoding import pcm16_bytes, pcm16_wav_bytes, wav_data_url


def test_pcm16_wav_encoder_builds_valid_header_without_wave_roundtrip():
    audio = np.array([[0.0, 0.0], [1.0, 1.0], [-1.0, -1.0]], dtype=np.float32)

    wav = pcm16_wav_bytes(audio, 16000)

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert struct.unpack_from("<I", wav, 40)[0] == 6
    assert len(wav) == 50


def test_audio_encoder_sanitizes_nonfinite_samples_and_reuses_data_url_shape():
    audio = np.array([0.0, np.nan, np.inf, -np.inf], dtype=np.float32)

    pcm = pcm16_bytes(audio)
    data_url = wav_data_url(audio, 16000)

    assert len(pcm) == 8
    assert data_url.startswith("data:audio/wav;base64,")
    decoded = base64.b64decode(data_url.split(",", 1)[1])
    assert decoded.startswith(b"RIFF")
    assert decoded.endswith(pcm)
