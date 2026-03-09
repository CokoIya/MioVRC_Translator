"""VAD と重複チャンク切り出しを併用する録音器  """

from __future__ import annotations

import collections
import queue
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .chunk_streamer import ChunkStreamer
from .vad_detector import VADDetector


class AudioRecorder:
    """
    マイクから連続的に音声を取得する    final 用の文単位切り出しと partial 用の重複チャンクを同時に扱う  
    """

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 2,
        silence_threshold_s: float = 0.8,
        vad_speech_ratio: float = 0.72,
        vad_activation_threshold_s: float = 0.24,
        input_device: Optional[int] = None,
        on_vad_state: Optional[Callable[[bool], None]] = None,
        pre_speech_s: float = 0.30,
        on_chunk: Optional[Callable[[np.ndarray], None]] = None,
        chunk_interval_ms: int = 250,
        chunk_window_s: float = 1.6,
        ring_buffer_s: float = 4.0,
        recent_speech_hold_s: float = 0.8,
        min_segment_s: float = 0.45,
        partial_min_speech_s: float = 0.45,
        vad_min_rms: float = 0.012,
        max_segment_s: float = 12.0,
    ):
        self.on_segment = on_segment
        self.on_chunk = on_chunk
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_threshold_s = silence_threshold_s
        self.input_device = input_device
        self.on_vad_state = on_vad_state
        self.pre_speech_s = pre_speech_s

        self.vad = VADDetector(
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            sensitivity=vad_sensitivity,
            silence_threshold_s=silence_threshold_s,
            speech_ratio=vad_speech_ratio,
            activation_threshold_s=vad_activation_threshold_s,
            min_rms=vad_min_rms,
            max_speech_s=max_segment_s,
        )

        self._buffer: list[np.ndarray] = []
        self._pre_speech_buffer = collections.deque(
            maxlen=max(1, int(pre_speech_s * 1000 / frame_duration_ms))
        )
        self._min_segment_samples = max(int(min_segment_s * sample_rate), 1)
        self._partial_min_speech_samples = max(int(partial_min_speech_s * sample_rate), 0)
        self._speech_samples = 0
        self._was_in_speech = False
        self._frame_queue: queue.Queue = queue.Queue()
        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._capture_rate: int = sample_rate
        self._capture_channels: int = 1
        self._capture_dtype: str = "int16"
        self._chunk_streamer = (
            ChunkStreamer(
                sample_rate=sample_rate,
                chunk_interval_ms=chunk_interval_ms,
                chunk_window_s=chunk_window_s,
                ring_buffer_s=ring_buffer_s,
                recent_speech_hold_s=recent_speech_hold_s,
            )
            if on_chunk is not None
            else None
        )

    def start(self):
        if self._running:
            return
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        self._capture_rate = self.sample_rate
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()

        self._stream = self._open_stream(self.input_device)
        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()

    def _open_stream(self, device) -> sd.InputStream:
        """利用可能な形式を順に試して InputStream を開く  """
        try:
            dev_idx = device if device is not None else sd.default.device[0]
            native_rate = int(sd.query_devices(dev_idx)["default_samplerate"])
        except Exception:
            native_rate = 48000

        candidates = [
            (self.sample_rate, 1, "int16"),
            (native_rate, 1, "int16"),
            (native_rate, 2, "int16"),
            (native_rate, 1, "float32"),
            (native_rate, 2, "float32"),
        ]
        deduped: list[tuple[int, int, str]] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)

        def _try_open_and_start(rate, channels, dtype, dev):
            blocksize = int(rate * self.frame_duration_ms / 1000)
            self._capture_rate = rate
            self._capture_channels = channels
            self._capture_dtype = dtype
            stream = sd.InputStream(
                samplerate=rate,
                channels=channels,
                dtype=dtype,
                blocksize=blocksize,
                device=dev,
                callback=self._sd_callback,
            )
            try:
                stream.start()
            except Exception:
                try:
                    stream.close()
                except Exception:
                    pass
                raise
            return stream

        last_err = None
        for dev in ([device, None] if device is not None else [None]):
            if dev is None and device is not None:
                self.input_device = None
            for rate, channels, dtype in deduped:
                try:
                    return _try_open_and_start(rate, channels, dtype, dev)
                except sd.PortAudioError as exc:
                    last_err = exc

        raise last_err

    def stop(self):
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2)
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()

    def _sd_callback(self, indata, frames, time_info, status):
        del frames
        del time_info
        del status

        data = indata
        if self._capture_channels == 2:
            mono = (data[:, 0].astype(np.float32) + data[:, 1].astype(np.float32)) / 2
            data = mono.reshape(-1, 1)
        if self._capture_dtype == "float32":
            data = np.clip(data * 32768, -32768, 32767).astype(np.int16)
        if self._capture_rate != self.sample_rate:
            ratio = max(self._capture_rate // self.sample_rate, 1)
            data = data[::ratio]
        self._frame_queue.put(data.copy())

    def _process_loop(self):
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            normalized = frame.flatten().astype(np.float32) / 32768.0
            previous_in_speech = self._was_in_speech
            if not previous_in_speech:
                self._pre_speech_buffer.append(normalized)

            pcm_bytes = frame.tobytes()
            in_speech = self.vad.process_frame(pcm_bytes)

            if in_speech:
                if not previous_in_speech:
                    self._buffer = list(self._pre_speech_buffer)
                    self._pre_speech_buffer.clear()
                    self._speech_samples = normalized.size
                else:
                    self._buffer.append(normalized)
                    self._speech_samples += normalized.size

            if self._chunk_streamer is not None and self.on_chunk is not None:
                for chunk in self._chunk_streamer.push_frame(normalized, in_speech):
                    if self._speech_samples < self._partial_min_speech_samples:
                        continue
                    try:
                        self.on_chunk(chunk)
                    except Exception as exc:
                        print(f"[Recorder] on_chunk error: {exc}")

            if in_speech != previous_in_speech and self.on_vad_state:
                try:
                    self.on_vad_state(in_speech)
                except Exception:
                    pass

            if in_speech:
                self._was_in_speech = True
                continue

            if not previous_in_speech:
                continue

            segment = np.concatenate(self._buffer) if self._buffer else None
            speech_samples = self._speech_samples
            self._buffer.clear()
            self._speech_samples = 0
            self._was_in_speech = False
            self.vad.reset()
            self._pre_speech_buffer.clear()
            if segment is None or speech_samples < self._min_segment_samples:
                continue
            try:
                self.on_segment(segment)
            except Exception as exc:
                print(f"[Recorder] on_segment error: {exc}")

    @staticmethod
    def list_devices() -> list[dict]:
        """入力デバイスを名前単位で重複排除して返す  """
        api_preference = {
            "Windows WASAPI": 0,
            "Windows WDM-KS": 1,
            "Windows DirectSound": 2,
            "MME": 3,
        }
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            hostapis = []

        seen: dict[str, dict] = {}
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] <= 0:
                continue
            api_name = hostapis[device["hostapi"]]["name"] if hostapis else ""
            pref = api_preference.get(api_name, 99)
            existing = seen.get(device["name"])
            if existing is None or pref < existing["_pref"]:
                seen[device["name"]] = {"index": index, "name": device["name"], "_pref": pref}

        result = sorted(seen.values(), key=lambda item: item["index"])
        return [{"index": item["index"], "name": item["name"]} for item in result]
