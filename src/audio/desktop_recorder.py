from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .recorder import AudioRecorder
from .vad_detector import SileroVADDetector

logger = logging.getLogger(__name__)

_MAX_DESKTOP_CAPTURE_CHANNELS = 8
_COMMON_DESKTOP_CAPTURE_RATES = (
    384000,
    352800,
    192000,
    176400,
    96000,
    88200,
    48000,
    44100,
    32000,
    24000,
    22050,
    16000,
)


# ---------------------------------------------------------------------------
# PyAudioWPatch import helper
# ---------------------------------------------------------------------------

def _import_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "Desktop audio capture component is unavailable in the current runtime. "
            "If you are running from source, install `PyAudioWPatch`; "
            "if you are using the packaged app, rebuild the bundle with PyAudioWPatch included."
        ) from exc
    return pyaudio


# ---------------------------------------------------------------------------
# Public device enumeration (uses PyAudioWPatch / PortAudio — same names as sounddevice)
# ---------------------------------------------------------------------------

def list_output_devices() -> list[dict[str, object]]:
    """Return all active WASAPI output devices.

    Names are in PortAudio format, identical to sounddevice, so there is no
    cross-library name mismatch when the user picks a device.
    """
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    p = None
    try:
        pa = _import_pyaudio()
        p = pa.PyAudio()
        try:
            wasapi_info = p.get_host_api_info_by_type(pa.paWASAPI)
        except OSError:
            return result

        wasapi_index = int(wasapi_info["index"])
        default_out_idx = int(wasapi_info.get("defaultOutputDevice", -1))
        default_name = ""
        if default_out_idx >= 0:
            try:
                default_name = str(
                    p.get_device_info_by_index(default_out_idx).get("name", "")
                ).strip()
            except Exception:
                pass

        for i in range(int(p.get_device_count())):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            if int(info.get("hostApi", -1)) != wasapi_index:
                continue
            if int(info.get("maxOutputChannels", 0)) <= 0:
                continue
            name = str(info.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append({"name": name, "is_default": name == default_name})
    except Exception as exc:
        logger.warning("Failed to enumerate WASAPI output devices: %s", exc)
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
    return result


def default_output_device_name() -> str | None:
    devices = list_output_devices()
    for device in devices:
        if device.get("is_default"):
            return str(device.get("name", "")).strip() or None
    if devices:
        return str(devices[0].get("name", "")).strip() or None
    return None


def desktop_audio_supported() -> bool:
    return bool(list_output_devices())


# ---------------------------------------------------------------------------
# Name-matching helpers
# ---------------------------------------------------------------------------

def _loopback_name_candidates(name: str) -> list[str]:
    """Return normalized candidate forms for cross-library name matching.

    PyAudioWPatch loopback names are "<output_name> [Loopback]", so the full
    lowercased name, the outer prefix ("speakers"), and the inner model name
    ("realtek high definition audio") all serve as match candidates.
    """
    s = str(name or "").strip().lower()
    # Strip trailing " [loopback]" suffix so candidates represent the output name
    s = re.sub(r"\s*\[loopback\]\s*$", "", s).strip()
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
    """Return the first loopback device whose name matches *preferred_name*.

    Works with both dicts (PyAudioWPatch) and attribute-style objects.
    """
    pref_candidates = _loopback_name_candidates(preferred_name)

    def _name(item) -> str:
        if isinstance(item, dict):
            return str(item.get("name", "") or "").strip()
        return str(getattr(item, "name", "") or "").strip()

    # Pass 1: candidate set intersection (catches exact normalised matches)
    for lb in loopbacks:
        lb_candidates = _loopback_name_candidates(_name(lb))
        if set(pref_candidates) & set(lb_candidates):
            return lb

    # Pass 2: substring (catches truncated sounddevice names vs. full COM names)
    for lb in loopbacks:
        lb_name = _name(lb).lower()
        for pc in pref_candidates:
            if pc and (pc in lb_name or lb_name in pc):
                return lb

    return None


# ---------------------------------------------------------------------------
# DesktopAudioRecorder
# ---------------------------------------------------------------------------

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
        on_runtime_error: Optional[Callable[[str], None]] = None,
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
        self._loopback_device_info: Optional[dict] = None
        self._on_runtime_error = on_runtime_error
        self._last_error: str | None = None
        self._stats_lock = threading.Lock()
        self._last_frame_at = 0.0
        self._last_non_silent_at = 0.0
        self._last_rms = 0.0
        self._total_frames = 0
        self._non_silent_frames = 0
        self._stream_config: Optional[dict[str, int]] = None

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _report_runtime_error(self, exc: BaseException | str) -> None:
        # 记录错误并通过回调通知 UI
        message = str(exc).strip()
        if not message:
            message = exc.__class__.__name__ if isinstance(exc, BaseException) else "Unknown error"
        self._last_error = message
        logger.warning("DesktopAudioRecorder runtime error: %s", message)
        if self._on_runtime_error is not None:
            try:
                self._on_runtime_error(message)
            except Exception:
                pass

    def start(self):
        if self._running:
            return

        # Resolve and validate the loopback device before doing anything else.
        pa = _import_pyaudio()
        p = pa.PyAudio()
        try:
            self._loopback_device_info = self._resolve_loopback_device_info(p, pa)
            self._stream_config = self._select_stream_config(p, pa, self._loopback_device_info)
        finally:
            p.terminate()

        if self._stream_config is None:
            raise RuntimeError("No valid desktop audio capture configuration was found")

        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        self._capture_rate = int(self._stream_config["rate"])
        self._capture_channels = int(self._stream_config["channels"])
        self._capture_dtype = "float32"
        self._active_device_name = (
            str(self._loopback_device_info.get("name", "")).strip()
            if self._loopback_device_info is not None
            else None
        )
        self._last_error = None
        with self._stats_lock:
            self._last_frame_at = 0.0
            self._last_non_silent_at = 0.0
            self._last_rms = 0.0
            self._total_frames = 0
            self._non_silent_frames = 0
        self._clear_frame_queue()
        self._denoiser.reset()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()

        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info(
            "DesktopAudioRecorder started (requested_output=%s loopback_device=%s rate=%s channels=%s)",
            self._output_device_name or "auto",
            self._loopback_device_info.get("name") if self._loopback_device_info else None,
            self._capture_rate,
            self._capture_channels,
        )

    def stop(self):
        self._running = False
        self._enqueue_frame(None)  # unblock worker thread
        if self._capture_thread:
            self._capture_thread.join(timeout=3.0)
            self._capture_thread = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None
        self._clear_frame_queue()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()
        self._loopback_device_info = None
        self._stream_config = None
        self._active_device_name = None
        logger.info("DesktopAudioRecorder stopped (requested_output=%s)", self._output_device_name or "auto")

    def diagnostics_snapshot(self) -> dict[str, object]:
        with self._stats_lock:
            return {
                "last_frame_at": self._last_frame_at,
                "last_non_silent_at": self._last_non_silent_at,
                "last_rms": self._last_rms,
                "total_frames": self._total_frames,
                "non_silent_frames": self._non_silent_frames,
                "requested_output_device": self._output_device_name or None,
                "loopback_device": (
                    str(self._loopback_device_info.get("name", "")).strip()
                    if self._loopback_device_info is not None
                    else None
                ),
                "capture_rate": self._capture_rate,
                "capture_channels": self._capture_channels,
                "last_error": self._last_error,
            }

    def _update_capture_stats(self, arr: np.ndarray) -> None:
        now = time.monotonic()
        rms = float(np.sqrt(np.mean(np.square(arr, dtype=np.float64)))) if arr.size else 0.0
        with self._stats_lock:
            self._last_frame_at = now
            self._last_rms = rms
            self._total_frames += 1
            if rms >= 0.002:
                self._last_non_silent_at = now
                self._non_silent_frames += 1

    # ------------------------------------------------------------------
    # Capture loop — runs in a dedicated daemon thread
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        device_info = self._loopback_device_info
        stream_config = self._stream_config
        if device_info is None or stream_config is None:
            self._running = False
            self._enqueue_frame(None)
            return

        rate = int(stream_config["rate"])
        channels = int(stream_config["channels"])
        device_index = int(device_info["index"])
        blocksize = int(stream_config["blocksize"])

        pa = None
        p = None
        stream = None
        runtime_error: BaseException | None = None
        try:
            pa = _import_pyaudio()
            p = pa.PyAudio()
            stream = p.open(
                format=pa.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=blocksize,
            )
            while self._running:
                try:
                    data = stream.read(blocksize, exception_on_overflow=False)
                except Exception as exc:
                    if self._running:
                        runtime_error = exc
                    break
                arr = np.frombuffer(data, dtype=np.float32)
                if arr.size == 0:
                    continue
                self._update_capture_stats(arr)
                # Reshape to (frames, channels) so _prepare_frame can mix down
                frame = arr.reshape(-1, channels) if channels > 1 else arr
                self._enqueue_frame(frame.copy())
        except Exception as exc:
            if self._running:
                runtime_error = exc
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
            self._running = False
            self._enqueue_frame(None)
            if runtime_error is not None:
                self._report_runtime_error(runtime_error)

    def _select_stream_config(self, p, pa, device_info: dict) -> dict[str, int]:
        device_index = int(device_info["index"])
        native_rate = int(round(float(device_info.get("defaultSampleRate", 48000))))
        max_channels = max(
            min(int(device_info.get("maxInputChannels", 2) or 2), _MAX_DESKTOP_CAPTURE_CHANNELS),
            1,
        )
        last_error: BaseException | None = None

        for rate, channels in self._candidate_stream_configs(native_rate, max_channels):
            blocksize = max(int(rate * self.frame_duration_ms / 1000), 1)
            try:
                self._validate_stream_config(
                    p,
                    pa,
                    device_index=device_index,
                    rate=rate,
                    channels=channels,
                    blocksize=blocksize,
                )
                logger.info(
                    "Selected desktop capture config (device=%s rate=%s channels=%s blocksize=%s)",
                    device_info.get("name"),
                    rate,
                    channels,
                    blocksize,
                )
                return {
                    "rate": int(rate),
                    "channels": int(channels),
                    "blocksize": int(blocksize),
                }
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "Desktop capture config failed (device=%s rate=%s channels=%s): %s",
                    device_info.get("name"),
                    rate,
                    channels,
                    exc,
                )

        if last_error is None:
            last_error = RuntimeError("No compatible desktop audio capture configuration was found")
        raise last_error

    def _candidate_stream_configs(
        self,
        native_rate: int,
        max_channels: int,
    ) -> list[tuple[int, int]]:
        rate_candidates: list[int] = []
        for candidate in (native_rate, *_COMMON_DESKTOP_CAPTURE_RATES, self.sample_rate):
            rate = int(candidate)
            if rate > 0 and rate not in rate_candidates:
                rate_candidates.append(rate)

        channel_candidates: list[int] = []
        for candidate in (max_channels, 8, 6, 4, 2, 1):
            channels = max(min(int(candidate), max_channels), 1)
            if channels not in channel_candidates:
                channel_candidates.append(channels)

        return [(rate, channels) for rate in rate_candidates for channels in channel_candidates]

    @staticmethod
    def _validate_stream_config(
        p,
        pa,
        *,
        device_index: int,
        rate: int,
        channels: int,
        blocksize: int,
    ) -> None:
        stream = None
        try:
            stream = p.open(
                format=pa.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=blocksize,
            )
            stream.read(blocksize, exception_on_overflow=False)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Loopback device resolution
    # ------------------------------------------------------------------

    def _resolve_loopback_device_info(self, p, pa) -> dict:
        """Find the WASAPI loopback device for the selected output device.

        Priority:
          1. User-specified / UI-detected output device name
          2. sounddevice default output (PortAudio naming, same as PyAudioWPatch)
          3. WASAPI defaultOutputDevice from the host API info
        Never falls back to a non-loopback input device.
        """
        try:
            wasapi_info = p.get_host_api_info_by_type(pa.paWASAPI)
        except OSError as exc:
            raise RuntimeError("WASAPI host API is not available on this system") from exc

        # Build ordered list of preferred output-device names
        preferred_names: list[str] = []
        if self._output_device_name:
            preferred_names.append(self._output_device_name)
        sd_default = _default_output_device_name_from_sounddevice()
        if sd_default and sd_default not in preferred_names:
            preferred_names.append(sd_default)
        logger.info(
            "Resolving loopback device (requested_output=%s preferred_names=%s)",
            self._output_device_name or None,
            preferred_names,
        )

        # Enumerate all loopback devices (PyAudioWPatch extension)
        try:
            all_loopbacks: list[dict] = list(p.get_loopback_device_info_generator())
        except Exception:
            all_loopbacks = []
        logger.debug(
            "Enumerated %s loopback devices: %s",
            len(all_loopbacks),
            [str(lb.get("name", "")).strip() for lb in all_loopbacks],
        )

        if not all_loopbacks:
            raise RuntimeError(
                "No WASAPI loopback devices found. "
                "Ensure you are on Windows with an active WASAPI output device."
            )

        # Pass 1: substring match — loopback name is "<output_name> [Loopback]"
        # PyAudioWPatch and sounddevice share PortAudio, so names are identical.
        for pref in preferred_names:
            pref_lower = pref.lower()
            for lb in all_loopbacks:
                lb_name = str(lb.get("name", "")).strip().lower()
                if pref_lower in lb_name:
                    logger.info("Matched loopback device by substring: %s", lb.get("name"))
                    return lb

        # Pass 2: fuzzy match for edge cases (e.g. truncated or differently formatted names)
        for pref in preferred_names:
            matched = _fuzzy_match_loopback(pref, all_loopbacks)
            if matched is not None:
                logger.info("Matched loopback device by fuzzy match: %s", matched.get("name"))
                return matched

        # Pass 3: WASAPI default output device's loopback
        default_out_idx = int(wasapi_info.get("defaultOutputDevice", -1))
        if default_out_idx >= 0:
            try:
                default_info = p.get_device_info_by_index(default_out_idx)
                default_name = str(default_info.get("name", "")).strip().lower()
                for lb in all_loopbacks:
                    lb_name = str(lb.get("name", "")).strip().lower()
                    if default_name and default_name in lb_name:
                        logger.info("Matched loopback device from WASAPI default output: %s", lb.get("name"))
                        return lb
            except Exception:
                pass

        # Never fall back to a non-loopback input device.
        available = ", ".join(str(lb.get("name", "")) for lb in all_loopbacks)
        raise RuntimeError(
            f"No loopback device matched the selected output device "
            f"({self._output_device_name!r}). "
            f"Available loopback devices: {available}"
        )


# ---------------------------------------------------------------------------
# Sounddevice helpers (mic path still uses sounddevice — keep these)
# ---------------------------------------------------------------------------

def _default_output_device_name_from_sounddevice() -> str:
    try:
        default_out = sd.default.device[1]
        if default_out is None or int(default_out) < 0:
            return ""
        return str(sd.query_devices(int(default_out))["name"]).strip()
    except Exception:
        return ""
