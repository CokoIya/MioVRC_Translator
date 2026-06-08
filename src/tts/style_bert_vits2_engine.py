"""Style-Bert-VITS2 local custom voice integration."""
from __future__ import annotations

import io
import importlib
import logging
import os
import re
import sys
import time
import types
import wave
from collections import OrderedDict
from importlib.machinery import ModuleSpec
from typing import Any

import numpy as np

try:
    import torch
except ImportError:
    torch = None

from src.asr.hf_model_downloader import model_dir, model_is_complete
from src.version import APP_VERSION

from .base import BaseTTS, TTSVoice
from .style_bert_vits2_models import (
    StyleBertVits2ModelError,
    list_imported_style_bert_models,
    parse_style_bert_voice_id,
    style_bert_preset_title,
    style_bert_voice_id,
)
from src.utils.config_manager import normalize_style_bert_bert_language

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
_DEFAULT_SBV2_CPU_THREADS = 2
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

        sample = "Hello, Mio 2026." if bert_language == "en" else "你好，Mio 2026。"
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


def _style_bert_cpu_thread_limit() -> int:
    raw_value = str(os.environ.get("MIO_SBV2_CPU_THREADS") or "").strip()
    if raw_value:
        try:
            return max(0, int(raw_value))
        except ValueError:
            logger.warning("Ignoring invalid MIO_SBV2_CPU_THREADS value: %s", raw_value)
    cpu_count = os.cpu_count() or _DEFAULT_SBV2_CPU_THREADS
    return max(1, min(_DEFAULT_SBV2_CPU_THREADS, cpu_count))


def _configure_style_bert_cpu_runtime() -> None:
    """Keep SBV2 CPU inference from over-subscribing fragile player PCs."""
    global _SBV2_CPU_RUNTIME_CONFIGURED
    if _SBV2_CPU_RUNTIME_CONFIGURED or torch is None:
        return
    _SBV2_CPU_RUNTIME_CONFIGURED = True

    thread_limit = _style_bert_cpu_thread_limit()
    if thread_limit <= 0:
        logger.info("Style-Bert-VITS2 CPU thread limiting disabled by environment")
        return

    os.environ.setdefault("OMP_NUM_THREADS", str(thread_limit))
    os.environ.setdefault("MKL_NUM_THREADS", str(thread_limit))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(thread_limit))

    actual_threads: int | str = "unknown"
    actual_interop: int | str = "unknown"
    try:
        current_threads = int(torch.get_num_threads())
        target_threads = min(current_threads, thread_limit) if current_threads > 0 else thread_limit
        if target_threads > 0 and target_threads != current_threads:
            torch.set_num_threads(target_threads)
        actual_threads = int(torch.get_num_threads())
    except Exception:
        logger.debug("Could not configure SBV2 torch thread count", exc_info=True)

    try:
        current_interop = int(torch.get_num_interop_threads())
        if current_interop > 1:
            torch.set_num_interop_threads(1)
        actual_interop = int(torch.get_num_interop_threads())
    except Exception:
        logger.debug("Could not configure SBV2 torch interop thread count", exc_info=True)

    logger.info(
        "Configured Style-Bert-VITS2 CPU runtime (torch_threads=%s interop_threads=%s)",
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
    existing = sys.modules.get("g2p_en")
    if existing is not None:
        _ensure_module_spec("g2p_en", existing)
        return

    # g2p_en checks specifically for these zip resources at import time.
    # If they are missing, importing the real package calls nltk.download()
    # and can block players behind a slow or unreachable network.
    if (
        _nltk_zip_resource_ready("taggers/averaged_perceptron_tagger.zip")
        and _nltk_zip_resource_ready("corpora/cmudict.zip")
    ):
        try:
            module = importlib.import_module("g2p_en")
            _ensure_module_spec("g2p_en", module)
            return
        except Exception:
            pass

    fallback_module = types.ModuleType("g2p_en")
    fallback_module.G2p = _OfflineG2p
    fallback_module._MIO_FALLBACK = True
    _ensure_module_spec("g2p_en", fallback_module)
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


def list_style_bert_vits2_voices(bert_language: object = "jp") -> list[TTSVoice]:
    """List imported Style-Bert-VITS2 voices without loading the inference runtime."""
    normalized_language = normalize_style_bert_bert_language(bert_language)
    language, locale = _STYLE_BERT_VOICE_LOCALES.get(
        normalized_language.upper(),
        _STYLE_BERT_VOICE_LOCALES["JP"],
    )
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
        requested_device = device if device in ("cpu", "cuda") else "cpu"
        if requested_device == "cuda" and not style_bert_cuda_available():
            logger.warning(
                "CUDA was requested for Style-Bert-VITS2, but this PyTorch build "
                "does not provide CUDA; falling back to CPU"
            )
            requested_device = "cpu"
        self._device = requested_device
        self._bert_language = normalize_style_bert_bert_language(bert_language)
        self._patch_applied = False
        if self._device == "cpu":
            _configure_style_bert_cpu_runtime()

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

    @property
    def device(self) -> str:
        """Return the actual inference device after runtime validation."""
        return self._device

    def is_available(self) -> bool:
        """Check whether the runtime and at least one imported model are ready."""
        return (
            self._tts_model_cls is not None
            and style_bert_bert_assets_ready(self._bert_language)
            and bool(list_imported_style_bert_models())
        )

    def get_available_voices(self) -> list[TTSVoice]:
        """Expose imported model / speaker / style combinations as voices."""
        return list_style_bert_vits2_voices(self._bert_language)

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

        voice_bert_language = self._voice_bert_language(model_name, speaker_name)
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
            evicted = self._model_cache.pop(oldest_key)
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

    def _voice_bert_language(self, _model_name: str, _speaker_name: str) -> str:
        return self._bert_language

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
