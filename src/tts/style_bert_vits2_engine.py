"""Style-Bert-VITS2 local custom voice integration."""
from __future__ import annotations

import io
import importlib
import logging
import os
import re
import sys
import types
import wave
from typing import Any

import numpy as np
import torch

from src.asr.hf_model_downloader import model_dir, model_is_complete

from .base import BaseTTS, TTSVoice
from .style_bert_vits2_models import (
    StyleBertVits2ModelError,
    list_imported_style_bert_models,
    parse_style_bert_voice_id,
    style_bert_preset_language,
    style_bert_preset_title,
    style_bert_voice_id,
)

logger = logging.getLogger(__name__)

STYLE_BERT_JP_BERT_MODEL_ID = "ku-nlp/deberta-v2-large-japanese-char-wwm"
STYLE_BERT_EN_BERT_MODEL_ID = "microsoft/deberta-v3-large"
STYLE_BERT_ZH_BERT_MODEL_ID = "hfl/chinese-roberta-wwm-ext-large"
STYLE_BERT_BERT_MODEL_IDS = {
    "jp": STYLE_BERT_JP_BERT_MODEL_ID,
    "en": STYLE_BERT_EN_BERT_MODEL_ID,
    "zh": STYLE_BERT_ZH_BERT_MODEL_ID,
}
STYLE_BERT_LANGUAGE_NAMES = {
    "jp": "Japanese",
    "en": "English",
    "zh": "Chinese",
}
_STYLE_BERT_VOICE_LOCALES = {
    "JP": ("ja", "ja-JP"),
    "EN": ("en", "en-US"),
    "ZH": ("zh", "zh-CN"),
}

_RUNTIME_LOAD_ATTEMPTED = False
_RUNTIME_TTS_MODEL_CLS: Any | None = None
_RUNTIME_IMPORT_ERROR = ""
_STDIO_FALLBACKS: list[Any] = []

_OFFLINE_G2P_COMMON: dict[str, list[str]] = {
    "a": ["AH0"],
    "ai": ["EY1", "AY1"],
    "hello": ["HH", "AH0", "L", "OW1"],
    "hi": ["HH", "AY1"],
    "mio": ["M", "IY1", "OW1"],
    "ok": ["OW1", "K"],
    "okay": ["OW1", "K", "EY1"],
    "test": ["T", "EH1", "S", "T"],
    "vrchat": ["V", "IY1", "AA1", "R", "CH", "AE1", "T"],
}
_OFFLINE_G2P_DIGRAPHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tion", ("SH", "AH0", "N")),
    ("sion", ("ZH", "AH0", "N")),
    ("ch", ("CH",)),
    ("sh", ("SH",)),
    ("th", ("TH",)),
    ("ph", ("F",)),
    ("ng", ("NG",)),
    ("ck", ("K",)),
    ("qu", ("K", "W")),
)
_OFFLINE_G2P_LETTERS: dict[str, tuple[str, ...]] = {
    "a": ("AE1",),
    "b": ("B",),
    "c": ("K",),
    "d": ("D",),
    "e": ("EH1",),
    "f": ("F",),
    "g": ("G",),
    "h": ("HH",),
    "i": ("IH1",),
    "j": ("JH",),
    "k": ("K",),
    "l": ("L",),
    "m": ("M",),
    "n": ("N",),
    "o": ("OW1",),
    "p": ("P",),
    "q": ("K",),
    "r": ("R",),
    "s": ("S",),
    "t": ("T",),
    "u": ("AH1",),
    "v": ("V",),
    "w": ("W",),
    "x": ("K", "S"),
    "y": ("IY1",),
    "z": ("Z",),
}
_OFFLINE_G2P_TOKEN_RE = re.compile(r"[A-Za-z']+|[.,!?;:\-]+")

# Monkey-patch safetensors to convert FP16 to FP32 on CPU
_original_load_safetensors = None

_REQUIRED_TRANSFORMERS_BERT_EXPORTS = (
    "AutoModelForMaskedLM",
    "AutoTokenizer",
    "DebertaV2Model",
    "DebertaV2Tokenizer",
    "PreTrainedModel",
    "PreTrainedTokenizer",
    "PreTrainedTokenizerFast",
)


def _missing_transformers_bert_exports(transformers_module: Any) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_TRANSFORMERS_BERT_EXPORTS:
        try:
            getattr(transformers_module, name)
        except Exception:
            missing.append(name)
    return missing


def _drop_transformers_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "transformers" or module_name.startswith("transformers."):
            sys.modules.pop(module_name, None)


def _ensure_transformers_bert_exports() -> None:
    """Refresh transformers if its lazy top-level exports are incomplete."""
    try:
        transformers_module = importlib.import_module("transformers")
    except Exception as exc:
        raise RuntimeError(f"Could not import transformers: {exc}") from exc

    missing = _missing_transformers_bert_exports(transformers_module)
    if not missing:
        return

    version = str(getattr(transformers_module, "__version__", "unknown"))
    logger.warning(
        "Transformers %s is missing Style-Bert-VITS2 exports %s; refreshing import cache",
        version,
        ", ".join(missing),
    )
    importlib.invalidate_caches()
    _drop_transformers_modules()

    try:
        refreshed = importlib.import_module("transformers")
    except Exception as exc:
        raise RuntimeError(f"Could not re-import transformers after refresh: {exc}") from exc

    missing = _missing_transformers_bert_exports(refreshed)
    if missing:
        version = str(getattr(refreshed, "__version__", version))
        raise RuntimeError(
            "The installed transformers package "
            f"({version}) is missing exports required by Style-Bert-VITS2: "
            + ", ".join(missing)
        )


def _patched_load_safetensors(checkpoint_path, model, for_infer=False):
    """Load safetensors and convert FP16 to FP32 for CPU inference."""
    from safetensors import safe_open
    import torch

    tensors = {}
    iteration = None
    with safe_open(str(checkpoint_path), framework="pt", device="cpu") as f:
        for key in f.keys():
            if key == "iteration":
                iteration = f.get_tensor(key).item()
            tensor = f.get_tensor(key)
            # Convert FP16 to FP32 for CPU
            if tensor.dtype == torch.float16:
                tensor = tensor.float()
            tensors[key] = tensor

    if hasattr(model, "module"):
        result = model.module.load_state_dict(tensors, strict=False)
    else:
        result = model.load_state_dict(tensors, strict=False)

    # Log missing/unexpected keys (simplified from original)
    for key in result.missing_keys:
        if key.startswith("enc_q") and for_infer:
            continue
        logger.warning(f"Missing key: {key}")
    for key in result.unexpected_keys:
        if key == "iteration":
            continue
        logger.warning(f"Unexpected key: {key}")

    if iteration is None:
        logger.info(f"Loaded '{checkpoint_path}'")
    else:
        logger.info(f"Loaded '{checkpoint_path}' (iteration {iteration})")

    return model, iteration


def _encode_wav_bytes(sample_rate: int, audio_array: np.ndarray) -> bytes:
    """Encode PCM audio into a WAV byte stream without scipy."""
    pcm_audio = np.asarray(audio_array)
    if pcm_audio.ndim == 1:
        channels = 1
    elif pcm_audio.ndim == 2:
        channels = int(pcm_audio.shape[1])
        if channels <= 0:
            raise RuntimeError("Audio must have at least one channel")
    else:
        raise RuntimeError("Audio must be 1D or 2D")

    if np.issubdtype(pcm_audio.dtype, np.floating):
        pcm_audio = np.clip(pcm_audio, -1.0, 1.0)
        pcm_audio = np.round(pcm_audio * 32767.0).astype(np.int16)
    elif np.issubdtype(pcm_audio.dtype, np.integer):
        pcm_audio = np.clip(
            pcm_audio,
            np.iinfo(np.int16).min,
            np.iinfo(np.int16).max,
        ).astype(np.int16)
    else:
        pcm_audio = np.asarray(pcm_audio, dtype=np.float32)
        pcm_audio = np.clip(pcm_audio, -1.0, 1.0)
        pcm_audio = np.round(pcm_audio * 32767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(np.ascontiguousarray(pcm_audio).tobytes())
    return buffer.getvalue()


def _ensure_importable_stdio() -> None:
    """Provide safe text sinks for Style-Bert-VITS2 loguru setup in windowed builds."""
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and hasattr(stream, "write"):
            continue
        sink = open(os.devnull, "w", encoding="utf-8")
        _STDIO_FALLBACKS.append(sink)
        setattr(sys, attr, sink)

    stdout_wrapper = sys.modules.get("style_bert_vits2.utils.stdout_wrapper")
    if stdout_wrapper is not None and getattr(stdout_wrapper, "SAFE_STDOUT", None) is None:
        stdout_wrapper.SAFE_STDOUT = sys.stdout


def _nltk_zip_resource_ready(resource_name: str) -> bool:
    try:
        import nltk

        nltk.data.find(resource_name)
        return True
    except Exception:
        return False


def _rough_english_pronunciation(word: str) -> list[str]:
    phones: list[str] = []
    index = 0
    clean = re.sub(r"[^a-z]", "", word.lower())
    while index < len(clean):
        for prefix, replacement in _OFFLINE_G2P_DIGRAPHS:
            if clean.startswith(prefix, index):
                phones.extend(replacement)
                index += len(prefix)
                break
        else:
            phones.extend(_OFFLINE_G2P_LETTERS.get(clean[index], ("AH0",)))
            index += 1
    return phones or ["AH0"]


class _OfflineG2p:
    """Minimal g2p_en replacement that avoids NLTK network downloads."""

    def __call__(self, text: object) -> list[str]:
        result: list[str] = []
        for token in _OFFLINE_G2P_TOKEN_RE.findall(str(text or "")):
            if re.fullmatch(r"[.,!?;:\-]+", token):
                result.append(token[0])
                continue
            clean = token.strip("'").lower()
            if not clean:
                continue
            result.extend(
                _OFFLINE_G2P_COMMON.get(clean)
                or _rough_english_pronunciation(clean)
            )
            result.append(" ")
        if result and result[-1] == " ":
            result.pop()
        return result


def _install_g2p_en_offline_fallback() -> None:
    """Prevent g2p_en from downloading NLTK corpora during SBV2 inference."""
    if "g2p_en" in sys.modules:
        return

    # g2p_en checks specifically for these zip resources at import time.
    # If they are missing, importing the real package calls nltk.download()
    # and can block players behind a slow or unreachable network.
    if (
        _nltk_zip_resource_ready("taggers/averaged_perceptron_tagger.zip")
        and _nltk_zip_resource_ready("corpora/cmudict.zip")
    ):
        return

    fallback_module = types.ModuleType("g2p_en")
    fallback_module.G2p = _OfflineG2p
    fallback_module._MIO_FALLBACK = True
    sys.modules["g2p_en"] = fallback_module
    logger.warning(
        "Using offline g2p_en fallback for Style-Bert-VITS2; "
        "NLTK tagger/cmudict resources were not available locally"
    )


def _maximum_path_fallback(neg_cent: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Pure Python replacement for SBV2's numba-only monotonic alignment helper."""
    device = neg_cent.device
    dtype = neg_cent.dtype
    values = neg_cent.detach().cpu().numpy().astype(np.float32)
    paths = np.zeros(values.shape, dtype=np.int32)
    t_ys = mask.sum(1)[:, 0].detach().cpu().numpy().astype(np.int32)
    t_xs = mask.sum(2)[:, 0].detach().cpu().numpy().astype(np.int32)

    batch = int(paths.shape[0])
    max_neg_val = -1e9
    for index_batch in range(batch):
        path = paths[index_batch]
        value = values[index_batch]
        t_y = int(t_ys[index_batch])
        t_x = int(t_xs[index_batch])
        path_index = t_x - 1

        for y in range(t_y):
            for x in range(max(0, t_x + y - t_y), min(t_x, y + 1)):
                v_cur = max_neg_val if x == y else value[y - 1, x]
                if x == 0:
                    v_prev = 0.0 if y == 0 else max_neg_val
                else:
                    v_prev = value[y - 1, x - 1]
                value[y, x] += max(v_prev, v_cur)

        for y in range(t_y - 1, -1, -1):
            if path_index < 0:
                break
            path[y, path_index] = 1
            if path_index != 0 and (
                path_index == y
                or value[y - 1, path_index] < value[y - 1, path_index - 1]
            ):
                path_index -= 1

    return torch.from_numpy(paths).to(device=device, dtype=dtype)


def _install_monotonic_alignment_fallback() -> None:
    """Avoid SBV2's hard import-time dependency on numba in packaged builds."""
    module_name = "style_bert_vits2.models.monotonic_alignment"

    def _patch_if_numba_backed(module: Any) -> bool:
        maximum_path = getattr(module, "maximum_path", None)
        if getattr(module, "_MIO_FALLBACK", False):
            return True
        if maximum_path is None or getattr(maximum_path, "py_func", None) is not None:
            module.maximum_path = _maximum_path_fallback
            module._MIO_FALLBACK = True
            logger.warning(
                "Using Style-Bert-VITS2 monotonic alignment fallback; "
                "runtime helper is not safe in packaged mode"
            )
            return True
        return False

    current = sys.modules.get(module_name)
    if current is not None:
        _patch_if_numba_backed(current)
        return

    try:
        module = importlib.import_module(module_name)
        _patch_if_numba_backed(module)
        return
    except Exception:
        pass

    fallback_module = types.ModuleType(module_name)
    fallback_module.maximum_path = _maximum_path_fallback
    fallback_module._MIO_FALLBACK = True
    sys.modules[module_name] = fallback_module


def _load_runtime_tts_model_cls() -> Any | None:
    """Load the optional Style-Bert-VITS2 runtime once per process."""
    global _RUNTIME_LOAD_ATTEMPTED
    global _RUNTIME_TTS_MODEL_CLS
    global _RUNTIME_IMPORT_ERROR

    if _RUNTIME_LOAD_ATTEMPTED:
        return _RUNTIME_TTS_MODEL_CLS

    _RUNTIME_LOAD_ATTEMPTED = True
    try:
        _ensure_importable_stdio()
        _install_g2p_en_offline_fallback()
        _install_monotonic_alignment_fallback()
        from style_bert_vits2.tts_model import TTSModel

        _RUNTIME_TTS_MODEL_CLS = TTSModel
        _RUNTIME_IMPORT_ERROR = ""
    except Exception as exc:
        _RUNTIME_TTS_MODEL_CLS = None
        _RUNTIME_IMPORT_ERROR = str(exc).strip() or exc.__class__.__name__
        logger.info("Style-Bert-VITS2 runtime unavailable: %s", _RUNTIME_IMPORT_ERROR)
    return _RUNTIME_TTS_MODEL_CLS


def style_bert_runtime_available() -> bool:
    """Return whether the optional Style-Bert-VITS2 inference runtime is importable."""
    return _load_runtime_tts_model_cls() is not None


def style_bert_runtime_error() -> str:
    """Return the cached import error for the optional Style-Bert-VITS2 runtime."""
    _load_runtime_tts_model_cls()
    return _RUNTIME_IMPORT_ERROR


def style_bert_runtime_assets_ready(language: object = "jp") -> bool:
    """Return whether the selected Style-Bert-VITS2 BERT assets are downloaded."""
    return style_bert_bert_assets_ready(language)


def normalize_style_bert_bert_language(value: object) -> str:
    """Normalize the configured Style-Bert-VITS2 BERT language."""
    language = str(value or "").strip().lower()
    aliases = {
        "ja": "jp",
        "japanese": "jp",
        "jp": "jp",
        "en": "en",
        "english": "en",
        "zh": "zh",
        "cn": "zh",
        "chinese": "zh",
        "zh-cn": "zh",
    }
    return aliases.get(language, "jp")


def style_bert_bert_model_id(language: object) -> str:
    """Return the managed Hugging Face BERT model id for one SBV2 language."""
    return STYLE_BERT_BERT_MODEL_IDS[
        normalize_style_bert_bert_language(language)
    ]


def style_bert_bert_assets_ready(language: object) -> bool:
    """Return whether the configured Style-Bert-VITS2 BERT assets are downloaded."""
    return model_is_complete(style_bert_bert_model_id(language))


class StyleBertVits2TTS(BaseTTS):
    """Use custom Style-Bert-VITS2 voices imported into the app folder."""

    def __init__(self, device: str = "cpu", bert_language: str = "jp") -> None:
        self._tts_model_cls = _load_runtime_tts_model_cls()
        self._model_cache: dict[str, Any] = {}
        self._device = device if device in ("cpu", "cuda") else "cpu"
        self._bert_language = normalize_style_bert_bert_language(bert_language)
        self._patch_applied = False

        # Apply monkey-patch for FP16->FP32 conversion on CPU
        global _original_load_safetensors
        if _original_load_safetensors is None and self._device == "cpu":
            try:
                from style_bert_vits2.models.utils import safetensors as sbv2_safetensors
                _original_load_safetensors = sbv2_safetensors.load_safetensors
                sbv2_safetensors.load_safetensors = _patched_load_safetensors
                self._patch_applied = True
                logger.info("Applied FP16->FP32 conversion patch for CPU inference")
            except Exception as e:
                logger.warning(f"Failed to apply safetensors patch: {e}")

    def __del__(self) -> None:
        """Clean up monkey-patch on destruction."""
        if self._patch_applied:
            self._restore_original_loader()

    def _restore_original_loader(self) -> None:
        """Restore original safetensors loader."""
        global _original_load_safetensors
        if _original_load_safetensors is not None:
            try:
                from style_bert_vits2.models.utils import safetensors as sbv2_safetensors
                sbv2_safetensors.load_safetensors = _original_load_safetensors
                _original_load_safetensors = None
                self._patch_applied = False
                logger.debug("Restored original safetensors loader")
            except Exception as exc:
                logger.debug("Failed to restore safetensors loader: %s", exc)

    @property
    def runtime_available(self) -> bool:
        """Expose runtime state separately from model-folder state."""
        return self._tts_model_cls is not None

    def is_available(self) -> bool:
        """Check whether the runtime and at least one imported model are ready."""
        return (
            self._tts_model_cls is not None
            and style_bert_bert_assets_ready(self._bert_language)
            and bool(list_imported_style_bert_models())
        )

    def get_available_voices(self) -> list[TTSVoice]:
        """Expose imported model / speaker / style combinations as voices."""
        voices: list[TTSVoice] = []
        for model in list_imported_style_bert_models():
            for speaker in model.speakers:
                for style in model.styles:
                    voice_id = style_bert_voice_id(model.name, speaker, style)
                    display_title = style_bert_preset_title(model.name, speaker)
                    display_name = (
                        f"{display_title} / {style}"
                        if display_title
                        else voice_id
                    )
                    preset_language = (
                        style_bert_preset_language(model.name, speaker)
                        or self._bert_language.upper()
                    )
                    language, locale = _STYLE_BERT_VOICE_LOCALES.get(
                        preset_language.upper(),
                        _STYLE_BERT_VOICE_LOCALES["JP"],
                    )
                    voices.append(
                        TTSVoice(
                            id=voice_id,
                            name=display_name,
                            language=language,
                            gender="Neutral",
                            locale=locale,
                        )
                    )
        return voices

    def get_sample_rate(self) -> int:
        """Return the sample rate for Style-Bert-VITS2 models."""
        return 44100

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Render imported custom Style-Bert-VITS2 voices to WAV bytes."""
        if self._tts_model_cls is None:
            raise RuntimeError("Style-Bert-VITS2 runtime is not available")

        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")

        model_name, speaker_name, style_name = parse_style_bert_voice_id(voice)
        model_info = next(
            (
                item
                for item in list_imported_style_bert_models()
                if item.name == model_name
            ),
            None,
        )
        if model_info is None:
            raise RuntimeError("The selected Style-Bert-VITS2 model folder is missing")

        self._ensure_bert_runtime()
        model = self._get_or_create_model(model_info)
        speaker_id = getattr(model, "spk2id", {}).get(speaker_name)
        if speaker_id is None:
            raise RuntimeError("The selected Style-Bert-VITS2 speaker is unavailable")
        if style_name not in getattr(model, "style2id", {}):
            raise RuntimeError("The selected Style-Bert-VITS2 style is unavailable")

        speed = max(0.5, min(2.0, float(rate)))
        loudness = max(0.0, min(1.0, float(volume)))
        length = max(0.5, min(2.0, 1.0 / speed))

        try:
            sample_rate, audio = model.infer(
                text=clean_text,
                language=self._runtime_language_enum(),
                speaker_id=speaker_id,
                style=style_name,
                length=length,
            )
            audio_array = self._apply_volume(np.asarray(audio), loudness)
            return _encode_wav_bytes(int(sample_rate), audio_array)
        except Exception as exc:
            logger.error("Style-Bert-VITS2 synthesis failed: %s", exc)
            raise RuntimeError(f"Style-Bert-VITS2 synthesis failed: {exc}") from exc

    def _get_or_create_model(self, model_info):
        cache_key = str(model_info.model_path)
        cached = self._model_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            model = self._tts_model_cls(
                model_path=model_info.model_path,
                config_path=model_info.config_path,
                style_vec_path=model_info.style_vectors_path,
                device=self._device,
            )
        except StyleBertVits2ModelError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not load the custom voice model: {exc}") from exc

        self._model_cache[cache_key] = model
        return model

    def _ensure_japanese_bert_runtime(self) -> None:
        self._ensure_bert_runtime("jp")

    def _ensure_bert_runtime(self, language: str | None = None) -> None:
        bert_language = normalize_style_bert_bert_language(
            language or self._bert_language
        )
        model_id = style_bert_bert_model_id(bert_language)
        language_name = STYLE_BERT_LANGUAGE_NAMES[bert_language]
        if not style_bert_bert_assets_ready(bert_language):
            raise RuntimeError(
                f"The shared {language_name} Style-Bert-VITS2 runtime model is not downloaded."
        )

        try:
            _ensure_transformers_bert_exports()

            from style_bert_vits2.constants import Languages
            from style_bert_vits2.nlp import bert_models

            runtime_language = getattr(Languages, bert_language.upper())
            bert_path = str(model_dir(model_id))
            bert_model = bert_models.load_model(runtime_language, bert_path)
            bert_models.load_tokenizer(runtime_language, bert_path)

            # Convert BERT model to FP32 if using CPU
            if self._device == "cpu":
                bert_model.float()

                # Verify conversion succeeded
                try:
                    import torch
                    param_dtype = next(bert_model.parameters()).dtype
                    if param_dtype != torch.float32:
                        logger.warning(
                            "BERT model conversion to FP32 may have failed (got %s)",
                            param_dtype
                        )
                    else:
                        logger.info("Converted BERT model to FP32 for CPU inference")
                except Exception as exc:
                    logger.debug("Could not verify BERT model dtype: %s", exc)

        except Exception as exc:
            raise RuntimeError(
                f"Could not initialize the shared {language_name} Style-Bert-VITS2 runtime: {exc}"
            ) from exc

    def _runtime_language_enum(self):
        from style_bert_vits2.constants import Languages

        return getattr(Languages, self._bert_language.upper())

    @staticmethod
    def _apply_volume(audio: np.ndarray, volume: float) -> np.ndarray:
        if volume >= 0.999:
            return audio
        if np.issubdtype(audio.dtype, np.floating):
            return np.clip(audio * volume, -1.0, 1.0).astype(audio.dtype, copy=False)
        if np.issubdtype(audio.dtype, np.integer):
            limits = np.iinfo(audio.dtype)
            scaled = np.clip(audio.astype(np.float64) * volume, limits.min, limits.max)
            return scaled.astype(audio.dtype)
        return audio
