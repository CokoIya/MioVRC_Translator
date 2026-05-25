from __future__ import annotations

import re
import sys
import threading
import logging
import os
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
from src.asr.text_corrections import LayeredASRCorrector

_DEFAULT_SPEC = get_asr_engine_spec("sensevoice-small")
logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    "auto": "auto",
    "zh": "zh",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "ru": "ru",
    "yue": "yue",
}
_LANGUAGE_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "cn": "zh",
    "jp": "ja",
    "jpn": "ja",
    "ja-jp": "ja",
    "kr": "ko",
    "ko-kr": "ko",
    "en-us": "en",
    "en-gb": "en",
    "ru-ru": "ru",
}
_CJK_SCRIPT_RANGE = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_CJK_CLOSE_PUNCT = r"\u3001\u3002\uff0c\uff01\uff1f\uff1b\uff1a\uff09\uff3d\uff5d\u300d\u300f"
_CJK_OPEN_PUNCT = r"\uff08\uff3b\uff5b\u300c\u300e"
_JA_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KO_RE = re.compile(r"[\uac00-\ud7af]")
_RU_RE = re.compile(r"[\u0400-\u04ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _emit_progress(progress_callback, *, stage: str, message: str) -> None:
    if progress_callback is None:
        return
    progress_callback({"stage": stage, "message": message})


def _normalize_language_code(language: str | None) -> str:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized:
        return ""
    return _LANGUAGE_ALIASES.get(normalized, normalized.split("-", 1)[0])


def _infer_text_language(text: str) -> str | None:
    if _JA_KANA_RE.search(text):
        return "ja"
    if _KO_RE.search(text):
        return "ko"
    if _RU_RE.search(text):
        return "ru"
    if _HAN_RE.search(text):
        return "zh"
    if _LATIN_RE.search(text):
        return "en"
    return None


def _correction_language(text: str, language: str | None) -> str | None:
    normalized = _normalize_language_code(language)
    mapped = _LANGUAGE_MAP.get(normalized)
    if mapped and mapped != "auto":
        return mapped
    return _infer_text_language(text)


def _normalize_spoken_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(
        rf"(?<=[{_CJK_SCRIPT_RANGE}])\s+(?=[{_CJK_SCRIPT_RANGE}])",
        "",
        normalized,
    )
    normalized = re.sub(rf"\s+(?=[{_CJK_CLOSE_PUNCT}])", "", normalized)
    normalized = re.sub(rf"(?<=[{_CJK_OPEN_PUNCT}])\s+", "", normalized)
    return normalized.strip()


def _dependency_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if getattr(sys, "frozen", False):
        return (
            "The packaged build is missing SenseVoice runtime dependencies: "
            f"{detail}. Re-download the full package or rebuild with MioTranslator.spec."
        )
    return (
        "The current environment is missing SenseVoice runtime dependencies: "
        f"{detail}. Run `python -m pip install -r requirements.txt` in the same "
        "Python environment first. Release builds should use the locked Python 3.11 environment."
    )


def _load_runtime_symbols():
    patch_sentencepiece_unicode_path_support()
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    return AutoModel, rich_transcription_postprocess


def validate_runtime_dependencies() -> tuple[bool, str]:
    try:
        auto_model_cls, _ = _load_runtime_symbols()
    except (ImportError, OSError) as exc:
        return False, _dependency_error_message(exc)

    return True, f"SenseVoice runtime imports OK: {auto_model_cls.__module__}.AutoModel"


class SenseVoiceASR(ASRProvider):
    provider_id = "sensevoice-small"
    display_name = "SenseVoice Small"
    requires_api_key = False
    supports_partial = True

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
        self._postprocess = None
        self._lock = threading.Lock()
        self._corrector = corrector

    def _runtime_spec(self):
        required_file_sha256 = (
            _DEFAULT_SPEC.required_file_sha256
            if self.model_id == _DEFAULT_SPEC.model_id
            else ()
        )
        return _DEFAULT_SPEC.__class__(
            engine=_DEFAULT_SPEC.engine,
            label=_DEFAULT_SPEC.label,
            config_key=_DEFAULT_SPEC.config_key,
            model_id=self.model_id,
            model_revision=self.model_revision,
            bundled_dir_names=_DEFAULT_SPEC.bundled_dir_names,
            required_files=_DEFAULT_SPEC.required_files,
            required_file_sha256=required_file_sha256,
        )

    def load(self, progress_callback=None):
        with self._lock:
            if self._model is not None:
                return
            logger.info(
                "Loading SenseVoice ASR (model_id=%s revision=%s device=%s)",
                self.model_id,
                self.model_revision,
                self.device,
            )
            _emit_progress(progress_callback, stage="loading", message="loading")
            try:
                AutoModel, rich_transcription_postprocess = _load_runtime_symbols()
            except (ImportError, OSError) as exc:
                logger.exception("SenseVoice runtime dependency load failed")
                raise RuntimeError(_dependency_error_message(exc)) from exc

            spec = self._runtime_spec()
            if not model_exists(spec):
                download_model(
                    spec,
                    progress_callback=progress_callback,
                )
            _emit_progress(progress_callback, stage="loading", message="loading")

            model_path = resolve_model_path(spec)
            if not verify_model_integrity(model_path, spec):
                raise RuntimeError(
                    "SenseVoice model integrity verification failed. Please delete the runtime model folder and download it again."
                )
            self._model = AutoModel(
                model=model_path,
                device=self.device,
                disable_update=True,
                disable_pbar=True,
                log_level="ERROR",
                ncpu=self.ncpu,
            )
            self._postprocess = rich_transcription_postprocess
            _emit_progress(progress_callback, stage="ready", message="ready")
            logger.info("SenseVoice ASR loaded successfully from %s", model_path)

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

        lang = _LANGUAGE_MAP.get(_normalize_language_code(language), "auto")
        with self._lock:
            result = self._model.generate(
                input=audio_input,
                language=lang,
                use_itn=True,
                disable_pbar=True,
            )
        return self._clean_text(result, language=language)

    def _clean_text(self, result, language: str | None = None) -> str:
        if not isinstance(result, list) or not result:
            return ""
        text = str(result[0].get("text", "")).strip()
        if not text:
            return ""
        if self._postprocess is not None:
            try:
                text = self._postprocess(text)
            except Exception:
                pass
        text = re.sub(r"<\|[^|]+\|>", "", text)
        text = clean_asr_text(text)
        if self._corrector is not None:
            text = self._corrector.apply(
                text,
                language=_correction_language(text, language),
            )
        return text

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
