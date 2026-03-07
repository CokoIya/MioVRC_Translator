"""faster-whisper ASR — loads from bundled models/ directory only (no network download)."""

import threading
import pathlib
import sys
import numpy as np
from typing import Optional

ALLOWED_SIZES = ("base", "small")


def _resolve_model_path(size: str) -> str:
    """
    Return the absolute path to the local model directory.
    Looks for  <project_root>/models/whisper-<size>  (source mode)
    or         <exe_dir>/models/whisper-<size>         (frozen/PyInstaller mode).
    Raises FileNotFoundError if the directory does not exist so that the caller
    can surface a clear error to the user instead of silently downloading.
    """
    if getattr(sys, "frozen", False):
        base_dir = pathlib.Path(sys.executable).parent
    else:
        base_dir = pathlib.Path(__file__).resolve().parents[2]

    local = base_dir / "models" / f"whisper-{size}"
    if local.exists():
        return str(local)

    raise FileNotFoundError(
        f"模型文件未找到：{local}\n"
        f"请先运行项目根目录中的 download_models.py 下载模型，然后再启动程序。"
    )


class WhisperASR:
    """
    faster-whisper wrapper restricted to 'base' and 'small' sizes.
    Models must be pre-downloaded into models/whisper-<size>/ — no network access.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu"):
        if model_size not in ALLOWED_SIZES:
            model_size = "base"
        self.model_size = model_size
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        """Load the model from local path. Idempotent."""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise RuntimeError(
                    "faster-whisper 未安装，请执行: pip install faster-whisper"
                )
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

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000,
                   language: Optional[str] = None) -> str:
        """Transcribe float32 mono audio array.

        language: ISO-639-1 code (e.g. 'ja', 'zh', 'en') or None for auto-detect.
        """
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
