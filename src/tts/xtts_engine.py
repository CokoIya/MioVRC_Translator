"""XTTS-v2 TTS engine with voice cloning support."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 銇撱亾_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
import tempfile
import warnings
import wave
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .base import BaseTTS, TTSVoice
from .wav_utils import decode_wav_bytes, encode_pcm16_wav, scale_wav_volume_pcm16
from .xtts_downloader import xtts_coqui_model_kwargs
from src.utils.app_paths import writable_app_dir

logger = logging.getLogger(__name__)

XTTS_DEFAULT_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
XTTS_API_CLASS: Any | None = None
XTTS_IMPORT_ERROR: str | None = None
XTTS_AVAILABLE = importlib.util.find_spec("TTS") is not None
XTTS_REFERENCE_SAMPLE_RATE = 24000
XTTS_OUTPUT_SAMPLE_RATE = 24000
XTTS_REFERENCE_MIN_DURATION_SECONDS = 2.0
XTTS_REFERENCE_RECOMMENDED_DURATION_SECONDS = 5.0
XTTS_REFERENCE_MAX_DURATION_SECONDS = 30.0
XTTS_REFERENCE_MIN_PEAK = 0.015
XTTS_REFERENCE_MIN_RMS = 0.0015
XTTS_REFERENCE_MIN_ACTIVE_SECONDS = 1.0
XTTS_REFERENCE_MIN_DYNAMIC_RANGE = 0.02
XTTS_REFERENCE_MAX_DC_OFFSET = 0.2
XTTS_REFERENCE_MAX_CLIPPED_RATIO = 0.02
XTTS_REFERENCE_TARGET_PEAK = 0.72
XTTS_REFERENCE_TARGET_RMS = 0.08
XTTS_REFERENCE_MAX_GAIN = 128.0
XTTS_OPTIMIZED_SPLIT_PAUSE_SECONDS = 0.18
XTTS_LANGUAGE_TEXT_LIMITS = {
    "en": 249,
    "de": 252,
    "fr": 272,
    "es": 238,
    "it": 212,
    "pt": 202,
    "pl": 223,
    "tr": 225,
    "ru": 181,
    "nl": 250,
    "cs": 185,
    "ar": 165,
    "zh": 70,
    "zh-cn": 70,
    "ja": 70,
    "hu": 223,
    "ko": 94,
    "hi": 149,
}
XTTS_REFERENCE_AUDIO_EXTENSIONS = (
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".webm",
    ".wma",
)
XTTS_REFERENCE_AUDIO_NAME_FILTER = (
    "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.webm *.wma)"
)
XTTS_CONDITIONING_CACHE_MAX_ITEMS = 4
XTTS_SUPPORTED_LANGUAGES = (
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "pl",
    "tr",
    "ru",
    "nl",
    "cs",
    "ar",
    "zh-cn",
    "ja",
    "hu",
    "ko",
    "hi",
)
XTTS_LANGUAGE_ALIASES = {
    "auto": "auto",
    "automatic": "auto",
    "detect": "auto",
    "english": "en",
    "eng": "en",
    "en-us": "en",
    "en-gb": "en",
    "chinese": "zh-cn",
    "zh": "zh-cn",
    "zh-hans": "zh-cn",
    "zh-sg": "zh-cn",
    "zh-tw": "zh-cn",
    "zh-hant": "zh-cn",
    "cn": "zh-cn",
    "japanese": "ja",
    "jp": "ja",
    "kor": "ko",
    "kr": "ko",
    "korean": "ko",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "pt-br": "pt",
    "pt-pt": "pt",
    "polish": "pl",
    "turkish": "tr",
    "russian": "ru",
    "dutch": "nl",
    "czech": "cs",
    "arabic": "ar",
    "hungarian": "hu",
    "hindi": "hi",
}
XTTS_RUNTIME_COMPONENTS = {
    "TTS": "Coqui TTS runtime",
    "av": "MP3/audio decoder runtime",
    "av.audio.resampler": "MP3/audio resampler runtime",
    "pypinyin": "Chinese text frontend",
    "ko_speech_tools": "Korean text frontend",
    "num2words": "multilingual number normalizer",
    "cutlet": "Japanese text frontend",
    "fugashi": "Japanese tokenizer",
    "unidic_lite": "Japanese dictionary",
    "mojimoji": "Japanese normalizer",
}

_XTTS_SENTENCE_END_CHARS = {".", "!", "?", "\n", "。", "！", "？"}
_XTTS_SOFT_BREAK_CHARS = {",", ";", ":", "、", "，", "；", "："}

class XTTSAudioDecoderUnavailableError(RuntimeError):
    """Raised when bundled PyAV/FFmpeg support is missing for non-WAV imports."""


class XTTSAudioDecodeError(RuntimeError):
    """Raised when a supported-looking reference file cannot be decoded."""


@dataclass(frozen=True)
class XTTSReferenceAudioQuality:
    sample_rate: int
    duration_seconds: float
    peak: float
    rms: float
    active_duration_seconds: float
    clipped_ratio: float
    dc_offset: float = 0.0
    dynamic_range: float = 0.0


@dataclass
class _XTTSConditioningCacheEntry:
    gpt_cond_latent: Any
    speaker_embedding: Any


@dataclass(frozen=True)
class XTTSRuntimeStatus:
    missing_modules: tuple[str, ...]
    import_error: str | None = None

    @property
    def coqui_available(self) -> bool:
        return "TTS" not in self.missing_modules and self.import_error is None

    @property
    def audio_import_available(self) -> bool:
        return "av" not in self.missing_modules and "av.audio.resampler" not in self.missing_modules

    @property
    def language_frontends_available(self) -> bool:
        language_modules = {
            "pypinyin",
            "ko_speech_tools",
            "num2words",
            "cutlet",
            "fugashi",
            "unidic_lite",
            "mojimoji",
        }
        return not any(module in self.missing_modules for module in language_modules)

    @property
    def ready(self) -> bool:
        return self.coqui_available and self.audio_import_available and self.language_frontends_available

    @property
    def missing_component_names(self) -> tuple[str, ...]:
        names = [XTTS_RUNTIME_COMPONENTS.get(module, module) for module in self.missing_modules]
        if self.import_error:
            names.append(f"Coqui TTS import error: {self.import_error}")
        return tuple(names)


warnings.filterwarnings(
    "ignore",
    message=r"In 2\.9, this function's implementation will be changed to use torchaudio\.load_with_torchcodec.*",
    category=UserWarning,
    module=r"torchaudio\._backend\.utils",
)


def _load_xtts_api() -> Any | None:
    """Load Coqui TTS lazily so importing the app does not probe torch/runtime DLLs."""
    global XTTS_API_CLASS, XTTS_IMPORT_ERROR, XTTS_AVAILABLE
    if XTTS_API_CLASS is not None:
        return XTTS_API_CLASS
    try:
        from TTS.api import TTS as api_class
    except Exception as exc:
        XTTS_AVAILABLE = False
        XTTS_IMPORT_ERROR = str(exc)
        logger.warning("TTS library not available. Install Coqui TTS to enable XTTS-v2: %s", exc)
        return None
    XTTS_API_CLASS = api_class
    XTTS_IMPORT_ERROR = None
    XTTS_AVAILABLE = True
    return XTTS_API_CLASS


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def xtts_runtime_status(*, require_api: bool = False) -> XTTSRuntimeStatus:
    """Return a lightweight diagnostic for bundled Voice Cloning components."""
    required_modules = tuple(XTTS_RUNTIME_COMPONENTS)
    missing = [module for module in required_modules if not _module_available(module)]
    import_error = None
    if "TTS" not in missing and require_api and _load_xtts_api() is None:
        import_error = XTTS_IMPORT_ERROR or "TTS.api could not be imported"
    return XTTSRuntimeStatus(tuple(missing), import_error)


def xtts_runtime_missing_summary(*, require_api: bool = False) -> str:
    status = xtts_runtime_status(require_api=require_api)
    components = ", ".join(status.missing_component_names)
    return components or ""


def is_xtts_runtime_available(*, require_api: bool = False) -> bool:
    """Return whether the Coqui TTS runtime needed by XTTS is importable."""
    global XTTS_AVAILABLE
    has_package = _module_available("TTS")
    if not has_package:
        XTTS_AVAILABLE = False
        return False
    if not require_api:
        XTTS_AVAILABLE = True
        return True
    return _load_xtts_api() is not None


def normalize_xtts_language_code(language: object) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text:
        return "auto"
    normalized = XTTS_LANGUAGE_ALIASES.get(text, text)
    if normalized == "auto" or normalized in XTTS_SUPPORTED_LANGUAGES:
        return normalized
    return "auto"


def xtts_language_from_target_language(language: object) -> str:
    """Resolve an app target-language code to an XTTS language, if supported."""
    return normalize_xtts_language_code(language)


def xtts_reference_import_error_message(error: BaseException) -> str:
    message = str(error).strip()
    if isinstance(error, XTTSAudioDecoderUnavailableError):
        return (
            "This audio format needs the bundled MP3/audio decoder runtime "
            "(PyAV/FFmpeg). Reinstall the full Mio Translator release, then restart "
            "the app, or convert the reference sample to WAV and import the WAV file."
        )
    if isinstance(error, XTTSAudioDecodeError):
        detail = f" Details: {message}" if message else ""
        return (
            "The selected audio file could not be decoded. Try another common audio "
            f"file, or convert it to WAV before importing.{detail}"
        )
    return message or "Reference audio import failed"


def xtts_reference_audio_dir() -> Path:
    """Return the writable directory for XTTS reference WAV files."""
    return writable_app_dir() / "tts_models" / "xtts" / "reference_audio"


def _safe_voice_name(voice_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", str(voice_name or "").strip())
    safe = safe.strip(" ._")
    return safe or "custom"


def safe_xtts_voice_name(voice_name: str) -> str:
    """Return a filesystem-safe XTTS reference voice name."""
    return _safe_voice_name(voice_name)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_i16.tobytes())


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = decode_wav_bytes(path.read_bytes())
    if audio.ndim > 1 and audio.size:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), sample_rate


def analyze_xtts_reference_audio(
    audio: np.ndarray,
    sample_rate: int,
) -> XTTSReferenceAudioQuality:
    """Measure whether a reference clip has enough real voice signal for XTTS."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    sample_rate = int(sample_rate or 0)
    if sample_rate <= 0:
        sample_rate = XTTS_REFERENCE_SAMPLE_RATE
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

    return XTTSReferenceAudioQuality(
        sample_rate=sample_rate,
        duration_seconds=duration,
        peak=peak,
        rms=rms,
        active_duration_seconds=active_duration,
        clipped_ratio=clipped,
        dc_offset=dc_offset,
        dynamic_range=dynamic_range,
    )


def analyze_xtts_reference_audio_bytes(data: bytes) -> XTTSReferenceAudioQuality:
    audio, sample_rate = decode_wav_bytes(data)
    if audio.ndim > 1 and audio.size:
        audio = audio.mean(axis=1)
    return analyze_xtts_reference_audio(audio, sample_rate)


def xtts_reference_quality_problem(stats: XTTSReferenceAudioQuality) -> str | None:
    if stats.duration_seconds < XTTS_REFERENCE_MIN_DURATION_SECONDS:
        return (
            "Reference audio is too short "
            f"({stats.duration_seconds:.1f}s). Record or import at least 5-10 seconds."
        )
    if (
        stats.peak < XTTS_REFERENCE_MIN_PEAK
        or stats.rms < XTTS_REFERENCE_MIN_RMS
        or stats.active_duration_seconds < XTTS_REFERENCE_MIN_ACTIVE_SECONDS
        or stats.dynamic_range < XTTS_REFERENCE_MIN_DYNAMIC_RANGE
    ):
        return (
            "Reference audio is too quiet or mostly silent "
            f"(peak {stats.peak:.3f}, rms {stats.rms:.4f}, "
            f"active {stats.active_duration_seconds:.1f}s). "
            "Record again closer to the microphone or import a clearer voice sample."
        )
    if stats.dc_offset > XTTS_REFERENCE_MAX_DC_OFFSET:
        return (
            "Reference audio has a large DC offset "
            f"({stats.dc_offset:.3f}). "
            "Use a cleaner recording or normalize it in an audio editor before importing."
        )
    if stats.clipped_ratio > XTTS_REFERENCE_MAX_CLIPPED_RATIO:
        return (
            "Reference audio is clipped or distorted "
            f"({stats.clipped_ratio:.1%} clipped samples). "
            "Lower the input gain and record again."
        )
    return None


def validate_xtts_reference_audio_file(
    audio_path: str | os.PathLike[str],
) -> tuple[bool, str, XTTSReferenceAudioQuality | None]:
    path = Path(audio_path)
    if not path.is_file():
        return False, f"Reference audio file not found: {path}", None
    try:
        audio, sample_rate = _read_pcm_wav(path)
        stats = analyze_xtts_reference_audio(audio, sample_rate)
    except Exception as exc:
        return False, f"Reference audio could not be decoded: {exc}", None
    problem = xtts_reference_quality_problem(stats)
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
    max_samples = int(XTTS_REFERENCE_MAX_DURATION_SECONDS * sample_rate)
    if sample_rate <= 0 or max_samples <= 0 or samples.size <= max_samples:
        return samples
    return samples[:max_samples]


def _normalize_reference_level(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    if samples.size == 0:
        return samples
    samples = samples - float(np.median(samples))
    stats = analyze_xtts_reference_audio(samples, sample_rate)
    if stats.duration_seconds < XTTS_REFERENCE_MIN_DURATION_SECONDS:
        problem = xtts_reference_quality_problem(stats)
        raise RuntimeError(problem or "Reference audio is too short")
    if stats.clipped_ratio > XTTS_REFERENCE_MAX_CLIPPED_RATIO:
        problem = xtts_reference_quality_problem(stats)
        raise RuntimeError(problem or "Reference audio is clipped or distorted")
    peak_gain = XTTS_REFERENCE_TARGET_PEAK / max(stats.peak, 1e-6)
    rms_gain = XTTS_REFERENCE_TARGET_RMS / max(stats.rms, 1e-6)
    gain = min(peak_gain, rms_gain, XTTS_REFERENCE_MAX_GAIN)
    normalized = np.clip(samples * gain, -1.0, 1.0).astype(np.float32, copy=False)
    normalized_stats = analyze_xtts_reference_audio(normalized, sample_rate)
    problem = xtts_reference_quality_problem(normalized_stats)
    if problem is not None:
        raise RuntimeError(problem)
    return normalized


def _read_audio_with_av(path: Path, target_sample_rate: int) -> tuple[np.ndarray, int]:
    try:
        import av
        from av.audio.resampler import AudioResampler
    except Exception as exc:
        raise XTTSAudioDecoderUnavailableError(
            "PyAV/FFmpeg is required to import MP3, M4A, FLAC, OGG, Opus, WebM, AAC, or WMA "
            "Voice Cloning reference audio."
        ) from exc

    chunks: list[np.ndarray] = []
    try:
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise XTTSAudioDecodeError("The selected file does not contain an audio stream")
            resampler = AudioResampler(format="s16", layout="mono", rate=target_sample_rate)

            def collect(frames: object) -> None:
                if frames is None:
                    return
                frame_list = frames if isinstance(frames, list) else [frames]
                for frame in frame_list:
                    arr = frame.to_ndarray()
                    if arr.size:
                        chunks.append(np.asarray(arr).reshape(-1).astype(np.int16))

            for frame in container.decode(stream):
                collect(resampler.resample(frame))
            collect(resampler.resample(None))
    except XTTSAudioDecodeError:
        raise
    except Exception as exc:
        raise XTTSAudioDecodeError(str(exc) or f"Could not decode {path.name}") from exc

    if not chunks:
        raise XTTSAudioDecodeError("No decodable audio was found in the selected file")
    pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
    return pcm, target_sample_rate


def normalize_xtts_reference_audio_file(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    sample_rate: int = XTTS_REFERENCE_SAMPLE_RATE,
) -> Path:
    """Convert an imported reference clip to a stable mono PCM WAV file."""
    src = Path(source_path)
    dest = Path(output_path)
    if not src.is_file():
        raise FileNotFoundError(f"Reference audio file not found: {src}")

    try:
        audio, source_rate = _read_pcm_wav(src)
    except Exception:
        audio, source_rate = _read_audio_with_av(src, sample_rate)

    if audio.size == 0:
        raise RuntimeError("Reference audio is empty")
    audio = _resample_audio(audio, source_rate, sample_rate)
    audio = _trim_reference_silence(audio, sample_rate)
    audio = _cap_reference_duration(audio, sample_rate)
    audio = _normalize_reference_level(audio, sample_rate)
    _write_wav_int16(dest, audio, sample_rate)
    return dest


def repair_xtts_reference_audio_file(
    audio_path: str | os.PathLike[str],
) -> tuple[bool, str, XTTSReferenceAudioQuality | None]:
    """Try to repair a saved XTTS reference WAV by re-normalizing it in place."""
    path = Path(audio_path)
    if not path.is_file():
        return False, f"Reference audio file not found: {path}", None

    tmp_path = path.with_name(f"{path.stem}.repair.tmp.wav")
    try:
        normalize_xtts_reference_audio_file(path, tmp_path)
        usable, reason, stats = validate_xtts_reference_audio_file(tmp_path)
        if not usable:
            return False, reason, stats
        tmp_path.replace(path)
        logger.info("Repaired XTTS reference audio by normalizing gain: %s", path)
        return True, "", stats
    except Exception as exc:
        return False, str(exc), None
    finally:
        tmp_path.unlink(missing_ok=True)


def list_xtts_reference_voices(ref_audio_dir: Path | None = None) -> list[TTSVoice]:
    """List saved XTTS reference voices without requiring the Coqui runtime."""
    voices = [
        TTSVoice(
            id="custom",
            name="Custom Cloned Voice",
            language="multi",
            gender="Neutral",
            locale="multi",
        )
    ]

    directory = ref_audio_dir or xtts_reference_audio_dir()
    if directory.exists():
        for audio_file in sorted(directory.glob("*.wav"), key=lambda path: path.stem.casefold()):
            voice_name = audio_file.stem
            voices.append(
                TTSVoice(
                    id=voice_name,
                    name=f"Cloned: {voice_name}",
                    language="multi",
                    gender="Neutral",
                    locale="multi",
                )
            )

    return voices


def first_xtts_reference_audio_path(ref_audio_dir: Path | None = None) -> Path | None:
    """Return the first saved XTTS reference WAV, if any."""
    directory = ref_audio_dir or xtts_reference_audio_dir()
    if not directory.exists():
        return None
    return next(
        (
            audio_file
            for audio_file in sorted(directory.glob("*.wav"), key=lambda path: path.stem.casefold())
            if audio_file.is_file() and audio_file.stat().st_size > 0
        ),
        None,
    )


def first_usable_xtts_reference_audio_path(ref_audio_dir: Path | None = None) -> Path | None:
    """Return the first saved XTTS reference WAV that passes quality checks."""
    directory = ref_audio_dir or xtts_reference_audio_dir()
    if not directory.exists():
        return None
    for audio_file in sorted(directory.glob("*.wav"), key=lambda path: path.stem.casefold()):
        if not audio_file.is_file() or audio_file.stat().st_size <= 0:
            continue
        usable, reason, _stats = validate_xtts_reference_audio_file(audio_file)
        if usable:
            return audio_file
        logger.warning("Skipping unusable XTTS reference audio %s: %s", audio_file.name, reason)
    return None


class XTTSTS(BaseTTS):
    """XTTS-v2 TTS engine with voice cloning capability."""

    def __init__(
        self,
        device: str = "cpu",
        model_name: str = XTTS_DEFAULT_MODEL_NAME,
        language: str = "auto",
        *,
        lazy_load: bool = True,
        optimized_inference: bool = True,
        conditioning_cache_size: int = XTTS_CONDITIONING_CACHE_MAX_ITEMS,
        enable_text_splitting: bool = True,
        temperature: float | None = None,
        length_penalty: float | None = None,
        repetition_penalty: float | None = None,
        top_k: int | None = None,
        top_p: float | None = None,
        do_sample: bool | None = None,
        num_beams: int | None = None,
    ):
        super().__init__()
        requested_device = str(device or "cpu").strip().lower()
        if requested_device not in {"cpu", "cuda"}:
            requested_device = "cpu"
        self._requested_device = requested_device
        self._device = requested_device
        self._runtime_device = "cpu"
        self._model_name = model_name
        self._model: Any | None = None
        self._reference_audio_path: str | None = None
        self._language = normalize_xtts_language_code(language)
        self._supported_languages = list(XTTS_SUPPORTED_LANGUAGES)
        self._lazy_load = bool(lazy_load)
        self._optimized_inference = bool(optimized_inference)
        self._enable_text_splitting = bool(enable_text_splitting)
        try:
            cache_size = int(conditioning_cache_size)
        except (TypeError, ValueError):
            cache_size = XTTS_CONDITIONING_CACHE_MAX_ITEMS
        self._conditioning_cache_size = max(0, min(cache_size, 16))
        self._conditioning_cache: OrderedDict[tuple[object, ...], _XTTSConditioningCacheEntry] = OrderedDict()
        self._model_lock = threading.RLock()
        self._inference_kwargs = self._build_inference_kwargs(
            temperature=temperature,
            length_penalty=length_penalty,
            repetition_penalty=repetition_penalty,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            num_beams=num_beams,
        )

        if not self._lazy_load and is_xtts_runtime_available():
            try:
                self._initialize_model()
            except Exception as exc:
                logger.error("Failed to initialize XTTS-v2 model: %s", exc)
                self._model = None

    @staticmethod
    def _build_inference_kwargs(**values: object) -> dict[str, object]:
        """Return only valid XTTS inference overrides supplied by config."""
        kwargs: dict[str, object] = {}
        float_ranges = {
            "temperature": (0.05, 2.0),
            "length_penalty": (-10.0, 10.0),
            "repetition_penalty": (0.1, 50.0),
            "top_p": (0.01, 1.0),
        }
        int_ranges = {
            "top_k": (1, 200),
            "num_beams": (1, 8),
        }
        for key, (minimum, maximum) in float_ranges.items():
            value = values.get(key)
            if value is None:
                continue
            try:
                parsed = max(minimum, min(float(value), maximum))
            except (TypeError, ValueError):
                continue
            kwargs[key] = parsed
        for key, (minimum, maximum) in int_ranges.items():
            value = values.get(key)
            if value is None:
                continue
            try:
                parsed = max(minimum, min(int(value), maximum))
            except (TypeError, ValueError):
                continue
            kwargs[key] = parsed
        if isinstance(values.get("do_sample"), bool):
            kwargs["do_sample"] = values["do_sample"]
        return kwargs

    def _initialize_model(self) -> None:
        """Initialize XTTS-v2 model."""
        try:
            if self._model is not None:
                return
            logger.info("Loading XTTS-v2 model on requested device: %s", self._requested_device)

            use_gpu = False
            runtime_device = "cpu"
            if self._requested_device == "cuda":
                try:
                    import torch

                    use_gpu = torch.cuda.is_available()
                except ImportError:
                    logger.warning("PyTorch not available, using CPU")
                    use_gpu = False
                if use_gpu:
                    runtime_device = "cuda"
                else:
                    logger.warning("CUDA requested but not available; loading XTTS on CPU for this session")
            self._runtime_device = runtime_device
            self._device = runtime_device

            api_class = _load_xtts_api()
            if api_class is None:
                raise RuntimeError(XTTS_IMPORT_ERROR or "Coqui TTS is not installed")

            self._model = api_class(**self._coqui_model_kwargs(use_gpu=use_gpu))
            logger.info("XTTS-v2 model loaded successfully")

        except Exception as exc:
            logger.error("Failed to load XTTS-v2 model: %s", exc)
            self._model = None
            raise

    def _ensure_model_loaded(self) -> None:
        """Load the heavy XTTS model on demand."""
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            self._initialize_model()

    def _coqui_model_kwargs(self, *, use_gpu: bool) -> dict[str, object]:
        """Build Coqui API kwargs, preferring the managed local XTTS model."""
        local_kwargs = xtts_coqui_model_kwargs()
        if local_kwargs is not None:
            return {**local_kwargs, "gpu": use_gpu}

        configured = str(self._model_name or "").strip()
        if configured and configured != XTTS_DEFAULT_MODEL_NAME:
            configured_path = Path(configured).expanduser()
            if configured_path.is_dir():
                config_path = configured_path / "config.json"
                if not config_path.is_file():
                    raise RuntimeError(f"XTTS model config is missing: {config_path}")
                return {
                    "model_path": str(configured_path),
                    "config_path": str(config_path),
                    "progress_bar": False,
                    "gpu": use_gpu,
                }
            return {
                "model_name": configured,
                "progress_bar": False,
                "gpu": use_gpu,
            }

        raise RuntimeError("XTTS-v2 local model files are not ready. Download the XTTS-v2 model first.")

    def is_available(self) -> bool:
        """Check if XTTS-v2 synthesis is available."""
        if not is_xtts_runtime_available():
            return False
        if self._model is not None:
            return True
        try:
            self._coqui_model_kwargs(use_gpu=False)
        except Exception as exc:
            logger.debug("XTTS model source is not ready: %s", exc)
            return False
        return True

    def _can_synthesize(self) -> bool:
        """Check if TTS synthesis is actually possible."""
        if not is_xtts_runtime_available():
            logger.debug("XTTS library not installed")
            return False
        if self._model is None and not self.is_available():
            logger.debug("XTTS model not initialized")
            return False
        return True

    def get_available_voices(self) -> list[TTSVoice]:
        """Get available saved reference voices."""
        return list_xtts_reference_voices()

    def set_reference_audio(self, audio_path: str) -> bool:
        """Set reference audio for voice cloning."""
        path = Path(audio_path)
        if not path.exists():
            logger.error("Reference audio file not found: %s", audio_path)
            return False
        if path.suffix.lower() not in XTTS_REFERENCE_AUDIO_EXTENSIONS:
            logger.error("Unsupported XTTS reference audio format: %s", path.suffix or path.name)
            return False

        if path.suffix.lower() != ".wav":
            try:
                ref_audio_dir = xtts_reference_audio_dir()
                ref_audio_dir.mkdir(parents=True, exist_ok=True)
                normalized_path = ref_audio_dir / f"{_safe_voice_name(path.stem)}.wav"
                normalize_xtts_reference_audio_file(path, normalized_path)
                path = normalized_path
            except Exception as exc:
                logger.error("Failed to import reference audio %s: %s", audio_path, exc)
                return False

        usable, reason, stats = validate_xtts_reference_audio_file(path)
        if not usable:
            logger.error("Reference audio is not usable for XTTS: %s", reason)
            return False

        try:
            with wave.open(str(path), "rb") as wf:
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                duration = n_frames / float(framerate)
                logger.info(
                    "Reference audio: %d channels, %d Hz, %.2f seconds",
                    wf.getnchannels(),
                    framerate,
                    duration,
                )
                if duration < 2.0:
                    logger.warning("Reference audio is very short (%.2f sec). 5-10 seconds recommended.", duration)
                elif duration > 30.0:
                    logger.warning("Reference audio is long (%.2f sec). This may slow down synthesis.", duration)
        except Exception as exc:
            logger.error("Failed to validate reference audio: %s", exc)
            return False

        self._reference_audio_path = str(path)
        logger.info("Reference audio set: %s", path)
        return True

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize speech using XTTS-v2."""
        speed = self._normalize_speed(rate)
        if not self._can_synthesize():
            raise RuntimeError(
                "XTTS-v2 engine not available. Install Coqui TTS and ensure the model can load."
            )

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        ref_audio = self._reference_audio_path
        selected_voice_path = False
        voice_id = str(voice or "").strip()
        if voice_id and voice_id != "custom":
            voice_file = xtts_reference_audio_dir() / f"{voice_id}.wav"
            if voice_file.exists():
                ref_audio = str(voice_file)
                selected_voice_path = True
        if ref_audio:
            usable, reason, _stats = validate_xtts_reference_audio_file(ref_audio)
            if not usable:
                repaired, repair_reason, _repair_stats = repair_xtts_reference_audio_file(ref_audio)
                if repaired:
                    usable = True
                    reason = ""
                else:
                    reason = repair_reason or reason
            if not usable:
                if selected_voice_path:
                    raise RuntimeError(
                        f'Selected XTTS reference voice "{voice_id}" is not usable: {reason}'
                    )
                logger.warning("Ignoring unusable XTTS reference audio %s: %s", ref_audio, reason)
                ref_audio = None
        if not ref_audio:
            first_reference = first_usable_xtts_reference_audio_path()
            if first_reference is not None:
                ref_audio = str(first_reference)

        if not ref_audio:
            raise RuntimeError(
                "No usable XTTS reference audio is available. Please record or import a clear "
                "5-10 second voice sample before testing synthesis."
            )

        language = self._detect_language(text) if self._language == "auto" else self._language
        self._ensure_model_loaded()
        logger.info(
            "Synthesizing with XTTS-v2: text_len=%d, lang=%s (mode=%s), ref_audio=%s",
            len(text),
            language,
            self._language,
            Path(ref_audio).name,
        )

        if getattr(self, "_optimized_inference", False):
            try:
                audio_data = self._synthesize_with_cached_conditioning(
                    text=text,
                    ref_audio=ref_audio,
                    language=language,
                    speed=speed,
                    volume=volume,
                )
                logger.info("XTTS-v2 optimized synthesis successful, audio size: %d bytes", len(audio_data))
                return audio_data
            except Exception as exc:
                logger.warning(
                    "XTTS optimized inference failed; falling back to Coqui API path: %s",
                    exc,
                )

        try:
            audio_data = self._synthesize_with_coqui_api_no_spacy(
                text=text,
                ref_audio=ref_audio,
                language=language,
                speed=speed,
                volume=volume,
            )
            logger.info("XTTS-v2 synthesis successful, audio size: %d bytes", len(audio_data))
            return audio_data
        except Exception as exc:
            logger.error("XTTS-v2 synthesis failed: %s", exc)
            raise RuntimeError(f"XTTS-v2 synthesis failed: {exc}") from exc

    def _xtts_core_model(self) -> Any | None:
        model = getattr(self, "_model", None)
        synthesizer = getattr(model, "synthesizer", None)
        core_model = getattr(synthesizer, "tts_model", None)
        if (
            core_model is not None
            and hasattr(core_model, "get_conditioning_latents")
            and hasattr(core_model, "inference")
        ):
            return core_model
        return None

    def _conditioning_cache_key(self, ref_audio: str) -> tuple[object, ...]:
        path = Path(ref_audio)
        try:
            stat = path.stat()
            resolved = str(path.resolve(strict=False))
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError:
            resolved = str(path)
            mtime_ns = 0
            size = 0
        return (
            resolved,
            mtime_ns,
            size,
            XTTS_REFERENCE_SAMPLE_RATE,
        )

    def _get_conditioning_latents(self, ref_audio: str) -> tuple[Any, Any]:
        cache_key = self._conditioning_cache_key(ref_audio)
        cache = getattr(self, "_conditioning_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._conditioning_cache = cache
        if cache_key in cache:
            cache.move_to_end(cache_key)
            entry = cache[cache_key]
            return entry.gpt_cond_latent, entry.speaker_embedding

        core_model = self._xtts_core_model()
        if core_model is None:
            raise RuntimeError("Loaded Coqui XTTS model does not expose optimized inference APIs")

        gpt_cond_latent, speaker_embedding = core_model.get_conditioning_latents(
            audio_path=str(ref_audio),
            max_ref_length=int(XTTS_REFERENCE_MAX_DURATION_SECONDS),
            gpt_cond_len=6,
            gpt_cond_chunk_len=6,
            sound_norm_refs=False,
            load_sr=22050,
        )

        max_items = int(getattr(self, "_conditioning_cache_size", XTTS_CONDITIONING_CACHE_MAX_ITEMS))
        if max_items > 0:
            cache[cache_key] = _XTTSConditioningCacheEntry(
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
            )
            cache.move_to_end(cache_key)
            while len(cache) > max_items:
                cache.popitem(last=False)
        return gpt_cond_latent, speaker_embedding

    def _synthesize_with_cached_conditioning(
        self,
        *,
        text: str,
        ref_audio: str,
        language: str,
        speed: float,
        volume: float,
    ) -> bytes:
        core_model = self._xtts_core_model()
        if core_model is None:
            raise RuntimeError("Loaded Coqui XTTS model does not expose optimized inference APIs")
        gpt_cond_latent, speaker_embedding = self._get_conditioning_latents(ref_audio)
        chunks = self._xtts_text_chunks(text, language, core_model)
        sample_rate = int(
            getattr(getattr(getattr(core_model, "config", None), "audio", None), "output_sample_rate", 0)
            or XTTS_OUTPUT_SAMPLE_RATE
        )
        generated: list[np.ndarray] = []
        pause = np.zeros(
            int(sample_rate * XTTS_OPTIMIZED_SPLIT_PAUSE_SECONDS),
            dtype=np.float32,
        )
        if len(chunks) > 1:
            logger.info("XTTS optimized path split text into %d chunk(s)", len(chunks))
        for index, chunk in enumerate(chunks):
            chunk_samples = self._run_xtts_core_inference(
                core_model=core_model,
                text=chunk,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                speed=speed,
            )
            if chunk_samples.size:
                if generated and pause.size:
                    generated.append(pause)
                generated.append(chunk_samples)
            logger.debug(
                "XTTS optimized chunk %d/%d generated (%d samples)",
                index + 1,
                len(chunks),
                chunk_samples.size,
            )
        samples = np.concatenate(generated) if generated else np.zeros(0, dtype=np.float32)
        if volume != 1.0:
            samples = np.clip(samples * max(0.0, float(volume)), -1.0, 1.0)
        return encode_pcm16_wav(samples, sample_rate)

    def _synthesize_with_coqui_api_no_spacy(
        self,
        *,
        text: str,
        ref_audio: str,
        language: str,
        speed: float,
        volume: float,
    ) -> bytes:
        chunks = self._xtts_text_chunks(text, language, self._xtts_core_model())
        sample_rate = XTTS_OUTPUT_SAMPLE_RATE
        generated: list[np.ndarray] = []
        pause = np.zeros(
            int(sample_rate * XTTS_OPTIMIZED_SPLIT_PAUSE_SECONDS),
            dtype=np.float32,
        )
        tmp_paths: list[str] = []
        try:
            if len(chunks) > 1:
                logger.info("XTTS Coqui API path split text into %d chunk(s)", len(chunks))
            for index, chunk in enumerate(chunks or [text]):
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                tmp_paths.append(tmp_path)
                kwargs = dict(getattr(self, "_inference_kwargs", {}))
                kwargs["speed"] = speed
                kwargs["split_sentences"] = False
                self._model.tts_to_file(
                    text=chunk,
                    speaker_wav=ref_audio,
                    language=language,
                    file_path=tmp_path,
                    **kwargs,
                )
                chunk_samples, chunk_rate = decode_wav_bytes(Path(tmp_path).read_bytes())
                if chunk_samples.ndim > 1 and chunk_samples.size:
                    chunk_samples = chunk_samples.mean(axis=1)
                if chunk_rate != sample_rate:
                    chunk_samples = _resample_audio(chunk_samples, chunk_rate, sample_rate)
                if generated and pause.size:
                    generated.append(pause)
                generated.append(np.asarray(chunk_samples, dtype=np.float32).reshape(-1))
                logger.debug(
                    "XTTS Coqui API chunk %d/%d generated (%d samples)",
                    index + 1,
                    len(chunks),
                    int(np.asarray(chunk_samples).size),
                )
        finally:
            for tmp_path in tmp_paths:
                try:
                    os.unlink(tmp_path)
                except Exception as exc:
                    logger.debug("Failed to delete temp file %s: %s", tmp_path, exc)
        samples = np.concatenate(generated) if generated else np.zeros(0, dtype=np.float32)
        if volume != 1.0:
            samples = np.clip(samples * max(0.0, float(volume)), -1.0, 1.0)
        return encode_pcm16_wav(samples, sample_rate)

    def _run_xtts_core_inference(
        self,
        *,
        core_model: Any,
        text: str,
        language: str,
        gpt_cond_latent: Any,
        speaker_embedding: Any,
        speed: float,
    ) -> np.ndarray:
        kwargs = dict(getattr(self, "_inference_kwargs", {}))
        kwargs["speed"] = speed
        kwargs["enable_text_splitting"] = False
        try:
            result = core_model.inference(
                text=text,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                **kwargs,
            )
        except TypeError as exc:
            if "enable_text_splitting" not in str(exc):
                raise
            kwargs.pop("enable_text_splitting", None)
            result = core_model.inference(
                text=text,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                **kwargs,
            )
        wav = result["wav"] if isinstance(result, dict) and "wav" in result else result
        samples = np.asarray(wav, dtype=np.float32)
        if samples.ndim > 1:
            samples = np.asarray(samples).reshape(-1)
        return np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0).astype(np.float32, copy=False)

    def _xtts_text_chunks(self, text: str, language: str, core_model: Any) -> list[str]:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return []
        if not bool(getattr(self, "_enable_text_splitting", True)):
            return [normalized]

        limit = self._xtts_text_limit(language, core_model)
        sentences = self._xtts_sentence_candidates(normalized)
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            parts = (
                [sentence]
                if len(sentence) <= limit
                else self._split_xtts_long_text(sentence, limit)
            )
            for part in parts:
                if not part:
                    continue
                separator = " " if current and self._xtts_join_with_space(current, part) else ""
                if current and len(current) + len(separator) + len(part) <= limit:
                    current = f"{current}{separator}{part}"
                else:
                    if current:
                        chunks.append(current)
                    current = part
        if current:
            chunks.append(current)
        return chunks or [normalized]

    def _xtts_text_limit(self, language: str, core_model: Any) -> int:
        tokenizer = getattr(core_model, "tokenizer", None)
        char_limits = getattr(tokenizer, "char_limits", None)
        language_keys = (language, str(language).split("-", 1)[0])
        if isinstance(char_limits, dict):
            for key in language_keys:
                try:
                    limit = int(char_limits.get(key, 0))
                except (TypeError, ValueError):
                    limit = 0
                if limit > 0:
                    return max(40, limit - 1)
        normalized_language = str(language or "").lower()
        return XTTS_LANGUAGE_TEXT_LIMITS.get(
            normalized_language,
            XTTS_LANGUAGE_TEXT_LIMITS.get(normalized_language.split("-", 1)[0], 180),
        )

    @staticmethod
    def _xtts_sentence_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        current: list[str] = []
        for char in text:
            current.append(char)
            if char in _XTTS_SENTENCE_END_CHARS:
                item = "".join(current).strip()
                if item:
                    candidates.append(item)
                current = []
        item = "".join(current).strip()
        if item:
            candidates.append(item)
        return candidates

    @staticmethod
    def _split_xtts_long_text(text: str, limit: int) -> list[str]:
        remaining = str(text or "").strip()
        chunks: list[str] = []
        while len(remaining) > limit:
            split_at = -1
            search_start = max(1, int(limit * 0.55))
            for index in range(limit, search_start, -1):
                if remaining[index - 1].isspace() or remaining[index - 1] in _XTTS_SOFT_BREAK_CHARS:
                    split_at = index
                    break
            if split_at <= 0:
                split_at = limit
            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    @staticmethod
    def _xtts_join_with_space(left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left[-1].isspace() or right[0].isspace():
            return False
        return ord(left[-1]) < 128 or ord(right[0]) < 128

    def set_language(self, language: str) -> None:
        """Set the speaking language for XTTS synthesis."""
        normalized_language = normalize_xtts_language_code(language)
        if normalized_language == "auto" or normalized_language in self._supported_languages:
            self._language = normalized_language
            logger.info("XTTS language set to: %s", normalized_language)
        else:
            logger.warning("Unsupported XTTS language '%s', keeping '%s'", language, self._language)

    def get_language(self) -> str:
        """Get current language setting."""
        return self._language

    def get_supported_languages(self) -> list[str]:
        """Get list of supported languages."""
        return ["auto"] + self._supported_languages

    def _detect_language(self, text: str) -> str:
        """Detect language from broad Unicode ranges."""
        lowered = str(text or "").lower()
        latin_hints = (
            ("es", ("ñ", "¿", "¡")),
            ("fr", ("ç", "œ", "æ")),
            ("de", ("ß",)),
            ("pt", ("ã", "õ")),
            ("tr", ("ğ", "ı", "İ", "ş")),
            ("cs", ("č", "ď", "ě", "ř", "ť", "ů")),
            ("pl", ("ą", "ę", "ł", "ń", "ś", "ź", "ż")),
            ("hu", ("ő", "ű")),
        )
        for c in text.strip():
            codepoint = ord(c)
            if 0x4E00 <= codepoint <= 0x9FFF:
                return "zh-cn"
            if 0x3040 <= codepoint <= 0x309F or 0x30A0 <= codepoint <= 0x30FF:
                return "ja"
            if 0xAC00 <= codepoint <= 0xD7AF:
                return "ko"
            if 0x0900 <= codepoint <= 0x097F:
                return "hi"
            if 0x0400 <= codepoint <= 0x04FF:
                return "ru"
            if 0x0600 <= codepoint <= 0x06FF:
                return "ar"
        for language, markers in latin_hints:
            if any(marker.lower() in lowered for marker in markers):
                return language
        return "en"

    def _apply_volume(self, audio_data: bytes, volume: float) -> bytes:
        """Apply volume adjustment to WAV audio data."""
        if volume == 1.0:
            return audio_data

        try:
            return scale_wav_volume_pcm16(audio_data, volume)
        except Exception as exc:
            logger.warning("Failed to apply volume adjustment: %s", exc)
            return audio_data

    @staticmethod
    def _normalize_speed(rate: object) -> float:
        try:
            speed = float(rate)
        except (TypeError, ValueError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    def save_reference_audio(self, audio_data: bytes, voice_name: str) -> bool:
        """Save reference audio for future use."""
        try:
            ref_audio_dir = xtts_reference_audio_dir()
            ref_audio_dir.mkdir(parents=True, exist_ok=True)
            output_path = ref_audio_dir / f"{_safe_voice_name(voice_name)}.wav"
            tmp_path = output_path.with_suffix(".tmp.wav")
            with open(tmp_path, "wb") as f:
                f.write(audio_data)
            try:
                normalize_xtts_reference_audio_file(tmp_path, output_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            logger.info("Saved reference audio: %s", output_path)
            return True
        except Exception as exc:
            logger.error("Failed to save reference audio: %s", exc)
            return False

    def get_model_info(self) -> dict:
        """Get information about the loaded model."""
        if not self.is_available():
            return {"available": False}
        return {
            "available": True,
            "model_name": self._model_name,
            "device": self._device,
            "requested_device": self._requested_device,
            "runtime_device": self._runtime_device,
            "model_loaded": self._model is not None,
            "lazy_load": self._lazy_load,
            "optimized_inference": self._optimized_inference,
            "conditioning_cache_items": len(self._conditioning_cache),
            "supported_languages": self._supported_languages,
            "has_reference_audio": self._reference_audio_path is not None,
            "reference_audio": self._reference_audio_path,
        }
