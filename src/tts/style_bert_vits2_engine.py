"""Style-Bert-VITS2 local custom voice integration."""
from __future__ import annotations

import gc
import io
import importlib
import logging
import os
import re
import sys
import threading
import time
import types
import unicodedata
import wave
from collections import OrderedDict
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
except ImportError:
    torch = None

from src.utils.app_paths import resource_base_dirs
from src.utils.hf_model_downloader import model_dir, model_is_complete
from src.version import APP_VERSION

from .base import BaseTTS, TTSVoice
from .style_bert_vits2_models import (
    StyleBertVits2ModelError,
    StyleBertVits2ModelInfo,
    list_imported_style_bert_models,
    parse_style_bert_voice_id,
    resolve_style_bert_voice_language,
    style_bert_preset_title,
    style_bert_voice_id,
    style_bert_voice_language,
)
from src.utils.config_manager import normalize_style_bert_bert_language
from src.utils.gpu_support import clear_torch_cuda_cache

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
_SBV2_CPU_RUNTIME_CONFIGURED = False
_SBV2_RUNTIME_PATCHES_INSTALLED = False
_SBV2_FEATURE_LOGGING_PATCHED = False
_SBV2_PYOPENJTALK_WORKER_PATCHED = False
_SBV2_TYPEGUARD_PATCHED = False
_SBV2_LANGUAGE_PREFLIGHTED: set[str] = set()

# Words the GRU below gets wrong but players say constantly.  Style-Bert-VITS2
# consults its own CMU dictionary first, so only out-of-vocabulary words ever
# reach this table.
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
# g2p_en ships a small GRU encoder/decoder (checkpoint20.npz, Apache-2.0)
# for out-of-vocabulary words.  Running it here with numpy keeps the rest of
# g2p_en, which needs NLTK corpora the packaged app cannot download, out of
# the process entirely.
_G2P_EN_CHECKPOINT_NAME = "checkpoint20.npz"
_G2P_EN_GRAPHEMES: tuple[str, ...] = ("<pad>", "<unk>", "</s>") + tuple(
    "abcdefghijklmnopqrstuvwxyz"
)
_G2P_EN_PHONEMES: tuple[str, ...] = ("<pad>", "<unk>", "<s>", "</s>") + (
    "AA0", "AA1", "AA2", "AE0", "AE1", "AE2", "AH0", "AH1", "AH2", "AO0",
    "AO1", "AO2", "AW0", "AW1", "AW2", "AY0", "AY1", "AY2", "B", "CH", "D", "DH",
    "EH0", "EH1", "EH2", "ER0", "ER1", "ER2", "EY0", "EY1",
    "EY2", "F", "G", "HH",
    "IH0", "IH1", "IH2", "IY0", "IY1", "IY2", "JH", "K", "L",
    "M", "N", "NG", "OW0", "OW1",
    "OW2", "OY0", "OY1", "OY2", "P", "R", "S", "SH", "T", "TH",
    "UH0", "UH1", "UH2", "UW",
    "UW0", "UW1", "UW2", "V", "W", "Y", "Z", "ZH",
)
_G2P_EN_MAX_PHONES = 20
_G2P_EN_WEIGHT_KEYS = (
    "enc_emb", "enc_w_ih", "enc_w_hh", "enc_b_ih", "enc_b_hh",
    "dec_emb", "dec_w_ih", "dec_w_hh", "dec_b_ih", "dec_b_hh",
    "fc_w", "fc_b",
)
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

_REQUIRED_TRANSFORMERS_BERT_EXPORTS = (
    "AutoModelForMaskedLM",
    "AutoTokenizer",
    "DebertaV2Model",
    "DebertaV2Tokenizer",
    "PreTrainedModel",
    "PreTrainedTokenizer",
    "PreTrainedTokenizerFast",
)
_TRANSFORMERS_BERT_EXPORT_IMPORTS = {
    "AutoModelForMaskedLM": (
        "transformers.models.auto.modeling_auto",
        "AutoModelForMaskedLM",
    ),
    "AutoTokenizer": (
        "transformers.models.auto.tokenization_auto",
        "AutoTokenizer",
    ),
    "DebertaV2Model": (
        "transformers.models.deberta_v2.modeling_deberta_v2",
        "DebertaV2Model",
    ),
    "DebertaV2Tokenizer": (
        "transformers.models.deberta_v2.tokenization_deberta_v2",
        "DebertaV2Tokenizer",
    ),
    "PreTrainedModel": (
        "transformers.modeling_utils",
        "PreTrainedModel",
    ),
    "PreTrainedTokenizer": (
        "transformers.tokenization_utils",
        "PreTrainedTokenizer",
    ),
    "PreTrainedTokenizerFast": (
        "transformers.tokenization_utils_fast",
        "PreTrainedTokenizerFast",
    ),
}
_STYLE_BERT_LANGUAGE_RUNTIME_DEPENDENCIES = {
    "en": (
        ("inflect", "inflect"),
        ("typeguard", "typeguard"),
        ("sentencepiece", "sentencepiece"),
        ("sentencepiece.sentencepiece_model_pb2", "sentencepiece protobuf bindings"),
        ("google.protobuf", "protobuf"),
    ),
    "zh": (
        ("jieba", "jieba"),
        ("jieba.posseg", "jieba POS tokenizer"),
        ("pypinyin", "pypinyin"),
        ("cn2an", "cn2an"),
    ),
}

_MAX_CACHED_SBV2_MODELS = 2
_OPEN_JTALK_REQUIRED_DICT_FILES = ("char.bin", "matrix.bin", "sys.dic", "unk.dic")


def _ensure_module_spec(module_name: str, module: Any | None = None) -> Any | None:
    """Give synthetic/frozen modules a spec so importlib.find_spec will not fail."""
    target = module if module is not None else sys.modules.get(module_name)
    if target is None:
        return None
    if getattr(target, "__spec__", None) is not None:
        return target

    try:
        is_package = hasattr(target, "__path__")
        spec = ModuleSpec(
            module_name,
            getattr(target, "__loader__", None),
            origin=getattr(target, "__file__", None) or "mio-runtime",
            is_package=is_package,
        )
        if is_package:
            spec.submodule_search_locations = list(getattr(target, "__path__", []))
        target.__spec__ = spec
        if getattr(target, "__package__", None) is None:
            target.__package__ = (
                module_name if is_package else module_name.rpartition(".")[0]
            )
    except Exception:
        logger.debug("Could not repair module spec for %s", module_name, exc_info=True)
    return target


def _missing_transformers_bert_exports(transformers_module: Any) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_TRANSFORMERS_BERT_EXPORTS:
        try:
            getattr(transformers_module, name)
        except Exception:
            missing.append(name)
    return missing


def _install_transformers_bert_exports(transformers_module: Any) -> None:
    """Restore top-level transformers exports used by Style-Bert-VITS2."""
    for name in _missing_transformers_bert_exports(transformers_module):
        import_info = _TRANSFORMERS_BERT_EXPORT_IMPORTS.get(name)
        if import_info is None:
            continue
        module_name, attr_name = import_info
        try:
            source_module = importlib.import_module(module_name)
            value = getattr(source_module, attr_name)
        except Exception:
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

    _install_transformers_bert_exports(transformers_module)
    missing = _missing_transformers_bert_exports(transformers_module)
    if not missing:
        logger.info("Recovered Style-Bert-VITS2 transformers exports without reload")
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

    _install_transformers_bert_exports(refreshed)
    missing = _missing_transformers_bert_exports(refreshed)
    if missing:
        version = str(getattr(refreshed, "__version__", version))
        raise RuntimeError(
            "The installed transformers package "
            f"({version}) is missing exports required by Style-Bert-VITS2: "
            + ", ".join(missing)
        )


def _ensure_style_bert_language_runtime_dependencies(language: object) -> None:
    """Fail early with a clear message for language-specific SBV2 dependencies."""
    bert_language = normalize_style_bert_bert_language(language)
    _install_packaged_typeguard_noop_patch()
    if bert_language == "en":
        _install_g2p_en_offline_fallback()

    missing: list[str] = []
    for module_name, display_name in _STYLE_BERT_LANGUAGE_RUNTIME_DEPENDENCIES.get(
        bert_language,
        (),
    ):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            detail = str(exc).strip()
            if detail:
                missing.append(f"{display_name} ({detail})")
            else:
                missing.append(display_name)

    if missing:
        language_name = STYLE_BERT_LANGUAGE_NAMES[bert_language]
        raise RuntimeError(
            f"{language_name} BERT runtime dependency is missing: "
            + ", ".join(missing)
            + f". Please reinstall MioTranslator v{APP_VERSION} or run "
            + "pip install -r requirements.txt."
        )

    if bert_language == "jp":
        _ensure_packaged_pyopenjtalk_dictionary()


def _install_packaged_typeguard_noop_patch() -> None:
    """Avoid typeguard source inspection failures in PyInstaller frozen modules."""
    global _SBV2_TYPEGUARD_PATCHED
    if _SBV2_TYPEGUARD_PATCHED or not _packaged_pyopenjtalk_worker_disabled():
        return

    def _typechecked_noop(target=None, **_kwargs):
        if target is None:
            return lambda wrapped: wrapped
        return target

    patched = False
    for module_name in ("typeguard", "typeguard._decorators"):
        try:
            module = importlib.import_module(module_name)
            module.typechecked = _typechecked_noop
            patched = True
        except Exception:
            logger.debug("Could not patch %s.typechecked", module_name, exc_info=True)

    if patched:
        logger.info("Disabled typeguard runtime instrumentation for packaged SBV2 text normalization")
    _SBV2_TYPEGUARD_PATCHED = True


def _preflight_style_bert_text_processing(bert_language: str) -> None:
    if bert_language in _SBV2_LANGUAGE_PREFLIGHTED:
        return
    try:
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.nlp import clean_text

        sample = (
            "Hello, nice to meet you in 2026."
            if bert_language == "en"
            else "你好，Mio 2026。"
        )
        language_enum = getattr(Languages, bert_language.upper())
        clean_text(sample, language_enum)
    except Exception as exc:
        language_name = STYLE_BERT_LANGUAGE_NAMES[bert_language]
        raise RuntimeError(
            f"{language_name} Style-Bert-VITS2 text processing is unavailable: {exc}. "
            f"Please reinstall MioTranslator v{APP_VERSION}."
        ) from exc
    _SBV2_LANGUAGE_PREFLIGHTED.add(bert_language)


def _ensure_packaged_pyopenjtalk_dictionary() -> None:
    """Do not let packaged Japanese SBV2 playback trigger a network download."""
    if not _packaged_pyopenjtalk_worker_disabled():
        return

    try:
        pyopenjtalk = importlib.import_module("pyopenjtalk")
    except Exception as exc:
        raise RuntimeError(
            "Japanese Style-Bert-VITS2 dependency is missing: pyopenjtalk. "
            f"Please reinstall MioTranslator v{APP_VERSION}."
        ) from exc

    raw_dict_dir = getattr(pyopenjtalk, "OPEN_JTALK_DICT_DIR", b"")
    if isinstance(raw_dict_dir, bytes):
        dict_dir = raw_dict_dir.decode("utf-8", "ignore")
    else:
        dict_dir = str(raw_dict_dir)
    missing_files = [
        name for name in _OPEN_JTALK_REQUIRED_DICT_FILES if not os.path.isfile(os.path.join(dict_dir, name))
    ]
    if not dict_dir or not os.path.isdir(dict_dir) or missing_files:
        raise RuntimeError(
            "Japanese Style-Bert-VITS2 Open JTalk dictionary is missing from the "
            f"packaged runtime: {dict_dir or '<unknown>'}. "
            f"Please reinstall MioTranslator v{APP_VERSION}."
        )


def style_bert_cuda_available() -> bool:
    """Return whether Style-Bert-VITS2 can use CUDA in this runtime."""
    if torch is None:
        return False
    try:
        cuda = getattr(torch, "cuda", None)
        return bool(cuda is not None and cuda.is_available())
    except Exception:
        return False


def _configure_style_bert_cpu_runtime() -> None:
    """Initialize SBV2 diagnostics without mutating process-wide thread pools.

    ``torch.set_num_threads`` and the OMP/MKL environment variables are global
    to the process. Changing them while merely probing the SBV2 engine used to
    throttle SenseVoice for the rest of the session, even when SBV2
    was never selected. Stage-level concurrency already bounds SBV2 synthesis,
    so an individual backend must not silently retune every other model.
    """

    global _SBV2_CPU_RUNTIME_CONFIGURED
    if _SBV2_CPU_RUNTIME_CONFIGURED or torch is None:
        return
    _SBV2_CPU_RUNTIME_CONFIGURED = True

    actual_threads: int | str = "unknown"
    actual_interop: int | str = "unknown"
    try:
        actual_threads = int(torch.get_num_threads())
    except Exception:
        logger.debug("Could not inspect SBV2 torch thread count", exc_info=True)

    try:
        actual_interop = int(torch.get_num_interop_threads())
    except Exception:
        logger.debug("Could not inspect SBV2 torch interop thread count", exc_info=True)

    logger.info(
        "Style-Bert-VITS2 using shared CPU runtime without global retuning "
        "(torch_threads=%s interop_threads=%s)",
        actual_threads,
        actual_interop,
    )


def _install_style_bert_feature_logging_patch() -> None:
    """Add diagnostic timing around SBV2 text normalization and BERT features."""
    global _SBV2_FEATURE_LOGGING_PATCHED
    if _SBV2_FEATURE_LOGGING_PATCHED:
        return
    try:
        infer_module = importlib.import_module("style_bert_vits2.models.infer")
    except Exception:
        logger.debug("Could not import SBV2 infer module for diagnostics", exc_info=True)
        return

    original_get_text = getattr(infer_module, "get_text", None)
    if original_get_text is None or getattr(original_get_text, "_MIO_LOGGING_PATCH", False):
        _SBV2_FEATURE_LOGGING_PATCHED = True
        return

    def logged_get_text(*args, **kwargs):
        text_value = args[0] if args else kwargs.get("text", "")
        language_value = args[1] if len(args) > 1 else kwargs.get("language_str", "")
        device_value = args[3] if len(args) > 3 else kwargs.get("device", "")
        started = time.monotonic()
        logger.info(
            "Style-Bert-VITS2 text/BERT feature extraction started (language=%s device=%s text_chars=%d)",
            language_value,
            device_value,
            len(str(text_value or "")),
        )
        result = original_get_text(*args, **kwargs)
        try:
            shapes = tuple(tuple(getattr(item, "shape", ())) for item in result[:6])
        except Exception:
            shapes = ()
        logger.info(
            "Style-Bert-VITS2 text/BERT feature extraction finished (elapsed=%.1fs shapes=%s)",
            time.monotonic() - started,
            shapes,
        )
        return result

    logged_get_text._MIO_LOGGING_PATCH = True
    infer_module.get_text = logged_get_text
    _SBV2_FEATURE_LOGGING_PATCHED = True


def _packaged_pyopenjtalk_worker_disabled() -> bool:
    return bool(
        getattr(sys, "frozen", False)
        or os.environ.get("MIO_SBV2_DISABLE_PYOPENJTALK_WORKER") == "1"
    )


def _close_style_bert_pyopenjtalk_worker(worker_module: Any) -> None:
    client = getattr(worker_module, "WORKER_CLIENT", None)
    if client is None:
        return
    try:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    except Exception:
        logger.debug("Could not close Style-Bert-VITS2 pyopenjtalk worker client", exc_info=True)
    finally:
        try:
            worker_module.WORKER_CLIENT = None
        except Exception:
            pass


def _disable_packaged_pyopenjtalk_worker() -> None:
    """Avoid SBV2's local socket worker in frozen builds.

    The upstream worker starts a child process with ``sys.executable -m ...``.
    In a PyInstaller windowed app that executable is MioTranslator.exe, not a
    normal Python interpreter, so the worker can fail or connect to a stale
    local server on the fixed port. Direct in-process pyopenjtalk calls are
    bundled and are more reliable for the desktop app.
    """
    global _SBV2_PYOPENJTALK_WORKER_PATCHED
    if not _packaged_pyopenjtalk_worker_disabled():
        return

    try:
        worker_module = importlib.import_module(
            "style_bert_vits2.nlp.japanese.pyopenjtalk_worker"
        )
    except Exception:
        logger.debug("Could not import SBV2 pyopenjtalk worker module", exc_info=True)
        return

    _close_style_bert_pyopenjtalk_worker(worker_module)

    def _disabled_initialize_worker(*_args, **_kwargs) -> None:
        _close_style_bert_pyopenjtalk_worker(worker_module)

    def _disabled_terminate_worker() -> None:
        _close_style_bert_pyopenjtalk_worker(worker_module)

    try:
        worker_module.initialize_worker = _disabled_initialize_worker
        worker_module.terminate_worker = _disabled_terminate_worker
    except Exception:
        logger.debug("Could not patch SBV2 pyopenjtalk worker functions", exc_info=True)

    if not _SBV2_PYOPENJTALK_WORKER_PATCHED:
        logger.info("Disabled Style-Bert-VITS2 pyopenjtalk socket worker for packaged runtime")
    _SBV2_PYOPENJTALK_WORKER_PATCHED = True


def _install_style_bert_cpu_duration_patch() -> None:
    """Skip SBV2's stochastic duration predictor when CPU deterministic mode is used."""
    global _SBV2_RUNTIME_PATCHES_INSTALLED
    if _SBV2_RUNTIME_PATCHES_INSTALLED or torch is None:
        return

    try:
        from style_bert_vits2.models import commons
        from style_bert_vits2.models.models import SynthesizerTrn
        from style_bert_vits2.models.models_jp_extra import SynthesizerTrn as JPExtraSynthesizerTrn
    except Exception:
        logger.debug("Could not import SBV2 model classes for CPU patch", exc_info=True)
        return

    def _duration_logw(model, x, x_mask, g, sdp_ratio: float, noise_scale_w: float):
        ratio = max(0.0, min(1.0, float(sdp_ratio or 0.0)))
        if ratio <= 0.0:
            return model.dp(x, x_mask, g=g), True
        if ratio >= 1.0:
            return model.sdp(x, x_mask, g=g, reverse=True, noise_scale=noise_scale_w), False
        return (
            model.sdp(x, x_mask, g=g, reverse=True, noise_scale=noise_scale_w) * ratio
            + model.dp(x, x_mask, g=g) * (1.0 - ratio),
            False,
        )

    def _model_float_dtype(model):
        try:
            for param in model.parameters():
                if getattr(param, "is_floating_point", lambda: False)():
                    return param.dtype
        except Exception:
            pass
        return torch.float32

    def _cast_float_tensor(value, dtype):
        if torch.is_tensor(value) and value.is_floating_point() and value.dtype != dtype:
            return value.to(dtype=dtype)
        return value

    def _finish_acoustic_infer(model, x, m_p, logs_p, x_mask, g, logw, length_scale, noise_scale, max_len):
        w = torch.exp(logw) * x_mask * length_scale
        w_ceil = torch.ceil(w)
        y_lengths = torch.clamp_min(torch.sum(w_ceil, [1, 2]), 1).long()
        y_mask = torch.unsqueeze(commons.sequence_mask(y_lengths, None), 1).to(x_mask.dtype)
        attn_mask = torch.unsqueeze(x_mask, 2) * torch.unsqueeze(y_mask, -1)
        attn = commons.generate_path(w_ceil, attn_mask)
        m_p = torch.matmul(attn.squeeze(1), m_p.transpose(1, 2)).transpose(1, 2)
        logs_p = torch.matmul(attn.squeeze(1), logs_p.transpose(1, 2)).transpose(1, 2)
        z_p = m_p + torch.randn_like(m_p) * torch.exp(logs_p) * noise_scale
        z = model.flow(z_p, y_mask, g=g, reverse=True)
        output = model.dec((z * y_mask)[:, :, :max_len], g=g)
        return output, attn, y_mask, (z, z_p, m_p, logs_p)

    def patched_normal_infer(
        self,
        x,
        x_lengths,
        sid,
        tone,
        language,
        bert,
        ja_bert,
        en_bert,
        style_vec,
        noise_scale=0.667,
        length_scale=1.0,
        noise_scale_w=0.8,
        max_len=None,
        sdp_ratio=0.0,
        y=None,
    ):
        started = time.monotonic()
        acoustic_dtype = _model_float_dtype(self)
        bert = _cast_float_tensor(bert, acoustic_dtype)
        ja_bert = _cast_float_tensor(ja_bert, acoustic_dtype)
        en_bert = _cast_float_tensor(en_bert, acoustic_dtype)
        style_vec = _cast_float_tensor(style_vec, acoustic_dtype)
        y = _cast_float_tensor(y, acoustic_dtype)
        if self.n_speakers > 0:
            g = self.emb_g(sid).unsqueeze(-1)
        else:
            if y is None:
                raise RuntimeError("Reference audio tensor is required for zero-speaker SBV2 models")
            g = self.ref_enc(y.transpose(1, 2)).unsqueeze(-1)
        x, m_p, logs_p, x_mask = self.enc_p(
            x, x_lengths, tone, language, bert, ja_bert, en_bert, style_vec, sid, g=g
        )
        logw, deterministic_duration = _duration_logw(self, x, x_mask, g, sdp_ratio, noise_scale_w)
        logger.info(
            "Style-Bert-VITS2 acoustic inference running (x_len=%s sdp_ratio=%.3f deterministic_duration=%s)",
            tuple(x_lengths.detach().cpu().tolist()),
            float(sdp_ratio or 0.0),
            deterministic_duration,
        )
        result = _finish_acoustic_infer(
            self, x, m_p, logs_p, x_mask, g, logw, length_scale, noise_scale, max_len
        )
        logger.info(
            "Style-Bert-VITS2 acoustic inference finished (elapsed=%.1fs)",
            time.monotonic() - started,
        )
        return result

    def patched_jp_extra_infer(
        self,
        x,
        x_lengths,
        sid,
        tone,
        language,
        bert,
        style_vec,
        noise_scale=0.667,
        length_scale=1.0,
        noise_scale_w=0.8,
        max_len=None,
        sdp_ratio=0.0,
        y=None,
    ):
        started = time.monotonic()
        acoustic_dtype = _model_float_dtype(self)
        bert = _cast_float_tensor(bert, acoustic_dtype)
        style_vec = _cast_float_tensor(style_vec, acoustic_dtype)
        y = _cast_float_tensor(y, acoustic_dtype)
        if self.n_speakers > 0:
            g = self.emb_g(sid).unsqueeze(-1)
        else:
            if y is None:
                raise RuntimeError("Reference audio tensor is required for zero-speaker SBV2 models")
            g = self.ref_enc(y.transpose(1, 2)).unsqueeze(-1)
        x, m_p, logs_p, x_mask = self.enc_p(
            x, x_lengths, tone, language, bert, style_vec, g=g
        )
        logw, deterministic_duration = _duration_logw(self, x, x_mask, g, sdp_ratio, noise_scale_w)
        logger.info(
            "Style-Bert-VITS2 acoustic inference running (x_len=%s sdp_ratio=%.3f deterministic_duration=%s)",
            tuple(x_lengths.detach().cpu().tolist()),
            float(sdp_ratio or 0.0),
            deterministic_duration,
        )
        result = _finish_acoustic_infer(
            self, x, m_p, logs_p, x_mask, g, logw, length_scale, noise_scale, max_len
        )
        logger.info(
            "Style-Bert-VITS2 acoustic inference finished (elapsed=%.1fs)",
            time.monotonic() - started,
        )
        return result

    if not getattr(SynthesizerTrn.infer, "_MIO_CPU_DURATION_PATCH", False):
        SynthesizerTrn._MIO_ORIGINAL_INFER = SynthesizerTrn.infer
        patched_normal_infer._MIO_CPU_DURATION_PATCH = True
        SynthesizerTrn.infer = patched_normal_infer
    if not getattr(JPExtraSynthesizerTrn.infer, "_MIO_CPU_DURATION_PATCH", False):
        JPExtraSynthesizerTrn._MIO_ORIGINAL_INFER = JPExtraSynthesizerTrn.infer
        patched_jp_extra_infer._MIO_CPU_DURATION_PATCH = True
        JPExtraSynthesizerTrn.infer = patched_jp_extra_infer

    _SBV2_RUNTIME_PATCHES_INSTALLED = True
    logger.info("Installed Style-Bert-VITS2 CPU duration stability patch")


def _install_style_bert_runtime_patches() -> None:
    _install_packaged_typeguard_noop_patch()
    _disable_packaged_pyopenjtalk_worker()
    _install_style_bert_feature_logging_patch()
    _install_style_bert_cpu_duration_patch()


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


def _g2p_en_checkpoint_candidates() -> list[Path]:
    """Locate g2p_en's bundled GRU weights without importing g2p_en."""
    candidates: list[Path] = []
    try:
        # PathFinder bypasses sys.modules, so the lookup still works after the
        # shim below has replaced the real package there.
        spec = PathFinder.find_spec("g2p_en")
    except Exception:
        spec = None
    if spec is not None:
        for location in list(spec.submodule_search_locations or []):
            candidates.append(Path(location) / _G2P_EN_CHECKPOINT_NAME)
        if spec.origin:
            candidates.append(Path(spec.origin).parent / _G2P_EN_CHECKPOINT_NAME)
    try:
        for base in resource_base_dirs():
            candidates.append(Path(base) / "g2p_en" / _G2P_EN_CHECKPOINT_NAME)
    except Exception:
        logger.debug("Could not enumerate resource dirs for g2p_en weights", exc_info=True)
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


class _NeuralEnglishG2p:
    """g2p_en's GRU grapheme-to-phoneme model, run from its weights with numpy."""

    def __init__(self, checkpoint_path: Path) -> None:
        with np.load(str(checkpoint_path)) as archive:
            weights = {key: np.asarray(archive[key], dtype=np.float32) for key in _G2P_EN_WEIGHT_KEYS}
        self._w = weights
        self._g2idx = {grapheme: index for index, grapheme in enumerate(_G2P_EN_GRAPHEMES)}

    @staticmethod
    def _gru_cell(x, h, w_ih, w_hh, b_ih, b_hh):
        gates_x = x @ w_ih.T + b_ih
        gates_h = h @ w_hh.T + b_hh
        split = gates_x.shape[-1] * 2 // 3
        rz = 1.0 / (1.0 + np.exp(-(gates_x[:, :split] + gates_h[:, :split])))
        r, z = np.split(rz, 2, axis=-1)
        n = np.tanh(gates_x[:, split:] + r * gates_h[:, split:])
        return (1.0 - z) * n + z * h

    def predict(self, word: str) -> list[str]:
        w = self._w
        indices = [self._g2idx.get(char, 1) for char in word] + [2]
        encoded = np.take(w["enc_emb"], np.expand_dims(indices, 0), axis=0)
        hidden = np.zeros((1, w["enc_w_hh"].shape[1]), np.float32)
        for step in range(encoded.shape[1]):
            hidden = self._gru_cell(
                encoded[:, step, :], hidden,
                w["enc_w_ih"], w["enc_w_hh"], w["enc_b_ih"], w["enc_b_hh"],
            )
        decoder_input = np.take(w["dec_emb"], [2], axis=0)  # <s>
        phones: list[str] = []
        for _ in range(_G2P_EN_MAX_PHONES):
            hidden = self._gru_cell(
                decoder_input, hidden,
                w["dec_w_ih"], w["dec_w_hh"], w["dec_b_ih"], w["dec_b_hh"],
            )
            logits = hidden @ w["fc_w"].T + w["fc_b"]
            predicted = int(logits.argmax())
            if predicted == 3:  # </s>
                break
            phones.append(_G2P_EN_PHONEMES[predicted] if predicted < len(_G2P_EN_PHONEMES) else "<unk>")
            decoder_input = np.take(w["dec_emb"], [predicted], axis=0)
        return [phone for phone in phones if phone not in {"<pad>", "<unk>", "<s>"}]


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


_NEURAL_G2P_LOADED = False
_NEURAL_G2P: _NeuralEnglishG2p | None = None
_NEURAL_G2P_LOCK = threading.Lock()


def _load_neural_english_g2p() -> _NeuralEnglishG2p | None:
    global _NEURAL_G2P_LOADED, _NEURAL_G2P
    with _NEURAL_G2P_LOCK:
        if _NEURAL_G2P_LOADED:
            return _NEURAL_G2P
        _NEURAL_G2P_LOADED = True
        for candidate in _g2p_en_checkpoint_candidates():
            try:
                if not candidate.is_file():
                    continue
                _NEURAL_G2P = _NeuralEnglishG2p(candidate)
                logger.info("Loaded Style-Bert-VITS2 English g2p weights from %s", candidate)
                break
            except Exception:
                logger.debug("Could not load g2p_en weights from %s", candidate, exc_info=True)
        if _NEURAL_G2P is None:
            logger.warning(
                "g2p_en GRU weights (%s) were not found; out-of-vocabulary English "
                "words fall back to letter rules",
                _G2P_EN_CHECKPOINT_NAME,
            )
        return _NEURAL_G2P


class _OfflineG2p:
    """Drop-in for ``g2p_en.G2p`` that never touches NLTK.

    Style-Bert-VITS2 resolves dictionary words itself and only calls this for
    out-of-vocabulary tokens, one word at a time.  The real g2p_en runs
    ``nltk.pos_tag`` on every call, which needs the ``averaged_perceptron_tagger_eng``
    corpus; the packaged app does not ship it and must not download at
    playback time, so English synthesis used to fail on the first unknown word.
    """

    def __init__(self) -> None:
        self._neural = _load_neural_english_g2p()

    def _word_phones(self, word: str) -> list[str]:
        known = _OFFLINE_G2P_COMMON.get(word)
        if known:
            return list(known)
        letters = re.sub(r"[^a-z]", "", word)
        if letters and self._neural is not None:
            try:
                phones = self._neural.predict(letters)
            except Exception:
                logger.debug("Neural g2p failed for %r", word, exc_info=True)
                phones = []
            if phones:
                return phones
        return _rough_english_pronunciation(word)

    def __call__(self, text: object) -> list[str]:
        normalized = "".join(
            char
            for char in unicodedata.normalize("NFD", str(text or ""))
            if unicodedata.category(char) != "Mn"
        )
        result: list[str] = []
        for token in _OFFLINE_G2P_TOKEN_RE.findall(normalized):
            if re.fullmatch(r"[.,!?;:\-]+", token):
                result.append(token[0])
                continue
            clean = token.strip("'").lower()
            if not clean:
                continue
            result.extend(self._word_phones(clean))
            result.append(" ")
        if result and result[-1] == " ":
            result.pop()
        return result


def _install_g2p_en_offline_fallback() -> None:
    """Make Style-Bert-VITS2 use the NLTK-free English g2p above.

    This always replaces ``g2p_en``: importing the real package calls
    ``nltk.download()`` when its corpora are missing and raises from
    ``nltk.pos_tag`` when only the pre-3.9 tagger is present, which is exactly
    what the frozen build ships.
    """
    existing = sys.modules.get("g2p_en")
    if existing is not None and getattr(existing, "_MIO_FALLBACK", False):
        return

    fallback_module = types.ModuleType("g2p_en")
    fallback_module.G2p = _OfflineG2p
    fallback_module._MIO_FALLBACK = True
    _ensure_module_spec("g2p_en", fallback_module)
    sys.modules["g2p_en"] = fallback_module
    if existing is not None:
        logger.info("Replaced the imported g2p_en package with the built-in English g2p")

    # SBV2 binds ``_g2p = G2p()`` at import time; swap an instance that was
    # already created from the real package.
    english_g2p = sys.modules.get("style_bert_vits2.nlp.english.g2p")
    if english_g2p is not None and not isinstance(
        getattr(english_g2p, "_g2p", None), _OfflineG2p
    ):
        try:
            english_g2p._g2p = _OfflineG2p()
        except Exception:
            logger.debug("Could not replace the SBV2 English g2p instance", exc_info=True)


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
        if torch is None:
            raise ImportError("torch is not installed")
        _ensure_importable_stdio()
        _install_packaged_typeguard_noop_patch()
        _install_g2p_en_offline_fallback()
        _install_monotonic_alignment_fallback()
        from style_bert_vits2.tts_model import TTSModel

        _install_style_bert_runtime_patches()
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


def style_bert_bert_model_id(language: object) -> str:
    """Return the managed Hugging Face BERT model id for one SBV2 language."""
    return STYLE_BERT_BERT_MODEL_IDS[
        normalize_style_bert_bert_language(language)
    ]


def style_bert_bert_assets_ready(language: object) -> bool:
    """Return whether the configured Style-Bert-VITS2 BERT assets are downloaded."""
    return model_is_complete(style_bert_bert_model_id(language))


__all__ = [
    "StyleBertVits2TTS",
    "list_style_bert_vits2_voices",
    "resolve_style_bert_voice_language",
    "style_bert_voice_language",
]


def list_style_bert_vits2_voices(bert_language: object = "jp") -> list[TTSVoice]:
    """List imported Style-Bert-VITS2 voices without loading the inference runtime."""
    normalized_language = normalize_style_bert_bert_language(bert_language)
    voices: list[TTSVoice] = []
    for model in list_imported_style_bert_models():
        for speaker in model.speakers:
            voice_language = resolve_style_bert_voice_language(
                model, speaker, normalized_language
            )
            language, locale = _STYLE_BERT_VOICE_LOCALES.get(
                voice_language.upper(),
                _STYLE_BERT_VOICE_LOCALES["JP"],
            )
            for style in model.styles:
                voice_id = style_bert_voice_id(model.name, speaker, style)
                display_title = style_bert_preset_title(model.name, speaker)
                display_name = (
                    f"{display_title} / {style}"
                    if display_title
                    else voice_id
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


class StyleBertVits2TTS(BaseTTS):
    """Use custom Style-Bert-VITS2 voices imported into the app folder."""

    def __init__(self, device: str = "cpu", bert_language: str = "jp") -> None:
        self._tts_model_cls = _load_runtime_tts_model_cls()
        self._model_cache: OrderedDict[str, Any] = OrderedDict()
        self._closed = False
        # BERT/model loading and inference share module-level state in the
        # upstream package and are far too heavy to run twice at once on a
        # CPU; the manager may still call in from several worker threads.
        self._synthesis_lock = threading.RLock()
        requested_device = device if device in ("cpu", "cuda") else "cpu"
        if requested_device == "cuda" and not style_bert_cuda_available():
            logger.warning(
                "CUDA was requested for Style-Bert-VITS2, but this PyTorch build "
                "does not provide CUDA; falling back to CPU"
            )
            requested_device = "cpu"
        self._device = requested_device
        self._bert_language = normalize_style_bert_bert_language(bert_language)
        if self._device == "cpu":
            _configure_style_bert_cpu_runtime()

    def close(self) -> None:
        """Release cached voice models and their CUDA tensors."""

        if getattr(self, "_closed", False):
            return
        self._closed = True
        cache = getattr(self, "_model_cache", None)
        if cache is not None:
            cache.clear()
        gc.collect()
        clear_torch_cuda_cache()

    @property
    def runtime_available(self) -> bool:
        """Expose runtime state separately from model-folder state."""
        return self._tts_model_cls is not None

    @property
    def device(self) -> str:
        """Return the actual inference device after runtime validation."""
        return self._device

    def is_available(self) -> bool:
        """Check whether the runtime and at least one imported model are ready.

        The BERT download is deliberately not part of this check: a missing
        BERT model raises a specific error at synthesis time, which the UI can
        explain, whereas an "unavailable" engine silently falls back or stays
        mute.
        """
        if getattr(self, "_closed", False):
            return False
        return (
            self._tts_model_cls is not None
            and bool(list_imported_style_bert_models())
        )

    def get_available_voices(self) -> list[TTSVoice]:
        """Expose imported model / speaker / style combinations as voices."""
        return list_style_bert_vits2_voices(self._bert_language)

    def get_sample_rate(self) -> int:
        """Return the sample rate for Style-Bert-VITS2 models."""
        return 44100

    def prewarm(self, voice: str = "") -> None:
        """Load the BERT runtime and the configured voice model ahead of speech.

        On a CPU this is the 10-30 s that otherwise lands on the first
        sentence of a conversation.
        """
        if getattr(self, "_closed", False) or self._tts_model_cls is None:
            return
        with self._synthesis_lock:
            models = list_imported_style_bert_models()
            if not models:
                return
            model_info = None
            speaker_name = ""
            if str(voice or "").strip():
                try:
                    model_name, speaker_name, _style = parse_style_bert_voice_id(voice)
                except Exception:
                    model_name = ""
                model_info = next((item for item in models if item.name == model_name), None)
            if model_info is None:
                model_info = models[0]
                speaker_name = model_info.speakers[0] if model_info.speakers else ""
            language = self._voice_bert_language(model_info, speaker_name)
            if not style_bert_bert_assets_ready(language):
                logger.info(
                    "Style-Bert-VITS2 prewarm skipped: %s BERT model is not downloaded",
                    STYLE_BERT_LANGUAGE_NAMES[language],
                )
                return
            started = time.monotonic()
            self._ensure_bert_runtime(language)
            self._get_or_create_model(model_info)
            logger.info(
                "Style-Bert-VITS2 prewarm finished (model=%s language=%s elapsed=%.1fs)",
                model_info.name,
                language,
                time.monotonic() - started,
            )

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Render imported custom Style-Bert-VITS2 voices to WAV bytes."""
        if getattr(self, "_closed", False):
            raise RuntimeError("Style-Bert-VITS2 engine is closed")
        if self._tts_model_cls is None:
            raise RuntimeError(
                "Style-Bert-VITS2 runtime is not available"
                + (f": {_RUNTIME_IMPORT_ERROR}" if _RUNTIME_IMPORT_ERROR else "")
            )

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

        with self._synthesis_lock:
            return self._synthesize_locked(
                clean_text, model_info, speaker_name, style_name, rate, volume
            )

    def _synthesize_locked(
        self,
        clean_text: str,
        model_info: StyleBertVits2ModelInfo,
        speaker_name: str,
        style_name: str,
        rate: float,
        volume: float,
    ) -> bytes:
        model_name = model_info.name
        voice_bert_language = self._voice_bert_language(model_info, speaker_name)
        self._ensure_bert_runtime(voice_bert_language)
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
            _disable_packaged_pyopenjtalk_worker()
            infer_started = time.monotonic()
            infer_kwargs: dict[str, Any] = {
                "text": clean_text,
                "language": self._runtime_language_enum(voice_bert_language),
                "speaker_id": speaker_id,
                "style": style_name,
                "length": length,
            }
            if self._device == "cpu":
                infer_kwargs["sdp_ratio"] = 0.0
            logger.info(
                "Style-Bert-VITS2 inference started (model=%s speaker=%s style=%s language=%s text_chars=%d sdp_ratio=%.3f)",
                model_name,
                speaker_name,
                style_name,
                voice_bert_language,
                len(clean_text),
                float(infer_kwargs.get("sdp_ratio", -1.0)),
            )
            sample_rate, audio = model.infer(**infer_kwargs)
            audio_array = self._apply_volume(np.asarray(audio), loudness)
            logger.info(
                "Style-Bert-VITS2 inference finished (model=%s speaker=%s style=%s language=%s elapsed=%.1fs sample_rate=%s audio_shape=%s)",
                model_name,
                speaker_name,
                style_name,
                voice_bert_language,
                time.monotonic() - infer_started,
                sample_rate,
                tuple(np.asarray(audio).shape),
            )
            return _encode_wav_bytes(int(sample_rate), audio_array)
        except Exception as exc:
            logger.exception("Style-Bert-VITS2 synthesis failed: %s", exc)
            raise RuntimeError(f"Style-Bert-VITS2 synthesis failed: {exc}") from exc

    def _get_or_create_model(self, model_info):
        if getattr(self, "_closed", False):
            raise RuntimeError("Style-Bert-VITS2 engine is closed")
        cache_key = str(model_info.model_path)
        cached = self._model_cache.get(cache_key)
        if cached is not None:
            # Move to end to mark as recently used (LRU)
            self._model_cache.move_to_end(cache_key)
            logger.debug("Style-Bert-VITS2 voice model cache hit: %s", model_info.name)
            return cached

        try:
            load_started = time.monotonic()
            logger.info(
                "Loading Style-Bert-VITS2 voice model (model=%s device=%s path=%s)",
                model_info.name,
                self._device,
                model_info.model_path,
            )
            model = self._tts_model_cls(
                model_path=model_info.model_path,
                config_path=model_info.config_path,
                style_vec_path=model_info.style_vectors_path,
                device=self._device,
            )
            self._prepare_voice_model_for_device(model)
            logger.info(
                "Loaded Style-Bert-VITS2 voice model (model=%s elapsed=%.1fs speakers=%d styles=%d)",
                model_info.name,
                time.monotonic() - load_started,
                len(getattr(model, "spk2id", {}) or {}),
                len(getattr(model, "style2id", {}) or {}),
            )
        except StyleBertVits2ModelError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not load the custom voice model: {exc}") from exc

        # Enforce LRU cap: evict oldest entries if at the limit
        while len(self._model_cache) >= _MAX_CACHED_SBV2_MODELS:
            oldest_key = next(iter(self._model_cache))
            self._model_cache.pop(oldest_key)
            logger.debug("Evicted SBV2 model from cache to enforce limit: %s", oldest_key)

        self._model_cache[cache_key] = model
        return model

    def _prepare_voice_model_for_device(self, model: Any) -> None:
        if self._device != "cuda":
            return
        try:
            net_g = getattr(model, "_TTSModel__net_g", None)
            if net_g is None and hasattr(model, "load"):
                model.load()
                net_g = getattr(model, "_TTSModel__net_g", None)
            if net_g is not None and hasattr(net_g, "float"):
                net_g.float()
                logger.info("Converted Style-Bert-VITS2 acoustic model to FP32 for CUDA inference")
        except Exception:
            logger.debug("Could not normalize Style-Bert-VITS2 CUDA model dtype", exc_info=True)

    def _ensure_japanese_bert_runtime(self) -> None:
        self._ensure_bert_runtime("jp")

    def _voice_bert_language(
        self,
        model_info: StyleBertVits2ModelInfo,
        speaker_name: str,
    ) -> str:
        return resolve_style_bert_voice_language(
            model_info, speaker_name, self._bert_language
        )

    def _ensure_bert_runtime(self, language: str | None = None) -> None:
        bert_language = normalize_style_bert_bert_language(
            language or self._bert_language
        )
        model_id = style_bert_bert_model_id(bert_language)
        language_name = STYLE_BERT_LANGUAGE_NAMES[bert_language]
        if not style_bert_bert_assets_ready(bert_language):
            raise RuntimeError(
                f"The shared {language_name} Style-Bert-VITS2 BERT runtime model "
                f"({model_id}) is not downloaded."
            )

        try:
            _ensure_style_bert_language_runtime_dependencies(bert_language)

            from style_bert_vits2.constants import Languages
            from style_bert_vits2.nlp import bert_models

            _ensure_transformers_bert_exports()

            runtime_language = getattr(Languages, bert_language.upper())
            bert_path = str(model_dir(model_id))
            runtime_started = time.monotonic()
            logger.info(
                "Loading Style-Bert-VITS2 BERT runtime (language=%s model_id=%s device=%s path=%s)",
                bert_language,
                model_id,
                self._device,
                bert_path,
            )
            bert_model = bert_models.load_model(runtime_language, bert_path)
            bert_models.load_tokenizer(runtime_language, bert_path)
            if bert_language in {"en", "zh"}:
                _preflight_style_bert_text_processing(bert_language)

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

            logger.info(
                "Style-Bert-VITS2 BERT runtime ready (language=%s elapsed=%.1fs)",
                bert_language,
                time.monotonic() - runtime_started,
            )

        except Exception as exc:
            raise RuntimeError(
                f"Could not initialize the shared {language_name} Style-Bert-VITS2 runtime: {exc}"
            ) from exc

    def _runtime_language_enum(self, language: str | None = None):
        bert_language = normalize_style_bert_bert_language(
            language or self._bert_language
        )
        language_name = bert_language.upper()
        try:
            from style_bert_vits2.constants import Languages
        except Exception:
            return language_name
        return getattr(Languages, language_name)

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
