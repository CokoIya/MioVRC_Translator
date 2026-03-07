"""同梱モデルと LocalAppData フォールバックに対応した faster-whisper ラッパー。"""

import threading
from typing import Optional

import numpy as np

from src.asr.model_manager import ALLOWED_SIZES, ensure_model, model_exists, resolve_model_path


def _resolve_model_path(size: str) -> str:
    """利用可能なモデルディレクトリの絶対パスを返す。"""
    return resolve_model_path(size)


class WhisperASR:
    """
    `base` と `small` のみを扱う faster-whisper ラッパー。  
    モデルは同梱済みアセット、または LocalAppData へ保存されたものを利用する。
    """

    def __init__(self, model_size: str = "base", device: str = "cpu"):
        if model_size not in ALLOWED_SIZES:
            model_size = "base"
        self.model_size = model_size
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        """ローカルモデルを読み込む。  二重初期化は行わない。"""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper 未安装，请执行 pip install faster-whisper"
                ) from exc

            if not model_exists(self.model_size):
                ensure_model(self.model_size, progress_callback=progress_callback)
            if progress_callback:
                progress_callback(f"正在加载 Whisper {self.model_size} 模型…")

            model_path = _resolve_model_path(self.model_size)
            compute = "float16" if self.device == "cuda" else "int8"
            self._model = WhisperModel(
                model_path,
                device=self.device,
                compute_type=compute,
            )
            if progress_callback:
                progress_callback("模型加载完成")

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> str:
        """float32 のモノラル音声配列を文字起こしする。"""
        if self._model is None:
            self.load()
        with self._lock:
            segments, _ = self._model.transcribe(
                audio, beam_size=5, language=language, vad_filter=True
            )
            return "".join(s.text for s in segments).strip()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
