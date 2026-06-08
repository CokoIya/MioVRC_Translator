from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


DEFAULT_MIN_RMS_FLOOR = 0.003
DEFAULT_MIN_RMS_CEILING = 0.04


def _finite_positive_values(values: Iterable[object]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0.0 and parsed != float("inf"):
            result.append(parsed)
    return result


def percentile(values: Iterable[object], percent: float) -> float:
    samples = sorted(_finite_positive_values(values))
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    p = max(0.0, min(float(percent), 100.0)) / 100.0
    pos = (len(samples) - 1) * p
    lower = int(pos)
    upper = min(lower + 1, len(samples) - 1)
    weight = pos - lower
    return samples[lower] * (1.0 - weight) + samples[upper] * weight


def clamp(value: float, lower: float, upper: float) -> float:
    return max(float(lower), min(float(value), float(upper)))


@dataclass(frozen=True)
class VadCalibrationResult:
    noise_floor: float
    speech_floor: float
    recommended_min_rms: float
    recommended_silence_s: float
    noise_sample_count: int
    speech_sample_count: int
    confidence: str

    def as_config_patch(self, *, target: str) -> dict[str, dict[str, float]]:
        section = "vrc_listen" if str(target or "").strip() == "vrc_listen" else "audio"
        silence_key = "tail_silence_s" if section == "vrc_listen" else "vad_silence_threshold"
        return {
            section: {
                "vad_min_rms": round(self.recommended_min_rms, 4),
                silence_key: round(self.recommended_silence_s, 2),
            }
        }


def recommend_vad_settings(
    noise_rms: Iterable[object],
    speech_rms: Iterable[object],
    *,
    current_silence_s: float = 0.65,
    min_rms_floor: float = DEFAULT_MIN_RMS_FLOOR,
    min_rms_ceiling: float = DEFAULT_MIN_RMS_CEILING,
) -> VadCalibrationResult:
    noise_samples = _finite_positive_values(noise_rms)
    speech_samples = _finite_positive_values(speech_rms)
    noise_floor = percentile(noise_samples, 90)
    speech_floor = percentile(speech_samples, 30)
    raw_min_rms = (noise_floor * 1.8 + speech_floor * 0.4) / 2.0
    recommended_min_rms = clamp(raw_min_rms, min_rms_floor, min_rms_ceiling)

    try:
        silence_s = clamp(float(current_silence_s), 0.35, 1.2)
    except (TypeError, ValueError):
        silence_s = 0.65
    if speech_floor > 0.0 and noise_floor > 0.0:
        ratio = speech_floor / max(noise_floor, 1e-6)
        if ratio < 2.2:
            silence_s = max(silence_s, 0.75)
        elif ratio > 5.0:
            silence_s = min(silence_s, 0.6)

    if len(noise_samples) >= 20 and len(speech_samples) >= 20:
        confidence = "high"
    elif len(noise_samples) >= 8 and len(speech_samples) >= 8:
        confidence = "medium"
    else:
        confidence = "low"

    return VadCalibrationResult(
        noise_floor=round(noise_floor, 6),
        speech_floor=round(speech_floor, 6),
        recommended_min_rms=round(recommended_min_rms, 6),
        recommended_silence_s=round(silence_s, 3),
        noise_sample_count=len(noise_samples),
        speech_sample_count=len(speech_samples),
        confidence=confidence,
    )


def rms_from_snapshot(snapshot: Mapping[str, object] | None) -> float | None:
    if not isinstance(snapshot, Mapping):
        return None
    for key in ("last_frame_rms", "last_prepared_rms", "last_rms"):
        try:
            value = snapshot.get(key)
        except Exception:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0.0:
            return parsed
    return None


class VadCalibrationService:
    """Collect RMS samples and calculate VAD recommendations."""

    def __init__(self, *, current_silence_s: float = 0.65) -> None:
        self.current_silence_s = current_silence_s
        self.noise_samples: list[float] = []
        self.speech_samples: list[float] = []

    def reset_noise(self) -> None:
        self.noise_samples.clear()

    def reset_speech(self) -> None:
        self.speech_samples.clear()

    def add_noise_rms(self, value: object) -> bool:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return False
        if parsed < 0.0:
            return False
        self.noise_samples.append(parsed)
        return True

    def add_speech_rms(self, value: object) -> bool:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return False
        if parsed < 0.0:
            return False
        self.speech_samples.append(parsed)
        return True

    def add_noise_snapshot(self, snapshot: Mapping[str, object] | None) -> bool:
        value = rms_from_snapshot(snapshot)
        return self.add_noise_rms(value) if value is not None else False

    def add_speech_snapshot(self, snapshot: Mapping[str, object] | None) -> bool:
        value = rms_from_snapshot(snapshot)
        return self.add_speech_rms(value) if value is not None else False

    def result(self) -> VadCalibrationResult:
        return recommend_vad_settings(
            self.noise_samples,
            self.speech_samples,
            current_silence_s=self.current_silence_s,
        )
