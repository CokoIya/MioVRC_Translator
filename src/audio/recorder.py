from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from math import gcd
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

try:
    from scipy.signal import resample_poly as _scipy_resample_poly

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from .adaptive_denoiser import AdaptiveDenoiser
from .chunk_streamer import ChunkStreamer
from .vad_detector import VADDetector

FRAME_QUEUE_MAXSIZE = 64
logger = logging.getLogger(__name__)
_MAX_CAPTURE_CHANNELS = 8
_COMMON_CAPTURE_RATES = (
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
_INPUT_HOSTAPI_FALLBACK_PREFERENCE = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 3,
}


def _normalize_device_name(name: object) -> str:
    return " ".join(str(name or "").casefold().split())


class AudioRecorder:

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 2,
        silence_threshold_s: float = 0.65,
        vad_speech_ratio: float = 0.6,
        vad_activation_threshold_s: float = 0.2,
        input_device: Optional[int] = None,
        on_vad_state: Optional[Callable[[bool], None]] = None,
        pre_speech_s: float = 0.30,
        on_chunk: Optional[Callable[[np.ndarray], None]] = None,
        chunk_interval_ms: int = 250,
        chunk_window_s: float = 1.6,
        ring_buffer_s: float = 4.0,
        recent_speech_hold_s: float = 0.8,
        min_segment_s: float = 0.45,
        partial_min_speech_s: float = 0.45,
        vad_min_rms: float = 0.012,
        max_segment_s: float = 6.0,
        denoise_strength: float = 0.0,
        extra_settings: object | None = None,
        allow_default_fallback: bool = True,
    ):
        self.on_segment = on_segment
        self.on_chunk = on_chunk
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_threshold_s = silence_threshold_s
        self.input_device = input_device
        self.extra_settings = extra_settings
        self.allow_default_fallback = allow_default_fallback
        self.on_vad_state = on_vad_state
        self.pre_speech_s = pre_speech_s

        self.vad = VADDetector(
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            sensitivity=vad_sensitivity,
            silence_threshold_s=silence_threshold_s,
            speech_ratio=vad_speech_ratio,
            activation_threshold_s=vad_activation_threshold_s,
            min_rms=vad_min_rms,
            max_speech_s=max_segment_s,
        )

        self._buffer: list[np.ndarray] = []
        self._pre_speech_buffer = collections.deque(
            maxlen=max(1, int(pre_speech_s * 1000 / frame_duration_ms))
        )
        self._min_segment_samples = max(int(min_segment_s * sample_rate), 1)
        self._partial_min_speech_samples = max(int(partial_min_speech_s * sample_rate), 0)
        self._speech_samples = 0
        self._was_in_speech = False
        self._frame_queue: queue.Queue[np.ndarray | None] = queue.Queue(
            maxsize=FRAME_QUEUE_MAXSIZE
        )
        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._capture_rate: int = sample_rate
        self._capture_channels: int = 1
        self._capture_dtype: str = "int16"
        self._active_device_name: str | None = None
        self._frames_processed = 0
        self._segments_emitted = 0
        self._last_frame_rms = 0.0
        self._peak_frame_rms = 0.0
        self._last_non_silent_at = 0.0
        self._denoiser = AdaptiveDenoiser(strength=denoise_strength)
        self._chunk_streamer = (
            ChunkStreamer(
                sample_rate=sample_rate,
                chunk_interval_ms=chunk_interval_ms,
                chunk_window_s=chunk_window_s,
                ring_buffer_s=ring_buffer_s,
                recent_speech_hold_s=recent_speech_hold_s,
            )
            if on_chunk is not None
            else None
        )

    def start(self):
        if self._running:
            return
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        self._capture_rate = self.sample_rate
        self._frames_processed = 0
        self._segments_emitted = 0
        self._last_frame_rms = 0.0
        self._peak_frame_rms = 0.0
        self._last_non_silent_at = 0.0
        self._clear_frame_queue()
        self._denoiser.reset()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()

        try:
            self._stream = self._open_stream(self.input_device, self.extra_settings)
        except Exception:
            self._running = False
            self._stream = None
            self._active_device_name = None
            self._clear_frame_queue()
            raise
        self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._worker_thread.start()
        logger.info(
            "AudioRecorder started (input_device=%s active_device=%s capture_rate=%s target_rate=%s)",
            self.input_device,
            self._active_device_name,
            self._capture_rate,
            self.sample_rate,
        )

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    @property
    def active_input_device_name(self) -> str | None:
        return self._active_device_name

    def diagnostics_snapshot(self) -> dict[str, object]:
        activation_window = getattr(self.vad, "_activation_window", None)
        try:
            activation_ratio = (
                sum(bool(item) for item in activation_window) / len(activation_window)
                if activation_window is not None and len(activation_window) > 0
                else 0.0
            )
        except Exception:
            activation_ratio = 0.0
        return {
            "active_device": self._active_device_name,
            "running": self.is_running,
            "frames_processed": self._frames_processed,
            "segments_emitted": self._segments_emitted,
            "last_frame_rms": round(self._last_frame_rms, 6),
            "peak_frame_rms": round(self._peak_frame_rms, 6),
            "last_non_silent_at": self._last_non_silent_at,
            "capture_rate": self._capture_rate,
            "target_rate": self.sample_rate,
            "channels": self._capture_channels,
            "dtype": self._capture_dtype,
            "vad_min_rms": getattr(self.vad, "_min_rms", None),
            "vad_in_speech": bool(getattr(self.vad, "in_speech", False)),
            "vad_speech_ratio": getattr(self.vad, "_speech_ratio", None),
            "vad_activation_ratio": round(float(activation_ratio), 3),
        }

    def _open_stream(self, device, extra_settings=None) -> sd.InputStream:
        loopback_enabled = extra_settings is not None

        def _default_input_index() -> int | None:
            try:
                default_idx = int(sd.default.device[0])
                return default_idx if default_idx >= 0 else None
            except Exception:
                return None

        def _actual_device_index(dev) -> int | None:
            if dev is not None:
                try:
                    idx = int(dev)
                    return idx if idx >= 0 else None
                except (TypeError, ValueError):
                    return None
            return _default_input_index()

        def _query_device_info(dev) -> tuple[dict, int, int, int]:
            try:
                actual_idx = _actual_device_index(dev)
                if actual_idx is None:
                    raise RuntimeError("default input device unavailable")
                device_info = sd.query_devices(actual_idx)
                native_rate = int(float(device_info.get("default_samplerate", 48000)) or 48000)
                max_input_channels = int(device_info.get("max_input_channels", 0))
                max_output_channels = int(device_info.get("max_output_channels", 0))
                return device_info, native_rate, max_input_channels, max_output_channels
            except Exception:
                return {}, 48000, 1, 2

        def _device_name(dev) -> str | None:
            try:
                actual_idx = _actual_device_index(dev)
                if actual_idx is None:
                    return None
                name = str(sd.query_devices(actual_idx)["name"]).strip()
                return name or None
            except Exception:
                return None

        def _stream_config_candidates(dev) -> list[tuple[int, int, str]]:
            _, native_rate, max_input_channels, max_output_channels = _query_device_info(dev)

            available_channels = max_input_channels
            if loopback_enabled and max_output_channels > 0:
                available_channels = max(available_channels, max_output_channels)
            if available_channels <= 0:
                available_channels = 2 if loopback_enabled else 1

            max_channel_limit = _MAX_CAPTURE_CHANNELS if loopback_enabled else 2
            available_channels = max(min(available_channels, max_channel_limit), 1)
            preferred_channels = [available_channels, 8, 6, 4, 2, 1] if loopback_enabled else [1, 2]
            channel_candidates = [
                max(min(channels, available_channels), 1)
                for channels in preferred_channels
                if 0 < channels <= available_channels
            ]
            channel_candidates = list(dict.fromkeys(channel_candidates))
            if not channel_candidates:
                channel_candidates = [max(available_channels, 1)]

            rate_candidates: list[int] = []
            # Open the device at its native rate first and resample internally
            # for ASR/VAD. Some Windows drivers reject 16 kHz directly even
            # though they work fine at 44.1/48 kHz.
            for candidate in (native_rate, self.sample_rate, *_COMMON_CAPTURE_RATES):
                try:
                    rate = int(candidate)
                except (TypeError, ValueError):
                    continue
                if rate > 0 and rate not in rate_candidates:
                    rate_candidates.append(rate)

            candidates: list[tuple[int, int, str]] = []
            for dtype in ("int16", "float32"):
                for rate in rate_candidates:
                    for channels in channel_candidates:
                        candidate = (rate, channels, dtype)
                        if candidate not in candidates:
                            candidates.append(candidate)
            return candidates

        def _same_named_input_devices(dev) -> list[int]:
            if dev is None or loopback_enabled:
                return []
            requested_idx = _actual_device_index(dev)
            requested_name = _device_name(dev)
            if requested_idx is None or not requested_name:
                return []
            requested_norm = _normalize_device_name(requested_name)
            try:
                all_devices = list(sd.query_devices())
                hostapis = list(sd.query_hostapis())
            except Exception:
                return []

            def _hostapi_name(device_info: dict) -> str:
                try:
                    return str(hostapis[int(device_info.get("hostapi", -1))]["name"])
                except Exception:
                    return ""

            matches: list[tuple[tuple[int, int], int]] = []
            for index, device_info in enumerate(all_devices):
                if index == requested_idx:
                    continue
                try:
                    if int(device_info.get("max_input_channels", 0)) <= 0:
                        continue
                except Exception:
                    continue
                if _normalize_device_name(device_info.get("name")) != requested_norm:
                    continue
                hostapi_name = _hostapi_name(device_info)
                pref = _INPUT_HOSTAPI_FALLBACK_PREFERENCE.get(hostapi_name, 99)
                matches.append(((pref, index), index))
            matches.sort(key=lambda item: item[0])
            return [index for _, index in matches]

        def _devices_to_try() -> list[int | None]:
            if device is None:
                return [None]
            if loopback_enabled:
                return [device]
            candidates: list[int | None] = [device]
            candidates.extend(_same_named_input_devices(device))
            if self.allow_default_fallback:
                candidates.append(None)
            deduped_devices: list[int | None] = []
            seen: set[int | None] = set()
            for candidate in candidates:
                key = _actual_device_index(candidate) if candidate is not None else None
                if key in seen:
                    continue
                seen.add(key)
                deduped_devices.append(candidate)
            return deduped_devices

        def _try_open_and_start(rate, channels, dtype, dev, stream_extra_settings):
            blocksize = int(rate * self.frame_duration_ms / 1000)
            self._capture_rate = rate
            self._capture_channels = channels
            self._capture_dtype = dtype
            active_device_name = None
            try:
                actual_device_index = dev if dev is not None else sd.default.device[0]
                if actual_device_index is not None and int(actual_device_index) >= 0:
                    active_device_name = str(sd.query_devices(int(actual_device_index))["name"]).strip() or None
            except Exception:
                active_device_name = None
            stream = sd.InputStream(
                samplerate=rate,
                channels=channels,
                dtype=dtype,
                blocksize=blocksize,
                device=dev,
                extra_settings=stream_extra_settings,
                callback=self._sd_callback,
            )
            try:
                stream.start()
            except Exception:
                try:
                    stream.close()
                except Exception:
                    pass
                raise
            self._active_device_name = active_device_name
            logger.debug(
                "Opened input stream (device=%s active_device=%s rate=%s channels=%s dtype=%s loopback=%s)",
                dev,
                self._active_device_name,
                rate,
                channels,
                dtype,
                extra_settings is not None,
            )
            return stream

        last_err = None
        devices_to_try = _devices_to_try()
        attempted_configs = 0
        for dev in devices_to_try:
            if dev is None and device is not None:
                self.input_device = None
            for rate, channels, dtype in _stream_config_candidates(dev):
                attempted_configs += 1
                try:
                    stream = _try_open_and_start(rate, channels, dtype, dev, extra_settings)
                    if dev is not None:
                        self.input_device = dev
                    return stream
                except Exception as exc:
                    last_err = exc
                    logger.debug(
                        "Input stream open attempt failed (device=%s rate=%s channels=%s dtype=%s): %s",
                        dev,
                        rate,
                        channels,
                        dtype,
                        exc,
                    )

        if last_err is None:
            last_err = RuntimeError("No compatible input stream configuration was found")
        logger.error(
            "Failed to open input stream (requested_device=%s tried_devices=%s attempted_configs=%s loopback=%s last_error=%s)",
            device,
            devices_to_try,
            attempted_configs,
            extra_settings is not None,
            last_err,
        )
        raise last_err

    def stop(self):
        self._running = False
        self._enqueue_frame(None)
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._worker_thread:
            self._worker_thread.join(timeout=2)
            self._worker_thread = None
        self._clear_frame_queue()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()
        logger.info("AudioRecorder stopped (active_device=%s)", self._active_device_name)
        self._active_device_name = None

    def _sd_callback(self, indata, frames, time_info, status):
        del frames
        del time_info

        if not self._running:
            return
        if status:
            logger.debug("sounddevice callback status: %s", status)
        self._enqueue_frame(indata.copy())

    def _process_loop(self):
        while True:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if frame is None:
                break

            previous_in_speech = self._was_in_speech
            normalized = self._prepare_frame(frame)
            if normalized.size == 0:
                continue
            normalized = self._denoiser.process(
                normalized,
                update_profile=not previous_in_speech,
            )
            rms = float(np.sqrt(np.mean(np.square(normalized)))) if normalized.size else 0.0
            self._frames_processed += 1
            self._last_frame_rms = rms
            self._peak_frame_rms = max(self._peak_frame_rms, rms)
            if rms >= max(float(getattr(self.vad, "_min_rms", 0.0) or 0.0), 0.004):
                self._last_non_silent_at = time.monotonic()

            pcm = np.clip(normalized * 32768.0, -32768.0, 32767.0).astype(np.int16)
            if not previous_in_speech:
                self._pre_speech_buffer.append(normalized)

            pcm_bytes = pcm.tobytes()
            in_speech = self.vad.process_frame(pcm_bytes)

            if in_speech:
                if not previous_in_speech:
                    # 语音刚开始：把预录缓冲一起并进去，补上起始辅音
                    self._buffer = list(self._pre_speech_buffer)
                    self._pre_speech_buffer.clear()
                    self._speech_samples = normalized.size
                else:
                    self._buffer.append(normalized)
                    self._speech_samples += normalized.size

            if self._chunk_streamer is not None and self.on_chunk is not None:
                for chunk in self._chunk_streamer.push_frame(normalized, in_speech):
                    if self._speech_samples < self._partial_min_speech_samples:
                        continue
                    try:
                        self.on_chunk(chunk)
                    except Exception as exc:
                        logger.exception("AudioRecorder on_chunk callback failed: %s", exc)

            if in_speech != previous_in_speech and self.on_vad_state:
                try:
                    self.on_vad_state(in_speech)
                except Exception:
                    pass

            if in_speech:
                self._was_in_speech = True
                continue

            if not previous_in_speech:
                continue

            segment = np.concatenate(self._buffer) if self._buffer else None
            speech_samples = self._speech_samples
            self._buffer.clear()
            self._speech_samples = 0
            self._was_in_speech = False
            self.vad.reset()
            self._pre_speech_buffer.clear()
            if segment is None or speech_samples < self._min_segment_samples:
                continue
            try:
                self._segments_emitted += 1
                self.on_segment(segment)
            except Exception as exc:
                logger.exception("AudioRecorder on_segment callback failed: %s", exc)

    def _enqueue_frame(self, frame: np.ndarray | None) -> None:
        try:
            self._frame_queue.put_nowait(frame)
            return
        except queue.Full:
            logger.debug("AudioRecorder frame queue full; dropping oldest frame")

        try:
            self._frame_queue.get_nowait()
        except queue.Empty:
            return

        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            pass

    def _clear_frame_queue(self) -> None:
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                return

    def _prepare_frame(self, frame: np.ndarray) -> np.ndarray:
        audio = np.asarray(frame)
        if audio.ndim == 2:
            if audio.shape[1] == 1:
                audio = audio[:, 0]
            else:
                audio = audio.astype(np.float32).mean(axis=1)
        else:
            audio = audio.astype(np.float32, copy=False)

        if self._capture_dtype == "float32":
            normalized = np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)
        else:
            normalized = (audio / 32768.0).astype(np.float32, copy=False)

        if self._capture_rate == self.sample_rate:
            return normalized
        return self._resample_audio(normalized, self._capture_rate, self.sample_rate)

    @staticmethod
    def _resample_audio(
        audio: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        if audio.size == 0 or source_rate == target_rate:
            return audio.astype(np.float32, copy=False)

        if _HAS_SCIPY:
            g = gcd(target_rate, source_rate)
            resampled = _scipy_resample_poly(audio, target_rate // g, source_rate // g)
            return resampled.astype(np.float32)

        # Fallback: linear interpolation (no scipy)
        target_size = max(int(round(audio.size * target_rate / source_rate)), 1)
        if target_size == audio.size:
            return audio.astype(np.float32, copy=False)
        if audio.size == 1:
            return np.repeat(audio, target_size).astype(np.float32, copy=False)

        source_index = np.arange(audio.size, dtype=np.float32)
        target_index = np.linspace(0, audio.size - 1, num=target_size, dtype=np.float32)
        return np.interp(target_index, source_index, audio).astype(np.float32, copy=False)

    @staticmethod
    def list_devices() -> list[dict]:
        api_preference = {
            "Windows WASAPI": 0,
            "Windows WDM-KS": 1,
            "Windows DirectSound": 2,
            "MME": 3,
        }
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            hostapis = []

        seen: dict[str, dict] = {}
        try:
            queried_devices = sd.query_devices()
        except Exception as exc:
            logger.warning("Failed to enumerate input devices: %s", exc)
            return []

        for index, device in enumerate(queried_devices):
            if device["max_input_channels"] <= 0:
                continue
            api_name = hostapis[device["hostapi"]]["name"] if hostapis else ""
            pref = api_preference.get(api_name, 99)
            existing = seen.get(device["name"])
            if existing is None or pref < existing["_pref"]:
                seen[device["name"]] = {"index": index, "name": device["name"], "_pref": pref}

        result = sorted(seen.values(), key=lambda item: item["index"])
        logger.debug("Enumerated %s input devices", len(result))
        return [{"index": item["index"], "name": item["name"]} for item in result]

    @staticmethod
    def list_loopback_devices() -> list[dict]:
        try:
            hostapis = sd.query_hostapis()
            devices = sd.query_devices()
        except Exception as exc:
            logger.warning("Failed to enumerate loopback devices via sounddevice: %s", exc)
            return []

        def _hostapi_name(device: dict) -> str:
            try:
                return str(hostapis[int(device.get("hostapi", -1))]["name"]).strip()
            except Exception:
                return ""

        try:
            default_output_index = int(sd.default.device[1])
            if default_output_index < 0:
                default_output_index = -1
        except Exception:
            default_output_index = -1

        seen: dict[str, dict] = {}

        for index, device in enumerate(devices):
            if int(device.get("max_output_channels", 0)) <= 0:
                continue
            hostapi_name = _hostapi_name(device)
            if "WASAPI" not in hostapi_name.upper():
                continue
            name = str(device.get("name", "")).strip()
            if not name:
                continue

            pref = 0 if index == default_output_index else 1
            existing = seen.get(name)
            if existing is None or pref < existing["_pref"]:
                seen[name] = {
                    "index": index,
                    "name": name,
                    "_pref": pref,
                }

        result = sorted(seen.values(), key=lambda item: (item["_pref"], item["index"]))
        logger.debug("Enumerated %s loopback-capable output devices", len(result))
        return [{"index": item["index"], "name": item["name"]} for item in result]
