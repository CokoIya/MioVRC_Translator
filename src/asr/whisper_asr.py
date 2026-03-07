"""同梱モデルと LocalAppData の両方を使える faster-whisper ラッパー。"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from src.asr.model_manager import ALLOWED_SIZES, ensure_model, model_exists, resolve_model_path


class WhisperASR:
    """Whisper Small を既存音声認識バックエンドとして扱う。"""

    def __init__(self, model_size: str = "small", device: str = "cpu"):
        if model_size not in ALLOWED_SIZES:
            model_size = ALLOWED_SIZES[0]
        self.model_size = model_size
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        """モデルは一度だけ初期化する。"""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper 未安装，请先执行 pip install faster-whisper"
                ) from exc

            if not model_exists(self.model_size):
                ensure_model(self.model_size, progress_callback=progress_callback)
            if progress_callback:
                progress_callback(f"正在加载 Whisper {self.model_size} 模型…")

            model_path = resolve_model_path(self.model_size)
            compute = "float16" if self.device == "cuda" else "int8"
            self._model = WhisperModel(
                model_path,
                device=self.device,
                compute_type=compute,
            )
            if progress_callback:
                progress_callback("Whisper 模型加载完成")

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        """整段音频与重叠分块都走同一套转写接口。"""
        if self._model is None:
            self.load()
        del sample_rate
        del is_final
        with self._lock:
            segments, _ = self._model.transcribe(
                audio,
                beam_size=5,
                language=language,
                vad_filter=False,
            )
            return "".join(segment.text for segment in segments).strip()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
