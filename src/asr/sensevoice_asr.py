from __future__ import annotations

import re
import sys
import threading
import logging
from typing import Optional

import numpy as np

from src.asr.funasr_runtime_compat import patch_sentencepiece_unicode_path_support
from src.asr.model_manager import download_model, model_exists, resolve_model_path
from src.asr.model_registry import get_asr_engine_spec
from src.asr.text_corrections import LayeredASRCorrector

_DEFAULT_SPEC = get_asr_engine_spec("sensevoice-small")
logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    "zh": "zh",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "ru": "ru",
    "yue": "yue",
}


def _emit_progress(progress_callback, *, stage: str, message: str) -> None:
    if progress_callback is None:
        return
    progress_callback({"stage": stage, "message": message})


def _dependency_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if getattr(sys, "frozen", False):
        return (
            "The packaged build is missing SenseVoice runtime dependencies: "
            f"{detail}. Re-download the full package or rebuild with MioTranslator.spec."
        )
    return (
        "The current environment is missing SenseVoice runtime dependencies: "
        f"{detail}. Run `pip install -r requirements.txt` first."
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


class SenseVoiceASR:
    def __init__(
        self,
        device: str = "cpu",
        ncpu: int | None = None,
        model_id: str = _DEFAULT_SPEC.model_id,
        model_revision: str = _DEFAULT_SPEC.model_revision,
        corrector: LayeredASRCorrector | None = None,
    ):
        self.device = device
        self.ncpu = max(ncpu or 4, 1)
        self.model_id = model_id
        self.model_revision = model_revision
        self._model = None
        self._postprocess = None
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

        lang = _LANGUAGE_MAP.get(language or "", "auto")
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
        text = re.sub(r"\s+", " ", text).strip()
        if self._corrector is not None:
            text = self._corrector.apply(text, language=language)
        return text

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
