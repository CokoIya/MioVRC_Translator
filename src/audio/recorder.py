"""VADベースのセグメント分割による連続マイク録音。"""

import threading
import queue
import numpy as np
import sounddevice as sd
from typing import Callable, Optional

from .vad_detector import VADDetector


class AudioRecorder:
    """
    マイクから連続的に音声を読み取り、フレームごとにVADを実行し、
    完全な音声セグメント（numpy float32配列）をコールバックで出力する。
    """

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 2,
        silence_threshold_s: float = 0.8,
        input_device: Optional[int] = None,
    ):
        self.on_segment = on_segment
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_threshold_s = silence_threshold_s
        self.input_device = input_device

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

    def start(self):
        if self._running:
            return
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._was_in_speech = False

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self._frame_size,
            device=self.input_device,
            callback=self._sd_callback,
        )
        self._stream.start()

        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()

    def stop(self):
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2)

    def _sd_callback(self, indata, frames, time_info, status):
        self._frame_queue.put(indata.copy())

    def _process_loop(self):
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            pcm_bytes = frame.tobytes()
            in_speech = self.vad.process_frame(pcm_bytes)

            if in_speech:
                self._buffer.append(frame.flatten().astype(np.float32) / 32768.0)
                self._was_in_speech = True
            elif self._was_in_speech:
                # 発話→無音への遷移：セグメントを出力
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
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append({"index": i, "name": d["name"]})
        return result
