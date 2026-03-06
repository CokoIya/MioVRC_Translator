"""FunASR を使用した SenseVoice Small のローカルASR推論  """

import sys
import threading
import pathlib
import numpy as np
from typing import Optional


def _resolve_model_path(model_id: str) -> str:
    """
    PyInstaller でバンドルされた場合は EXE 隣の models/ を優先して使用する。
    モデルがそこにあれば ModelScope のネットワーク検証を完全にスキップできる。
    """
    if getattr(sys, "frozen", False):
        exe_dir = pathlib.Path(sys.executable).parent
    else:
        exe_dir = pathlib.Path(__file__).resolve().parents[2]

    local = exe_dir / "models" / model_id.replace("/", pathlib.os.sep)
    if local.exists() and (local / "model.pt").exists():
        return str(local)
    return model_id


class SenseVoiceASR:
    """
    FunASR の AutoModel を使って SenseVoice Small をラップする
    モデルは初回使用時に遅延読み込みされる（load() で明示的にも可）
    """

    MODEL_ID = "iic/SenseVoiceSmall"

    def __init__(self, model_id: Optional[str] = None, device: str = "cpu"):
        self.model_id = model_id or self.MODEL_ID
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def load(self, progress_callback=None):
        """モデルを読み込む  複数回呼び出しても安全（冪等）  """
        with self._lock:
            if self._model is not None:
                return
            try:
                from funasr import AutoModel
                if progress_callback:
                    progress_callback("SenseVoice モデルを読み込み中…")
                resolved = _resolve_model_path(self.model_id)
                self._model = AutoModel(
                    model=resolved,
                    trust_remote_code=True,
                    device=self.device,
                    disable_update=True,
                )
                if progress_callback:
                    progress_callback("モデルの読み込み完了")
            except ImportError:
                raise RuntimeError(
                    "funasr がインストールされていません。実行してください: pip install funasr modelscope"
                )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """
        float32 モノラル音声配列を文字起こしする  
        認識テキスト文字列を返す  
        """
        if self._model is None:
            self.load()

        with self._lock:
            result = self._model.generate(
                input=audio,
                cache={},
                language="auto",
                use_itn=True,
                batch_size_s=60,
            )

        if result and isinstance(result, list) and "text" in result[0]:
            text = result[0]["text"]
            # SenseVoice が先頭に付加する感情・イベントタグを除去する
            # 例: "<|zh|><|NEUTRAL|><|Speech|><|woitn|>你好"
            import re
            text = re.sub(r"<\|[^|]+\|>", "", text).strip()
            return text
        return ""

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
