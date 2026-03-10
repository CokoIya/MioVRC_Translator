from __future__ import annotations

import re
import sys
import threading
from typing import Optional

import numpy as np

from src.asr.sensevoice_model_manager import (
    MODEL_ID,
    MODEL_REVISION,
    ensure_model,
    model_exists,
    resolve_model_path,
)

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
            f"当前发布包缺少 SenseVoice 运行依赖：{detail}。"
            "  请重新下载完整发布包，或使用当前 MioTranslator.spec 重新打包。"
        )
    return (
        f"当前环境缺少 SenseVoice 依赖：{detail}。"
        "  请先执行 pip install -r requirements.txt。"
    )


def _load_runtime_symbols():
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
        model_id: str = MODEL_ID,
        model_revision: str = MODEL_REVISION,
    ):
        self.device = device
        self.ncpu = max(ncpu or 4, 1)
        self.model_id = model_id
        self.model_revision = model_revision
        self._model = None
        self._postprocess = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        with self._lock:
            if self._model is not None:
                return
            try:
                AutoModel, rich_transcription_postprocess = _load_runtime_symbols()
            except (ImportError, OSError) as exc:
                raise RuntimeError(_dependency_error_message(exc)) from exc

            if not model_exists(self.model_id):
                ensure_model(
                    model_id=self.model_id,
                    model_revision=self.model_revision,
                    progress_callback=progress_callback,
                )
            _emit_progress(progress_callback, stage="loading", message="loading")

            model_path = resolve_model_path(self.model_id)
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
        return self._clean_text(result)

    def _clean_text(self, result) -> str:
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
        return text

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
