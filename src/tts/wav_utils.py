"""Small WAV helpers for TTS playback and post-processing."""
from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass

import numpy as np


WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_rate: int
    bits_per_sample: int
    format_tag: int
    block_align: int


def decode_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Decode PCM/IEEE-float WAV bytes to float32 samples in [-1.0, 1.0]."""
    audio, sample_rate, _info = decode_wav_bytes_with_info(data)
    return audio, sample_rate


def decode_wav_bytes_with_info(data: bytes) -> tuple[np.ndarray, int, WavInfo]:
    """Decode WAV bytes and return decoded audio plus parsed format metadata."""
    info, frames = _read_wav_chunks(data)
    if info.channels <= 0:
        raise RuntimeError("Invalid WAV channel count")
    if info.sample_rate <= 0:
        raise RuntimeError("Invalid WAV sample rate")

    if info.format_tag == WAVE_FORMAT_PCM:
        audio = _decode_pcm_frames(frames, info)
    elif info.format_tag == WAVE_FORMAT_IEEE_FLOAT:
        audio = _decode_float_frames(frames, info)
    else:
        raise RuntimeError(f"Unsupported WAV format tag: 0x{info.format_tag:04x}")

    if audio.size and audio.size % info.channels:
        raise RuntimeError("WAV sample data is not aligned to channel count")
    if info.channels > 1:
        audio = audio.reshape(-1, info.channels)

    audio = np.nan_to_num(
        audio.astype(np.float32, copy=False),
        nan=0.0,
        posinf=1.0,
        neginf=-1.0,
    )
    return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False), info.sample_rate, info


def scale_wav_volume_pcm16(data: bytes, volume: float) -> bytes:
    """Scale WAV audio safely and return a broadly compatible 16-bit PCM WAV."""
    audio, sample_rate, _info = decode_wav_bytes_with_info(data)
    try:
        loudness = max(0.0, float(volume))
    except (TypeError, ValueError):
        loudness = 1.0
    return encode_pcm16_wav(np.clip(audio * loudness, -1.0, 1.0), sample_rate)


def encode_pcm16_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode float audio samples as signed 16-bit PCM WAV bytes."""
    samples = np.asarray(audio, dtype=np.float32)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    samples = np.clip(samples, -1.0, 1.0)
    channels = 1 if samples.ndim == 1 else int(samples.shape[1])
    pcm = np.where(samples < 0.0, samples * 32768.0, samples * 32767.0).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def _read_wav_chunks(data: bytes) -> tuple[WavInfo, bytes]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("Invalid WAV data")

    fmt_chunk: bytes | None = None
    data_chunk: bytes | None = None
    offset = 12
    data_len = len(data)

    while offset + 8 <= data_len:
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > data_len:
            raise RuntimeError("Truncated WAV chunk")
        chunk_data = data[chunk_start:chunk_end]

        if chunk_id == b"fmt ":
            fmt_chunk = chunk_data
        elif chunk_id == b"data":
            data_chunk = chunk_data

        offset = chunk_end + (chunk_size & 1)

    if fmt_chunk is None:
        raise RuntimeError("WAV fmt chunk is missing")
    if data_chunk is None:
        raise RuntimeError("WAV data chunk is missing")
    return _parse_fmt_chunk(fmt_chunk), data_chunk


def _parse_fmt_chunk(fmt_chunk: bytes) -> WavInfo:
    if len(fmt_chunk) < 16:
        raise RuntimeError("WAV fmt chunk is too short")
    format_tag, channels, sample_rate, _byte_rate, block_align, bits_per_sample = struct.unpack_from(
        "<HHIIHH",
        fmt_chunk,
        0,
    )
    effective_tag = format_tag
    if format_tag == WAVE_FORMAT_EXTENSIBLE:
        if len(fmt_chunk) < 40:
            raise RuntimeError("WAVE_FORMAT_EXTENSIBLE chunk is too short")
        effective_tag = struct.unpack_from("<H", fmt_chunk, 24)[0]

    return WavInfo(
        channels=int(channels),
        sample_rate=int(sample_rate),
        bits_per_sample=int(bits_per_sample),
        format_tag=int(effective_tag),
        block_align=int(block_align),
    )


def _decode_pcm_frames(frames: bytes, info: WavInfo) -> np.ndarray:
    bits = info.bits_per_sample
    if bits == 8:
        raw = np.frombuffer(frames, dtype=np.uint8)
        return (raw.astype(np.float32) - 128.0) / 128.0
    if bits == 16:
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if bits == 24:
        raw = np.frombuffer(frames, dtype=np.uint8)
        if raw.size % 3:
            raise RuntimeError("24-bit WAV data is not sample aligned")
        triplets = raw.reshape(-1, 3).astype(np.int32)
        values = triplets[:, 0] | (triplets[:, 1] << 8) | (triplets[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        return values.astype(np.float32) / 8388608.0
    if bits == 32:
        return np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    if bits == 64:
        return np.frombuffer(frames, dtype="<i8").astype(np.float32) / 9223372036854775808.0
    raise RuntimeError(f"Unsupported PCM WAV bit depth: {bits}")


def _decode_float_frames(frames: bytes, info: WavInfo) -> np.ndarray:
    bits = info.bits_per_sample
    if bits == 32:
        return np.frombuffer(frames, dtype="<f4").astype(np.float32)
    if bits == 64:
        return np.frombuffer(frames, dtype="<f8").astype(np.float32)
    raise RuntimeError(f"Unsupported float WAV bit depth: {bits}")
