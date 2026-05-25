from __future__ import annotations

import numpy as np


class AdaptiveDenoiser:
    def __init__(self, strength: float = 0.0):
        self._strength = min(max(float(strength), 0.0), 1.0)
        self._quiet_frames_required = 2 + int(round(self._strength * 2.0))
        self.reset()

    def reset(self) -> None:
        self._noise_magnitude: np.ndarray | None = None
        self._noise_rms = 0.0
        self._quiet_frames = 0

    def process(self, frame: np.ndarray, update_profile: bool) -> np.ndarray:
        audio = np.asarray(frame, dtype=np.float32)
        if audio.size == 0 or self._strength <= 0.0:
            return audio.astype(np.float32, copy=False)

        rms = float(np.sqrt(np.mean(np.square(audio))))
        quiet_threshold = max(self._noise_rms * (1.45 - 0.3 * self._strength), 0.014)
        if update_profile and rms <= quiet_threshold:
            self._quiet_frames += 1
        else:
            self._quiet_frames = 0

        # Wait for several consistently quiet frames before refreshing the
        # noise profile so speech onsets are less likely to be learned as noise.
        should_update = update_profile and (
            self._noise_magnitude is None
            or self._quiet_frames >= self._quiet_frames_required
        )

        spectrum = np.fft.rfft(audio)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        if should_update:
            if self._noise_magnitude is None:
                self._noise_magnitude = magnitude.astype(np.float32, copy=True)
                self._noise_rms = rms
            else:
                self._noise_magnitude = (
                    self._noise_magnitude * 0.9 + magnitude.astype(np.float32) * 0.1
                )
                self._noise_rms = self._noise_rms * 0.9 + rms * 0.1

        if self._noise_magnitude is None:
            return audio.astype(np.float32, copy=False)

        subtract_scale = 0.35 + 2.9 * self._strength
        floor_scale = max(0.05, 0.32 - 0.22 * self._strength)
        cleaned_magnitude = np.maximum(
            magnitude - self._noise_magnitude * subtract_scale,
            self._noise_magnitude * floor_scale,
        )

        restored = np.fft.irfft(cleaned_magnitude * np.exp(1j * phase), n=audio.size)
        blend = 0.16 + 0.74 * self._strength
        output = audio * (1.0 - blend) + restored.astype(np.float32) * blend

        noise_gate = max(self._noise_rms * (1.1 + 3.0 * self._strength), 0.0025)
        if rms < noise_gate:
            attenuation = max(
                0.02,
                (rms / max(noise_gate, 1e-6)) ** (1.0 + 2.4 * self._strength),
            )
            output *= attenuation

        return np.clip(output, -1.0, 1.0).astype(np.float32, copy=False)
