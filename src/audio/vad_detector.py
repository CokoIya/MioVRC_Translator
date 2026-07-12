# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

import collections
import gc
import hashlib
from io import BytesIO
import logging
import os
from pathlib import Path
import threading
import time

import numpy as np
import webrtcvad

# Import envelope follower for smooth audio level processing
try:
    from src.audio.envelope_follower import EnvelopeFollower
    ENVELOPE_AVAILABLE = True
except ImportError:
    ENVELOPE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Pre-bundled Silero VAD TorchScript model (downloaded at build time).
_SILERO_LOCAL_JIT = os.path.join(os.path.dirname(__file__), "models", "silero_vad.jit")
_SILERO_LOCAL_JIT_SIZE = 2_272_526
_SILERO_LOCAL_JIT_SHA256 = (
    "e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720"
)


def silero_vad_model_available() -> bool:
    """Return whether the pinned local Silero model is present and authentic."""

    path = Path(_SILERO_LOCAL_JIT)
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != _SILERO_LOCAL_JIT_SIZE:
            return False
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        return digest == _SILERO_LOCAL_JIT_SHA256
    except OSError:
        return False


class VADDetector:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        sensitivity: int = 2,
        silence_threshold_s: float = 0.65,
        speech_ratio: float = 0.6,
        activation_threshold_s: float = 0.2,
        min_rms: float = 0.012,
        max_speech_s: float = 6.0,
        use_envelope_follower: bool = True,
    ):
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(
                "sample_rate must be one of 8000, 16000, 32000, or 48000 Hz"
            )
        if frame_duration_ms not in (10, 20, 30):
            raise ValueError("frame_duration_ms must be 10, 20, or 30 ms")
        if not 0 <= sensitivity <= 3:
            raise ValueError("sensitivity must be between 0 and 3")

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

        # Envelope follower for smooth RMS (tomari-guruguru inspired)
        self.envelope_follower = None
        if use_envelope_follower and ENVELOPE_AVAILABLE:
            self.envelope_follower = EnvelopeFollower(
                attack_rate=0.6,
                release_rate=0.12,
                gain=1.0  # Gain is applied separately
            )
            logger.info("VADDetector: Envelope follower enabled")

        # Store latest smooth RMS for external monitoring
        self.latest_smooth_rms = 0.0

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

        # Calculate raw RMS
        raw_rms = float(np.sqrt(np.mean(np.square(audio / 32768.0))))

        # Apply envelope follower for smooth RMS (if enabled)
        if self.envelope_follower:
            smooth_rms = self.envelope_follower.process(raw_rms)
            self.latest_smooth_rms = smooth_rms
            rms = smooth_rms
        else:
            rms = raw_rms
            self.latest_smooth_rms = raw_rms

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
        if self.envelope_follower:
            self.envelope_follower.reset()
        self.latest_smooth_rms = 0.0

    def activation_speech_samples(self, fallback_frame_samples: int) -> int:
        """Return voiced samples already confirmed by the activation window."""

        frame_samples = max(int(self.frame_bytes // 2), int(fallback_frame_samples), 1)
        return sum(bool(item) for item in self._activation_window) * frame_samples

    def set_envelope_params(self, attack_rate: float, release_rate: float):
        """动态调整包络参数（用于实时调整）"""
        if self.envelope_follower:
            self.envelope_follower.set_attack_rate(attack_rate)
            self.envelope_follower.set_release_rate(release_rate)
            logger.debug(f"Envelope params updated: attack={attack_rate}, release={release_rate}")

    def get_smooth_rms(self) -> float:
        """获取平滑后的 RMS 值（用于音量表显示）"""
        return self.latest_smooth_rms


class SileroVADDetector:
    """Neural-network VAD using Silero VAD, tolerant of background music and SFX."""

    # Silero VAD requires exactly 512 samples per inference at 16 kHz (32 ms).
    CHUNK_SAMPLES = 512

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        silence_threshold_s: float = 1.2,
        speech_ratio: float = 0.6,
        activation_threshold_s: float = 0.2,
        min_rms: float = 0.02,
        max_speech_s: float = 6.0,
        speech_threshold: float = 0.5,
        use_envelope_follower: bool = True,
    ):
        if sample_rate != 16000:
            raise ValueError("SileroVADDetector only supports sample_rate=16000")
        self._last_prob_log_at: float = 0.0

        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self._min_rms = max(float(min_rms), 0.0)
        self._speech_threshold = speech_threshold
        self._fallback_settings = {
            "sample_rate": sample_rate,
            "frame_duration_ms": frame_duration_ms,
            "sensitivity": 0,
            "silence_threshold_s": silence_threshold_s,
            "speech_ratio": speech_ratio,
            "activation_threshold_s": activation_threshold_s,
            "min_rms": min_rms,
            "max_speech_s": max_speech_s,
            "use_envelope_follower": use_envelope_follower,
        }

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
        self._model_lock = threading.Lock()
        self._fallback_vad: VADDetector | None = None

        # Envelope follower for smooth RMS (tomari-guruguru inspired)
        self.envelope_follower = None
        if use_envelope_follower and ENVELOPE_AVAILABLE:
            self.envelope_follower = EnvelopeFollower(
                attack_rate=0.6,
                release_rate=0.12,
                gain=1.0
            )
            logger.info("SileroVADDetector: Envelope follower enabled")

        self.latest_smooth_rms = 0.0

    def _get_model(self):
        if self._model is not None:
            return self._model
        if self._model_error is not None:
            raise self._model_error

        with self._model_lock:
            if self._model is None:
                try:
                    if not silero_vad_model_available():
                        raise RuntimeError(
                            "The pinned Silero VAD model is missing or failed integrity verification"
                        )
                    import torch

                    # Load only verified, build-time-bundled bytes. Runtime
                    # remote-code loading through torch.hub is forbidden.
                    model_bytes = Path(_SILERO_LOCAL_JIT).read_bytes()
                    model = torch.jit.load(BytesIO(model_bytes), map_location="cpu")
                    model.eval()
                    self._model = model
                except Exception as exc:
                    self._model_error = exc
                    raise

        return self._model

    def _activate_fallback(self, error: BaseException) -> VADDetector:
        with self._model_lock:
            if self._fallback_vad is None:
                self._fallback_vad = VADDetector(**self._fallback_settings)
                logger.warning(
                    "Silero VAD is unavailable; using WebRTC VAD fallback: %s",
                    error,
                )
            return self._fallback_vad

    def prewarm(self) -> None:
        """Load the Silero VAD model in the background so the first audio frame
        is not blocked by torch.jit.load. Safe to call multiple times."""
        try:
            self._get_model()
        except Exception as exc:
            self._activate_fallback(exc)

    def process_frame(self, pcm_bytes: bytes) -> bool:
        """Accept int16 PCM bytes and return the current in-speech state."""
        if self._fallback_vad is not None:
            result = self._fallback_vad.process_frame(pcm_bytes)
            self.in_speech = result
            self.latest_smooth_rms = self._fallback_vad.get_smooth_rms()
            return result

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._sample_buffer = np.concatenate([self._sample_buffer, audio])

        while len(self._sample_buffer) >= self.CHUNK_SAMPLES:
            chunk = self._sample_buffer[: self.CHUNK_SAMPLES]
            self._sample_buffer = self._sample_buffer[self.CHUNK_SAMPLES :]
            voiced = self._is_voiced(chunk)
            if self._fallback_vad is not None:
                self._sample_buffer = np.zeros(0, dtype=np.float32)
                result = self._fallback_vad.process_frame(pcm_bytes)
                self.in_speech = result
                self.latest_smooth_rms = self._fallback_vad.get_smooth_rms()
                return result
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
        if self._fallback_vad is not None:
            self._fallback_vad.reset()
        if self.envelope_follower:
            self.envelope_follower.reset()
        self.latest_smooth_rms = 0.0

    def close(self) -> None:
        """Release the per-recorder TorchScript model after capture stops."""

        with self._model_lock:
            model = self._model
            self._model = None
            self._model_error = None
            self._fallback_vad = None
        self.reset()
        close = getattr(model, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Silero VAD model close failed", exc_info=True)
        del model
        gc.collect()

    def activation_speech_samples(self, fallback_frame_samples: int) -> int:
        """Return voiced samples already confirmed by the active VAD."""

        if self._fallback_vad is not None:
            return self._fallback_vad.activation_speech_samples(
                fallback_frame_samples
            )
        return sum(bool(item) for item in self._activation_window) * self.CHUNK_SAMPLES

    def set_envelope_params(self, attack_rate: float, release_rate: float):
        """动态调整包络参数（用于实时调整）"""
        if self.envelope_follower:
            self.envelope_follower.set_attack_rate(attack_rate)
            self.envelope_follower.set_release_rate(release_rate)
        if self._fallback_vad is not None:
            self._fallback_vad.set_envelope_params(attack_rate, release_rate)

    def get_smooth_rms(self) -> float:
        """获取平滑后的 RMS 值（用于音量表显示）"""
        if self._fallback_vad is not None:
            return self._fallback_vad.get_smooth_rms()
        return self.latest_smooth_rms

    def _is_voiced(self, chunk: np.ndarray) -> bool:
        raw_rms = float(np.sqrt(np.mean(np.square(chunk))))

        # Apply envelope follower for smooth RMS (if enabled)
        if self.envelope_follower:
            smooth_rms = self.envelope_follower.process(raw_rms)
            self.latest_smooth_rms = smooth_rms
            rms = smooth_rms
        else:
            rms = raw_rms
            self.latest_smooth_rms = raw_rms

        now = time.monotonic()
        if rms < self._min_rms:
            if now - self._last_prob_log_at >= 5.0:
                self._last_prob_log_at = now
                logger.info(
                    "Silero VAD gate: rms=%.5f min_rms=%.4f BLOCKED (no inference)",
                    rms,
                    self._min_rms,
                )
            return False

        try:
            import torch

            model = self._get_model()
            with torch.no_grad():
                tensor = torch.from_numpy(chunk).unsqueeze(0)
                prob = model(tensor, self.sample_rate).item()
            voiced = prob >= self._speech_threshold
            if now - self._last_prob_log_at >= 5.0:
                self._last_prob_log_at = now
                logger.info(
                    "Silero VAD sample: rms=%.4f prob=%.3f threshold=%.2f voiced=%s",
                    rms,
                    prob,
                    self._speech_threshold,
                    voiced,
                )
            return voiced
        except Exception as exc:
            self._activate_fallback(exc)
            if now - self._last_prob_log_at >= 5.0:
                self._last_prob_log_at = now
                logger.warning(
                    "Silero VAD inference failed (rms=%.4f): %s",
                    rms,
                    exc,
                )
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
