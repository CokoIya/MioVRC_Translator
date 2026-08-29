# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

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

from .adaptive_denoiser import AdaptiveDenoiser
from .chunk_streamer import ChunkStreamer
from .device_inventory import (
    default_input_device_index,
    device_inventory_diagnostics,
    list_input_devices,
    list_output_devices as list_sounddevice_output_devices,
)
from .vad_detector import VADDetector

# SciPy is only needed when the microphone's native rate differs from the
# capture rate, yet importing it costs ~80 MB of resident memory at startup for
# every player. Resolve it on first use and cache the outcome.
_SCIPY_RESAMPLE_POLY: Callable[..., "np.ndarray"] | None = None
_SCIPY_RESAMPLE_CHECKED = False


def _scipy_resample_poly():
    """Return ``scipy.signal.resample_poly``, or None when SciPy is absent."""

    global _SCIPY_RESAMPLE_POLY, _SCIPY_RESAMPLE_CHECKED
    if not _SCIPY_RESAMPLE_CHECKED:
        _SCIPY_RESAMPLE_CHECKED = True
        try:
            from scipy.signal import resample_poly

            _SCIPY_RESAMPLE_POLY = resample_poly
        except ImportError:
            logger.info(
                "SciPy is unavailable; capture resampling falls back to linear "
                "interpolation"
            )
            _SCIPY_RESAMPLE_POLY = None
    return _SCIPY_RESAMPLE_POLY

try:
    import soxr as _soxr

    _HAS_SOXR = True
except ImportError:
    _HAS_SOXR = False

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
        self._lifecycle_lock = threading.RLock()
        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._last_worker_error: str | None = None
        self._last_worker_error_at = 0.0
        self._worker_failure_count = 0
        self._frame_queue_dropped = 0
        self._frame_queue_high_watermark = 0
        self._stale_frames_discarded = 0
        self._capture_rate: int = sample_rate
        self._capture_channels: int = 1
        self._capture_dtype: str = "int16"
        self._resample_stream = None
        self._resample_stream_key: tuple[int, int] | None = None
        self._resample_output_buffer = np.zeros(0, dtype=np.float32)
        self._active_device_name: str | None = None
        self._frames_processed = 0
        self._segments_emitted = 0
        self._last_segment_timing: dict[str, float] = {}
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

    def set_denoise_strength(self, strength: float) -> None:
        self._denoiser.set_strength(strength)

    def start(self):
        with self._lifecycle_lock:
            if self._running:
                return
            previous_worker = self._worker_thread
        if (
            previous_worker is not None
            and previous_worker is not threading.current_thread()
            and previous_worker.is_alive()
        ):
            previous_worker.join(timeout=2)
        with self._lifecycle_lock:
            if self._running:
                return
            if previous_worker is not None and previous_worker.is_alive():
                raise RuntimeError("Previous audio processing thread is still stopping")
            if self._worker_thread is previous_worker:
                self._worker_thread = None
            self._running = True
        worker: threading.Thread | None = None
        try:
            self.vad.reset()
            self._buffer.clear()
            self._pre_speech_buffer.clear()
            self._speech_samples = 0
            self._was_in_speech = False
            self._capture_rate = self.sample_rate
            self._reset_streaming_resampler()
            self._frames_processed = 0
            self._segments_emitted = 0
            self._last_segment_timing = {}
            self._last_frame_rms = 0.0
            self._peak_frame_rms = 0.0
            self._last_non_silent_at = 0.0
            self._clear_frame_queue()
            self._last_worker_error = None
            self._last_worker_error_at = 0.0
            self._frame_queue_dropped = 0
            self._frame_queue_high_watermark = 0
            self._stale_frames_discarded = 0
            self._denoiser.reset()
            if self._chunk_streamer is not None:
                self._chunk_streamer.reset()

            self._stream = self._open_stream(self.input_device, self.extra_settings)
            worker = threading.Thread(
                target=self._worker_main,
                daemon=True,
                name="audio-recorder-worker",
            )
            self._worker_thread = worker
            worker.start()
        except BaseException:
            with self._lifecycle_lock:
                self._running = False
                stream = self._stream
                self._stream = None
                if worker is None or self._worker_thread is worker:
                    self._worker_thread = None
            self._close_input_stream(stream)
            self._clear_frame_queue(count_as_stale=True)
            self._release_processing_state()
            self._active_device_name = None
            raise
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
    def worker_alive(self) -> bool:
        worker = self._worker_thread
        return bool(worker is not None and worker.is_alive())

    @property
    def last_worker_error(self) -> str | None:
        return self._last_worker_error

    @property
    def active_input_device_name(self) -> str | None:
        return self._active_device_name

    @property
    def last_segment_timing(self) -> dict[str, float]:
        """Return lightweight timing for the segment currently being emitted."""

        return dict(self._last_segment_timing)

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
        snapshot = {
            "active_device": self._active_device_name,
            "running": self.is_running,
            "worker_alive": self.worker_alive,
            "last_worker_error": self._last_worker_error,
            "last_worker_error_at": self._last_worker_error_at,
            "worker_failure_count": self._worker_failure_count,
            "stream_open": self._stream is not None,
            "frame_queue_size": self._frame_queue.qsize(),
            "frame_queue_capacity": self._frame_queue.maxsize,
            "frame_queue_high_watermark": self._frame_queue_high_watermark,
            "frame_queue_dropped": self._frame_queue_dropped,
            "stale_frames_discarded": self._stale_frames_discarded,
            "frames_processed": self._frames_processed,
            "segments_emitted": self._segments_emitted,
            "last_segment_timing": dict(self._last_segment_timing),
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
        try:
            snapshot["device_inventory"] = device_inventory_diagnostics()
        except Exception:
            logger.debug("Failed to attach microphone device diagnostics", exc_info=True)
        return snapshot

    def _open_stream(self, device, extra_settings=None) -> sd.InputStream:
        loopback_enabled = extra_settings is not None

        def _native_default_input_index() -> int | None:
            try:
                default_idx = int(sd.default.device[0])
                if default_idx >= 0:
                    info = sd.query_devices(default_idx)
                    if int(info.get("max_input_channels", 0) or 0) > 0:
                        return default_idx
            except Exception:
                pass
            return None

        def _default_input_index() -> int | None:
            native_default = _native_default_input_index()
            if native_default is not None:
                return native_default
            try:
                return default_input_device_index(
                    force_refresh=True,
                    sounddevice_module=sd,
                )
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
                fallback_default = _default_input_index()
                return [None] if _native_default_input_index() is not None else [fallback_default]
            if loopback_enabled:
                return [device]
            candidates: list[int | None] = [device]
            candidates.extend(_same_named_input_devices(device))
            if self.allow_default_fallback:
                candidates.append(
                    None
                    if _native_default_input_index() is not None
                    else _default_input_index()
                )
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
        with self._lifecycle_lock:
            self._running = False
            stream = self._stream
            self._stream = None
            worker = self._worker_thread
        self._enqueue_frame(None)
        self._close_input_stream(stream)
        worker_stopped = worker is None
        if worker is not None:
            if worker is not threading.current_thread():
                worker.join(timeout=2)
            worker_stopped = not worker.is_alive()
            with self._lifecycle_lock:
                if worker_stopped and self._worker_thread is worker:
                    self._worker_thread = None
            if not worker_stopped:
                logger.warning(
                    "Audio processing thread did not stop in time; retaining it until exit"
                )
        self._clear_frame_queue(count_as_stale=True)
        if worker_stopped:
            self._release_processing_state()
        logger.info("AudioRecorder stopped (active_device=%s)", self._active_device_name)
        self._active_device_name = None

    def _worker_main(self) -> None:
        worker_error: BaseException | None = None
        try:
            self._process_loop()
        except BaseException as exc:
            worker_error = exc
            logger.exception(
                "AudioRecorder processing worker failed "
                "(active_device=%s queue_size=%s frames_processed=%s "
                "segments_emitted=%s): %s",
                self._active_device_name,
                self._frame_queue.qsize(),
                self._frames_processed,
                self._segments_emitted,
                exc,
            )
        finally:
            if worker_error is None:
                with self._lifecycle_lock:
                    if self._running:
                        worker_error = RuntimeError(
                            "Audio processing loop exited while capture was still running"
                        )
            if worker_error is not None:
                self._handle_worker_failure(worker_error)
            else:
                self._release_processing_state()

    def _handle_worker_failure(self, exc: BaseException) -> None:
        message = str(exc).strip() or exc.__class__.__name__
        error_text = f"{exc.__class__.__name__}: {message}"
        with self._lifecycle_lock:
            self._last_worker_error = error_text
            self._last_worker_error_at = time.time()
            self._worker_failure_count += 1
            self._running = False
            stream = self._stream
            self._stream = None
            active_device_name = self._active_device_name

        # Mark capture stopped before touching the native stream so callbacks
        # stop admitting frames. Closing the stream waits out any callback that
        # was already in flight; clearing afterward guarantees no stale audio
        # can survive into a subsequent start.
        self._close_input_stream(stream)
        discarded = self._clear_frame_queue(count_as_stale=True)
        self._release_processing_state()
        self._active_device_name = None
        logger.error(
            "AudioRecorder worker failure cleanup completed "
            "(active_device=%s error=%s discarded_frames=%s queue_dropped=%s)",
            active_device_name,
            error_text,
            discarded,
            self._frame_queue_dropped,
        )

    @staticmethod
    def _close_input_stream(stream) -> None:
        if stream is None:
            return
        stopped = False
        abort = getattr(stream, "abort", None)
        if callable(abort):
            try:
                abort()
                stopped = True
            except Exception:
                logger.debug("Failed to abort input stream", exc_info=True)
        if not stopped:
            try:
                stream.stop()
            except Exception:
                logger.debug("Failed to stop input stream", exc_info=True)
        try:
            stream.close()
        except Exception:
            logger.debug("Failed to close input stream", exc_info=True)

    def _release_processing_state(self) -> None:
        """Drop queued/partial audio once no processing callback is using it."""

        notify_vad_idle = self._was_in_speech or bool(
            getattr(self.vad, "in_speech", False)
        )
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        try:
            self.vad.reset()
        except Exception:
            logger.debug("Failed to reset VAD during recorder shutdown", exc_info=True)
        try:
            self._denoiser.reset()
        except Exception:
            logger.debug("Failed to reset denoiser during recorder shutdown", exc_info=True)
        self._reset_streaming_resampler()
        if self._chunk_streamer is not None:
            try:
                self._chunk_streamer.reset()
            except Exception:
                logger.debug(
                    "Failed to reset chunk streamer during recorder shutdown",
                    exc_info=True,
                )
        if notify_vad_idle and self.on_vad_state is not None:
            try:
                self.on_vad_state(False)
            except Exception:
                logger.debug(
                    "Failed to report idle VAD state during recorder shutdown",
                    exc_info=True,
                )

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
                    # VAD activation intentionally waits for several voiced
                    # frames. Count those confirmed activation frames toward
                    # the minimum speech duration; otherwise every utterance
                    # is under-counted by the activation delay and valid short
                    # sentences are discarded despite being present in the
                    # pre-roll buffer.
                    activation_counter = getattr(
                        self.vad,
                        "activation_speech_samples",
                        None,
                    )
                    try:
                        activation_samples = (
                            int(activation_counter(normalized.size))
                            if callable(activation_counter)
                            else sum(
                                bool(item)
                                for item in getattr(
                                    self.vad,
                                    "_activation_window",
                                    (),
                                )
                            )
                            * normalized.size
                        )
                    except (TypeError, ValueError):
                        activation_samples = normalized.size
                    self._speech_samples = max(
                        activation_samples,
                        normalized.size,
                    )
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
                emitted_at = time.monotonic()
                audio_duration_s = float(segment.size) / max(self.sample_rate, 1)
                vad_finalization_s = min(
                    max(float(self.silence_threshold_s), 0.0),
                    audio_duration_s,
                )
                self._last_segment_timing = {
                    "speech_ended_at": emitted_at - vad_finalization_s,
                    "segment_emitted_at": emitted_at,
                    "vad_finalization_s": vad_finalization_s,
                    "audio_duration_s": audio_duration_s,
                }
                self._segments_emitted += 1
                self.on_segment(segment)
            except Exception as exc:
                logger.exception("AudioRecorder on_segment callback failed: %s", exc)

    def _enqueue_frame(self, frame: np.ndarray | None) -> None:
        try:
            self._frame_queue.put_nowait(frame)
            self._frame_queue_high_watermark = max(
                self._frame_queue_high_watermark,
                self._frame_queue.qsize(),
            )
            return
        except queue.Full:
            pass

        try:
            dropped = self._frame_queue.get_nowait()
        except queue.Empty:
            dropped = None
        else:
            if dropped is not None:
                self._frame_queue_dropped += 1
                logger.debug(
                    "AudioRecorder frame queue full; dropping oldest frame "
                    "(dropped=%s capacity=%s)",
                    self._frame_queue_dropped,
                    self._frame_queue.maxsize,
                )

        try:
            self._frame_queue.put_nowait(frame)
            self._frame_queue_high_watermark = max(
                self._frame_queue_high_watermark,
                self._frame_queue.qsize(),
            )
        except queue.Full:
            if frame is not None:
                self._frame_queue_dropped += 1
                logger.debug(
                    "AudioRecorder frame queue remained full; dropping incoming frame "
                    "(dropped=%s capacity=%s)",
                    self._frame_queue_dropped,
                    self._frame_queue.maxsize,
                )

    def _clear_frame_queue(self, *, count_as_stale: bool = False) -> int:
        discarded = 0
        while True:
            try:
                frame = self._frame_queue.get_nowait()
            except queue.Empty:
                if count_as_stale:
                    self._stale_frames_discarded += discarded
                return discarded
            if frame is not None:
                discarded += 1

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
            self._reset_streaming_resampler()
            return normalized
        return self._resample_frame_streaming(
            normalized,
            self._capture_rate,
            self.sample_rate,
        )

    def _reset_streaming_resampler(self) -> None:
        stream = self._resample_stream
        if stream is not None:
            try:
                stream.clear()
            except Exception:
                pass
        self._resample_stream = None
        self._resample_stream_key = None
        self._resample_output_buffer = np.zeros(0, dtype=np.float32)

    def _resample_frame_streaming(
        self,
        audio: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        if not _HAS_SOXR:
            return self._resample_audio(audio, source_rate, target_rate)
        key = (int(source_rate), int(target_rate))
        if self._resample_stream is None or self._resample_stream_key != key:
            self._reset_streaming_resampler()
            self._resample_stream = _soxr.ResampleStream(
                source_rate,
                target_rate,
                1,
                dtype="float32",
                quality="MQ",
            )
            self._resample_stream_key = key

        chunk = self._resample_stream.resample_chunk(
            np.ascontiguousarray(audio, dtype=np.float32),
            last=False,
        )
        if chunk.size:
            if self._resample_output_buffer.size:
                self._resample_output_buffer = np.concatenate(
                    (self._resample_output_buffer, chunk.astype(np.float32, copy=False))
                )
            else:
                self._resample_output_buffer = chunk.astype(np.float32, copy=False)

        target_samples = max(
            int(target_rate * self.frame_duration_ms / 1000),
            1,
        )
        if self._resample_output_buffer.size < target_samples:
            return np.zeros(0, dtype=np.float32)
        result = self._resample_output_buffer[:target_samples].copy()
        self._resample_output_buffer = self._resample_output_buffer[target_samples:]
        return result

    @staticmethod
    def _resample_audio(
        audio: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        if audio.size == 0 or source_rate == target_rate:
            return audio.astype(np.float32, copy=False)

        resample_poly = _scipy_resample_poly()
        if resample_poly is not None:
            g = gcd(target_rate, source_rate)
            resampled = resample_poly(audio, target_rate // g, source_rate // g)
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
        devices = list_input_devices(
            force_refresh=True,
            sounddevice_module=sd,
        )
        logger.debug("Enumerated %s canonical input devices", len(devices))
        return devices

    @staticmethod
    def list_loopback_devices() -> list[dict]:
        devices = [
            item
            for item in list_sounddevice_output_devices(
                force_refresh=True,
                sounddevice_module=sd,
            )
            if "WASAPI" in str(item.get("hostapi", "")).upper()
        ]
        logger.debug("Enumerated %s loopback-capable WASAPI outputs", len(devices))
        return devices
