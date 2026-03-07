"""有声・無声フレームを判定する WebRTC VAD ラッパー。"""

import collections

import webrtcvad


class VADDetector:
    """リングバッファを使って継続発話を判定する。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        sensitivity: int = 2,
        silence_threshold_s: float = 0.8,
        speech_ratio: float = 0.50,
    ):
        assert sample_rate in (8000, 16000, 32000, 48000)
        assert frame_duration_ms in (10, 20, 30)
        assert 0 <= sensitivity <= 3

        self.vad = webrtcvad.Vad(sensitivity)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * frame_duration_ms / 1000) * 2  # 16ビット

        # 短い無音区間を平滑化するリングバッファ。
        buf_frames = int(silence_threshold_s * 1000 / frame_duration_ms)
        self._ring = collections.deque(maxlen=buf_frames)
        self._speech_ratio = speech_ratio

        self.in_speech = False

    def process_frame(self, pcm_bytes: bytes) -> bool:
        """PCM 1 フレームを処理し、発話中なら `True` を返す。"""
        if len(pcm_bytes) != self.frame_bytes:
            return self.in_speech

        voiced = self.vad.is_speech(pcm_bytes, self.sample_rate)
        self._ring.append(voiced)

        num_voiced = sum(self._ring)
        ratio = num_voiced / len(self._ring) if self._ring else 0

        if ratio >= self._speech_ratio:
            self.in_speech = True
        elif ratio < (1 - self._speech_ratio):
            self.in_speech = False

        return self.in_speech

    def reset(self):
        self._ring.clear()
        self.in_speech = False
