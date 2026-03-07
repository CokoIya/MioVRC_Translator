"""有声・無声フレームを判定する WebRTC VAD ラッパー。"""

import collections

import numpy as np
import webrtcvad


class VADDetector:
    """リングバッファを使って継続発話を判定する。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        sensitivity: int = 2,
        silence_threshold_s: float = 0.8,
        speech_ratio: float = 0.72,
        activation_threshold_s: float = 0.24,
        min_rms: float = 0.012,
        max_speech_s: float = 12.0,
    ):
        assert sample_rate in (8000, 16000, 32000, 48000)
        assert frame_duration_ms in (10, 20, 30)
        assert 0 <= sensitivity <= 3

        self.vad = webrtcvad.Vad(sensitivity)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * frame_duration_ms / 1000) * 2  # 16ビット

        activation_frames = max(1, int(activation_threshold_s * 1000 / frame_duration_ms))
        self._activation_window = collections.deque(maxlen=activation_frames)
        self._silence_frames = max(1, int(silence_threshold_s * 1000 / frame_duration_ms))
        self._speech_ratio = speech_ratio
        self._min_rms = max(float(min_rms), 0.0)
        self._max_speech_frames = (
            max(1, int(max_speech_s * 1000 / frame_duration_ms))
            if max_speech_s and max_speech_s > 0
            else None
        )
        self._trailing_silence = 0
        self._speech_frames = 0

        self.in_speech = False

    def process_frame(self, pcm_bytes: bytes) -> bool:
        """PCM 1 フレームを処理し、発話中なら `True` を返す。"""
        if len(pcm_bytes) != self.frame_bytes:
            return self.in_speech

        voiced = self._is_voiced(pcm_bytes)
        self._activation_window.append(voiced)

        if self.in_speech:
            self._speech_frames += 1
            if (
                self._max_speech_frames is not None
                and self._speech_frames >= self._max_speech_frames
            ):
                self._finish_speech()
                return False
            if voiced:
                self._trailing_silence = 0
            else:
                self._trailing_silence += 1
                if self._trailing_silence >= self._silence_frames:
                    self._finish_speech()
            return self.in_speech

        ratio = sum(self._activation_window) / len(self._activation_window)
        if (
            len(self._activation_window) == self._activation_window.maxlen
            and ratio >= self._speech_ratio
        ):
            self.in_speech = True
            self._trailing_silence = 0
            self._speech_frames = 1

        return self.in_speech

    def _is_voiced(self, pcm_bytes: bytes) -> bool:
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if audio.size == 0:
            return False

        rms = float(np.sqrt(np.mean(np.square(audio / 32768.0))))
        if rms < self._min_rms:
            return False
        return self.vad.is_speech(pcm_bytes, self.sample_rate)

    def _finish_speech(self):
        self.in_speech = False
        self._trailing_silence = 0
        self._speech_frames = 0
        self._activation_window.clear()

    def reset(self):
        self._activation_window.clear()
        self._trailing_silence = 0
        self._speech_frames = 0
        self.in_speech = False
