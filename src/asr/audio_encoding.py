from __future__ import annotations

import base64
import struct

import numpy as np


def normalized_mono_audio(audio: np.ndarray) -> np.ndarray:
    """Return contiguous mono float32 audio with minimal copying."""

    array = np.asarray(audio)
    if array.size == 0:
        return np.asarray([], dtype=np.float32)
    if array.ndim > 1:
        array = array.mean(axis=1, dtype=np.float32)
    array = np.ravel(array).astype(np.float32, copy=False)
    if array.size == 0:
        return array
    if not np.isfinite(array).all():
        array = np.nan_to_num(array, copy=True)
    peak = float(np.max(np.abs(array)))
    if peak > 1.5:
        array = array / 32768.0
    return np.ascontiguousarray(array)


def pcm16_bytes(audio: np.ndarray) -> bytes:
    array = normalized_mono_audio(audio)
    if array.size == 0:
        return b""
    pcm16 = (np.clip(array, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
    return pcm16.tobytes()


def pcm16_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono PCM WAV without the BytesIO/wave module overhead."""

    pcm = pcm16_bytes(audio)
    if not pcm:
        return b""
    rate = max(int(sample_rate or 16000), 1)
    channels = 1
    sample_width = 2
    block_align = channels * sample_width
    byte_rate = rate * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        byte_rate,
        block_align,
        sample_width * 8,
        b"data",
        len(pcm),
    )
    return header + pcm


def wav_data_url(audio: np.ndarray, sample_rate: int) -> str:
    wav = pcm16_wav_bytes(audio, sample_rate)
    if not wav:
        return ""
    encoded = base64.b64encode(wav).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"
