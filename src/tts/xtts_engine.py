"""XTTS-v2 TTS engine with voice cloning support."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 銇撱亾_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import importlib
import importlib.util
import contextlib
import gc
import logging
import os
import re
import sys
import threading
import tempfile
import types
import warnings
import wave
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from importlib.machinery import ModuleSpec

import numpy as np

from .base import BaseTTS, TTSVoice
from .wav_utils import decode_wav_bytes, encode_pcm16_wav, scale_wav_volume_pcm16
from .xtts_downloader import xtts_coqui_model_kwargs
from src.utils.app_paths import (
    app_temp_dir,
    atomic_write_bytes,
    open_secure_read,
    read_secure_bytes,
    require_existing_real_directory,
    require_real_directory,
    secure_file_path,
    secure_file_size,
    secure_unlink,
    writable_app_dir,
)
from src.utils.gpu_support import (
    TorchCudaRuntimeStatus,
    choose_torch_precision,
    clear_torch_cuda_cache,
    inspect_torch_cuda_runtime,
    normalize_torch_precision,
)

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
XTTS_REFERENCE_MAX_FILE_BYTES = 256 * 1024 * 1024
_XTTS_REFERENCE_DECODE_MAX_SECONDS = 5 * 60
_XTTS_SYNTHESIS_MAX_FILE_BYTES = 256 * 1024 * 1024
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
XTTS_CUDA_PRECISIONS = ("auto", "float32", "float16", "bfloat16")
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
    "sklearn": "scikit-learn runtime",
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
_REQUIRED_TRANSFORMERS_XTTS_EXPORTS = (
    "GenerationMixin",
    "GenerationConfig",
    "LogitsProcessorList",
    "PreTrainedModel",
    "StoppingCriteriaList",
    "GPT2Config",
    "GPT2Model",
    "GPT2PreTrainedModel",
)
_TRANSFORMERS_XTTS_EXPORT_IMPORTS = {
    "GenerationMixin": ("transformers.generation", "GenerationMixin"),
    "GenerationConfig": (
        "transformers.generation.configuration_utils",
        "GenerationConfig",
    ),
    "LogitsProcessorList": (
        "transformers.generation.logits_process",
        "LogitsProcessorList",
    ),
    "PreTrainedModel": ("transformers.modeling_utils", "PreTrainedModel"),
    "StoppingCriteriaList": (
        "transformers.generation.stopping_criteria",
        "StoppingCriteriaList",
    ),
    "GPT2Config": ("transformers.models.gpt2.configuration_gpt2", "GPT2Config"),
    "GPT2Model": ("transformers.models.gpt2.modeling_gpt2", "GPT2Model"),
    "GPT2PreTrainedModel": (
        "transformers.models.gpt2.modeling_gpt2",
        "GPT2PreTrainedModel",
    ),
}

_XTTS_SENTENCE_END_CHARS = {".", "!", "?", "\n", "。", "！", "？"}
_XTTS_SOFT_BREAK_CHARS = {",", ";", ":", "、", "，", "；", "："}

class XTTSAudioDecoderUnavailableError(RuntimeError):
    """Raised when bundled PyAV/FFmpeg support is missing for non-WAV imports."""


class XTTSAudioDecodeError(RuntimeError):
    """Raised when a supported-looking reference file cannot be decoded."""


class _XTTSPCMWavDecodeError(RuntimeError):
    """The secured input was read successfully but is not supported PCM WAV."""


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
        return not self.missing_modules and self.import_error is None

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


def _missing_transformers_xtts_exports(transformers_module: Any) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_TRANSFORMERS_XTTS_EXPORTS:
        try:
            getattr(transformers_module, name)
        except Exception:
            missing.append(name)
    return missing


def _disable_broken_transformers_optional_vision() -> None:
    """Prevent optional torchvision failures from breaking text-only XTTS imports."""
    try:
        import_utils = importlib.import_module("transformers.utils.import_utils")
    except Exception:
        return

    try:
        torchvision_available = bool(import_utils.is_torchvision_available())
    except Exception:
        torchvision_available = bool(getattr(import_utils, "_torchvision_available", False))
    if not torchvision_available:
        return

    try:
        importlib.import_module("torchvision.transforms")
    except Exception as exc:
        try:
            setattr(import_utils, "_torchvision_available", False)
            setattr(import_utils, "_torchvision_version", "unavailable")
        except Exception:
            logger.debug("Could not patch transformers torchvision availability", exc_info=True)
        logger.warning(
            "Disabling broken optional torchvision integration for Voice Cloning: %s",
            exc,
        )


def _install_transformers_xtts_exports(transformers_module: Any) -> dict[str, str]:
    """Restore top-level transformers exports used by Coqui XTTS."""
    errors: dict[str, str] = {}
    for name in _missing_transformers_xtts_exports(transformers_module):
        import_info = _TRANSFORMERS_XTTS_EXPORT_IMPORTS.get(name)
        if import_info is None:
            continue
        module_name, attr_name = import_info
        try:
            source_module = importlib.import_module(module_name)
            value = getattr(source_module, attr_name)
        except Exception as exc:
            errors[name] = f"{module_name}.{attr_name}: {exc}"
            logger.debug(
                "Could not recover transformers export %s from %s",
                name,
                module_name,
                exc_info=True,
            )
            continue
        try:
            setattr(transformers_module, name, value)
        except Exception:
            logger.debug("Could not attach transformers export %s", name, exc_info=True)
    return errors


def _install_transformers_pytorch_utils_compat() -> None:
    """Restore the small helper Coqui imports from Transformers 4.x."""

    try:
        pytorch_utils = importlib.import_module("transformers.pytorch_utils")
        if hasattr(pytorch_utils, "isin_mps_friendly"):
            return
        import torch

        def isin_mps_friendly(elements, test_elements):
            return torch.isin(elements, test_elements)

        pytorch_utils.isin_mps_friendly = isin_mps_friendly
        logger.info(
            "Installed Transformers 5 compatibility shim for Coqui Voice Cloning"
        )
    except Exception:
        logger.debug(
            "Could not install Transformers pytorch_utils compatibility shim",
            exc_info=True,
        )


def _drop_transformers_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "transformers" or module_name.startswith("transformers."):
            sys.modules.pop(module_name, None)


def _install_matplotlib_runtime_stub() -> None:
    """Provide no-op plotting modules for Coqui utilities in slim frozen builds."""
    if "matplotlib" not in sys.modules and _module_available("matplotlib"):
        return
    if "matplotlib" in sys.modules and "matplotlib.pyplot" in sys.modules:
        return

    class _NoOpFigure:
        def colorbar(self, *_args, **_kwargs) -> None:
            return None

        def savefig(self, *_args, **_kwargs) -> None:
            return None

    class _NoOpAxes:
        def imshow(self, *_args, **_kwargs) -> object:
            return object()

        def plot(self, *_args, **_kwargs) -> list[object]:
            return []

        def set_xlabel(self, *_args, **_kwargs) -> None:
            return None

        def set_ylabel(self, *_args, **_kwargs) -> None:
            return None

        def twinx(self) -> "_NoOpAxes":
            return _NoOpAxes()

    def _subplots(*_args, **_kwargs) -> tuple[_NoOpFigure, _NoOpAxes]:
        return _NoOpFigure(), _NoOpAxes()

    def _figure(*_args, **_kwargs) -> _NoOpFigure:
        return _NoOpFigure()

    def _noop(*_args, **_kwargs) -> None:
        return None

    matplotlib_module = types.ModuleType("matplotlib")
    matplotlib_module.__path__ = []  # type: ignore[attr-defined]
    matplotlib_module.__package__ = "matplotlib"
    matplotlib_module.__spec__ = ModuleSpec("matplotlib", loader=None, is_package=True)
    matplotlib_module.use = _noop  # type: ignore[attr-defined]

    pyplot_module = types.ModuleType("matplotlib.pyplot")
    pyplot_module.__package__ = "matplotlib"
    pyplot_module.__spec__ = ModuleSpec("matplotlib.pyplot", loader=None)
    pyplot_module.rcParams = {"figure.figsize": [6.4, 4.8]}  # type: ignore[attr-defined]
    for name in (
        "axis",
        "close",
        "colorbar",
        "imshow",
        "plot",
        "subplot",
        "tight_layout",
        "title",
        "xlabel",
        "xticks",
        "ylabel",
        "yticks",
    ):
        setattr(pyplot_module, name, _noop)
    pyplot_module.figure = _figure  # type: ignore[attr-defined]
    pyplot_module.subplots = _subplots  # type: ignore[attr-defined]

    colors_module = types.ModuleType("matplotlib.colors")
    colors_module.__package__ = "matplotlib"
    colors_module.__spec__ = ModuleSpec("matplotlib.colors", loader=None)

    class LogNorm:  # noqa: N801 - matches matplotlib public API
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    colors_module.LogNorm = LogNorm  # type: ignore[attr-defined]
    matplotlib_module.pyplot = pyplot_module  # type: ignore[attr-defined]
    matplotlib_module.colors = colors_module  # type: ignore[attr-defined]
    sys.modules.setdefault("matplotlib", matplotlib_module)
    sys.modules.setdefault("matplotlib.pyplot", pyplot_module)
    sys.modules.setdefault("matplotlib.colors", colors_module)


def _ensure_transformers_xtts_exports() -> None:
    """Refresh transformers if frozen lazy exports needed by XTTS are incomplete."""
    _disable_broken_transformers_optional_vision()
    try:
        transformers_module = importlib.import_module("transformers")
    except Exception as exc:
        raise RuntimeError(f"Could not import transformers: {exc}") from exc
    _install_transformers_pytorch_utils_compat()

    missing = _missing_transformers_xtts_exports(transformers_module)
    if not missing:
        return

    import_errors = _install_transformers_xtts_exports(transformers_module)
    missing = _missing_transformers_xtts_exports(transformers_module)
    if not missing:
        logger.info("Recovered Voice Cloning transformers exports without reload")
        return

    version = str(getattr(transformers_module, "__version__", "unknown"))
    logger.warning(
        "Transformers %s is missing Voice Cloning exports %s; refreshing import cache",
        version,
        ", ".join(missing),
    )
    importlib.invalidate_caches()
    _drop_transformers_modules()
    _disable_broken_transformers_optional_vision()

    try:
        refreshed = importlib.import_module("transformers")
    except Exception as exc:
        raise RuntimeError(f"Could not re-import transformers after refresh: {exc}") from exc

    import_errors.update(_install_transformers_xtts_exports(refreshed))
    missing = _missing_transformers_xtts_exports(refreshed)
    if missing:
        version = str(getattr(refreshed, "__version__", version))
        details = "; ".join(import_errors.get(name, "") for name in missing)
        details = f" ({details})" if details else ""
        raise RuntimeError(
            "The bundled transformers package "
            f"({version}) is missing exports required by Voice Cloning: "
            + ", ".join(missing)
            + details
        )


def xtts_packaging_selftest() -> tuple[bool, str]:
    """Exercise XTTS imports that PyInstaller can otherwise miss."""
    try:
        _install_matplotlib_runtime_stub()
        _ensure_transformers_xtts_exports()
        importlib.import_module("TTS.tts.layers.xtts.gpt_inference")
        importlib.import_module("TTS.tts.layers.xtts.gpt")
        importlib.import_module("TTS.tts.models.xtts")
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__
    return True, "Voice Cloning packaged XTTS imports are available."


def _load_xtts_api() -> Any | None:
    """Load Coqui TTS lazily so importing the app does not probe torch/runtime DLLs."""
    global XTTS_API_CLASS, XTTS_IMPORT_ERROR, XTTS_AVAILABLE
    if XTTS_API_CLASS is not None:
        return XTTS_API_CLASS
    try:
        _install_matplotlib_runtime_stub()
        _ensure_transformers_xtts_exports()
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
    return require_real_directory(
        writable_app_dir() / "tts_models" / "xtts" / "reference_audio"
    )


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
    target = secure_file_path(path)
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    atomic_write_bytes(target, encode_pcm16_wav(pcm, sample_rate))


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    payload = read_secure_bytes(path, max_bytes=XTTS_REFERENCE_MAX_FILE_BYTES)
    try:
        audio, sample_rate = decode_wav_bytes(payload)
    except Exception as exc:
        raise _XTTSPCMWavDecodeError(str(exc) or "Unsupported PCM WAV data") from exc
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
    try:
        path = secure_file_path(path, must_exist=True)
        audio, sample_rate = _read_pcm_wav(path)
        stats = analyze_xtts_reference_audio(audio, sample_rate)
    except FileNotFoundError:
        return False, f"Reference audio file not found: {path}", None
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
    source = secure_file_path(path, must_exist=True)
    if secure_file_size(source) > XTTS_REFERENCE_MAX_FILE_BYTES:
        raise ValueError("Reference audio exceeds the 256 MiB safety limit.")

    try:
        import av
        from av.audio.resampler import AudioResampler
    except Exception as exc:
        raise XTTSAudioDecoderUnavailableError(
            "PyAV/FFmpeg is required to import MP3, M4A, FLAC, OGG, Opus, WebM, AAC, or WMA "
            "Voice Cloning reference audio."
        ) from exc

    chunks: list[np.ndarray] = []
    decoded_samples = 0
    max_decoded_samples = max(
        1,
        int(target_sample_rate * _XTTS_REFERENCE_DECODE_MAX_SECONDS),
    )
    try:
        with open_secure_read(source, binary=True) as source_handle:
            with av.open(source_handle) as container:
                stream = next(
                    (item for item in container.streams if item.type == "audio"),
                    None,
                )
                if stream is None:
                    raise XTTSAudioDecodeError(
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
    except XTTSAudioDecodeError:
        raise
    except Exception as exc:
        raise XTTSAudioDecodeError(
            str(exc) or f"Could not decode {source.name}"
        ) from exc

    if not chunks:
        raise XTTSAudioDecodeError(
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


def normalize_xtts_reference_audio_file(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    sample_rate: int = XTTS_REFERENCE_SAMPLE_RATE,
) -> Path:
    """Convert an imported reference clip to a stable mono PCM WAV file."""
    src = secure_file_path(source_path, must_exist=True)
    if secure_file_size(src) > XTTS_REFERENCE_MAX_FILE_BYTES:
        raise ValueError("Reference audio exceeds the 256 MiB safety limit.")
    dest = secure_file_path(output_path)

    try:
        audio, source_rate = _read_pcm_wav(src)
    except _XTTSPCMWavDecodeError:
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
        normalize_xtts_reference_audio_file(path, tmp_path)
        tmp_stat = os.lstat(tmp_path)
        usable, reason, stats = validate_xtts_reference_audio_file(tmp_path)
        if not usable:
            return False, reason, stats
        repaired_audio = read_secure_bytes(
            tmp_path,
            max_bytes=XTTS_REFERENCE_MAX_FILE_BYTES,
        )
        atomic_write_bytes(path, repaired_audio)
        logger.info("Repaired XTTS reference audio by normalizing gain: %s", path)
        return True, "", stats
    except Exception as exc:
        return False, str(exc), None
    finally:
        _cleanup_private_temp_file(
            tmp_path,
            tmp_stat,
            purpose="XTTS repair",
        )



def _safe_reference_audio_files(ref_audio_dir: Path | None = None) -> list[Path]:
    directory = ref_audio_dir or xtts_reference_audio_dir()
    try:
        directory = require_existing_real_directory(directory)
    except (OSError, RuntimeError, ValueError):
        if os.path.lexists(directory):
            logger.warning(
                "Refusing unsafe XTTS reference audio directory: %s",
                directory,
            )
        return []
    files: list[Path] = []
    for candidate in sorted(
        directory.glob("*.wav"),
        key=lambda path: path.stem.casefold(),
    ):
        try:
            files.append(secure_file_path(candidate, must_exist=True))
        except (OSError, RuntimeError, ValueError):
            logger.warning("Skipping unsafe XTTS reference audio path: %s", candidate)
    return files


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

    for audio_file in _safe_reference_audio_files(ref_audio_dir):
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
    for audio_file in _safe_reference_audio_files(ref_audio_dir):
        try:
            if secure_file_size(audio_file) > 0:
                return audio_file
        except (OSError, RuntimeError, ValueError):
            logger.warning("Skipping changed XTTS reference audio path: %s", audio_file)
    return None


def first_usable_xtts_reference_audio_path(ref_audio_dir: Path | None = None) -> Path | None:
    """Return the first saved XTTS reference WAV that passes quality checks."""
    for audio_file in _safe_reference_audio_files(ref_audio_dir):
        try:
            if secure_file_size(audio_file) <= 0:
                continue
        except (OSError, RuntimeError, ValueError):
            logger.warning("Skipping changed XTTS reference audio path: %s", audio_file)
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
        precision: str = "auto",
        cuda_device_index: int = 0,
        allow_cpu_fallback: bool = True,
        cuda_tf32: bool = True,
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
        self._torch_device = "cpu"
        self._requested_precision = normalize_torch_precision(precision)
        self._runtime_precision = "float32"
        try:
            self._cuda_device_index = max(0, min(int(cuda_device_index), 15))
        except (TypeError, ValueError):
            self._cuda_device_index = 0
        self._allow_cpu_fallback = bool(allow_cpu_fallback)
        self._cuda_tf32 = bool(cuda_tf32)
        self._cuda_status: TorchCudaRuntimeStatus | None = None
        self._cuda_fallback_reason = ""
        self._precision_fallback_reason = ""
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

    def _initialize_model(self, *, force_device: str | None = None) -> None:
        """Initialize XTTS with explicit runtime planning and safe CPU fallback."""

        if self._model is not None:
            return
        requested = str(force_device or self._requested_device or "cpu").lower()
        use_gpu = False
        runtime_device = "cpu"
        torch_device = "cpu"
        runtime_precision = "float32"
        cuda_status: TorchCudaRuntimeStatus | None = None

        if requested == "cuda":
            cuda_status = inspect_torch_cuda_runtime(
                getattr(self, "_cuda_device_index", 0)
            )
            self._cuda_status = cuda_status
            if cuda_status.ready:
                use_gpu = True
                runtime_device = "cuda"
                torch_device = f"cuda:{cuda_status.device_index}"
                runtime_precision = choose_torch_precision(
                    getattr(self, "_requested_precision", "auto"),
                    cuda_status,
                )
            else:
                reason = cuda_status.detail or "CUDA runtime is unavailable"
                self._cuda_fallback_reason = reason
                if not getattr(self, "_allow_cpu_fallback", True):
                    raise RuntimeError(reason)
                logger.warning(
                    "CUDA requested for XTTS but unavailable; using CPU: %s",
                    reason,
                )

        self._runtime_device = runtime_device
        self._torch_device = torch_device
        self._runtime_precision = runtime_precision
        self._device = runtime_device
        logger.info(
            "Loading XTTS-v2 requested=%s runtime=%s torch_device=%s precision=%s "
            "torch=%s cuda_build=%s gpu=%s",
            self._requested_device,
            runtime_device,
            torch_device,
            runtime_precision,
            cuda_status.torch_version if cuda_status else "",
            cuda_status.cuda_build if cuda_status else "",
            cuda_status.device_name if cuda_status else "",
        )

        api_class = _load_xtts_api()
        if api_class is None:
            raise RuntimeError(XTTS_IMPORT_ERROR or "Coqui TTS is not installed")

        try:
            model = api_class(**self._coqui_model_kwargs(use_gpu=use_gpu))
            self._model = model
            self._configure_loaded_model_runtime()
            logger.info(
                "XTTS-v2 model loaded successfully (runtime=%s precision=%s)",
                self._runtime_device,
                self._runtime_precision,
            )
        except Exception as exc:
            self._model = None
            if (
                use_gpu
                and getattr(self, "_allow_cpu_fallback", True)
                and self._is_cuda_runtime_failure(exc)
            ):
                self._cuda_fallback_reason = str(exc) or exc.__class__.__name__
                logger.warning(
                    "XTTS CUDA model initialization failed; retrying on CPU: %s",
                    exc,
                )
                clear_torch_cuda_cache()
                gc.collect()
                self._initialize_model(force_device="cpu")
                return
            logger.error("Failed to load XTTS-v2 model: %s", exc)
            raise

    def _ensure_model_loaded(self) -> None:
        """Load the heavy XTTS model on demand."""
        model_lock = getattr(self, "_model_lock", None)
        if model_lock is None:
            model_lock = threading.RLock()
            self._model_lock = model_lock
        with model_lock:
            if self._model is not None:
                return
            self._initialize_model()

    @staticmethod
    def _is_cuda_runtime_failure(error: BaseException) -> bool:
        name = error.__class__.__name__.lower()
        message = str(error or "").lower()
        markers = (
            "cuda",
            "cudnn",
            "cublas",
            "cusolver",
            "device-side",
            "gpu",
            "out of memory",
            "no kernel image",
            "driver version",
        )
        return "outofmemory" in name or any(marker in message for marker in markers)

    def _configure_loaded_model_runtime(self) -> None:
        if getattr(self, "_runtime_device", "cpu") != "cuda":
            return
        try:
            import torch

            index = int(getattr(self, "_cuda_device_index", 0))
            torch.cuda.set_device(index)
            if getattr(self, "_cuda_tf32", True):
                cuda_backends = getattr(getattr(torch, "backends", None), "cuda", None)
                matmul = getattr(cuda_backends, "matmul", None)
                if matmul is not None:
                    matmul.allow_tf32 = True
                cudnn = getattr(getattr(torch, "backends", None), "cudnn", None)
                if cudnn is not None:
                    cudnn.allow_tf32 = True
                    cudnn.benchmark = True
                set_precision = getattr(torch, "set_float32_matmul_precision", None)
                if callable(set_precision):
                    set_precision("high")
        except Exception as exc:
            raise RuntimeError(f"Could not initialize the CUDA execution context: {exc}") from exc

        target = str(getattr(self, "_torch_device", "cuda:0"))
        moved_ids: set[int] = set()
        candidates = (
            getattr(self, "_model", None),
            getattr(getattr(self, "_model", None), "synthesizer", None),
            self._xtts_core_model(),
        )
        for candidate in candidates:
            if candidate is None or id(candidate) in moved_ids:
                continue
            moved_ids.add(id(candidate))
            move = getattr(candidate, "to", None)
            if callable(move):
                try:
                    move(target)
                except TypeError:
                    move(device=target)
            evaluate = getattr(candidate, "eval", None)
            if callable(evaluate):
                evaluate()

        actual_device = self._core_model_device()
        if actual_device and not actual_device.startswith("cuda"):
            raise RuntimeError(
                f"XTTS model placement verification failed: expected {target}, got {actual_device}"
            )

    def _core_model_device(self) -> str:
        core_model = self._xtts_core_model()
        if core_model is None:
            return ""
        direct = getattr(core_model, "device", None)
        if direct is not None:
            text = str(direct)
            if text:
                return text
        parameters = getattr(core_model, "parameters", None)
        if callable(parameters):
            try:
                parameter = next(iter(parameters()))
                return str(getattr(parameter, "device", "") or "")
            except (StopIteration, TypeError):
                return ""
            except Exception:
                return ""
        return ""

    def _torch_execution_context(self, *, mixed_precision: bool) -> contextlib.AbstractContextManager:
        try:
            import torch
        except Exception:
            return contextlib.nullcontext()

        stack = contextlib.ExitStack()
        inference_mode = getattr(torch, "inference_mode", None)
        if callable(inference_mode):
            stack.enter_context(inference_mode())
        if (
            mixed_precision
            and getattr(self, "_runtime_device", "cpu") == "cuda"
            and getattr(self, "_runtime_precision", "float32") != "float32"
        ):
            dtype_name = str(self._runtime_precision)
            dtype = getattr(
                torch,
                "bfloat16" if dtype_name == "bfloat16" else "float16",
                None,
            )
            autocast = getattr(torch, "autocast", None)
            if callable(autocast) and dtype is not None:
                stack.enter_context(
                    autocast(device_type="cuda", dtype=dtype, enabled=True)
                )
        return stack

    def _move_tensor_to_runtime(self, value: Any) -> Any:
        if getattr(self, "_runtime_device", "cpu") != "cuda":
            return value
        move = getattr(value, "to", None)
        if not callable(move):
            return value
        target = str(getattr(self, "_torch_device", "cuda:0"))
        try:
            return move(target, non_blocking=True)
        except TypeError:
            return move(target)

    @staticmethod
    def _samples_to_numpy(value: Any) -> np.ndarray:
        tensor = value
        detach = getattr(tensor, "detach", None)
        if callable(detach):
            tensor = detach()
        float_method = getattr(tensor, "float", None)
        if callable(float_method):
            tensor = float_method()
        cpu = getattr(tensor, "cpu", None)
        if callable(cpu):
            tensor = cpu()
        numpy_method = getattr(tensor, "numpy", None)
        if callable(numpy_method):
            tensor = numpy_method()
        return np.asarray(tensor, dtype=np.float32)

    def _switch_to_cpu_after_cuda_failure(self, error: BaseException) -> None:
        model_lock = getattr(self, "_model_lock", None)
        if model_lock is None:
            model_lock = threading.RLock()
            self._model_lock = model_lock
        with model_lock:
            if getattr(self, "_runtime_device", "cpu") != "cuda":
                return
            self._cuda_fallback_reason = str(error) or error.__class__.__name__
            self._model = None
            cache = getattr(self, "_conditioning_cache", None)
            if cache is not None:
                cache.clear()
            clear_torch_cuda_cache()
            gc.collect()
            self._initialize_model(force_device="cpu")

    def _coqui_model_kwargs(self, *, use_gpu: bool) -> dict[str, object]:
        """Build Coqui API kwargs, preferring the managed local XTTS model.

        Coqui's legacy ``gpu`` constructor flag is deprecated and cannot
        select a specific CUDA device.  Models are intentionally loaded on
        CPU first and then moved to the validated ``cuda:N`` target by
        ``_configure_loaded_model_runtime``.
        """
        local_kwargs = xtts_coqui_model_kwargs()
        if local_kwargs is not None:
            return dict(local_kwargs)

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
                }
            return {
                "model_name": configured,
                "progress_bar": False,
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

    def _resolve_reference_audio(self, voice: str) -> str:
        ref_audio = self._reference_audio_path
        selected_voice_path = False
        voice_id = str(voice or "").strip()
        if voice_id and voice_id != "custom":
            safe_voice_id = _safe_voice_name(voice_id)
            if safe_voice_id == voice_id:
                voice_file = secure_file_path(
                    xtts_reference_audio_dir() / f"{safe_voice_id}.wav"
                )
                if voice_file.exists():
                    ref_audio = str(voice_file)
                    selected_voice_path = True
            else:
                logger.warning("Ignoring unsafe XTTS voice identifier: %r", voice_id)
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
        return ref_audio

    def prewarm(self, voice: str = "") -> None:
        """Load XTTS and cache voice conditioning before the first utterance."""

        if not self._can_synthesize():
            return
        ref_audio = self._resolve_reference_audio(voice)
        model_lock = getattr(self, "_model_lock", None)
        if model_lock is None:
            model_lock = threading.RLock()
            self._model_lock = model_lock
        with model_lock:
            self._ensure_model_loaded()
            if getattr(self, "_optimized_inference", False):
                self._get_conditioning_latents(ref_audio)
        logger.info("XTTS-v2 prewarm completed (ref_audio=%s)", Path(ref_audio).name)

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

        ref_audio = self._resolve_reference_audio(voice)

        language = self._detect_language(text) if self._language == "auto" else self._language
        self._ensure_model_loaded()
        logger.info(
            "Synthesizing with XTTS-v2: text_len=%d, lang=%s (mode=%s), ref_audio=%s",
            len(text),
            language,
            self._language,
            Path(ref_audio).name,
        )

        try:
            return self._synthesize_loaded(
                text=text,
                ref_audio=ref_audio,
                language=language,
                speed=speed,
                volume=volume,
            )
        except Exception as exc:
            if (
                getattr(self, "_runtime_device", "cpu") == "cuda"
                and getattr(self, "_allow_cpu_fallback", True)
                and self._is_cuda_runtime_failure(exc)
            ):
                logger.warning(
                    "XTTS CUDA synthesis failed; reloading on CPU and retrying once: %s",
                    exc,
                )
                self._switch_to_cpu_after_cuda_failure(exc)
                return self._synthesize_loaded(
                    text=text,
                    ref_audio=ref_audio,
                    language=language,
                    speed=speed,
                    volume=volume,
                )
            logger.error("XTTS-v2 synthesis failed: %s", exc)
            raise RuntimeError(f"XTTS-v2 synthesis failed: {exc}") from exc

    def _synthesize_loaded(
        self,
        *,
        text: str,
        ref_audio: str,
        language: str,
        speed: float,
        volume: float,
    ) -> bytes:

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
                if (
                    getattr(self, "_runtime_device", "cpu") == "cuda"
                    and self._is_cuda_runtime_failure(exc)
                ):
                    raise
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
            raise RuntimeError(f"XTTS Coqui API synthesis failed: {exc}") from exc

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
            str(getattr(self, "_torch_device", "cpu")),
            str(getattr(self, "_runtime_precision", "float32")),
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

        with self._torch_execution_context(mixed_precision=False):
            gpt_cond_latent, speaker_embedding = core_model.get_conditioning_latents(
                audio_path=str(ref_audio),
                max_ref_length=int(XTTS_REFERENCE_MAX_DURATION_SECONDS),
                gpt_cond_len=6,
                gpt_cond_chunk_len=6,
                sound_norm_refs=False,
                load_sr=22050,
            )
        gpt_cond_latent = self._move_tensor_to_runtime(gpt_cond_latent)
        speaker_embedding = self._move_tensor_to_runtime(speaker_embedding)

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
        temp_files: list[tuple[Path, os.stat_result]] = []
        synthesis_temp_dir = require_real_directory(
            app_temp_dir() / "xtts_synthesis"
        )
        try:
            if len(chunks) > 1:
                logger.info("XTTS Coqui API path split text into %d chunk(s)", len(chunks))
            for index, chunk in enumerate(chunks or [text]):
                tmp_path, original_stat = _allocate_private_temp_file(
                    synthesis_temp_dir,
                    prefix=".xtts-output.",
                    suffix=".wav",
                )
                temp_files.append((tmp_path, original_stat))
                kwargs = dict(getattr(self, "_inference_kwargs", {}))
                kwargs["speed"] = speed
                kwargs["split_sentences"] = False
                self._model.tts_to_file(
                    text=chunk,
                    speaker_wav=ref_audio,
                    language=language,
                    file_path=str(tmp_path),
                    **kwargs,
                )
                generated_path = secure_file_path(tmp_path, must_exist=True)
                generated_stat = os.lstat(generated_path)
                if not os.path.samestat(original_stat, generated_stat):
                    raise RuntimeError(
                        "XTTS synthesis output path was replaced during generation."
                    )
                chunk_payload = read_secure_bytes(
                    generated_path,
                    max_bytes=_XTTS_SYNTHESIS_MAX_FILE_BYTES,
                )
                generated_stat = os.lstat(generated_path)
                temp_files[-1] = (generated_path, generated_stat)
                chunk_samples, chunk_rate = decode_wav_bytes(chunk_payload)
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
            for tmp_path, expected_stat in temp_files:
                _cleanup_private_temp_file(
                    tmp_path,
                    expected_stat,
                    purpose="XTTS synthesis",
                )
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
        def run_inference() -> Any:
            try:
                return core_model.inference(
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
                return core_model.inference(
                    text=text,
                    language=language,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                    **kwargs,
                )

        try:
            with self._torch_execution_context(mixed_precision=True):
                result = run_inference()
        except Exception as exc:
            if (
                getattr(self, "_runtime_device", "cpu") == "cuda"
                and getattr(self, "_runtime_precision", "float32") != "float32"
                and not self._is_cuda_runtime_failure(exc)
            ):
                previous_precision = str(self._runtime_precision)
                self._runtime_precision = "float32"
                self._precision_fallback_reason = str(exc) or exc.__class__.__name__
                logger.warning(
                    "XTTS %s mixed precision failed; retrying this session in float32: %s",
                    previous_precision,
                    exc,
                )
                with self._torch_execution_context(mixed_precision=False):
                    result = run_inference()
            else:
                raise
        wav = result["wav"] if isinstance(result, dict) and "wav" in result else result
        samples = self._samples_to_numpy(wav)
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
        tmp_path: Path | None = None
        tmp_stat: os.stat_result | None = None
        try:
            if len(audio_data) > XTTS_REFERENCE_MAX_FILE_BYTES:
                raise ValueError("Reference audio exceeds the 256 MiB safety limit.")
            ref_audio_dir = xtts_reference_audio_dir()
            output_path = secure_file_path(
                ref_audio_dir / f"{_safe_voice_name(voice_name)}.wav"
            )
            tmp_path, tmp_stat = _allocate_private_temp_file(
                ref_audio_dir,
                prefix=f".{output_path.stem}.recording.",
                suffix=".wav",
            )
            atomic_write_bytes(tmp_path, audio_data)
            tmp_stat = os.lstat(tmp_path)
            normalize_xtts_reference_audio_file(tmp_path, output_path)
            logger.info("Saved reference audio: %s", output_path)
            return True
        except Exception as exc:
            logger.error("Failed to save reference audio: %s", exc)
            return False
        finally:
            _cleanup_private_temp_file(
                tmp_path,
                tmp_stat,
                purpose="XTTS recording",
            )

    def get_model_info(self) -> dict:
        """Get information about the loaded model."""
        if not self.is_available():
            return {"available": False}
        info = {
            "available": True,
            "model_name": self._model_name,
            "device": self._device,
            "requested_device": self._requested_device,
            "runtime_device": self._runtime_device,
            "torch_device": getattr(self, "_torch_device", "cpu"),
            "requested_precision": getattr(self, "_requested_precision", "auto"),
            "runtime_precision": getattr(self, "_runtime_precision", "float32"),
            "cuda_device_index": getattr(self, "_cuda_device_index", 0),
            "allow_cpu_fallback": getattr(self, "_allow_cpu_fallback", True),
            "cuda_fallback_reason": getattr(self, "_cuda_fallback_reason", ""),
            "precision_fallback_reason": getattr(
                self,
                "_precision_fallback_reason",
                "",
            ),
            "model_loaded": self._model is not None,
            "lazy_load": self._lazy_load,
            "optimized_inference": self._optimized_inference,
            "conditioning_cache_items": len(self._conditioning_cache),
            "supported_languages": self._supported_languages,
            "has_reference_audio": self._reference_audio_path is not None,
            "reference_audio": self._reference_audio_path,
        }
        status = getattr(self, "_cuda_status", None)
        if isinstance(status, TorchCudaRuntimeStatus):
            info["cuda"] = {
                "ready": status.ready,
                "torch_version": status.torch_version,
                "cuda_build": status.cuda_build,
                "cuda_available": status.cuda_available,
                "device_count": status.device_count,
                "device_index": status.device_index,
                "device_name": status.device_name,
                "capability": status.capability,
                "total_memory_bytes": status.total_memory_bytes,
                "bf16_supported": status.bf16_supported,
                "runtime_source": status.runtime_source,
                "detail": status.detail,
            }
        return info
