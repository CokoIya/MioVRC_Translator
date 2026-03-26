from __future__ import annotations

import re
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .recorder import AudioRecorder
from .vad_detector import SileroVADDetector


def _import_soundcard():
    try:
        import soundcard as sc
    except ImportError as exc:
        raise RuntimeError(
            "Desktop audio capture component is unavailable in the current runtime. "
            "If you are running from source, install `soundcard`; if you are using the packaged app, rebuild the bundle with soundcard included."
        ) from exc
    return sc


def list_output_devices() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    default_name = _default_output_device_name_from_sounddevice()

    try:
        sc = _import_soundcard()
        try:
            default_speaker = sc.default_speaker()
            soundcard_default = str(getattr(default_speaker, "name", "") or "").strip()
            if soundcard_default:
                default_name = soundcard_default
        except Exception:
            pass

        for speaker in sc.all_speakers():
            name = str(getattr(speaker, "name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(
                {
                    "name": name,
                    "is_default": name == default_name,
                }
            )
    except Exception:
        pass

    # SoundCard 枚举不全时用 PortAudio 兜底，补漏掉的输出设备
    for device in _sounddevice_output_devices():
        name = str(device.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(
            {
                "name": name,
                "is_default": name == default_name,
            }
        )
    return result


def default_output_device_name() -> str | None:
    for device in list_output_devices():
        if device.get("is_default"):
            return str(device.get("name", "")).strip() or None
    devices = list_output_devices()
    if devices:
        return str(devices[0].get("name", "")).strip() or None
    return None


def desktop_audio_supported() -> bool:
    return bool(list_output_devices())


def _loopback_name_candidates(name: str) -> list[str]:
    """Return normalized candidate forms to handle cross-library naming differences.

    Windows audio devices surface under three different name formats:
      - Windows COM / WASAPI:  "Speakers (Realtek High Definition Audio)"
      - sounddevice/PortAudio: "Speakers (Realtek HD Aud"  (hard-truncated to ~31 chars)
      - soundcard:             "Realtek High Definition Audio"  (model name only, no prefix)

    We emit the full lowercased name, the outer prefix ("speakers"), and the inner model
    name ("realtek high definition audio") so that any two of the three schemes can match.
    """
    s = str(name or "").strip().lower()
    results: list[str] = [" ".join(s.split())]
    # outer prefix before first "(" → "speakers"
    prefix = re.split(r"\s*\(", s, maxsplit=1)[0].strip()
    if prefix and prefix not in results:
        results.append(prefix)
    # inner model name inside first "(…)" → "realtek high definition audio"
    m = re.search(r"\(([^)]+)\)", s)
    if m:
        inner = " ".join(m.group(1).strip().split())
        if inner and inner not in results:
            results.append(inner)
    return results


def _fuzzy_match_loopback(preferred_name: str, loopbacks: list) -> object | None:
    """Return the first loopback microphone whose name matches *preferred_name*."""
    pref_candidates = _loopback_name_candidates(preferred_name)

    # Pass 1: any candidate form equals any candidate form of a loopback device
    for mic in loopbacks:
        mic_name = str(getattr(mic, "name", "") or "").strip()
        mic_candidates = _loopback_name_candidates(mic_name)
        if set(pref_candidates) & set(mic_candidates):
            return mic

    # Pass 2: substring — one normalized form contains the other
    for mic in loopbacks:
        mic_name = str(getattr(mic, "name", "") or "").strip().lower()
        for pc in pref_candidates:
            if pc and (pc in mic_name or mic_name in pc):
                return mic

    return None


class DesktopAudioRecorder(AudioRecorder):
    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 1,
        silence_threshold_s: float = 1.2,
        vad_speech_ratio: float = 0.72,
        vad_activation_threshold_s: float = 0.24,
        output_device_name: str | None = None,
        on_vad_state: Optional[Callable[[bool], None]] = None,
        pre_speech_s: float = 0.30,
        on_chunk: Optional[Callable[[np.ndarray], None]] = None,
        chunk_interval_ms: int = 250,
        chunk_window_s: float = 1.6,
        ring_buffer_s: float = 4.0,
        recent_speech_hold_s: float = 0.8,
        min_segment_s: float = 0.45,
        partial_min_speech_s: float = 0.45,
        vad_min_rms: float = 0.05,
        max_segment_s: float = 12.0,
        denoise_strength: float = 0.0,
        silero_speech_threshold: float = 0.5,
    ):
        super().__init__(
            on_segment=on_segment,
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            vad_sensitivity=vad_sensitivity,
            silence_threshold_s=silence_threshold_s,
            vad_speech_ratio=vad_speech_ratio,
            vad_activation_threshold_s=vad_activation_threshold_s,
            input_device=None,
            on_vad_state=on_vad_state,
            pre_speech_s=pre_speech_s,
            on_chunk=on_chunk,
            chunk_interval_ms=chunk_interval_ms,
            chunk_window_s=chunk_window_s,
            ring_buffer_s=ring_buffer_s,
            recent_speech_hold_s=recent_speech_hold_s,
            min_segment_s=min_segment_s,
            partial_min_speech_s=partial_min_speech_s,
            vad_min_rms=vad_min_rms,
            max_segment_s=max_segment_s,
            denoise_strength=denoise_strength,
        )
        # Replace the webrtcvad-based VAD with Silero VAD for desktop audio.
        # Mic path (AudioRecorder) keeps webrtcvad; this path uses the neural model
        # which is much more robust to background music and game SFX.
        self.vad = SileroVADDetector(
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            silence_threshold_s=silence_threshold_s,
            speech_ratio=vad_speech_ratio,
            activation_threshold_s=vad_activation_threshold_s,
            min_rms=vad_min_rms,
            max_speech_s=max_segment_s,
            speech_threshold=silero_speech_threshold,
        )
        self._output_device_name = str(output_device_name or "").strip()
        self._capture_thread: Optional[threading.Thread] = None
        self._loopback_microphone = None

    def start(self):
        if self._running:
            return
        sc = _import_soundcard()
        self._loopback_microphone = self._resolve_loopback_microphone(sc)
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        self._capture_rate = self.sample_rate
        self._capture_channels = 2
        self._capture_dtype = "float32"
        self._clear_frame_queue()
        self._denoiser.reset()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()

        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()

    def stop(self):
        self._running = False
        self._enqueue_frame(None)
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None
        self._clear_frame_queue()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()
        self._loopback_microphone = None

    def _capture_loop(self) -> None:
        microphone = self._loopback_microphone
        if microphone is None:
            sc = _import_soundcard()
            microphone = self._resolve_loopback_microphone(sc)
        blocksize = max(int(self.sample_rate * self.frame_duration_ms / 1000), 1)

        # Windows 下录的是选中扬声器的回环，浏览器、播放器等走同一设备的音都会进来
        try:
            with microphone.recorder(
                samplerate=self.sample_rate,
                blocksize=blocksize,
            ) as recorder:
                while self._running:
                    frame = recorder.record(numframes=blocksize)
                    if frame is None:
                        continue
                    self._enqueue_frame(np.asarray(frame, dtype=np.float32, order="C"))
        except Exception as exc:
            self._running = False
            self._enqueue_frame(None)
            print(f"[DesktopAudioRecorder] capture loop error: {exc}")

    def _resolve_loopback_microphone(self, sc):
        # Build ordered list of candidate output-device names to search for.
        # Priority: user-specified → soundcard default speaker → sounddevice default output.
        preferred_names: list[str] = []
        if self._output_device_name:
            preferred_names.append(self._output_device_name)
        try:
            default_speaker = sc.default_speaker()
            default_name = str(getattr(default_speaker, "name", "") or "").strip()
            if default_name and default_name not in preferred_names:
                preferred_names.append(default_name)
        except Exception:
            pass
        default_sd_name = _default_output_device_name_from_sounddevice()
        if default_sd_name and default_sd_name not in preferred_names:
            preferred_names.append(default_sd_name)

        # Collect every loopback-capable microphone from soundcard.
        # We never use non-loopback (input) devices — that would silently capture the mic.
        try:
            all_loopbacks = [
                m
                for m in sc.all_microphones(include_loopback=True)
                if bool(getattr(m, "isloopback", False))
            ]
        except Exception:
            all_loopbacks = []

        # Pass 1: try soundcard's native lookup (exact id match).
        for name in preferred_names:
            try:
                mic = sc.get_microphone(id=name, include_loopback=True)
                if mic is not None and bool(getattr(mic, "isloopback", False)):
                    return mic
            except Exception:
                pass

        # Pass 2: fuzzy match across naming-scheme differences
        # (COM full name / sounddevice truncated / soundcard model-only).
        for name in preferred_names:
            matched = _fuzzy_match_loopback(name, all_loopbacks)
            if matched is not None:
                return matched

        # No match found — never fall back to a non-loopback device.
        if all_loopbacks:
            available = ", ".join(
                str(getattr(m, "name", "") or "") for m in all_loopbacks
            )
            raise RuntimeError(
                f"No loopback device matched the selected output device "
                f"({self._output_device_name!r}). "
                f"Available loopback devices: {available}"
            )
        raise RuntimeError(
            "No desktop loopback capture source is available. "
            "Make sure a WASAPI loopback device exists for the selected output."
        )


def _default_output_device_name_from_sounddevice() -> str:
    try:
        default_out = sd.default.device[1]
        if default_out is None or int(default_out) < 0:
            return ""
        return str(sd.query_devices(int(default_out))["name"]).strip()
    except Exception:
        return ""


def _sounddevice_output_devices() -> list[dict[str, object]]:
    default_name = _default_output_device_name_from_sounddevice()
    devices: list[dict[str, object]] = []
    try:
        hostapis = sd.query_hostapis()
        preferred_hostapi_ids = [
            index
            for index, hostapi in enumerate(hostapis)
            if "WASAPI" in str(hostapi.get("name", "")).upper()
        ]
        if not preferred_hostapi_ids:
            preferred_hostapi_ids = list(range(len(hostapis)))

        for index, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_output_channels", 0)) <= 0:
                continue
            if int(dev.get("hostapi", -1)) not in preferred_hostapi_ids:
                continue
            name = str(dev.get("name", "")).strip()
            if not name:
                continue
            devices.append(
                {
                    "name": name,
                    "is_default": name == default_name,
                    "index": index,
                }
            )
    except Exception:
        return []
    return devices
