from __future__ import annotations

import logging
import os
import re
import sys
import threading
from importlib import import_module
from typing import Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.base import ASRProvider
from src.asr.funasr_runtime_compat import patch_sentencepiece_unicode_path_support
from src.asr.model_manager import (
    download_model,
    model_exists,
    resolve_model_path,
    verify_model_integrity,
)
from src.asr.model_registry import get_asr_engine_spec
from src.asr.sensevoice_asr import _correction_language, _normalize_spoken_text
from src.asr.text_corrections import LayeredASRCorrector

_DEFAULT_SPEC = get_asr_engine_spec("whisper-large-v3-turbo")
logger = logging.getLogger(__name__)

_LANGUAGE_ALIASES = {
    "auto": "auto",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "ja-jp": "ja",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "ko": "ko",
    "kr": "ko",
    "ko-kr": "ko",
    "ru": "ru",
    "ru-ru": "ru",
}


def _emit_progress(progress_callback, *, stage: str, message: str) -> None:
    if progress_callback is not None:
        progress_callback({"stage": stage, "message": message})


def _normalize_language(language: str | None) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text:
        return "auto"
    return _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])


def _dependency_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if getattr(sys, "frozen", False):
        return (
            "The packaged build is missing Whisper runtime dependencies: "
            f"{detail}. Re-download the full package or rebuild with MioTranslator.spec."
        )
    return (
        "The current environment is missing Whisper runtime dependencies: "
        f"{detail}. Run `python -m pip install -r requirements.txt` in the same "
        "Python environment first."
    )


def _load_runtime_symbols():
    patch_sentencepiece_unicode_path_support()
    for module_name in ("scipy", "numba", "llvmlite", "librosa", "whisper.timing"):
        import_module(module_name)
    from funasr import AutoModel
    from whisper.tokenizer import get_tokenizer

    del get_tokenizer

    return AutoModel


def validate_runtime_dependencies() -> tuple[bool, str]:
    try:
        auto_model_cls = _load_runtime_symbols()
    except (ImportError, OSError) as exc:
        return False, _dependency_error_message(exc)

    return True, f"Whisper runtime imports OK: {auto_model_cls.__module__}.AutoModel"


class WhisperASR(ASRProvider):
    provider_id = "whisper-large-v3-turbo"
    display_name = "Whisper Small"
    requires_api_key = False
    supports_partial = False

    def __init__(
        self,
        device: str = "cpu",
        ncpu: int | None = None,
        model_id: str = _DEFAULT_SPEC.model_id,
        model_revision: str = _DEFAULT_SPEC.model_revision,
        corrector: LayeredASRCorrector | None = None,
    ):
        self.device = device
        self.ncpu = max(ncpu or max(2, (os.cpu_count() or 4) // 2), 1)
        self.model_id = model_id
        self.model_revision = model_revision
        self._model = None
        self._lock = threading.Lock()
        self._corrector = corrector

    def _runtime_spec(self):
        return _DEFAULT_SPEC.__class__(
            engine=_DEFAULT_SPEC.engine,
            label=_DEFAULT_SPEC.label,
            config_key=_DEFAULT_SPEC.config_key,
            model_id=self.model_id,
            model_revision=self.model_revision,
            bundled_dir_names=_DEFAULT_SPEC.bundled_dir_names,
            required_files=_DEFAULT_SPEC.required_files,
            required_file_sha256=(),
        )

    def load(self, progress_callback=None):
        with self._lock:
            if self._model is not None:
                return
            logger.info(
                "Loading Whisper ASR (model_id=%s revision=%s device=%s)",
                self.model_id,
                self.model_revision,
                self.device,
            )
            _emit_progress(progress_callback, stage="loading", message="loading")
            try:
                AutoModel = _load_runtime_symbols()
            except (ImportError, OSError) as exc:
                logger.exception("Whisper runtime dependency load failed")
                raise RuntimeError(_dependency_error_message(exc)) from exc

            spec = self._runtime_spec()
            if not model_exists(spec):
                download_model(spec, progress_callback=progress_callback)
            _emit_progress(progress_callback, stage="loading", message="loading")

            model_path = resolve_model_path(spec)
            if not verify_model_integrity(model_path, spec):
                raise RuntimeError(
                    "Whisper model integrity verification failed. Please delete the runtime model folder and download it again."
                )
            self._model = AutoModel(
                model=model_path,
                device=self.device,
                disable_update=True,
                disable_pbar=True,
                log_level="ERROR",
                ncpu=self.ncpu,
            )
            _emit_progress(progress_callback, stage="ready", message="ready")
            logger.info("Whisper ASR loaded successfully from %s", model_path)

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        if self._model is None:
            self.load()

        del sample_rate
        del is_final

        audio_input = np.asarray(audio, dtype=np.float32).flatten()
        if audio_input.size == 0:
            return ""

        kwargs = {
            "input": audio_input,
            "batch_size_s": 300,
            "disable_pbar": True,
        }
        lang = _normalize_language(language)
        if lang != "auto":
            kwargs["language"] = lang
        with self._lock:
            result = self._model.generate(**kwargs)
        return self._clean_text(result, language=language)

    def _clean_text(self, result, language: str | None = None) -> str:
        text = ""
        if isinstance(result, list) and result:
            item = result[0]
            if isinstance(item, dict):
                text = str(item.get("text", "") or item.get("value", "") or "")
            else:
                text = str(item or "")
        elif isinstance(result, dict):
            text = str(result.get("text", "") or result.get("value", "") or "")
        else:
            text = str(result or "")
        text = text.strip()
        if not text:
            return ""
        text = re.sub(r"<\|[^|]+\|>", "", text)
        text = clean_asr_text(text)
        text = _normalize_spoken_text(text)
        text = re.sub(r"(?<=[、。，！？；：])\s+(?=[A-Za-z0-9])", "", text)
        if self._corrector is not None:
            text = self._corrector.apply(
                text,
                language=_correction_language(text, language),
            )
        return text

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
