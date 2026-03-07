"""VAD ベースのセグメント分割で連続マイク録音を行う。"""

import queue
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .vad_detector import VADDetector


class AudioRecorder:
    """
    マイクから連続的に音声を取得し、フレーム単位で VAD を実行する。  
    完全な音声セグメントを `numpy.float32` 配列としてコールバックへ渡す。
    """

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 2,
        silence_threshold_s: float = 0.8,
        input_device: Optional[int] = None,
        on_vad_state: Optional[Callable[[bool], None]] = None,
    ):
        self.on_segment = on_segment
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_threshold_s = silence_threshold_s
        self.input_device = input_device
        self.on_vad_state = on_vad_state

        self.vad = VADDetector(
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            sensitivity=vad_sensitivity,
            silence_threshold_s=silence_threshold_s,
        )

        self._frame_size = int(sample_rate * frame_duration_ms / 1000)
        self._buffer: list[np.ndarray] = []
        self._was_in_speech = False

        self._frame_queue: queue.Queue = queue.Queue()
        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._capture_rate: int = sample_rate  # リサンプリング時は `sample_rate` と異なる場合がある。
        self._capture_channels: int = 1
        self._capture_dtype: str = "int16"

    def start(self):
        if self._running:
            return
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._was_in_speech = False
        self._capture_rate = self.sample_rate

        self._stream = self._open_stream(self.input_device)
        # ストリームは `_open_stream` 内ですでに開始されている。

        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()

    def _open_stream(self, device) -> sd.InputStream:
        """利用可能な形式とサンプルレートを順に試しながら `InputStream` を開く。"""
        # フォールバック用にデバイスのネイティブサンプルレートを取得する。
        try:
            dev_idx = device if device is not None else sd.default.device[0]
            native_rate = int(sd.query_devices(dev_idx)["default_samplerate"])
        except Exception:
            native_rate = 48000

        # 試行順は `(rate, channels, dtype)`。
        candidates = [
            (self.sample_rate, 1, "int16"),
            (native_rate,      1, "int16"),
            (native_rate,      2, "int16"),
            (native_rate,      1, "float32"),
            (native_rate,      2, "float32"),
        ]
        # 順序を保ったまま重複候補を除外する。
        seen_c: list = []
        for c in candidates:
            if c not in seen_c:
                seen_c.append(c)

        def _try_open_and_start(rate, channels, dtype, dev):
            blocksize = int(rate * self.frame_duration_ms / 1000)
            self._capture_rate = rate
            self._capture_channels = channels
            self._capture_dtype = dtype
            stream = sd.InputStream(
                samplerate=rate, channels=channels, dtype=dtype,
                blocksize=blocksize, device=dev, callback=self._sd_callback,
            )
            try:
                stream.start()
            except Exception:
                try:
                    stream.close()  # 次の試行前に PortAudio リソースを必ず解放する。
                except Exception:
                    pass
                raise
            return stream

        last_err = None
        for dev in ([device, None] if device is not None else [None]):
            if dev is None and device is not None:
                self.input_device = None
            for rate, channels, dtype in seen_c:
                try:
                    return _try_open_and_start(rate, channels, dtype, dev)
                except sd.PortAudioError as e:
                    last_err = e

        raise last_err

    def stop(self):
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2)

    def _sd_callback(self, indata, frames, time_info, status):
        data = indata
        # ステレオ入力をモノラルへ変換する。
        if self._capture_channels == 2:
            mono = (data[:, 0].astype(np.float32) + data[:, 1].astype(np.float32)) / 2
            data = mono.reshape(-1, 1)
        # `float32` 入力を `int16` に変換する。
        if self._capture_dtype == "float32":
            data = np.clip(data * 32768, -32768, 32767).astype(np.int16)
        # キャプチャレートが異なる場合は間引いて合わせる。
        if self._capture_rate != self.sample_rate:
            ratio = self._capture_rate // self.sample_rate
            data = data[::ratio]
        self._frame_queue.put(data.copy())

    def _process_loop(self):
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            pcm_bytes = frame.tobytes()
            in_speech = self.vad.process_frame(pcm_bytes)

            if in_speech != self._was_in_speech and self.on_vad_state:
                try:
                    self.on_vad_state(in_speech)
                except Exception:
                    pass

            if in_speech:
                self._buffer.append(frame.flatten().astype(np.float32) / 32768.0)
                self._was_in_speech = True
            elif self._was_in_speech:
                # 発話から無音へ遷移したのでセグメントを出力する。
                if self._buffer:
                    segment = np.concatenate(self._buffer)
                    self._buffer.clear()
                    self._was_in_speech = False
                    self.vad.reset()
                    try:
                        self.on_segment(segment)
                    except Exception as e:
                        print(f"[Recorder] on_segment error: {e}")

    @staticmethod
    def list_devices() -> list[dict]:
        """入力デバイスを名前単位で重複排除して返す。  優先順は WASAPI > WDM-KS > DirectSound > MME。"""
        _API_PREF = {"Windows WASAPI": 0, "Windows WDM-KS": 1, "Windows DirectSound": 2, "MME": 3}
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            hostapis = []

        seen: dict[str, dict] = {}  # デバイス名ごとの最良候補を保持する。
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            api_name = hostapis[d["hostapi"]]["name"] if hostapis else ""
            pref = _API_PREF.get(api_name, 99)
            existing = seen.get(d["name"])
            if existing is None or pref < existing["_pref"]:
                seen[d["name"]] = {"index": i, "name": d["name"], "_pref": pref}

        result = sorted(seen.values(), key=lambda x: x["index"])
        return [{"index": r["index"], "name": r["name"]} for r in result]
