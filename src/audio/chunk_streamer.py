"""最近数秒の音声を保持しながら重複チャンクを切り出す  """

from __future__ import annotations

import collections

import numpy as np


class ChunkStreamer:
    """固定幅ウィンドウを一定間隔で切り出す簡易チャンクャ  """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_interval_ms: int = 250,
        chunk_window_s: float = 1.6,
        ring_buffer_s: float = 4.0,
        recent_speech_hold_s: float = 0.8,
    ):
        self.sample_rate = sample_rate
        self._interval_samples = max(int(sample_rate * chunk_interval_ms / 1000), 1)
        self._window_samples = max(int(sample_rate * chunk_window_s), self._interval_samples)
        self._buffer_limit = max(int(sample_rate * ring_buffer_s), self._window_samples)
        self._recent_speech_hold_samples = max(int(sample_rate * recent_speech_hold_s), 0)

        self._frames: collections.deque[np.ndarray] = collections.deque()
        self._buffered_samples = 0
        self._total_samples = 0
        self._last_emit_sample = 0
        self._last_speech_sample = -self._recent_speech_hold_samples

    def reset(self):
        self._frames.clear()
        self._buffered_samples = 0
        self._total_samples = 0
        self._last_emit_sample = 0
        self._last_speech_sample = -self._recent_speech_hold_samples

    def push_frame(self, frame: np.ndarray, in_speech: bool) -> list[np.ndarray]:
        audio = np.asarray(frame, dtype=np.float32).flatten()
        if audio.size == 0:
            return []

        self._frames.append(audio)
        self._buffered_samples += audio.size
        self._total_samples += audio.size

        if in_speech:
            self._last_speech_sample = self._total_samples

        self._trim_buffer()
        if not self._is_recently_active():
            self._last_emit_sample = 0
            return []

        emitted: list[np.ndarray] = []
        if self._last_emit_sample == 0:
            if self._buffered_samples < self._window_samples:
                return []
            emitted.append(self._slice_last(self._window_samples))
            self._last_emit_sample = self._total_samples
            return emitted

        while self._total_samples - self._last_emit_sample >= self._interval_samples:
            emitted.append(self._slice_last(self._window_samples))
            self._last_emit_sample += self._interval_samples
        return emitted

    def _is_recently_active(self) -> bool:
        return (self._total_samples - self._last_speech_sample) <= self._recent_speech_hold_samples

    def _trim_buffer(self):
        while self._buffered_samples > self._buffer_limit and self._frames:
            removed = self._frames.popleft()
            self._buffered_samples -= removed.size

    def _slice_last(self, num_samples: int) -> np.ndarray:
        if num_samples <= 0 or self._buffered_samples <= 0:
            return np.array([], dtype=np.float32)

        remaining = min(num_samples, self._buffered_samples)
        collected: list[np.ndarray] = []
        for frame in reversed(self._frames):
            if remaining <= 0:
                break
            if frame.size <= remaining:
                collected.append(frame)
                remaining -= frame.size
            else:
                collected.append(frame[-remaining:])
                remaining = 0

        if not collected:
            return np.array([], dtype=np.float32)
        return np.concatenate(list(reversed(collected))).astype(np.float32, copy=False)
