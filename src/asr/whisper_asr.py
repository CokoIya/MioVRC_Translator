"""faster-whisper を使用した Whisper ASR 推論。CPU/GPU 両対応、高速起動。"""

import threading
import numpy as np
from typing import Optional


class WhisperASR:
    """
    faster-whisper ラッパー。
    SenseVoice より起動が速く、モデルサイズが小さい。
    """

    MODEL_SIZES = ("tiny", "base", "small", "medium")

    def __init__(self, model_size: str = "small", device: str = "cpu"):
        self.model_size = model_size if model_size in self.MODEL_SIZES else "small"
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        """モデルをロードする。冪等。"""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
                if progress_callback:
                    progress_callback(f"Whisper {self.model_size} モデルを読み込み中…")
                compute = "float16" if self.device == "cuda" else "int8"
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=compute,
                )
                if progress_callback:
                    progress_callback("モデルの読み込み完了")
            except ImportError:
                raise RuntimeError(
                    "faster-whisper がインストールされていません: pip install faster-whisper"
                )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """float32 モノラル音声を文字起こしする。"""
        if self._model is None:
            self.load()
        with self._lock:
            segments, _ = self._model.transcribe(
                audio, beam_size=5, language=None, vad_filter=True
            )
            return "".join(s.text for s in segments).strip()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
