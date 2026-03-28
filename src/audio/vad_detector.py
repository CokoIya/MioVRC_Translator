import collections
from io import BytesIO
import logging
import os
from pathlib import Path
import threading

import numpy as np
import webrtcvad

_log = logging.getLogger(__name__)

# Pre-bundled Silero VAD TorchScript model (downloaded at build time).
_SILERO_LOCAL_JIT = os.path.join(os.path.dirname(__file__), "models", "silero_vad.jit")


class VADDetector:
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
        self.frame_bytes = int(sample_rate * frame_duration_ms / 1000) * 2

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
        if len(pcm_bytes) != self.frame_bytes:
            return self.in_speech

        voiced = self._is_voiced(pcm_bytes)
        self._activation_window.append(voiced)

        if self.in_speech:
            self._speech_frames += 1
            # Force-close very long speech segments so the queue can drain.
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

        # Start speech only when the activation window is stable enough.
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


class SileroVADDetector:
    """Neural-network VAD using Silero VAD, tolerant of background music and SFX."""

    # Silero VAD requires exactly 512 samples per inference at 16 kHz (32 ms).
    CHUNK_SAMPLES = 512

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        silence_threshold_s: float = 1.2,
        speech_ratio: float = 0.72,
        activation_threshold_s: float = 0.24,
        min_rms: float = 0.02,
        max_speech_s: float = 12.0,
        speech_threshold: float = 0.5,
    ):
        if sample_rate != 16000:
            raise ValueError("SileroVADDetector only supports sample_rate=16000")

        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self._min_rms = max(float(min_rms), 0.0)
        self._speech_threshold = speech_threshold

        activation_frames = max(1, int(activation_threshold_s * 1000 / frame_duration_ms))
        self._activation_window = collections.deque(maxlen=activation_frames)
        self._silence_frames = max(1, int(silence_threshold_s * 1000 / frame_duration_ms))
        self._speech_ratio = speech_ratio
        self._max_speech_frames = (
            max(1, int(max_speech_s * 1000 / frame_duration_ms))
            if max_speech_s and max_speech_s > 0
            else None
        )

        self._trailing_silence = 0
        self._speech_frames = 0
        self.in_speech = False
        self._sample_buffer = np.zeros(0, dtype=np.float32)

        self._model = None
        self._model_error = None
        self._model_error_logged = False
        self._model_lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        if self._model_error is not None:
            raise self._model_error

        with self._model_lock:
            if self._model is None:
                import torch

                try:
                    if os.path.isfile(_SILERO_LOCAL_JIT):
                        # Load from bytes so frozen apps installed under
                        # non-ASCII Windows paths still work.
                        model_bytes = Path(_SILERO_LOCAL_JIT).read_bytes()
                        model = torch.jit.load(BytesIO(model_bytes), map_location="cpu")
                    else:
                        try:
                            model, _ = torch.hub.load(
                                "snakers4/silero-vad",
                                "silero_vad",
                                trust_repo=True,
                                verbose=False,
                            )
                        except TypeError:
                            model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad")
                    model.eval()
                    self._model = model
                except Exception as exc:
                    self._model_error = exc
                    raise

        return self._model

    def process_frame(self, pcm_bytes: bytes) -> bool:
        """Accept int16 PCM bytes and return the current in-speech state."""
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._sample_buffer = np.concatenate([self._sample_buffer, audio])

        while len(self._sample_buffer) >= self.CHUNK_SAMPLES:
            chunk = self._sample_buffer[: self.CHUNK_SAMPLES]
            self._sample_buffer = self._sample_buffer[self.CHUNK_SAMPLES :]
            voiced = self._is_voiced(chunk)
            self._update_state(voiced)

        return self.in_speech

    def reset(self):
        self._activation_window.clear()
        self._trailing_silence = 0
        self._speech_frames = 0
        self.in_speech = False
        self._sample_buffer = np.zeros(0, dtype=np.float32)
        if self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass

    def _is_voiced(self, chunk: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms < self._min_rms:
            # Log roughly once per second while audio is too quiet.
            self._rms_log_counter = getattr(self, "_rms_log_counter", 0) + 1
            if self._rms_log_counter >= 32:
                _log.debug(
                    "SileroVAD: audio below min_rms threshold - peak_rms=%.4f min_rms=%.4f "
                    "(audio too quiet; check system volume or lower vad_min_rms)",
                    rms,
                    self._min_rms,
                )
                self._rms_log_counter = 0
            return False

        self._rms_log_counter = 0
        try:
            import torch

            model = self._get_model()
            with torch.no_grad():
                tensor = torch.from_numpy(chunk).unsqueeze(0)
                prob = model(tensor, self.sample_rate).item()
            _log.debug(
                "SileroVAD: rms=%.4f prob=%.4f threshold=%.4f voiced=%s",
                rms,
                prob,
                self._speech_threshold,
                prob >= self._speech_threshold,
            )
            return prob >= self._speech_threshold
        except Exception:
            if not self._model_error_logged:
                _log.error("SileroVAD: inference failed - VAD will not activate", exc_info=True)
                self._model_error_logged = True
            return False

    def _update_state(self, voiced: bool) -> None:
        self._activation_window.append(voiced)

        if self.in_speech:
            self._speech_frames += 1
            if (
                self._max_speech_frames is not None
                and self._speech_frames >= self._max_speech_frames
            ):
                self._finish_speech()
                return
            if voiced:
                self._trailing_silence = 0
            else:
                self._trailing_silence += 1
                if self._trailing_silence >= self._silence_frames:
                    self._finish_speech()
            return

        ratio = sum(self._activation_window) / len(self._activation_window)
        if (
            len(self._activation_window) == self._activation_window.maxlen
            and ratio >= self._speech_ratio
        ):
            self.in_speech = True
            self._trailing_silence = 0
            self._speech_frames = 1

    def _finish_speech(self):
        self.in_speech = False
        self._trailing_silence = 0
        self._speech_frames = 0
        self._activation_window.clear()
