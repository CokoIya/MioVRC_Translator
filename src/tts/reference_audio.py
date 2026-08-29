# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Reference-audio preparation and quality checks for voice cloning.

Cloning services all want the same thing from a reference clip: mono PCM at a
known sample rate, trimmed of leading/trailing silence, level-normalized, and
long enough to actually carry a voice.  These helpers do that locally and
reject unusable recordings before anything is uploaded, so a bad take is
reported in the recording dialog rather than as a provider-side error.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .wav_utils import decode_wav_bytes, encode_pcm16_wav
from src.utils.app_paths import (
    atomic_write_bytes,
    open_secure_read,
    read_secure_bytes,
    require_existing_real_directory,
    secure_file_path,
    secure_file_size,
    secure_unlink,
)

logger = logging.getLogger(__name__)

# 24 kHz mono is both the recording format this app captures and the minimum
# the DashScope enrollment endpoint accepts, so no resampling is needed on the
# common path.
REFERENCE_SAMPLE_RATE = 24000
# The service requires at least 3 seconds of speech and accepts up to 60, but
# recommends 10-20.  Cap well inside the ceiling: longer clips only inflate the
# base64 upload without improving the clone.
REFERENCE_MIN_DURATION_SECONDS = 3.0
REFERENCE_RECOMMENDED_DURATION_SECONDS = 10.0
REFERENCE_MAX_DURATION_SECONDS = 30.0
REFERENCE_MIN_PEAK = 0.015
REFERENCE_MIN_RMS = 0.0015
REFERENCE_MIN_ACTIVE_SECONDS = 1.0
REFERENCE_MIN_DYNAMIC_RANGE = 0.02
REFERENCE_MAX_DC_OFFSET = 0.2
REFERENCE_MAX_CLIPPED_RATIO = 0.02
REFERENCE_TARGET_PEAK = 0.72
REFERENCE_TARGET_RMS = 0.08
REFERENCE_MAX_GAIN = 128.0
REFERENCE_MAX_FILE_BYTES = 256 * 1024 * 1024
_REFERENCE_DECODE_MAX_SECONDS = 5 * 60


class ReferenceAudioDecoderUnavailableError(RuntimeError):
    """Raised when bundled PyAV/FFmpeg support is missing for non-WAV imports."""


class ReferenceAudioDecodeError(RuntimeError):
    """Raised when a supported-looking reference file cannot be decoded."""


class _PCMWavDecodeError(RuntimeError):
    """The secured input was read successfully but is not supported PCM WAV."""


@dataclass(frozen=True)
class ReferenceAudioQuality:
    sample_rate: int
    duration_seconds: float
    peak: float
    rms: float
    active_duration_seconds: float
    clipped_ratio: float
    dc_offset: float = 0.0
    dynamic_range: float = 0.0



def _resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate or audio.size == 0:
        return np.asarray(audio, dtype=np.float32)
    try:
        from scipy.signal import resample_poly

        divisor = int(np.gcd(source_rate, target_rate))
        return np.asarray(
            resample_poly(audio, target_rate // divisor, source_rate // divisor),
            dtype=np.float32,
        )
    except Exception as exc:
        logger.warning("Failed to resample reference audio with scipy: %s", exc)
        source_len = len(audio)
        if source_len <= 1:
            return np.asarray(audio, dtype=np.float32)
        target_len = max(1, int(round(source_len * target_rate / source_rate)))
        source_x = np.linspace(0.0, 1.0, source_len, endpoint=False)
        target_x = np.linspace(0.0, 1.0, target_len, endpoint=False)
        return np.asarray(np.interp(target_x, source_x, audio), dtype=np.float32)


def _write_wav_int16(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    target = secure_file_path(path)
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    atomic_write_bytes(target, encode_pcm16_wav(pcm, sample_rate))


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    payload = read_secure_bytes(path, max_bytes=REFERENCE_MAX_FILE_BYTES)
    try:
        audio, sample_rate = decode_wav_bytes(payload)
    except Exception as exc:
        raise _PCMWavDecodeError(str(exc) or "Unsupported PCM WAV data") from exc
    if audio.ndim > 1 and audio.size:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), sample_rate


def analyze_reference_audio(
    audio: np.ndarray,
    sample_rate: int,
) -> ReferenceAudioQuality:
    """Measure whether a reference clip has enough real voice signal to clone."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    sample_rate = int(sample_rate or 0)
    if sample_rate <= 0:
        sample_rate = REFERENCE_SAMPLE_RATE
    duration = float(samples.size / sample_rate) if samples.size else 0.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
    clipped = float(np.mean(np.abs(samples) >= 0.999)) if samples.size else 0.0
    dc_offset = float(abs(np.mean(samples.astype(np.float64)))) if samples.size else 0.0
    dynamic_range = 0.0
    if samples.size:
        lo, hi = np.percentile(samples, [5, 95])
        dynamic_range = float(hi - lo)

    active_duration = 0.0
    if samples.size and sample_rate > 0:
        frame_len = max(1, int(sample_rate * 0.03))
        active_threshold = max(0.008, min(0.03, peak * 0.08))
        active_frames = 0
        for start in range(0, samples.size, frame_len):
            chunk = samples[start : start + frame_len]
            if not chunk.size:
                continue
            chunk_rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            if chunk_rms >= active_threshold:
                active_frames += chunk.size
        active_duration = float(active_frames / sample_rate)

    return ReferenceAudioQuality(
        sample_rate=sample_rate,
        duration_seconds=duration,
        peak=peak,
        rms=rms,
        active_duration_seconds=active_duration,
        clipped_ratio=clipped,
        dc_offset=dc_offset,
        dynamic_range=dynamic_range,
    )


def analyze_reference_audio_bytes(data: bytes) -> ReferenceAudioQuality:
    audio, sample_rate = decode_wav_bytes(data)
    if audio.ndim > 1 and audio.size:
        audio = audio.mean(axis=1)
    return analyze_reference_audio(audio, sample_rate)


def reference_quality_problem(stats: ReferenceAudioQuality) -> str | None:
    if stats.duration_seconds < REFERENCE_MIN_DURATION_SECONDS:
        return (
            "Reference audio is too short "
            f"({stats.duration_seconds:.1f}s). Record or import 10-20 seconds."
        )
    if (
        stats.peak < REFERENCE_MIN_PEAK
        or stats.rms < REFERENCE_MIN_RMS
        or stats.active_duration_seconds < REFERENCE_MIN_ACTIVE_SECONDS
        or stats.dynamic_range < REFERENCE_MIN_DYNAMIC_RANGE
    ):
        return (
            "Reference audio is too quiet or mostly silent "
            f"(peak {stats.peak:.3f}, rms {stats.rms:.4f}, "
            f"active {stats.active_duration_seconds:.1f}s). "
            "Record again closer to the microphone or import a clearer voice sample."
        )
    if stats.dc_offset > REFERENCE_MAX_DC_OFFSET:
        return (
            "Reference audio has a large DC offset "
            f"({stats.dc_offset:.3f}). "
            "Use a cleaner recording or normalize it in an audio editor before importing."
        )
    if stats.clipped_ratio > REFERENCE_MAX_CLIPPED_RATIO:
        return (
            "Reference audio is clipped or distorted "
            f"({stats.clipped_ratio:.1%} clipped samples). "
            "Lower the input gain and record again."
        )
    return None


def validate_reference_audio_file(
    audio_path: str | os.PathLike[str],
) -> tuple[bool, str, ReferenceAudioQuality | None]:
    path = Path(audio_path)
    try:
        path = secure_file_path(path, must_exist=True)
        audio, sample_rate = _read_pcm_wav(path)
        stats = analyze_reference_audio(audio, sample_rate)
    except FileNotFoundError:
        return False, f"Reference audio file not found: {path}", None
    except Exception as exc:
        return False, f"Reference audio could not be decoded: {exc}", None
    problem = reference_quality_problem(stats)
    return problem is None, problem or "", stats


def _trim_reference_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0 or sample_rate <= 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak <= 0:
        return samples
    frame_len = max(1, int(sample_rate * 0.03))
    threshold = max(0.008, min(0.03, peak * 0.06))
    active: list[tuple[int, int]] = []
    for start in range(0, samples.size, frame_len):
        end = min(start + frame_len, samples.size)
        chunk = samples[start:end]
        if not chunk.size:
            continue
        chunk_rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        if chunk_rms >= threshold:
            active.append((start, end))
    if not active:
        return samples
    pad = int(sample_rate * 0.2)
    trim_start = max(0, active[0][0] - pad)
    trim_end = min(samples.size, active[-1][1] + pad)
    return samples[trim_start:trim_end]


def _cap_reference_duration(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    max_samples = int(REFERENCE_MAX_DURATION_SECONDS * sample_rate)
    if sample_rate <= 0 or max_samples <= 0 or samples.size <= max_samples:
        return samples
    return samples[:max_samples]


def _normalize_reference_level(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    if samples.size == 0:
        return samples
    samples = samples - float(np.median(samples))
    stats = analyze_reference_audio(samples, sample_rate)
    if stats.duration_seconds < REFERENCE_MIN_DURATION_SECONDS:
        problem = reference_quality_problem(stats)
        raise RuntimeError(problem or "Reference audio is too short")
    if stats.clipped_ratio > REFERENCE_MAX_CLIPPED_RATIO:
        problem = reference_quality_problem(stats)
        raise RuntimeError(problem or "Reference audio is clipped or distorted")
    peak_gain = REFERENCE_TARGET_PEAK / max(stats.peak, 1e-6)
    rms_gain = REFERENCE_TARGET_RMS / max(stats.rms, 1e-6)
    gain = min(peak_gain, rms_gain, REFERENCE_MAX_GAIN)
    normalized = np.clip(samples * gain, -1.0, 1.0).astype(np.float32, copy=False)
    normalized_stats = analyze_reference_audio(normalized, sample_rate)
    problem = reference_quality_problem(normalized_stats)
    if problem is not None:
        raise RuntimeError(problem)
    return normalized


def _read_audio_with_av(path: Path, target_sample_rate: int) -> tuple[np.ndarray, int]:
    source = secure_file_path(path, must_exist=True)
    if secure_file_size(source) > REFERENCE_MAX_FILE_BYTES:
        raise ValueError("Reference audio exceeds the 256 MiB safety limit.")

    try:
        import av
        from av.audio.resampler import AudioResampler
    except Exception as exc:
        raise ReferenceAudioDecoderUnavailableError(
            "PyAV/FFmpeg is required to import MP3, M4A, FLAC, OGG, Opus, WebM, AAC, or WMA "
            "voice cloning reference audio."
        ) from exc

    chunks: list[np.ndarray] = []
    decoded_samples = 0
    max_decoded_samples = max(
        1,
        int(target_sample_rate * _REFERENCE_DECODE_MAX_SECONDS),
    )
    try:
        with open_secure_read(source, binary=True) as source_handle:
            with av.open(source_handle) as container:
                stream = next(
                    (item for item in container.streams if item.type == "audio"),
                    None,
                )
                if stream is None:
                    raise ReferenceAudioDecodeError(
                        "The selected file does not contain an audio stream"
                    )
                resampler = AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=target_sample_rate,
                )

                def collect(frames: object) -> bool:
                    nonlocal decoded_samples
                    if frames is None:
                        return decoded_samples >= max_decoded_samples
                    frame_list = frames if isinstance(frames, list) else [frames]
                    for frame in frame_list:
                        arr = np.asarray(frame.to_ndarray()).reshape(-1)
                        if not arr.size:
                            continue
                        remaining = max_decoded_samples - decoded_samples
                        if remaining <= 0:
                            return True
                        pcm = arr[:remaining].astype(np.int16, copy=False)
                        chunks.append(pcm)
                        decoded_samples += int(pcm.size)
                        if decoded_samples >= max_decoded_samples:
                            return True
                    return False

                limit_reached = False
                for frame in container.decode(stream):
                    if collect(resampler.resample(frame)):
                        limit_reached = True
                        break
                if not limit_reached:
                    collect(resampler.resample(None))
    except ReferenceAudioDecodeError:
        raise
    except Exception as exc:
        raise ReferenceAudioDecodeError(
            str(exc) or f"Could not decode {source.name}"
        ) from exc

    if not chunks:
        raise ReferenceAudioDecodeError(
            "No decodable audio was found in the selected file"
        )
    pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
    return pcm, target_sample_rate


def _allocate_private_temp_file(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
) -> tuple[Path, os.stat_result]:
    safe_directory = require_existing_real_directory(directory)
    fd, temp_name = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        dir=safe_directory,
    )
    try:
        file_stat = os.fstat(fd)
    finally:
        os.close(fd)
    return Path(temp_name), file_stat


def _cleanup_private_temp_file(
    path: Path | None,
    expected_stat: os.stat_result | None,
    *,
    purpose: str,
) -> None:
    if path is None:
        return
    try:
        if expected_stat is not None:
            try:
                current_path = secure_file_path(path, must_exist=True)
                current_stat = os.lstat(current_path)
                if os.path.samestat(expected_stat, current_stat):
                    expected_stat = current_stat
            except FileNotFoundError:
                return
        secure_unlink(
            path,
            missing_ok=True,
            expected_stat=expected_stat,
        )
    except Exception as exc:
        logger.warning(
            "Refused unsafe %s temporary-file cleanup %s: %s",
            purpose,
            path,
            exc,
        )


def normalize_reference_audio_file(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    sample_rate: int = REFERENCE_SAMPLE_RATE,
) -> Path:
    """Convert an imported reference clip to a stable mono PCM WAV file."""
    src = secure_file_path(source_path, must_exist=True)
    if secure_file_size(src) > REFERENCE_MAX_FILE_BYTES:
        raise ValueError("Reference audio exceeds the 256 MiB safety limit.")
    dest = secure_file_path(output_path)

    try:
        audio, source_rate = _read_pcm_wav(src)
    except _PCMWavDecodeError:
        audio, source_rate = _read_audio_with_av(src, sample_rate)

    if audio.size == 0:
        raise RuntimeError("Reference audio is empty")
    audio = _resample_audio(audio, source_rate, sample_rate)
    audio = _trim_reference_silence(audio, sample_rate)
    audio = _cap_reference_duration(audio, sample_rate)
    audio = _normalize_reference_level(audio, sample_rate)
    _write_wav_int16(dest, audio, sample_rate)
    return dest



def repair_reference_audio_file(
    audio_path: str | os.PathLike[str],
) -> tuple[bool, str, ReferenceAudioQuality | None]:
    """Try to repair a saved reference WAV by re-normalizing it in place."""
    path = Path(audio_path)
    try:
        path = secure_file_path(path, must_exist=True)
    except FileNotFoundError:
        return False, f"Reference audio file not found: {path}", None
    except Exception as exc:
        return False, str(exc), None

    tmp_path: Path | None = None
    tmp_stat: os.stat_result | None = None
    try:
        tmp_path, tmp_stat = _allocate_private_temp_file(
            path.parent,
            prefix=f".{path.stem}.repair.",
            suffix=".wav",
        )
        normalize_reference_audio_file(path, tmp_path)
        tmp_stat = os.lstat(tmp_path)
        usable, reason, stats = validate_reference_audio_file(tmp_path)
        if not usable:
            return False, reason, stats
        repaired_audio = read_secure_bytes(
            tmp_path,
            max_bytes=REFERENCE_MAX_FILE_BYTES,
        )
        atomic_write_bytes(path, repaired_audio)
        logger.info("Repaired reference audio by normalizing gain: %s", path)
        return True, "", stats
    except Exception as exc:
        return False, str(exc), None
    finally:
        _cleanup_private_temp_file(
            tmp_path,
            tmp_stat,
            purpose="reference audio repair",
        )
