from __future__ import annotations

import numpy as np


class AdaptiveDenoiser:
    def __init__(self, strength: float = 0.0):
        self._strength = min(max(float(strength), 0.0), 1.0)
        self.reset()

    def reset(self) -> None:
        self._noise_magnitude: np.ndarray | None = None
        self._noise_rms = 0.0

    def process(self, frame: np.ndarray, update_profile: bool) -> np.ndarray:
        audio = np.asarray(frame, dtype=np.float32)
        if audio.size == 0 or self._strength <= 0.0:
            return audio.astype(np.float32, copy=False)

        rms = float(np.sqrt(np.mean(np.square(audio))))
        should_update = update_profile and (
            self._noise_magnitude is None
            or rms <= max(self._noise_rms * (1.7 - 0.5 * self._strength), 0.018)
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
                    self._noise_magnitude * 0.92 + magnitude.astype(np.float32) * 0.08
                )
                self._noise_rms = self._noise_rms * 0.92 + rms * 0.08

        if self._noise_magnitude is None:
            return audio.astype(np.float32, copy=False)

        subtract_scale = 0.45 + 2.55 * self._strength
        floor_scale = max(0.08, 0.34 - 0.18 * self._strength)
        cleaned_magnitude = np.maximum(
            magnitude - self._noise_magnitude * subtract_scale,
            self._noise_magnitude * floor_scale,
        )

        restored = np.fft.irfft(cleaned_magnitude * np.exp(1j * phase), n=audio.size)
        blend = 0.22 + 0.63 * self._strength
        output = audio * (1.0 - blend) + restored.astype(np.float32) * blend

        noise_gate = max(self._noise_rms * (1.08 + 2.5 * self._strength), 0.002)
        if rms < noise_gate:
            attenuation = max(
                0.02,
                (rms / max(noise_gate, 1e-6)) ** (0.9 + 2.1 * self._strength),
            )
            output *= attenuation

        return np.clip(output, -1.0, 1.0).astype(np.float32, copy=False)
