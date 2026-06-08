"""TTS manager with playback queue and caching."""
from __future__ import annotations

import hashlib
import io
import logging
import math
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .base import BaseTTS
from .factory import create_tts_engine, create_tts_engine_with_fallback
from .style_bert_vits2_engine import style_bert_cuda_available
from src.utils.input_validation import validate_tts_text, ValidationError

logger = logging.getLogger(__name__)

# Cache settings
MAX_CACHE_SIZE_MB = 50
MAX_CACHE_ITEMS = 100
CACHE_TTL_SECONDS = 900.0
TTS_FAILURE_SUSPEND_THRESHOLD = 3
TTS_FAILURE_SUSPEND_SECONDS = 30.0
TTS_PLAYBACK_TAIL_PADDING_MS = 180
OutputDeviceRef = int | str | None

_VIRTUAL_OUTPUT_KEYWORDS = (
    "mixline",
    "mix line",
)

_RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS = {-9999, -9996, -9992}
_SCIPY_RESAMPLE_FALLBACK_LOGGED = False


def _style_bert_cuda_available() -> bool:
    return style_bert_cuda_available()


def _portaudio_error_code(exc: Exception) -> int | None:
    """Extract PortAudio error code from exception."""
    if hasattr(exc, "args"):
        for arg in exc.args:
            if isinstance(arg, int):
                return arg
    return None


def _append_tail_silence(audio_array: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.asarray(audio_array, dtype=np.float32)
    if sample_rate <= 0 or audio.size == 0:
        return audio

    tail_frames = int(math.ceil(sample_rate * TTS_PLAYBACK_TAIL_PADDING_MS / 1000.0))
    if tail_frames <= 0:
        return audio

    silence_shape = (tail_frames, *audio.shape[1:])
    silence = np.zeros(silence_shape, dtype=audio.dtype)
    return np.concatenate((audio, silence), axis=0)


def _is_rejected_mixline_device(device_name: str) -> bool:
    """Return True for the known-bad MIXLINE Wave Speaker endpoint."""
    return "mixline wave speaker" in str(device_name or "").lower()


def _mixline_endpoint_score(device_name: str) -> int:
    """Rank MixLine playback endpoints by how likely they feed the virtual mic."""
    name_lower = str(device_name or "").lower()
    if "mixline stream" in name_lower:
        return 280
    if "mixline input" in name_lower:
        return 260
    if "mixline" in name_lower or "mix line" in name_lower:
        return 180
    return 0


@dataclass
class TTSRequest:
    """TTS synthesis request."""
    text: str
    voice: str
    rate: float
    volume: float
    callback: Optional[Callable[[bool, str], None]] = None


class TTSManager:
    """TTS manager with queue and caching."""

    def __init__(
        self,
        engine_name: str = "edge",
        cache_enabled: bool = True,
        allow_fallback: bool = True,
        output_device: OutputDeviceRef = None,
        output_device_name: Optional[str] = None,
        prefer_virtual_output: bool = False,
        monitor_output: bool = False,
        config_save_callback: Optional[Callable[[OutputDeviceRef, str], None]] = None,
        device: str = "cpu",
        sbv2_device: Optional[str] = None,
        sbv2_bert_language: Optional[str] = None,
        engine_config: Optional[dict[str, object]] = None,
        max_cache_size_mb: Optional[int] = None,
        max_cache_items: Optional[int] = None,
    ):
        self._engine: Optional[BaseTTS] = None
        self._engine_name = engine_name
        self._device = str(sbv2_device or device or "cpu").strip().lower()
        if self._device not in {"cpu", "cuda"}:
            self._device = "cpu"
        if self._device == "cuda" and not _style_bert_cuda_available():
            logger.warning(
                "Style-Bert-VITS2 CUDA was requested, but CUDA is not available "
                "in this build; falling back to CPU"
            )
            self._device = "cpu"
        self._bert_language = (
            str(sbv2_bert_language or "jp").strip().lower().replace("_", "-")
        )
        if self._bert_language in {"ja", "japanese", "日本語", "日文", "日语"}:
            self._bert_language = "jp"
        elif self._bert_language in {"en-us", "en-gb", "eng", "english", "英文", "英语"}:
            self._bert_language = "en"
        elif self._bert_language in {
            "cn",
            "zh-cn",
            "zh-hans",
            "zh-sg",
            "zh-tw",
            "zh-hant",
            "chinese",
            "中文",
            "简体中文",
            "繁體中文",
            "中国語",
        }:
            self._bert_language = "zh"
        elif self._bert_language not in {"jp", "en", "zh"}:
            self._bert_language = "jp"
        self._cache_enabled = cache_enabled
        self._max_cache_size_mb = self._normalize_cache_limit(
            max_cache_size_mb,
            MAX_CACHE_SIZE_MB,
            minimum=0,
            maximum=512,
        )
        self._max_cache_items = self._normalize_cache_limit(
            max_cache_items,
            MAX_CACHE_ITEMS,
            minimum=0,
            maximum=2048,
        )
        self._allow_fallback = allow_fallback
        self._output_device = output_device
        self._output_device_name = str(output_device_name or "").strip()
        self._prefer_virtual_output = prefer_virtual_output
        self._monitor_output = bool(monitor_output)
        self._config_save_callback = config_save_callback
        self._engine_config = dict(engine_config or {})
        self._cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._cache_size_bytes = 0
        self._cache_lock = threading.Lock()

        self._request_queue: queue.Queue[TTSRequest | None] = queue.Queue(maxsize=10)
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._current_playback: Optional[sd.OutputStream] = None
        self._current_playbacks: list[sd.OutputStream] = []
        self._current_playback_done: Optional[threading.Event] = None
        self._playback_lock = threading.Lock()

        self._device_sample_rates_cache: dict[OutputDeviceRef, list[int]] = {}
        self._device_cache_lock = threading.Lock()
        self._consecutive_failures = 0
        self._last_failure_message = ""
        self._suspended_until = 0.0

        # Initialize engine eagerly so is_available() and get_available_voices()
        # return correct values before start() is called.
        self._initialize_engine()

    @staticmethod
    def _normalize_cache_limit(
        value: Optional[int],
        default: int,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            parsed = int(default if value is None else value)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(minimum, min(parsed, maximum))

    def _initialize_engine(self) -> None:
        """Initialize TTS engine."""
        try:
            if self._allow_fallback:
                self._engine = create_tts_engine_with_fallback(
                    self._engine_name,
                    device=self._device,
                    bert_language=self._bert_language,
                    config=self._engine_config,
                )
            else:
                self._engine = create_tts_engine(
                    self._engine_name,
                    device=self._device,
                    bert_language=self._bert_language,
                    config=self._engine_config,
                )
            if self._engine is None:
                logger.error("Failed to initialize any TTS engine")
        except Exception as exc:
            logger.error("Failed to initialize TTS engine: %s", exc)
            self._engine = None

    def is_available(self) -> bool:
        """Check if TTS is available."""
        return self._engine is not None and self._engine.is_available()

    def start(self) -> None:
        """Start TTS manager."""
        if self._running:
            return

        if not self.is_available():
            logger.warning("Cannot start TTS manager: no engine available")
            return

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("TTS manager started")

    def stop(self) -> None:
        """Stop TTS manager."""
        if self._worker_thread is None:
            self.stop_playback()
            return

        self._running = False
        self.clear_queue()
        if self._worker_thread is not None:
            self._signal_worker_stop()

        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

        self.stop_playback()
        logger.info("TTS manager stopped")

    def _signal_worker_stop(self) -> None:
        """Wake the worker without blocking the UI thread if the queue is full."""
        while True:
            try:
                self._request_queue.put_nowait(None)
                return
            except queue.Full:
                try:
                    self._request_queue.get_nowait()
                except queue.Empty:
                    return

    def speak(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
        callback: Optional[Callable[[bool, str], None]] = None,
    ) -> bool:
        """Queue text for speech synthesis.

        Args:
            text: Text to speak.
            voice: Voice ID.
            rate: Speech rate (0.5 - 2.0).
            volume: Volume (0.0 - 1.0).
            callback: Optional callback(success, message).

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self._running:
            logger.warning("TTS manager not running")
            return False

        suspended_for = self._suspended_until - time.monotonic()
        if suspended_for > 0:
            message = (
                "TTS playback is temporarily paused after repeated failures. "
                f"Please try again in {int(max(1, suspended_for))} seconds."
            )
            logger.info(message)
            if callback:
                callback(False, message)
            return False

        # Validate input
        try:
            text = validate_tts_text(text)
        except ValidationError as e:
            logger.error("Invalid TTS input: %s", e)
            if callback:
                callback(False, str(e))
            return False

        request = TTSRequest(
            text=text,
            voice=voice,
            rate=rate,
            volume=volume,
            callback=callback,
        )

        try:
            self._request_queue.put_nowait(request)
            return True
        except queue.Full:
            logger.warning("TTS queue full, dropping request")
            return False

    def stop_playback(self) -> None:
        """Stop current playback."""
        with self._playback_lock:
            playbacks = list(getattr(self, "_current_playbacks", []))
            playback = self._current_playback
            if playback is not None and all(active is not playback for active in playbacks):
                playbacks.append(playback)
            done_event = self._current_playback_done
            self._current_playbacks = []
            self._current_playback = None
            self._current_playback_done = None

        for playback in playbacks:
            try:
                playback.stop()
                playback.close()
            except Exception as exc:
                logger.debug("Error stopping playback: %s", exc)

        if done_event is not None:
            done_event.set()

    def _update_device_config(self, device_id: OutputDeviceRef, device_name: str) -> None:
        """Update device configuration and notify callback."""
        self._output_device = device_id
        self._output_device_name = device_name
        callback = getattr(self, "_config_save_callback", None)
        if callback is not None:
            try:
                callback(device_id, device_name)
                logger.info("Device configuration updated: %s (%s)", device_id, device_name)
            except Exception as exc:
                logger.error("Failed to save device configuration: %s", exc)

    def _persist_resolved_output_device(self, device_id: OutputDeviceRef) -> None:
        device_numeric_id = _coerce_device_id(device_id)
        if device_numeric_id is None:
            return
        matched = _find_output_device_by_id(device_numeric_id, _iter_output_devices())
        if matched is None:
            return
        resolved_id, device_name, _hostapi_name = matched
        self._update_device_config(resolved_id, device_name)

    def _decode_audio_data(self, audio_data: bytes) -> tuple[np.ndarray, int]:
        """Decode supported TTS audio bytes into float PCM samples."""
        if audio_data.startswith(b"RIFF"):
            return self._decode_wav(audio_data)
        if audio_data.startswith(b"ID3") or (
            len(audio_data) >= 2
            and audio_data[0] == 0xFF
            and (audio_data[1] & 0xE0) == 0xE0
        ):
            return self._decode_mp3(audio_data)
        raise RuntimeError("Unknown audio format")

    def _prepare_audio_for_device(
        self,
        audio_array: np.ndarray,
        sample_rate: int,
        playback_device: OutputDeviceRef,
    ) -> tuple[np.ndarray, int]:
        """Resample decoded audio to a rate supported by the target device."""
        target_audio = np.asarray(audio_array, dtype=np.float32)
        supported_rates = self._probe_supported_sample_rates(playback_device)
        target_sample_rate = self._choose_best_sample_rate(sample_rate, supported_rates)

        if target_sample_rate != sample_rate:
            logger.info(
                "Resampling audio: %d Hz -> %d Hz (device=%s supports: %s)",
                sample_rate,
                target_sample_rate,
                playback_device,
                supported_rates,
            )
            target_audio = self._resample_audio(
                target_audio,
                sample_rate,
                target_sample_rate,
            )
        return target_audio, target_sample_rate

    def _create_output_stream(
        self,
        audio_array: np.ndarray,
        sample_rate: int,
        playback_device: OutputDeviceRef,
        label: str,
        _retry_depth: int = 0,
    ) -> tuple[sd.OutputStream, threading.Event]:
        """Create a callback-driven output stream for one playback target."""
        if _retry_depth > 3:
            raise RuntimeError("Maximum retry depth exceeded for device fallback")

        playback_done = threading.Event()
        audio_index = [0]
        natural_end_reached = [False]
        completion_reported = [False]

        def mark_finished() -> None:
            if natural_end_reached[0] and not completion_reported[0]:
                completion_reported[0] = True
                logger.info(
                    "Audio playback completed (target=%s, total frames: %d)",
                    label,
                    audio_index[0],
                )
            playback_done.set()

        def audio_callback(outdata, frames, time_info, status):
            if status:
                logger.warning("Audio callback status (%s): %s", label, status)

            start_idx = audio_index[0]
            if natural_end_reached[0] or start_idx >= len(audio_array):
                outdata[:] = 0
                natural_end_reached[0] = True
                raise sd.CallbackStop

            end_idx = min(start_idx + frames, len(audio_array))
            chunk = audio_array[start_idx:end_idx]
            chunk_frames = end_idx - start_idx
            outdata[:] = 0
            if chunk_frames > 0:
                if audio_array.ndim == 1:
                    outdata[:chunk_frames, 0] = chunk
                else:
                    outdata[:chunk_frames] = chunk
            audio_index[0] = end_idx

            if end_idx >= len(audio_array):
                natural_end_reached[0] = True
                raise sd.CallbackStop

        channels = 1 if audio_array.ndim == 1 else audio_array.shape[1]
        logger.debug(
            "Creating audio output stream (target=%s, device=%s, sample_rate=%d, channels=%d)",
            label,
            playback_device,
            sample_rate,
            channels,
        )

        try:
            playback = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                device=playback_device,
                callback=audio_callback,
                finished_callback=mark_finished,
                blocksize=2048,
            )
            return playback, playback_done
        except sd.PortAudioError as exc:
            error_code = _portaudio_error_code(exc)
            if error_code in _RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS and playback_device is not None:
                logger.warning(
                    "PortAudio error %s on device %s, searching for alternatives",
                    error_code,
                    playback_device,
                )
                alternative_devices = self._find_alternative_devices(playback_device)
                for alt_device_id in alternative_devices:
                    try:
                        logger.info("Attempting fallback to device %s", alt_device_id)
                        alt_audio, alt_rate = self._prepare_audio_for_device(
                            audio_array,
                            sample_rate,
                            alt_device_id,
                        )
                        stream = self._create_output_stream(
                            alt_audio,
                            alt_rate,
                            alt_device_id,
                            f"{label}-fallback",
                            _retry_depth=_retry_depth + 1,
                        )
                        self._persist_resolved_output_device(alt_device_id)
                        return stream
                    except Exception as alt_exc:
                        logger.warning("Fallback device %s failed: %s", alt_device_id, alt_exc)
                        continue

                raise RuntimeError(
                    f"All alternative devices failed for device {playback_device}"
                ) from exc
            raise

    @staticmethod
    def _close_playbacks(playbacks: list[sd.OutputStream]) -> None:
        """Close playback streams, ignoring duplicate references."""
        closed_ids: set[int] = set()
        for playback in playbacks:
            playback_id = id(playback)
            if playback_id in closed_ids:
                continue
            closed_ids.add(playback_id)
            try:
                playback.stop()
                playback.close()
            except Exception as exc:
                logger.debug("Error closing playback stream: %s", exc)

    def _release_current_playbacks(
        self,
        playbacks: list[sd.OutputStream],
    ) -> list[sd.OutputStream]:
        """Detach streams from the current playback state before closing them."""
        with self._playback_lock:
            current = list(getattr(self, "_current_playbacks", []))
            owns_any = any(
                current_stream is playback
                for current_stream in current
                for playback in playbacks
            )
            if not owns_any and self._current_playback is not None:
                owns_any = any(self._current_playback is playback for playback in playbacks)
            if not owns_any:
                return []

            self._current_playbacks = [
                current_stream
                for current_stream in current
                if all(current_stream is not playback for playback in playbacks)
            ]
            if self._current_playback is not None and any(
                self._current_playback is playback for playback in playbacks
            ):
                self._current_playback = (
                    self._current_playbacks[0] if self._current_playbacks else None
                )
            if not self._current_playbacks:
                self._current_playback = None
                self._current_playback_done = None
            return list(playbacks)

    def clear_queue(self) -> None:
        """Clear pending TTS requests."""
        while True:
            try:
                self._request_queue.get_nowait()
            except queue.Empty:
                break

    def _worker_loop(self) -> None:
        """Worker thread loop."""
        while self._running:
            try:
                request = self._request_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if request is None:
                break

            self._process_request(request)

    def _process_request(self, request: TTSRequest) -> None:
        """Process TTS request."""
        try:
            # Get audio data (from cache or synthesize)
            audio_data = self._get_audio(
                request.text,
                request.voice,
                request.rate,
                request.volume,
            )

            # Play audio
            self._play_audio(audio_data)

            # Callback success
            self._consecutive_failures = 0
            self._last_failure_message = ""
            self._suspended_until = 0.0
            if request.callback:
                try:
                    request.callback(True, "")
                except Exception as exc:
                    logger.error("TTS callback error: %s", exc)

        except Exception as exc:
            message = str(exc)
            self._record_request_failure(message)
            logger.error("TTS request failed: %s", exc)
            if request.callback:
                try:
                    request.callback(False, message)
                except Exception:
                    pass

    def _record_request_failure(self, message: str) -> None:
        clean_message = str(message or "").strip() or "Unknown TTS error"
        if clean_message == self._last_failure_message:
            self._consecutive_failures += 1
        else:
            self._last_failure_message = clean_message
            self._consecutive_failures = 1

        if self._consecutive_failures < TTS_FAILURE_SUSPEND_THRESHOLD:
            return

        self._suspended_until = time.monotonic() + TTS_FAILURE_SUSPEND_SECONDS
        dropped = self._drop_pending_requests(
            "TTS playback was paused after repeated failures."
        )
        logger.warning(
            "TTS playback paused for %.0f seconds after %d repeated failures "
            "(dropped %d queued request(s)): %s",
            TTS_FAILURE_SUSPEND_SECONDS,
            self._consecutive_failures,
            dropped,
            clean_message,
        )

    def _drop_pending_requests(self, message: str) -> int:
        dropped = 0
        while True:
            try:
                pending = self._request_queue.get_nowait()
            except queue.Empty:
                return dropped
            if pending is None:
                try:
                    self._request_queue.put_nowait(None)
                except queue.Full:
                    pass
                return dropped
            if pending.callback:
                try:
                    pending.callback(False, message)
                except Exception:
                    pass
            dropped += 1

    def _get_audio(
        self,
        text: str,
        voice: str,
        rate: float,
        volume: float,
    ) -> bytes:
        """Get audio data (from cache or synthesize)."""
        # Generate cache key
        cache_key = self._generate_cache_key(text, voice, rate, volume)

        # Try cache first
        if self._cache_enabled:
            with self._cache_lock:
                now = time.monotonic()
                # Evict expired entries (LRU + TTL)
                expired: list[str] = []
                for k, (_, timestamp) in list(self._cache.items()):
                    if now - timestamp > CACHE_TTL_SECONDS:
                        expired.append(k)
                for k in expired:
                    data = self._cache.pop(k)
                    self._cache_size_bytes -= len(data[0])

                if cache_key in self._cache:
                    logger.debug("TTS cache hit")
                    # Move to end to mark as recently used (LRU)
                    self._cache.move_to_end(cache_key)
                    return self._cache[cache_key][0]

        # Synthesize
        if self._engine is None:
            raise RuntimeError("TTS engine not available")

        audio_data = self._engine.synthesize(text, voice, rate, volume)
        if isinstance(audio_data, (bytearray, memoryview)):
            audio_data = bytes(audio_data)
        if not isinstance(audio_data, bytes):
            raise RuntimeError(
                "TTS engine returned invalid audio data "
                f"({type(audio_data).__name__}); expected audio bytes"
            )
        if not audio_data:
            raise RuntimeError("TTS engine returned empty audio")

        # Store in cache
        if self._cache_enabled:
            self._add_to_cache(cache_key, audio_data)

        return audio_data

    def _generate_cache_key(
        self,
        text: str,
        voice: str,
        rate: float,
        volume: float,
    ) -> str:
        """Generate cache key."""
        key_str = f"{text}|{voice}|{rate:.2f}|{volume:.2f}"
        return hashlib.md5(key_str.encode("utf-8")).hexdigest()

    def _add_to_cache(self, key: str, data: bytes) -> None:
        """Add audio data to cache."""
        with self._cache_lock:
            data_size = len(data)
            if self._max_cache_size_mb <= 0 or self._max_cache_items <= 0:
                return

            # Check if cache is full
            max_size_bytes = self._max_cache_size_mb * 1024 * 1024
            if data_size > max_size_bytes:
                logger.debug(
                    "Skipping oversized TTS cache item: %.2f MB > %d MB limit",
                    data_size / (1024 * 1024),
                    self._max_cache_size_mb,
                )
                return
            while (
                self._cache_size_bytes + data_size > max_size_bytes
                or len(self._cache) >= self._max_cache_items
            ):
                if not self._cache:
                    break
                # Remove oldest item (LRU eviction)
                oldest_key = next(iter(self._cache))
                oldest_data = self._cache.pop(oldest_key)
                self._cache_size_bytes -= len(oldest_data[0])

            # Add to cache with timestamp for TTL
            self._cache[key] = (data, time.monotonic())
            self._cache_size_bytes += data_size
            logger.debug(
                "TTS cache: %d items, %.2f MB",
                len(self._cache),
                self._cache_size_bytes / (1024 * 1024),
            )

    def _resolve_monitor_playback_device(
        self,
        primary_device: OutputDeviceRef,
        primary_name: str,
    ) -> tuple[OutputDeviceRef, str] | None:
        if not self._monitor_output or primary_device is None:
            return None

        devices = _iter_output_devices()
        primary_id = _coerce_device_id(primary_device)
        try:
            default_out = sd.default.device[1]
            default_id = int(default_out) if default_out is not None and int(default_out) >= 0 else None
        except Exception:
            default_id = None

        if default_id is not None and default_id != primary_id:
            matched = _find_output_device_by_id(default_id, devices)
            if matched is not None:
                device_id, device_name, _hostapi_name = matched
                if not _is_virtual_output_device(device_name) and not _device_names_match(device_name, primary_name):
                    return device_id, device_name

        for device_id, device_name, hostapi_name in devices:
            if device_id == primary_id:
                continue
            if _is_virtual_output_device(device_name):
                continue
            if _device_names_match(device_name, primary_name):
                continue
            hostapi_lower = hostapi_name.lower()
            if "wasapi" in hostapi_lower or "directsound" in hostapi_lower or "mme" in hostapi_lower:
                return device_id, device_name

        logger.warning("TTS monitor output skipped: no safe local playback device found")
        return None

    def _play_audio(self, audio_data: bytes) -> None:
        """Play audio data with automatic sample rate detection and fallback."""
        playback_device, playback_name = self._resolve_playback_device()
        monitor_device = self._resolve_monitor_playback_device(playback_device, playback_name)
        monitor_output = monitor_device is not None
        logger.info(
            "Starting audio playback (configured_device=%s, resolved_device=%s, resolved_name=%s, monitor_output=%s, data_size=%d bytes)",
            self._output_device,
            playback_device,
            playback_name or "default",
            monitor_output,
            len(audio_data),
        )

        # Wait briefly so the audio device is fully released before starting the next stream
        # (avoids brief audio overlap/duplicate playback). Use the existing done event from
        # the prior playback so we don't wait longer than necessary — it fires as soon as
        # the stream finishes. Fall back to a short sleep if the event is already gone.
        done_event = self._current_playback_done
        with self._playback_lock:
            active_streams = list(getattr(self, "_current_playbacks", []))
        if done_event is not None:
            released = done_event.wait(timeout=0.05)
            del done_event
        elif active_streams:
            time.sleep(0.05)

        stream_records: list[
            tuple[sd.OutputStream, threading.Event, str, bool, OutputDeviceRef]
        ] = []
        registered_playback = False

        try:
            audio_array, sample_rate = self._decode_audio_data(audio_data)
            logger.debug(
                "Audio decoded: sample_rate=%d, shape=%s, dtype=%s",
                sample_rate,
                audio_array.shape,
                audio_array.dtype,
            )
            audio_array = _append_tail_silence(audio_array, sample_rate)
            logger.debug(
                "Audio tail padding applied: tail_ms=%d, padded_shape=%s",
                TTS_PLAYBACK_TAIL_PADDING_MS,
                audio_array.shape,
            )

            def build_stream(
                target_device: OutputDeviceRef,
                target_name: str,
                required: bool,
            ) -> tuple[sd.OutputStream, threading.Event, str, bool, OutputDeviceRef] | None:
                try:
                    target_audio, target_rate = self._prepare_audio_for_device(
                        audio_array,
                        sample_rate,
                        target_device,
                    )
                    playback, done_event = self._create_output_stream(
                        target_audio,
                        target_rate,
                        target_device,
                        target_name,
                    )
                    return playback, done_event, target_name, required, target_device
                except sd.PortAudioError as exc:
                    error_code = _portaudio_error_code(exc)
                    if required and error_code == -9997:
                        logger.error("Device does not support sample rate %d Hz", sample_rate)
                        raise RuntimeError(f"Unsupported sample rate: {sample_rate} Hz") from exc
                    if (
                        required
                        and error_code in _RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS
                        and target_device is not None
                    ):
                        logger.error(
                            "Host API error (WDM-KS may not support this operation): %s",
                            exc,
                        )
                        if self._prefer_virtual_output:
                            raise RuntimeError(f"MixLine output device error: {exc}") from exc
                        logger.info("Retrying with default output device")
                        fallback_audio, fallback_rate = self._prepare_audio_for_device(
                            audio_array,
                            sample_rate,
                            None,
                        )
                        fallback_playback, fallback_done = self._create_output_stream(
                            fallback_audio,
                            fallback_rate,
                            None,
                            "fallback-default",
                        )
                        return fallback_playback, fallback_done, "fallback-default", True, None
                    if required:
                        raise
                    logger.warning("TTS monitor output skipped: %s", exc)
                    return None
                except Exception as exc:
                    if required:
                        raise
                    logger.warning("TTS monitor output skipped: %s", exc)
                    return None

            primary_record = build_stream(playback_device, playback_name or "default", True)
            if primary_record is not None:
                stream_records.append(primary_record)

            if monitor_device is not None and primary_record is not None:
                monitor_id, monitor_name = monitor_device
                monitor_record = build_stream(monitor_id, f"monitor-{monitor_name}", False)
                if monitor_record is not None:
                    stream_records.append(monitor_record)

            if not stream_records:
                raise RuntimeError("Audio playback failed: no output stream")

            started_records: list[
                tuple[sd.OutputStream, threading.Event, str, bool, OutputDeviceRef]
            ] = []
            for playback, done_event, label, required, target_device in stream_records:
                try:
                    playback.start()
                    started_records.append(
                        (playback, done_event, label, required, target_device)
                    )
                    logger.debug("Audio stream started (target=%s)", label)
                except sd.PortAudioError as exc:
                    self._close_playbacks([playback])
                    error_code = _portaudio_error_code(exc)
                    if (
                        required
                        and error_code in _RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS
                        and target_device is not None
                    ):
                        logger.warning(
                            "Recoverable host API error while starting output stream: %s",
                            exc,
                        )
                        alternative_devices = self._find_alternative_devices(target_device)
                        for alt_device_id in alternative_devices:
                            alt_playback = None
                            try:
                                logger.info("Attempting fallback to device %s", alt_device_id)
                                alt_audio, alt_rate = self._prepare_audio_for_device(
                                    audio_array,
                                    sample_rate,
                                    alt_device_id,
                                )
                                alt_playback, alt_done = self._create_output_stream(
                                    alt_audio,
                                    alt_rate,
                                    alt_device_id,
                                    f"{label}-fallback",
                                )
                                alt_playback.start()
                                started_records.append(
                                    (
                                        alt_playback,
                                        alt_done,
                                        f"{label}-fallback",
                                        True,
                                        alt_device_id,
                                    )
                                )
                                self._persist_resolved_output_device(alt_device_id)
                                break
                            except Exception as alt_exc:
                                if alt_playback is not None:
                                    self._close_playbacks([alt_playback])
                                logger.warning("Fallback device %s failed: %s", alt_device_id, alt_exc)
                                continue
                        else:
                            if self._prefer_virtual_output:
                                raise RuntimeError(f"MixLine output device error: {exc}") from exc
                            logger.info("Retrying with default output device")
                            fallback_record = build_stream(None, "fallback-default", True)
                            if fallback_record is None:
                                raise RuntimeError(f"Audio device error: {exc}") from exc
                            fallback_playback = fallback_record[0]
                            try:
                                fallback_playback.start()
                            except Exception:
                                self._close_playbacks([fallback_playback])
                                raise
                            started_records.append(fallback_record)
                        continue
                    if required:
                        raise
                    logger.warning("TTS monitor output skipped while starting: %s", exc)
                except Exception as exc:
                    self._close_playbacks([playback])
                    if required:
                        raise
                    logger.warning("TTS monitor output skipped while starting: %s", exc)

            stream_records = started_records
            if not stream_records:
                raise RuntimeError("Audio playback failed: no output stream started")

            playbacks = [record[0] for record in stream_records]
            stop_event = threading.Event()
            with self._playback_lock:
                self._current_playbacks = playbacks
                self._current_playback = playbacks[0]
                self._current_playback_done = stop_event
                registered_playback = True

            logger.debug("Waiting for playback to complete...")
            deadline = time.monotonic() + 30.0
            while True:
                if stop_event.is_set():
                    logger.debug("Playback interrupted")
                    break
                if all(record[1].is_set() for record in stream_records):
                    logger.debug("Playback finished successfully")
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("Audio playback timeout")
                    break
                time.sleep(min(0.05, remaining))

        except sd.PortAudioError as exc:
            logger.error("PortAudio error during playback: %s", exc)
            raise RuntimeError(f"Audio playback failed: {exc}")
        except Exception as exc:
            logger.error("Audio playback failed: %s", exc)
            raise
        finally:
            playbacks = [record[0] for record in stream_records]
            if registered_playback:
                playbacks = self._release_current_playbacks(playbacks)
            self._close_playbacks(playbacks)

    def _probe_supported_sample_rates(self, device: OutputDeviceRef) -> list[int]:
        """Probe which sample rates the device supports.

        Args:
            device: Device ID or None for default device.

        Returns:
            List of supported sample rates in Hz.
        """
        # Check cache first
        with self._device_cache_lock:
            if device in self._device_sample_rates_cache:
                return self._device_sample_rates_cache[device]

        common_rates = [8000, 11025, 16000, 22050, 24000, 44100, 48000, 96000]
        supported = []

        for rate in common_rates:
            try:
                sd.check_output_settings(
                    device=device,
                    samplerate=rate,
                    channels=1
                )
                supported.append(rate)
            except Exception as exc:
                logger.debug(
                    "Output device sample rate probe failed (device=%s rate=%s): %s",
                    device,
                    rate,
                    exc,
                )

        # If no rates found, assume 48kHz (most common)
        if not supported:
            logger.warning("Could not probe device sample rates, assuming 48kHz")
            supported = [48000]

        logger.debug("Device supports sample rates: %s", supported)

        # Update cache
        with self._device_cache_lock:
            self._device_sample_rates_cache[device] = supported

        return supported

    def _find_alternative_devices(self, failed_device: OutputDeviceRef) -> list[int]:
        """Find alternative MIXLINE devices when the primary device fails.

        Args:
            failed_device: The device ID that failed.

        Returns:
            List of alternative device IDs, sorted by priority.
        """
        devices = _iter_output_devices()
        failed_device_id = _coerce_device_id(failed_device)
        if failed_device_id is None:
            return []

        # Get the failed device name
        failed_match = _find_output_device_by_id(failed_device_id, devices)
        if failed_match is None:
            return []

        _failed_id, failed_name, _failed_hostapi = failed_match
        failed_name_lower = failed_name.lower()

        # Only search for alternatives if it's a virtual device
        if not _is_virtual_output_device(failed_name):
            return []

        candidates: list[tuple[int, int, str]] = []

        for device_id, device_name, hostapi_name in devices:
            if device_id == failed_device_id:
                continue
            if _is_rejected_mixline_device(device_name):
                continue

            device_name_lower = device_name.lower()

            # Check if it's a similar virtual device
            if not _is_virtual_output_device(device_name):
                continue

            # Prefer the closest MixLine endpoint, but keep all MixLine virtual
            # devices as candidates so localized/renamed variants can recover.
            similarity = self._calculate_name_similarity(failed_name_lower, device_name_lower)
            similarity_bonus = int(similarity * 100)

            # Score by host API priority
            hostapi_lower = hostapi_name.lower()
            if "wasapi" in hostapi_lower:
                priority = 3
            elif "directsound" in hostapi_lower:
                priority = 2
            elif "mme" in hostapi_lower:
                priority = 1
            else:
                priority = 0

            score = _virtual_output_score(device_name, hostapi_name) + similarity_bonus + (priority * 10)
            candidates.append((score, device_id, device_name))

        if not candidates:
            logger.warning("No alternative devices found for %s", failed_name)
            return []

        # Sort by score (descending)
        candidates.sort(key=lambda x: x[0], reverse=True)
        alternative_ids = [device_id for _score, device_id, _name in candidates]

        logger.info(
            "Found %d alternative devices for %s: %s",
            len(alternative_ids),
            failed_name,
            [name for _score, _id, name in candidates],
        )

        return alternative_ids

    @staticmethod
    def _calculate_name_similarity(name1: str, name2: str) -> float:
        """Calculate similarity between two device names (0.0 to 1.0)."""
        # Simple word-based similarity
        words1 = set(name1.split())
        words2 = set(name2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union) if union else 0.0

    def _choose_best_sample_rate(self, source_rate: int, supported_rates: list[int]) -> int:
        """Choose the best target sample rate for resampling.

        Args:
            source_rate: Original audio sample rate.
            supported_rates: List of sample rates supported by the device.

        Returns:
            Best target sample rate.
        """
        # If source rate is supported, use it
        if source_rate in supported_rates:
            return source_rate

        # Otherwise, find the closest supported rate
        # Prefer higher rates to avoid quality loss
        closest = min(supported_rates, key=lambda x: abs(x - source_rate))

        # If there's a higher rate close to source, prefer it
        higher_rates = [r for r in supported_rates if r > source_rate]
        if higher_rates:
            closest_higher = min(higher_rates, key=lambda x: abs(x - source_rate))
            # If the higher rate is within 2x of source, use it
            if closest_higher <= source_rate * 2:
                return closest_higher

        return closest

    @staticmethod
    def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
        """Decode WAV audio data."""
        import wave

        with io.BytesIO(data) as f:
            with wave.open(f, "rb") as wav:
                sample_rate = wav.getframerate()
                n_channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                frames = wav.readframes(wav.getnframes())

                # Convert to numpy array
                if sample_width == 1:
                    dtype = np.uint8
                elif sample_width == 2:
                    dtype = np.int16
                else:
                    dtype = np.int32

                audio_array = np.frombuffer(frames, dtype=dtype)

                # Reshape for multi-channel
                if n_channels > 1:
                    audio_array = audio_array.reshape(-1, n_channels)

                # Convert to float32
                audio_array = audio_array.astype(np.float32) / 32768.0

                return audio_array, sample_rate

    @staticmethod
    def _decode_mp3(data: bytes) -> tuple[np.ndarray, int]:
        """Decode MP3 audio data using PyAV."""
        try:
            import av
        except ImportError:
            raise RuntimeError(
                "PyAV is required for MP3 playback. Install with: pip install av"
            )

        # Open MP3 data as a container
        container = av.open(io.BytesIO(data))

        # Get audio stream
        audio_stream = container.streams.audio[0]

        # Decode all frames
        audio_frames = []
        for frame in container.decode(audio_stream):
            # Convert frame to numpy array
            array = frame.to_ndarray()
            audio_frames.append(array)

        if not audio_frames:
            raise RuntimeError("No audio data decoded from MP3")

        # Concatenate all frames
        audio_data = np.concatenate(audio_frames, axis=1)

        # PyAV returns shape (channels, samples), we need (samples,) or (samples, channels)
        if audio_data.shape[0] == 1:
            # Mono: shape (1, samples) -> (samples,)
            audio_data = audio_data[0]
        else:
            # Stereo: shape (2, samples) -> (samples, 2)
            audio_data = audio_data.T

        # Convert to float32 and normalize to [-1, 1]
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32) / 32768.0

        sample_rate = audio_stream.rate

        container.close()

        return audio_data, sample_rate

    def get_engine_name(self) -> str:
        """Get current engine name."""
        if self._engine is None:
            return "none"
        return self._engine.__class__.__name__

    def get_available_voices(self) -> list:
        """Get available voices from current engine."""
        if self._engine is None:
            return []
        return self._engine.get_available_voices()

    def clear_cache(self) -> None:
        """Clear TTS cache."""
        with self._cache_lock:
            self._cache.clear()
            self._cache_size_bytes = 0
        logger.info("TTS cache cleared")

    def _resolve_playback_device(self) -> tuple[OutputDeviceRef, str]:
        """Resolve the configured output device shortly before playback."""
        resolved = resolve_output_device(
            self._output_device,
            self._output_device_name,
            prefer_virtual=self._prefer_virtual_output,
        )
        if resolved is None:
            return None, ""
        device_id, device_name = resolved
        if device_id != self._output_device or device_name != self._output_device_name:
            self._update_device_config(device_id, device_name)
        return device_id, device_name

    @staticmethod
    def _target_sample_rate(device: OutputDeviceRef, fallback_rate: int) -> int:
        """Return the output device's preferred sample rate when available."""
        try:
            device_info = sd.query_devices(device, kind="output")
        except Exception as exc:
            logger.debug("Failed to query output device sample rate: %s", exc)
            return int(fallback_rate)
        try:
            default_rate = int(round(float(device_info.get("default_samplerate", 0) or 0)))
        except Exception:
            return int(fallback_rate)
        return default_rate if default_rate > 0 else int(fallback_rate)

    @staticmethod
    def _resample_audio(
        audio_array: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        """Resample audio to the target output device sample rate."""
        source_rate = int(source_rate)
        target_rate = int(target_rate)
        if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
            return np.asarray(audio_array, dtype=np.float32)

        audio_float = np.asarray(audio_array, dtype=np.float32)
        try:
            from scipy.signal import resample_poly

            divisor = math.gcd(source_rate, target_rate)
            up = target_rate // divisor
            down = source_rate // divisor
            resampled = resample_poly(audio_float, up, down, axis=0)
        except Exception as exc:
            global _SCIPY_RESAMPLE_FALLBACK_LOGGED
            is_missing_scipy = isinstance(exc, ModuleNotFoundError) and (
                getattr(exc, "name", None) == "scipy"
                or "No module named 'scipy'" in str(exc)
            )
            if is_missing_scipy:
                if not _SCIPY_RESAMPLE_FALLBACK_LOGGED:
                    logger.info(
                        "scipy is not bundled; using numpy interpolation for audio resampling"
                    )
                    _SCIPY_RESAMPLE_FALLBACK_LOGGED = True
                else:
                    logger.debug("Using numpy interpolation for audio resampling")
            else:
                logger.warning(
                    "scipy resample failed, falling back to numpy interpolation: %s",
                    exc,
                )
            source_len = len(audio_float)
            if source_len <= 1:
                return audio_float
            target_len = max(1, int(round(source_len * target_rate / source_rate)))
            source_x = np.linspace(0.0, 1.0, source_len, endpoint=False)
            target_x = np.linspace(0.0, 1.0, target_len, endpoint=False)
            if audio_float.ndim == 1:
                resampled = np.interp(target_x, source_x, audio_float)
            else:
                channels = [
                    np.interp(target_x, source_x, audio_float[:, channel])
                    for channel in range(audio_float.shape[1])
                ]
                resampled = np.stack(channels, axis=1)

        resampled = np.asarray(resampled, dtype=np.float32)
        return np.clip(resampled, -1.0, 1.0)

    def set_output_device(
        self,
        device_id: OutputDeviceRef,
        device_name: Optional[str] = None,
        *,
        prefer_virtual_output: Optional[bool] = None,
    ) -> None:
        """Set output device for TTS playback.

        Args:
            device_id: Device ID or stable device name (None for default device).
            device_name: Optional stable device name used when numeric IDs change.
            prefer_virtual_output: Whether to fall back to a virtual audio input.
        """
        self._output_device = device_id
        self._output_device_name = str(device_name or "").strip()
        if prefer_virtual_output is not None:
            self._prefer_virtual_output = prefer_virtual_output
        logger.info(
            "TTS output device set to: %s (%s)",
            device_id if device_id is not None else "default",
            self._output_device_name or "no saved name",
        )

    def set_monitor_output(self, enabled: bool) -> None:
        """Enable or disable mirroring virtual-mic TTS to the default speaker."""
        self._monitor_output = bool(enabled)
        logger.info("TTS monitor output set to: %s", self._monitor_output)


def list_output_devices() -> list[tuple[int, str]]:
    """List available audio output devices.

    Returns:
        List of (device_id, device_name) tuples for output devices.
    """
    return [(device_id, device_name) for device_id, device_name, _hostapi in _iter_output_devices()]


def find_best_virtual_output_device(
    *,
    avoid_default_output: bool = True,
) -> tuple[int, str] | None:
    """Find the best virtual playback endpoint for routing TTS into VRChat."""
    devices = _iter_output_devices()
    default_names = _default_output_device_names(devices) if avoid_default_output else ()
    candidates: list[tuple[int, int, str]] = []
    for device_id, device_name, hostapi_name in devices:
        if _is_rejected_mixline_device(device_name):
            continue
        score = _virtual_output_score(device_name, hostapi_name)
        if score > 0:
            if _matches_any_device_name(device_name, default_names):
                # If the Windows default output is already MixLine, do not push
                # routing back to the fragile WDM-KS endpoint just to avoid it.
                score -= 20
            candidates.append((score, device_id, device_name))
    if not candidates:
        return None
    _score, device_id, device_name = max(candidates, key=lambda item: item[0])
    return device_id, device_name


def resolve_output_device(
    output_device: OutputDeviceRef,
    output_device_name: Optional[str] = None,
    *,
    prefer_virtual: bool = False,
    avoid_default_output: bool = True,
) -> tuple[OutputDeviceRef, str] | None:
    """Resolve a configured output device to the current PortAudio device ID.

    sounddevice numeric IDs can change when drivers are added, removed, or
    exposed through a different host API. A saved device name is preferred, and
    when VRChat output is requested we avoid stale IDs that now point to a
    physical headset or microphone endpoint.

    Priority order:
    1. Match by saved device name (most reliable)
    2. Validate saved device ID still points to correct device
    3. Search by device name string
    4. Fallback to virtual device if prefer_virtual=True
    5. Return None (use default device)
    """
    devices = _iter_output_devices()
    default_names = (
        _default_output_device_names(devices)
        if prefer_virtual and avoid_default_output
        else ()
    )
    configured_device_id = _coerce_device_id(output_device)

    def _prefer_higher_priority_virtual_device(
        current_device_id: OutputDeviceRef,
        current_device_name: str,
    ) -> tuple[int, str] | None:
        if not prefer_virtual or not _is_virtual_output_device(current_device_name):
            return None

        resolved_current_id = _coerce_device_id(current_device_id)
        current_entry = (
            _find_output_device_by_id(resolved_current_id, devices)
            if resolved_current_id is not None
            else None
        )
        current_hostapi = current_entry[2] if current_entry is not None else ""
        current_score = _virtual_output_score(current_device_name, current_hostapi)

        fallback = find_best_virtual_output_device(
            avoid_default_output=avoid_default_output,
        )
        if fallback is None:
            return None

        fallback_id, fallback_name = fallback
        if resolved_current_id is not None and fallback_id == resolved_current_id:
            return None
        if _normalize_device_name(fallback_name) == _normalize_device_name(current_device_name):
            return None

        fallback_entry = _find_output_device_by_id(fallback_id, devices)
        fallback_hostapi = fallback_entry[2] if fallback_entry is not None else ""
        fallback_score = _virtual_output_score(fallback_name, fallback_hostapi)
        if fallback_score > current_score:
            logger.info("Switching to higher-priority virtual device: %s", fallback_name)
            return fallback
        return None

    # Priority 1: Match by saved device name
    saved_name = str(output_device_name or "").strip()
    if saved_name:
        matched_saved_id = (
            _find_output_device_by_id(configured_device_id, devices)
            if configured_device_id is not None
            else None
        )
        if matched_saved_id is not None:
            _saved_id, matched_name, _matched_hostapi = matched_saved_id
            if _device_names_match(matched_name, saved_name):
                if prefer_virtual and _is_rejected_mixline_device(matched_name):
                    logger.warning(
                        "Saved TTS output device %s is rejected; searching for MixLine",
                        matched_name,
                    )
                elif prefer_virtual and not _is_virtual_output_device(matched_name):
                    logger.warning(
                        "Saved TTS output device %s is not a supported MixLine device; searching for MixLine",
                        matched_name,
                    )
                elif prefer_virtual and _matches_any_device_name(matched_name, default_names):
                    fallback = find_best_virtual_output_device(
                        avoid_default_output=avoid_default_output,
                    )
                    if fallback is not None:
                        logger.info("Switching from default to virtual device: %s", fallback[1])
                        return fallback
                else:
                    virtual_fallback = _prefer_higher_priority_virtual_device(
                        configured_device_id,
                        matched_name,
                    )
                    if virtual_fallback is not None:
                        return virtual_fallback
                    logger.info(
                        "Validated saved device ID %s: %s",
                        configured_device_id,
                        matched_name,
                    )
                    return configured_device_id, matched_name

        matched = _find_output_device_by_name(saved_name, devices)
        if matched is not None:
            matched_id, matched_name = matched
            logger.info(
                "Resolved device by name: '%s' -> device %s",
                saved_name,
                matched_id
            )
            if prefer_virtual and _is_rejected_mixline_device(matched_name):
                logger.warning(
                    "Saved TTS output device %s is rejected; searching for MixLine",
                    matched_name,
                )
            elif prefer_virtual and not _is_virtual_output_device(matched_name):
                logger.warning(
                    "Saved TTS output device %s is not a supported MixLine device; searching for MixLine",
                    matched_name,
                )
            else:
                if prefer_virtual and _matches_any_device_name(matched_name, default_names):
                    fallback = find_best_virtual_output_device(
                        avoid_default_output=avoid_default_output,
                    )
                    if fallback is not None:
                        logger.info("Switching from default to virtual device: %s", fallback[1])
                        return fallback
                virtual_fallback = _prefer_higher_priority_virtual_device(matched_id, matched_name)
                if virtual_fallback is not None:
                    return virtual_fallback
                return matched
        if matched is None:
            logger.warning("Saved TTS output device name not found: %s", saved_name)

    # Priority 2: Validate saved device ID
    device_id = configured_device_id
    if device_id is not None:
        matched_by_id = _find_output_device_by_id(device_id, devices)
        if matched_by_id is not None:
            _matched_id, matched_name, _matched_hostapi = matched_by_id

            # Check if device name matches what we expect
            if saved_name and not _device_names_match(matched_name, saved_name):
                logger.warning(
                    "Device index %s now points to '%s' (expected '%s'), searching by name",
                    device_id,
                    matched_name,
                    saved_name
                )
            elif prefer_virtual and _is_rejected_mixline_device(matched_name):
                logger.warning(
                    "Configured TTS output device %s is rejected; searching for MixLine",
                    matched_name,
                )
            elif not prefer_virtual or _is_virtual_output_device(matched_name):
                if prefer_virtual and _matches_any_device_name(matched_name, default_names):
                    fallback = find_best_virtual_output_device(
                        avoid_default_output=avoid_default_output,
                    )
                    if fallback is not None:
                        return fallback
                virtual_fallback = _prefer_higher_priority_virtual_device(device_id, matched_name)
                if virtual_fallback is not None:
                    return virtual_fallback
                logger.info("Validated device ID %s: %s", device_id, matched_name)
                return device_id, matched_name
            else:
                logger.warning(
                    "Configured TTS output device %s now points to non-virtual device %s; searching for virtual device",
                    device_id,
                    matched_name,
                )

    # Priority 3: Search by device name string
    if isinstance(output_device, str) and output_device.strip():
        matched = _find_output_device_by_name(output_device, devices)
        if matched is not None:
            matched_id, matched_name = matched
            if prefer_virtual and _is_rejected_mixline_device(matched_name):
                logger.warning(
                    "Configured TTS output device %s is rejected; searching for MixLine",
                    matched_name,
                )
            elif not prefer_virtual or _is_virtual_output_device(matched_name):
                if prefer_virtual and _matches_any_device_name(matched_name, default_names):
                    fallback = find_best_virtual_output_device(
                        avoid_default_output=avoid_default_output,
                    )
                    if fallback is not None:
                        return fallback
                virtual_fallback = _prefer_higher_priority_virtual_device(matched_id, matched_name)
                if virtual_fallback is not None:
                    return virtual_fallback
                return matched

    # Priority 4: Fallback to virtual device
    if prefer_virtual:
        fallback = find_best_virtual_output_device(
            avoid_default_output=avoid_default_output,
        )
        if fallback is not None:
            logger.info("Using fallback virtual device: %s", fallback[1])
            return fallback

    # Priority 5: Use default device
    logger.info("No specific device matched, using default output device")
    return None


def _iter_output_devices() -> list[tuple[int, str, str]]:
    devices: list[tuple[int, str, str]] = []
    try:
        device_list = sd.query_devices()
        hostapis = sd.query_hostapis()
        for i, device in enumerate(device_list):
            try:
                if int(device.get("max_output_channels", 0) or 0) <= 0:
                    continue
                hostapi_name = ""
                hostapi_index = int(device.get("hostapi", -1))
                if 0 <= hostapi_index < len(hostapis):
                    hostapi_name = str(hostapis[hostapi_index].get("name", "") or "")
                devices.append((i, str(device.get("name", "") or ""), hostapi_name))
            except Exception:
                logger.debug("Skipping malformed audio device entry: %r", device, exc_info=True)
    except Exception as exc:
        logger.error("Failed to list audio devices: %s", exc)
    return devices


def _coerce_device_id(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _find_output_device_by_id(
    device_id: int,
    devices: list[tuple[int, str, str]],
) -> tuple[int, str, str] | None:
    for current_id, device_name, hostapi_name in devices:
        if current_id == device_id:
            return current_id, device_name, hostapi_name
    return None


def _find_output_device_by_name(
    target_name: str,
    devices: list[tuple[int, str, str]],
) -> tuple[int, str] | None:
    target = _normalize_device_name(target_name)
    if not target:
        return None
    scored: list[tuple[int, int, str]] = []
    for device_id, device_name, hostapi_name in devices:
        current = _normalize_device_name(device_name)
        if current == target or current.startswith(target) or target.startswith(current):
            score = 1000 + _virtual_output_score(device_name, hostapi_name)
            scored.append((score, device_id, device_name))
        elif target in current or current in target:
            score = 500 + _virtual_output_score(device_name, hostapi_name)
            scored.append((score, device_id, device_name))
    if not scored:
        return None
    _score, device_id, device_name = max(scored, key=lambda item: item[0])
    return device_id, device_name


def _default_output_device_names(
    devices: list[tuple[int, str, str]],
) -> tuple[str, ...]:
    try:
        default_out = sd.default.device[1]
        if default_out is None or int(default_out) < 0:
            return ()
        default_id = int(default_out)
    except Exception:
        return ()
    matched = _find_output_device_by_id(default_id, devices)
    if matched is None:
        return ()
    _device_id, device_name, _hostapi_name = matched
    return (device_name,)


def _matches_any_device_name(device_name: str, names: tuple[str, ...]) -> bool:
    return any(_device_names_match(device_name, name) for name in names if name)


def _device_names_match(left: str, right: str) -> bool:
    left_norm = _normalize_device_name(left)
    right_norm = _normalize_device_name(right)
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.startswith(right_norm)
        or right_norm.startswith(left_norm)
        or left_norm in right_norm
        or right_norm in left_norm
    )


def _is_virtual_output_device(device_name: str) -> bool:
    name_lower = device_name.lower()
    return any(keyword in name_lower for keyword in _VIRTUAL_OUTPUT_KEYWORDS)


def _virtual_output_score(device_name: str, hostapi_name: str) -> int:
    if _is_rejected_mixline_device(device_name):
        return 0

    score = _mixline_endpoint_score(device_name)
    if score <= 0:
        return 0

    hostapi_lower = hostapi_name.lower()
    if "wasapi" in hostapi_lower:
        score += 45
    elif "directsound" in hostapi_lower:
        score += 30
    elif "mme" in hostapi_lower:
        score += 5
    elif "wdm-ks" in hostapi_lower:
        # WDM-KS exposes full MixLine names, but PortAudio often fails opening
        # it with -9996/-9992. Prefer a shared WASAPI/DirectSound endpoint.
        score -= 80

    # MME device names are often truncated; full names are safer to persist.
    if len(device_name) >= 40:
        score += 5
    return score


def _normalize_device_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())
