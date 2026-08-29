"""TTS manager with playback queue and caching."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from src.audio.device_inventory import (
    default_output_device_index as inventory_default_output_device_index,
    get_device_inventory,
)
from .base import BaseTTS
from .error_utils import tts_error_code, tts_error_token
from .factory import create_tts_engine, create_tts_engine_with_fallback
from .wav_utils import decode_wav_bytes
from src.utils.app_paths import (
    app_temp_dir,
    atomic_write_bytes,
    require_real_directory,
)
from src.utils.input_validation import validate_tts_text, ValidationError
from src.utils.provider_diagnostics import safe_exception_summary

logger = logging.getLogger(__name__)

# Cache settings
MAX_CACHE_SIZE_MB = 50
MAX_CACHE_ITEMS = 100
CACHE_TTL_SECONDS = 900.0
TTS_FAILURE_SUSPEND_THRESHOLD = 3
TTS_FAILURE_SUSPEND_SECONDS = 30.0
TTS_PLAYBACK_TAIL_PADDING_MS = 80
TTS_PLAYBACK_BLOCKSIZE = 1024
TTS_DEBUG_AUDIO_ENV = "MIO_TTS_DEBUG_AUDIO"
TTS_REQUEST_QUEUE_MAXSIZE = 10
TTS_PIPELINE_MAX_OUTSTANDING = 12
TTS_CLOUD_SYNTHESIS_CONCURRENCY = 2
TTS_WORKER_JOIN_TIMEOUT_SECONDS = 2.0
TTS_DEFAULT_SYNTHESIS_WALL_TIMEOUT_SECONDS = 120.0
TTS_ORDERED_TIMEOUT_GRACE_SECONDS = 1.0
TTS_DUPLICATE_COALESCE_WAIT_SECONDS = 5.0
TTS_PLAYBACK_TIMEOUT_MARGIN_SECONDS = 10.0
TTS_PLAYBACK_TIMEOUT_MAX_SECONDS = 1800.0
TTS_MAX_QUARANTINED_SYNTHESIS_WORKERS = 8
OutputDeviceRef = int | str | None

_VIRTUAL_OUTPUT_KEYWORDS = (
    "mixline",
    "mix line",
)

_RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS = {-9999, -9996, -9992}
_SCIPY_RESAMPLE_FALLBACK_LOGGED = False
_SOXR_RESAMPLE_FALLBACK_LOGGED = False
_CONCURRENT_SYNTHESIS_ENGINES = {
    "voicevox",
    "mimo",
    "mimo_tts",
    "xiaomi_tts",
    "qwen_tts",
    "qwen3_tts",
    "qwen-tts",
}


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


def _audio_stats(audio_array: np.ndarray, sample_rate: int) -> str:
    audio = np.asarray(audio_array, dtype=np.float32)
    if sample_rate <= 0 or audio.size == 0:
        return "sr=0 duration=0.00s peak=0.000 rms=0.000 clipped=0.000"
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio))))
    clipped = float(np.mean(np.abs(audio) >= 0.98))
    duration = float(audio.shape[0]) / float(sample_rate)
    return (
        f"sr={int(sample_rate)} duration={duration:.2f}s "
        f"peak={peak:.3f} rms={rms:.3f} clipped={clipped:.3f}"
    )


def _playback_timeout_seconds(audio_duration_s: object) -> float:
    try:
        duration = max(0.0, float(audio_duration_s))
    except (TypeError, ValueError):
        duration = 0.0
    return min(
        TTS_PLAYBACK_TIMEOUT_MAX_SECONDS,
        max(
            TTS_PLAYBACK_TIMEOUT_MARGIN_SECONDS,
            duration + TTS_PLAYBACK_TIMEOUT_MARGIN_SECONDS,
        ),
    )


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
    sequence: int = -1
    generation: int = 0
    admission_started_at: float = 0.0
    submitted_at: float = 0.0
    queue_depth_at_admission: int = 0
    context: dict[str, object] | None = None


@dataclass(frozen=True)
class _TTSPrewarmRequest:
    voice: str = ""


@dataclass
class _TTSSynthesisResult:
    request: TTSRequest
    audio_data: bytes = b""
    decoded_audio: np.ndarray | None = None
    decoded_sample_rate: int = 0
    error: Exception | None = None
    synthesis_started_at: float = 0.0
    synthesis_finished_at: float = 0.0
    decode_started_at: float = 0.0
    decode_finished_at: float = 0.0
    published_at: float = 0.0
    engine_diagnostics: dict[str, object] | None = None


class _TTSPlaybackInterrupted(RuntimeError):
    """Internal marker for an intentional stop during playback preparation."""


@dataclass(frozen=True)
class TTSQuiescenceState:
    """Bounded, truthful snapshot returned by ``stop`` and ``close``."""

    quiescent: bool
    running: bool
    closed: bool
    synthesis_workers_alive: int
    playback_worker_alive: bool
    outstanding_requests: int
    playback_streams_active: int
    engine_close_deferred: bool
    elapsed_s: float = 0.0
    engine_background_tasks: int = 0

    def __bool__(self) -> bool:
        return self.quiescent


class TTSManager:
    """TTS manager with queue and caching."""

    def __init__(
        self,
        engine_name: str = "qwen_tts",
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
        synthesis_concurrency: Optional[int] = None,
        max_outstanding: int = TTS_PIPELINE_MAX_OUTSTANDING,
    ):
        self._engine: Optional[BaseTTS] = None
        self._engine_name = engine_name
        self._device = str(sbv2_device or device or "cpu").strip().lower()
        if self._device not in {"cpu", "cuda"}:
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
        self._cache_inflight: dict[str, threading.Event] = {}

        self._request_queue: queue.Queue[TTSRequest | _TTSPrewarmRequest | None] = queue.Queue(
            maxsize=TTS_REQUEST_QUEUE_MAXSIZE
        )
        self._worker_thread: Optional[threading.Thread] = None
        self._synthesis_threads: list[threading.Thread] = []
        self._playback_thread: Optional[threading.Thread] = None
        self._running = False
        self._closed = False
        self._engine_closed = False
        self._engine_close_pending = False
        self._engine_close_in_progress = False
        self._engine_close_requesting = False
        self._engine_close_request_thread: Optional[threading.Thread] = None
        self._engine_close_thread: Optional[threading.Thread] = None
        self._manager_close_in_progress = False
        self._active_synthesis_workers = 0
        self._lifecycle_lock = threading.RLock()
        self._pipeline_lock = threading.RLock()
        self._result_available = threading.Condition(self._pipeline_lock)
        self._completed_synthesis: dict[int, _TTSSynthesisResult] = {}
        self._active_synthesis: dict[
            tuple[int, int],
            tuple[TTSRequest, float, threading.Thread],
        ] = {}
        self._worker_state_lock = threading.Lock()
        self._retired_synthesis_workers: set[threading.Thread] = set()
        self._worker_serial = 0
        self._next_sequence = 0
        self._next_playback_sequence = 0
        self._generation = 0
        self._outstanding_requests: set[tuple[int, int]] = set()
        self._max_outstanding = max(1, min(int(max_outstanding), 64))
        self._prewarm_queued = False
        self._prewarm_generation = 0
        self._prewarm_voice = ""
        self._prewarm_completed_workers: set[int] = set()
        self._prewarm_target_workers = 0
        self._request_queue_high_watermark = 0
        self._synthesis_concurrency = self._resolve_synthesis_concurrency(
            synthesis_concurrency
        )
        self._current_playback: Optional[sd.OutputStream] = None
        self._current_playbacks: list[sd.OutputStream] = []
        self._current_playback_done: Optional[threading.Event] = None
        self._playback_lock = threading.Lock()
        self._playback_context = threading.local()
        self._playback_activity_event: threading.Event | None = None
        self._active_playback_metrics: dict[str, object] | None = None
        self._playback_stop_epoch = 0
        self._preparing_playbacks: list[sd.OutputStream] = []

        self._device_sample_rates_cache: dict[
            tuple[OutputDeviceRef, int],
            list[int],
        ] = {}
        self._device_cache_lock = threading.Lock()
        self._consecutive_failures = 0
        self._last_failure_message = ""
        self._last_failure_stage = ""
        self._suspended_until = 0.0

        # Initialize engine eagerly so is_available() and get_available_voices()
        # return correct values before start() is called.
        self._initialize_engine()
        try:
            engine_limit = max(
                1,
                min(int(getattr(self._engine, "max_concurrent_synthesis", 1)), 4),
            )
        except (TypeError, ValueError):
            engine_limit = 1
        self._synthesis_concurrency = min(
            self._synthesis_concurrency,
            engine_limit,
        )
        self._synthesis_wall_timeout_seconds = self._resolve_synthesis_wall_timeout()

    def _resolve_synthesis_concurrency(self, configured: Optional[int]) -> int:
        if configured is None:
            configured = self._engine_config.get("synthesis_concurrency")
        if configured is None:
            normalized_engine = str(self._engine_name or "").strip().lower()
            return (
                TTS_CLOUD_SYNTHESIS_CONCURRENCY
                if normalized_engine in _CONCURRENT_SYNTHESIS_ENGINES
                else 1
            )
        try:
            parsed = int(configured)
        except (TypeError, ValueError):
            parsed = 1
        return max(1, min(parsed, 4))

    def _resolve_synthesis_wall_timeout(self) -> float:
        configured = self._engine_config.get("wall_timeout_seconds")
        if configured is None:
            configured = getattr(self._engine, "wall_timeout_seconds", None)
        if configured is None:
            configured = TTS_DEFAULT_SYNTHESIS_WALL_TIMEOUT_SECONDS
        try:
            parsed = float(configured)
        except (TypeError, ValueError):
            parsed = TTS_DEFAULT_SYNTHESIS_WALL_TIMEOUT_SECONDS
        return max(3.0, min(parsed, 300.0)) + TTS_ORDERED_TIMEOUT_GRACE_SECONDS

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
            logger.error(
                "Failed to initialize TTS engine (%s)",
                safe_exception_summary(exc),
            )
            self._engine = None

    def is_available(self) -> bool:
        """Check if TTS is available."""
        return self._engine is not None and self._engine.is_available()

    def _create_synthesis_thread_locked(self) -> threading.Thread:
        self._worker_serial += 1
        worker_serial = self._worker_serial
        return threading.Thread(
            target=self._synthesis_worker_loop,
            args=(worker_serial,),
            daemon=True,
            name=f"tts-synthesis-{worker_serial}",
        )

    def _retire_synthesis_worker(self, worker: threading.Thread) -> bool:
        with self._worker_state_lock:
            if worker in self._retired_synthesis_workers:
                return False
            self._retired_synthesis_workers.add(worker)
            return True

    def _is_synthesis_worker_retired(self, worker: threading.Thread) -> bool:
        with self._worker_state_lock:
            return worker in self._retired_synthesis_workers

    def _restore_synthesis_capacity(self) -> int:
        """Replace quarantined/stalled workers up to configured capacity."""

        started = 0
        start_failure: BaseException | None = None
        replacement_limit_reached = False
        with self._lifecycle_lock:
            if not self._running or self._closed:
                return 0
            with self._worker_state_lock:
                retired = set(self._retired_synthesis_workers)
            responsive = [
                thread
                for thread in self._synthesis_threads
                if thread not in retired
                and (thread.is_alive() or thread.ident is None)
            ]
            missing = max(0, self._synthesis_concurrency - len(responsive))
            quarantined_alive = sum(
                thread.is_alive() for thread in retired
            )
            replacement_budget = max(
                0,
                TTS_MAX_QUARANTINED_SYNTHESIS_WORKERS - quarantined_alive,
            )
            replacement_limit_reached = missing > 0 and replacement_budget == 0
            missing = min(missing, replacement_budget)
            for _index in range(missing):
                thread = self._create_synthesis_thread_locked()
                self._synthesis_threads.append(thread)
                self._active_synthesis_workers += 1
                try:
                    thread.start()
                except Exception as exc:
                    start_failure = exc
                    if thread.ident is not None or thread.is_alive():
                        responsive.append(thread)
                        started += 1
                        continue
                    self._synthesis_threads = [
                        existing
                        for existing in self._synthesis_threads
                        if existing is not thread
                    ]
                    self._active_synthesis_workers = max(
                        0,
                        self._active_synthesis_workers - 1,
                    )
                    with self._worker_state_lock:
                        self._retired_synthesis_workers.discard(thread)
                    break
                responsive.append(thread)
                started += 1
            self._worker_thread = responsive[0] if responsive else None
        if start_failure is not None:
            logger.error(
                "Failed to start replacement TTS synthesis worker (%s)",
                safe_exception_summary(start_failure),
            )
        if started:
            logger.warning(
                "Restored TTS synthesis capacity with %d replacement worker(s) "
                "after stalled/cancelled work",
                started,
            )
        elif replacement_limit_reached:
            with self._worker_state_lock:
                quarantined = sum(
                    thread.is_alive()
                    for thread in self._retired_synthesis_workers
                )
            if quarantined >= TTS_MAX_QUARANTINED_SYNTHESIS_WORKERS:
                logger.error(
                    "TTS synthesis replacement limit reached "
                    "(quarantined_workers=%d)",
                    quarantined,
                )
        return started

    def start(self) -> None:
        """Start TTS manager."""
        with self._lifecycle_lock:
            if self._closed:
                logger.warning("Cannot start TTS manager after it has been closed")
                return
            if self._running:
                return
            if self._active_synthesis_workers > 0:
                logger.warning(
                    "Cannot start TTS manager while %d previous synthesis worker(s) "
                    "are still stopping",
                    self._active_synthesis_workers,
                )
                return
            # Repeated stop/close calls can enqueue more wake-up sentinels than
            # the previous worker generation consumes.  Never let one of those
            # stale sentinels terminate a replacement worker immediately.
            self._discard_worker_stop_signals_locked()
            if not self.is_available():
                logger.warning("Cannot start TTS manager: no engine available")
                return

            self._running = True
            with self._worker_state_lock:
                self._retired_synthesis_workers.clear()
            synthesis_threads = [
                self._create_synthesis_thread_locked()
                for _index in range(self._synthesis_concurrency)
            ]
            playback_thread = threading.Thread(
                target=self._playback_worker_loop,
                daemon=True,
                name="tts-playback",
            )
            self._synthesis_threads = synthesis_threads
            self._worker_thread = synthesis_threads[0]
            self._playback_thread = playback_thread
            self._active_synthesis_workers = len(synthesis_threads)
            for thread in synthesis_threads:
                thread.start()
            playback_thread.start()
        logger.info("TTS manager started")

    @staticmethod
    def _normalize_lifecycle_timeout(timeout_seconds: float | None) -> float:
        try:
            return (
                TTS_WORKER_JOIN_TIMEOUT_SECONDS
                if timeout_seconds is None
                else max(0.0, min(float(timeout_seconds), 30.0))
            )
        except (TypeError, ValueError):
            return TTS_WORKER_JOIN_TIMEOUT_SECONDS

    def stop(
        self,
        timeout_seconds: float | None = None,
    ) -> TTSQuiescenceState:
        """Stop workers within a bounded wait and return their true state."""

        stop_started_at = time.monotonic()
        join_timeout_s = self._normalize_lifecycle_timeout(timeout_seconds)
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            synthesis_threads = tuple(getattr(self, "_synthesis_threads", ()))
            playback_thread = getattr(self, "_playback_thread", None)
            if not synthesis_threads and self._worker_thread is not None:
                synthesis_threads = (self._worker_thread,)
            self._running = False
            self.clear_queue(
                message="TTS manager stopped.",
                error_code="stopped",
            )
            for _thread in synthesis_threads:
                self._signal_worker_stop()
            condition = getattr(self, "_result_available", None)
            if condition is not None:
                with condition:
                    condition.notify_all()

        self.stop_playback()
        deadline = time.monotonic() + join_timeout_s
        current = threading.current_thread()
        for thread in (*synthesis_threads, playback_thread):
            if thread is None or thread is current:
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            alive_synthesis = [
                thread
                for thread in getattr(self, "_synthesis_threads", synthesis_threads)
                if thread.is_alive()
            ]
            self._synthesis_threads = alive_synthesis
            self._worker_thread = alive_synthesis[0] if alive_synthesis else None
            self._playback_thread = (
                playback_thread
                if playback_thread is not None and playback_thread.is_alive()
                else None
            )
            if not alive_synthesis:
                self._discard_worker_stop_signals_locked()
        state = self.get_quiescence_state(
            elapsed_s=time.monotonic() - stop_started_at,
        )
        closing_with_workers_stopped = bool(
            state.engine_close_deferred
            and getattr(self, "_manager_close_in_progress", False)
            and state.synthesis_workers_alive == 0
            and not state.playback_worker_alive
            and state.outstanding_requests == 0
            and state.playback_streams_active == 0
            and state.engine_background_tasks == 0
        )
        if state.quiescent:
            logger.info("TTS manager stopped and quiescent")
        elif closing_with_workers_stopped:
            logger.info("TTS manager workers stopped; engine close is pending")
        else:
            logger.warning(
                "TTS manager stop incomplete "
                "(synthesis_workers_alive=%d playback_worker_alive=%s "
                "outstanding=%d playback_streams=%d engine_background_tasks=%d "
                "elapsed_ms=%.0f)",
                state.synthesis_workers_alive,
                state.playback_worker_alive,
                state.outstanding_requests,
                state.playback_streams_active,
                state.engine_background_tasks,
                state.elapsed_s * 1000.0,
            )
        return state

    def close(
        self,
        timeout_seconds: float | None = None,
    ) -> TTSQuiescenceState:
        """Permanently stop resources and return bounded quiescence status."""

        close_started_at = time.monotonic()
        close_timeout_s = self._normalize_lifecycle_timeout(timeout_seconds)
        close_deadline = close_started_at + close_timeout_s

        with getattr(self, "_lifecycle_lock", threading.RLock()):
            already_closed = bool(getattr(self, "_closed", False))
            if not already_closed:
                self._closed = True
                self._manager_close_in_progress = True
        if already_closed:
            self._finalize_pending_engine_if_ready()
            self._wait_for_engine_close_handoffs(close_deadline)
            return self.get_quiescence_state(
                elapsed_s=time.monotonic() - close_started_at,
            )

        try:
            self.stop(
                timeout_seconds=max(
                    0.0,
                    close_deadline - time.monotonic(),
                )
            )
            self._release_retained_manager_state()
        except BaseException:
            with getattr(self, "_lifecycle_lock", threading.RLock()):
                if self._engine is not None:
                    self._engine_close_pending = True
                self._manager_close_in_progress = False
            raise

        deferred_engine = None
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            engine_background_tasks = self._engine_background_work_count(
                self._engine
            )
            if self._engine is not None:
                self._engine_close_pending = True
            if (
                self._engine is not None
                and (
                    self._active_synthesis_workers > 0
                    or engine_background_tasks > 0
                )
            ):
                deferred_engine = self._engine
                self._engine_close_requesting = True
            self._manager_close_in_progress = False

        if deferred_engine is not None:
            self._start_engine_close_request(deferred_engine)
            logger.warning(
                "TTS engine close deferred "
                "(synthesis_workers=%d engine_background_tasks=%d)",
                self._active_synthesis_workers,
                engine_background_tasks,
            )
        else:
            self._finalize_pending_engine_if_ready()
        self._wait_for_engine_close_handoffs(close_deadline)
        return self.get_quiescence_state(
            elapsed_s=time.monotonic() - close_started_at,
        )

    def _start_engine_close_request(
        self,
        engine: BaseTTS,
    ) -> threading.Thread | None:
        """Request provider cancellation without extending the close deadline."""

        def request_engine_close() -> None:
            try:
                request_close = getattr(engine, "request_close", None)
                if callable(request_close):
                    request_close()
            except Exception as exc:
                logger.debug(
                    "Failed to request deferred TTS engine close (%s)",
                    safe_exception_summary(exc),
                )
            finally:
                current = threading.current_thread()
                with getattr(self, "_lifecycle_lock", threading.RLock()):
                    if self._engine_close_request_thread is current:
                        self._engine_close_request_thread = None
                    self._engine_close_requesting = False
                self._finalize_pending_engine_if_ready()

        thread = threading.Thread(
            target=request_engine_close,
            daemon=True,
            name="tts-engine-close-request",
        )
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            self._engine_close_request_thread = thread
        try:
            thread.start()
        except Exception as exc:
            if thread.ident is not None or thread.is_alive():
                logger.error(
                    "TTS engine close-request handoff reported a startup "
                    "failure after launch (%s)",
                    safe_exception_summary(exc),
                )
                return thread
            with getattr(self, "_lifecycle_lock", threading.RLock()):
                if self._engine_close_request_thread is thread:
                    self._engine_close_request_thread = None
                self._engine_close_requesting = False
            logger.error(
                "Failed to start TTS engine close-request handoff (%s)",
                safe_exception_summary(exc),
            )
            self._finalize_pending_engine_if_ready()
            return None
        return thread

    def _wait_for_engine_close_handoffs(self, deadline: float) -> None:
        """Wait only within the caller's remaining close budget."""

        current = threading.current_thread()
        while True:
            with getattr(self, "_lifecycle_lock", threading.RLock()):
                threads = tuple(
                    thread
                    for thread in (
                        getattr(self, "_engine_close_request_thread", None),
                        getattr(self, "_engine_close_thread", None),
                    )
                    if thread is not None and thread is not current
                )
            if not threads:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for thread in threads:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if time.monotonic() >= deadline:
                    return

    def get_quiescence_state(
        self,
        *,
        elapsed_s: float = 0.0,
    ) -> TTSQuiescenceState:
        """Return a lock-consistent lifecycle snapshot without waiting."""

        self._finalize_pending_engine_if_ready()

        lifecycle_lock = getattr(self, "_lifecycle_lock", threading.RLock())
        with lifecycle_lock:
            threads = tuple(getattr(self, "_synthesis_threads", ()))
            synthesis_alive = sum(thread.is_alive() for thread in threads)
            playback_thread = getattr(self, "_playback_thread", None)
            playback_alive = bool(
                playback_thread is not None and playback_thread.is_alive()
            )
            active_synthesis_workers = max(
                0,
                int(getattr(self, "_active_synthesis_workers", 0)),
            )
            running = bool(getattr(self, "_running", False))
            closed = bool(getattr(self, "_closed", False))
            engine_close_deferred = bool(
                getattr(self, "_engine_close_pending", False)
                or getattr(self, "_engine_close_in_progress", False)
                or getattr(self, "_engine_close_requesting", False)
                or getattr(self, "_manager_close_in_progress", False)
            )
            engine_background_tasks = self._engine_background_work_count(
                getattr(self, "_engine", None)
            )
            with getattr(self, "_pipeline_lock", threading.RLock()):
                outstanding = len(getattr(self, "_outstanding_requests", ()))
            with getattr(self, "_playback_lock", threading.Lock()):
                playback_ids = {
                    id(playback)
                    for playback in (
                        *getattr(self, "_current_playbacks", ()),
                        *getattr(self, "_preparing_playbacks", ()),
                    )
                }
                current_playback = getattr(self, "_current_playback", None)
                if current_playback is not None:
                    playback_ids.add(id(current_playback))
            playback_streams = len(playback_ids)

        quiescent = bool(
            not running
            and synthesis_alive == 0
            and active_synthesis_workers == 0
            and not playback_alive
            and outstanding == 0
            and playback_streams == 0
            and engine_background_tasks == 0
            and not engine_close_deferred
        )
        return TTSQuiescenceState(
            quiescent=quiescent,
            running=running,
            closed=closed,
            synthesis_workers_alive=synthesis_alive,
            playback_worker_alive=playback_alive,
            outstanding_requests=outstanding,
            playback_streams_active=playback_streams,
            engine_background_tasks=engine_background_tasks,
            engine_close_deferred=engine_close_deferred,
            elapsed_s=max(0.0, float(elapsed_s)),
        )

    def _detach_engine_locked(self) -> Optional[BaseTTS]:
        if self._engine_closed:
            return None
        engine = self._engine
        self._engine = None
        self._engine_closed = True
        self._engine_close_pending = False
        self._engine_close_requesting = False
        return engine

    @staticmethod
    def _engine_background_work_count(engine: Optional[BaseTTS]) -> int:
        getter = getattr(engine, "background_work_count", None)
        if not callable(getter):
            return 0
        try:
            return max(0, int(getter() or 0))
        except Exception as exc:
            logger.debug(
                "Failed to inspect TTS engine background work (%s)",
                safe_exception_summary(exc),
            )
            return 1

    def _finalize_pending_engine_if_ready(self) -> None:
        engine = None
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            if (
                not getattr(self, "_closed", False)
                or not getattr(self, "_engine_close_pending", False)
                or getattr(self, "_engine_close_in_progress", False)
                or getattr(self, "_engine_close_requesting", False)
                or getattr(self, "_manager_close_in_progress", False)
                or int(getattr(self, "_active_synthesis_workers", 0)) > 0
                or self._engine_background_work_count(self._engine) > 0
            ):
                return
            engine = self._detach_engine_locked()
            self._engine_close_in_progress = engine is not None

        if engine is not None:
            self._start_engine_close_thread(engine)

    def _start_engine_close_thread(
        self,
        engine: BaseTTS,
    ) -> threading.Thread | None:
        """Destroy a detached engine asynchronously and exactly once."""

        def close_engine() -> None:
            try:
                self._release_retained_manager_state()
                self._close_engine_resource(engine)
            finally:
                current = threading.current_thread()
                with getattr(self, "_lifecycle_lock", threading.RLock()):
                    if self._engine_close_thread is current:
                        self._engine_close_thread = None
                    self._engine_close_in_progress = False

        thread = threading.Thread(
            target=close_engine,
            daemon=True,
            name="tts-engine-close",
        )
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            self._engine_close_thread = thread
        try:
            thread.start()
        except Exception as exc:
            if thread.ident is not None or thread.is_alive():
                logger.error(
                    "TTS engine close handoff reported a startup failure "
                    "after launch (%s)",
                    safe_exception_summary(exc),
                )
                return thread
            with getattr(self, "_lifecycle_lock", threading.RLock()):
                if self._engine_close_thread is thread:
                    self._engine_close_thread = None
                if self._engine is None and self._engine_closed:
                    self._engine = engine
                    self._engine_closed = False
                self._engine_close_pending = self._engine is not None
                self._engine_close_in_progress = False
            logger.error(
                "Failed to start TTS engine close handoff (%s)",
                safe_exception_summary(exc),
            )
            return None
        return thread

    @staticmethod
    def _close_engine_resource(engine: Optional[BaseTTS]) -> None:
        close_engine = getattr(engine, "close", None)
        if callable(close_engine):
            try:
                close_engine()
            except Exception as exc:
                logger.debug(
                    "Failed to close TTS engine (%s)",
                    safe_exception_summary(exc),
                )

    def _release_retained_manager_state(self) -> None:
        cache_lock = getattr(self, "_cache_lock", None)
        if cache_lock is not None:
            with cache_lock:
                self._cache.clear()
                self._cache_size_bytes = 0
                inflight = tuple(self._cache_inflight.values())
                self._cache_inflight.clear()
            for event in inflight:
                event.set()

        device_lock = getattr(self, "_device_cache_lock", None)
        if device_lock is not None:
            with device_lock:
                self._device_sample_rates_cache.clear()

        engine_config = getattr(self, "_engine_config", None)
        if isinstance(engine_config, dict):
            engine_config.clear()

    def _signal_worker_stop(self) -> None:
        """Wake the worker without blocking the UI thread if the queue is full."""
        while True:
            try:
                self._request_queue.put_nowait(None)
                return
            except queue.Full:
                try:
                    dropped = self._request_queue.get_nowait()
                except queue.Empty:
                    return
                if isinstance(dropped, TTSRequest):
                    self._finish_request_identity(dropped)
                    self._notify_request_callback(
                        dropped,
                        False,
                        tts_error_token("stopped"),
                    )
                elif isinstance(dropped, _TTSPrewarmRequest):
                    # The token only wakes a worker. The broadcast generation
                    # remains authoritative until every worker has attempted it.
                    pass

    def _discard_worker_stop_signals_locked(self) -> int:
        """Remove stale worker sentinels while preserving real queued work."""

        retained: list[TTSRequest | _TTSPrewarmRequest] = []
        removed = 0
        while True:
            try:
                pending = self._request_queue.get_nowait()
            except queue.Empty:
                break
            if pending is None:
                removed += 1
            else:
                retained.append(pending)
        for pending in retained:
            self._request_queue.put_nowait(pending)
        return removed

    def speak(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
        callback: Optional[Callable[[bool, str], None]] = None,
        *,
        request_context: Mapping[str, object] | None = None,
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
        admission_started_at = time.monotonic()
        # Validate input
        try:
            text = validate_tts_text(text)
        except ValidationError as exc:
            logger.error(
                "Invalid TTS input (%s)",
                safe_exception_summary(exc),
            )
            if callback:
                callback(False, tts_error_token("invalid_input"))
            return False

        pipeline_lock = getattr(self, "_pipeline_lock", None)
        if pipeline_lock is None:
            pipeline_lock = threading.RLock()
            self._pipeline_lock = pipeline_lock
        lifecycle_lock = getattr(self, "_lifecycle_lock", threading.RLock())
        with lifecycle_lock:
            if not self._running or self._closed:
                logger.warning("TTS manager not running")
                if callback:
                    callback(False, tts_error_token("stopped"))
                return False

            suspended_for = self._suspended_until - time.monotonic()
            if suspended_for > 0:
                message = self._suspension_message(
                    getattr(self, "_last_failure_stage", ""),
                    suspended_for,
                )
                logger.info(message)
                if callback:
                    callback(False, tts_error_token("suspended"))
                return False

            with pipeline_lock:
                # stop() holds the lifecycle lock while it flips _running and
                # drains this same pipeline lock. A request therefore either
                # commits before stop and is cancelled by it, or observes the
                # stopped state; it can never land behind stop's drain.
                if not self._running or self._closed:
                    if callback:
                        callback(False, tts_error_token("stopped"))
                    return False
                outstanding = getattr(self, "_outstanding_requests", set())
                if len(outstanding) >= getattr(
                    self,
                    "_max_outstanding",
                    TTS_PIPELINE_MAX_OUTSTANDING,
                ):
                    logger.warning("TTS pipeline full, dropping newest request")
                    if callback:
                        callback(False, tts_error_token("pipeline_full"))
                    return False
                sequence = int(getattr(self, "_next_sequence", 0))
                generation = int(getattr(self, "_generation", 0))
                queue_depth = self._request_queue.qsize()
                request = TTSRequest(
                    text=text,
                    voice=voice,
                    rate=rate,
                    volume=volume,
                    callback=callback,
                    sequence=sequence,
                    generation=generation,
                    admission_started_at=admission_started_at,
                    submitted_at=time.monotonic(),
                    queue_depth_at_admission=queue_depth,
                    context=self._normalize_request_context(request_context),
                )
                try:
                    self._request_queue.put_nowait(request)
                except queue.Full:
                    logger.warning("TTS synthesis queue full, dropping newest request")
                    if callback:
                        callback(False, tts_error_token("queue_full"))
                    return False
                self._next_sequence = sequence + 1
                outstanding.add((generation, sequence))
                self._outstanding_requests = outstanding
                self._request_queue_high_watermark = max(
                    int(getattr(self, "_request_queue_high_watermark", 0)),
                    self._request_queue.qsize(),
                )
                logger.info(
                    "TTS request admitted (engine=%s sequence=%d generation=%d "
                    "queue_depth=%d outstanding=%d admission_ms=%.1f)",
                    self._engine_name,
                    sequence,
                    generation,
                    queue_depth,
                    len(outstanding),
                    max(0.0, request.submitted_at - admission_started_at) * 1000.0,
                )
                return True

    @staticmethod
    def _normalize_request_context(
        context: Mapping[str, object] | None,
    ) -> dict[str, object]:
        if not isinstance(context, Mapping):
            return {}
        allowed = {
            "source",
            "session_id",
            "sequence",
            "upstream_started_at",
            "ui_delivered_at",
            "ui_delivery_s",
            "osc_finished_at",
            "osc_wait_s",
        }
        return {
            str(key): value
            for key, value in context.items()
            if str(key) in allowed
        }

    def prewarm(self, voice: str = "") -> bool:
        """Ask every synthesis worker to prepare its thread-local context."""

        with self._lifecycle_lock:
            engine = self._engine
            if (
                not self._running
                or self._closed
                or engine is None
                or type(engine).prewarm is BaseTTS.prewarm
            ):
                return False
            with self._pipeline_lock:
                if not self._running or self._closed:
                    return False
                if self._prewarm_queued:
                    return True
                self._prewarm_generation = int(
                    getattr(self, "_prewarm_generation", 0)
                ) + 1
                self._prewarm_voice = str(voice or "").strip()
                self._prewarm_completed_workers = set()
                self._prewarm_target_workers = max(
                    1,
                    int(getattr(self, "_synthesis_concurrency", 1)),
                )
                self._prewarm_queued = True
                try:
                    self._request_queue.put_nowait(
                        _TTSPrewarmRequest(self._prewarm_voice)
                    )
                except queue.Full:
                    # Workers also poll the generation before each real request,
                    # so a full speech queue must not cancel the warm-up request.
                    pass
                return True

    def _prewarm_synthesis_worker(
        self,
        worker_index: int,
        seen_generation: int,
    ) -> int:
        """Run one pending warm-up generation on the current worker."""

        with self._pipeline_lock:
            generation = int(getattr(self, "_prewarm_generation", 0))
            if (
                not self._running
                or not self._prewarm_queued
                or generation <= seen_generation
                or self._engine is None
            ):
                return seen_generation
            voice = str(getattr(self, "_prewarm_voice", "") or "")

        try:
            started_at = time.monotonic()
            self._engine.prewarm(voice)
            logger.info(
                "TTS engine worker prewarm finished "
                "(engine=%s worker=%d generation=%d elapsed_ms=%.0f)",
                self._engine_name,
                worker_index,
                generation,
                (time.monotonic() - started_at) * 1000.0,
            )
        except Exception as exc:
            logger.warning(
                "TTS engine prewarm failed "
                "(worker=%d generation=%d %s)",
                worker_index,
                generation,
                safe_exception_summary(exc),
            )
        finally:
            with self._pipeline_lock:
                if generation == int(getattr(self, "_prewarm_generation", 0)):
                    completed = set(
                        getattr(self, "_prewarm_completed_workers", set())
                    )
                    completed.add(int(worker_index))
                    self._prewarm_completed_workers = completed
                    target = max(
                        1,
                        int(getattr(self, "_prewarm_target_workers", 1)),
                    )
                    if len(completed) >= target:
                        self._prewarm_queued = False
        return max(seen_generation, generation)

    def stop_playback(self) -> None:
        """Stop current playback."""
        metrics = getattr(self, "_active_playback_metrics", None)
        if isinstance(metrics, dict):
            metrics["interrupted"] = True
            metrics["interrupted_at"] = time.monotonic()
        with self._playback_lock:
            self._playback_stop_epoch = int(
                getattr(self, "_playback_stop_epoch", 0)
            ) + 1
            playbacks = list(getattr(self, "_current_playbacks", []))
            playbacks.extend(getattr(self, "_preparing_playbacks", ()))
            playback = self._current_playback
            if playback is not None and all(active is not playback for active in playbacks):
                playbacks.append(playback)
            done_event = self._current_playback_done
            activity_event = getattr(self, "_playback_activity_event", None)
            self._preparing_playbacks = []
            self._current_playbacks = []
            self._current_playback = None
            self._current_playback_done = None

        self._close_playbacks(playbacks)

        if done_event is not None:
            done_event.set()
        if activity_event is not None:
            activity_event.set()

    def _register_preparing_playbacks(
        self,
        playbacks: list[sd.OutputStream],
        playback_epoch: int,
    ) -> None:
        with self._playback_lock:
            if playback_epoch != self._playback_stop_epoch:
                raise _TTSPlaybackInterrupted("TTS playback was stopped during preparation")
            preparing = list(getattr(self, "_preparing_playbacks", []))
            for playback in playbacks:
                if all(existing is not playback for existing in preparing):
                    preparing.append(playback)
            self._preparing_playbacks = preparing

    def _unregister_preparing_playbacks(
        self,
        playbacks: list[sd.OutputStream],
    ) -> None:
        with self._playback_lock:
            self._preparing_playbacks = [
                preparing
                for preparing in getattr(self, "_preparing_playbacks", [])
                if all(preparing is not playback for playback in playbacks)
            ]

    def _start_preparing_playback(
        self,
        playback: sd.OutputStream,
        playback_epoch: int,
    ) -> None:
        # The stream is registered before start. Do not hold the lock across
        # PortAudio start(): a misbehaving driver must not make stop/close
        # unbounded. If stop races the native call, it invalidates the epoch,
        # closes the registered stream, and this post-check closes it again
        # defensively before reporting intentional interruption.
        with self._playback_lock:
            if (
                playback_epoch != self._playback_stop_epoch
                or all(
                    preparing is not playback
                    for preparing in getattr(self, "_preparing_playbacks", [])
                )
            ):
                raise _TTSPlaybackInterrupted(
                    "TTS playback was stopped before the stream could start"
                )
        playback.start()
        with self._playback_lock:
            still_registered = any(
                preparing is playback
                for preparing in getattr(self, "_preparing_playbacks", [])
            )
            interrupted = (
                playback_epoch != self._playback_stop_epoch
                or not still_registered
            )
        if interrupted:
            self._close_playbacks([playback])
            raise _TTSPlaybackInterrupted(
                "TTS playback was stopped while the stream was starting"
            )

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
                logger.error(
                    "Failed to save device configuration (%s)",
                    safe_exception_summary(exc),
                )

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
        if (
            audio_data.startswith((b"ID3", b"OggS", b"fLaC", b"\x1aE\xdf\xa3"))
            or (len(audio_data) >= 12 and audio_data[4:8] == b"ftyp")
            or (
            len(audio_data) >= 2
            and audio_data[0] == 0xFF
            and (audio_data[1] & 0xE0) == 0xE0
            )
        ):
            return self._decode_mp3(audio_data)
        raise RuntimeError("Unknown audio format")

    def _prepare_audio_for_device(
        self,
        audio_array: np.ndarray,
        sample_rate: int,
        playback_device: OutputDeviceRef,
    ) -> tuple[np.ndarray, int]:
        """Adapt decoded audio to the target device's channel/rate contract."""
        target_audio = np.asarray(audio_array, dtype=np.float32)
        source_channels = 1 if target_audio.ndim == 1 else int(target_audio.shape[1])
        target_channels = self._preferred_output_channels(
            playback_device,
            source_channels,
        )
        if target_channels != source_channels:
            logger.info(
                "Converting TTS channel layout: %d -> %d channels (device=%s)",
                source_channels,
                target_channels,
                playback_device,
            )
            target_audio = self._convert_channel_layout(
                target_audio,
                target_channels,
            )

        supported_rates = self._probe_supported_sample_rates(
            playback_device,
            channels=target_channels,
        )
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
        return np.ascontiguousarray(target_audio, dtype=np.float32), target_sample_rate

    @staticmethod
    def _convert_channel_layout(
        audio_array: np.ndarray,
        target_channels: int,
    ) -> np.ndarray:
        """Convert interleaved/mono PCM without changing duration."""

        audio = np.asarray(audio_array, dtype=np.float32)
        target_channels = max(1, int(target_channels))
        source_channels = 1 if audio.ndim == 1 else int(audio.shape[1])
        if source_channels == target_channels:
            return audio
        if target_channels == 1:
            if audio.ndim == 1:
                return audio
            return np.mean(audio, axis=1, dtype=np.float32)
        if source_channels == 1:
            mono = audio if audio.ndim == 1 else audio[:, 0]
            return np.repeat(mono[:, np.newaxis], target_channels, axis=1)
        if target_channels < source_channels:
            return audio[:, :target_channels]

        repeats = int(math.ceil(target_channels / source_channels))
        return np.tile(audio, (1, repeats))[:, :target_channels]

    def _preferred_output_channels(
        self,
        device: OutputDeviceRef,
        source_channels: int,
    ) -> int:
        """Choose a broadly compatible channel count for a playback endpoint."""

        source_channels = max(1, int(source_channels))
        try:
            device_info = sd.query_devices(device, kind="output")
        except Exception as exc:
            logger.debug(
                "Failed to query output device channels (%s)",
                safe_exception_summary(exc),
            )
            return source_channels

        try:
            max_channels = int(device_info.get("max_output_channels", 0) or 0)
        except Exception:
            max_channels = 0
        if max_channels <= 0:
            return source_channels

        device_name = str(device_info.get("name", "") or "")
        if _is_virtual_output_device(device_name) and max_channels >= 2:
            # Qwen and several cloud engines return 24 kHz mono WAV. MixLine's
            # render endpoints are stereo/48 kHz devices; explicitly duplicate
            # mono into L/R so the virtual-microphone path receives both sides.
            return 2
        return max(1, min(source_channels, max_channels))

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
        completion_signal = getattr(self, "_playback_activity_event", None)
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
            if completion_signal is not None:
                completion_signal.set()

        def audio_callback(outdata, frames, time_info, status):
            metrics = getattr(self, "_active_playback_metrics", None)
            if isinstance(metrics, dict) and "first_audio_at" not in metrics:
                metrics["first_audio_at"] = time.monotonic()
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
                blocksize=TTS_PLAYBACK_BLOCKSIZE,
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
                        logger.warning(
                            "Fallback device %s failed (%s)",
                            alt_device_id,
                            safe_exception_summary(alt_exc),
                        )
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
                logger.debug(
                    "Error closing playback stream (%s)",
                    safe_exception_summary(exc),
                )

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

    def clear_queue(
        self,
        message: str = "TTS request was cancelled.",
        *,
        error_code: str | None = None,
    ) -> int:
        """Cancel queued/synthesized work without disturbing current playback."""

        cancelled: list[TTSRequest] = []
        saw_stop_signal = False
        retired_worker = False
        pipeline_lock = getattr(self, "_pipeline_lock", None)
        if pipeline_lock is None:
            pipeline_lock = threading.RLock()
            self._pipeline_lock = pipeline_lock
        with pipeline_lock:
            self._generation = int(getattr(self, "_generation", 0)) + 1
            self._next_playback_sequence = int(getattr(self, "_next_sequence", 0))
            self._prewarm_queued = False
            self._prewarm_completed_workers = set()
            self._prewarm_target_workers = 0
            while True:
                try:
                    pending = self._request_queue.get_nowait()
                except queue.Empty:
                    break
                if pending is None:
                    saw_stop_signal = True
                    continue
                if isinstance(pending, _TTSPrewarmRequest):
                    continue
                cancelled.append(pending)

            completed = getattr(self, "_completed_synthesis", {})
            cancelled.extend(result.request for result in completed.values())
            completed.clear()
            self._completed_synthesis = completed
            active = getattr(self, "_active_synthesis", {})
            cancelled.extend(
                request
                for request, _started_at, _worker in active.values()
            )
            for _request, _started_at, worker in active.values():
                retired_worker = self._retire_synthesis_worker(worker) or retired_worker
            active.clear()
            self._active_synthesis = active

            unique_cancelled: list[TTSRequest] = []
            seen: set[tuple[int, int]] = set()
            for request in cancelled:
                identity = (request.generation, request.sequence)
                if identity in seen:
                    continue
                seen.add(identity)
                unique_cancelled.append(request)
                self._finish_request_identity(request)
            cancelled = unique_cancelled

            condition = getattr(self, "_result_available", None)
            if condition is not None:
                condition.notify_all()

        if saw_stop_signal:
            try:
                self._request_queue.put_nowait(None)
            except queue.Full:
                pass
        callback_message = tts_error_token(
            error_code or tts_error_code(message)
        )
        for request in cancelled:
            self._notify_request_callback(request, False, callback_message)
        if cancelled:
            logger.info(
                "TTS queue cleared (cancelled=%d code=%s generation=%d)",
                len(cancelled),
                error_code or tts_error_code(message),
                int(getattr(self, "_generation", 0)),
            )
        if retired_worker:
            self._restore_synthesis_capacity()
        return len(cancelled)

    def _worker_loop(self) -> None:
        """Compatibility entry point for the first synthesis worker."""
        self._synthesis_worker_loop(0)

    def _synthesis_worker_loop(self, worker_index: int) -> None:
        current_worker = threading.current_thread()
        prewarm_generation = 0
        try:
            while True:
                prewarm_generation = self._prewarm_synthesis_worker(
                    worker_index,
                    prewarm_generation,
                )
                try:
                    request = self._request_queue.get(timeout=0.1)
                except queue.Empty:
                    if not self._running:
                        return
                    continue

                if request is None:
                    return

                if isinstance(request, _TTSPrewarmRequest):
                    continue

                prewarm_generation = self._prewarm_synthesis_worker(
                    worker_index,
                    prewarm_generation,
                )

                result = self._synthesize_request(request)
                if not self._publish_synthesis_result(result):
                    if self._finish_request_identity(request):
                        self._notify_request_callback(
                            request,
                            False,
                            tts_error_token("cancelled"),
                        )
                if self._is_synthesis_worker_retired(current_worker):
                    logger.info(
                        "Retired TTS synthesis worker exiting "
                        "(worker=%d sequence=%d generation=%d)",
                        worker_index,
                        request.sequence,
                        request.generation,
                    )
                    return
                logger.debug(
                    "TTS synthesis worker finished (worker=%d sequence=%d generation=%d)",
                    worker_index,
                    request.sequence,
                    request.generation,
                )
        finally:
            self._close_synthesis_thread_context()
            self._on_synthesis_worker_exit()

    def _on_synthesis_worker_exit(self) -> None:
        current = threading.current_thread()
        restore_capacity = False
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            with self._worker_state_lock:
                self._retired_synthesis_workers.discard(current)
            self._active_synthesis_workers = max(
                0,
                int(getattr(self, "_active_synthesis_workers", 0)) - 1,
            )
            self._synthesis_threads = [
                thread
                for thread in getattr(self, "_synthesis_threads", ())
                if thread is not current and thread.is_alive()
            ]
            with self._worker_state_lock:
                retired = set(self._retired_synthesis_workers)
            responsive = [
                thread
                for thread in self._synthesis_threads
                if thread is not current and thread not in retired
            ]
            self._worker_thread = responsive[0] if responsive else None
            if self._active_synthesis_workers == 0 and not self._running:
                self._discard_worker_stop_signals_locked()
            restore_capacity = self._running and not self._closed

        if restore_capacity:
            self._restore_synthesis_capacity()
        else:
            self._finalize_pending_engine_if_ready()

    def _close_synthesis_thread_context(self) -> None:
        cleanup = getattr(self._engine, "close_thread_context", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception as exc:
                logger.debug(
                    "Failed to close TTS worker context (%s)",
                    safe_exception_summary(exc),
                )

    def _synthesize_request(self, request: TTSRequest) -> _TTSSynthesisResult:
        started_at = time.monotonic()
        identity = (request.generation, request.sequence)
        with self._result_available:
            if (
                not self._running
                or request.generation != self._generation
                or identity not in self._outstanding_requests
            ):
                return _TTSSynthesisResult(
                    request=request,
                    error=RuntimeError("TTS request was cancelled before synthesis"),
                    synthesis_started_at=started_at,
                    synthesis_finished_at=time.monotonic(),
                )
            self._active_synthesis[identity] = (
                request,
                started_at,
                threading.current_thread(),
            )
            self._result_available.notify_all()

        consume_diagnostics = getattr(
            self._engine,
            "consume_last_synthesis_diagnostics",
            None,
        )
        if callable(consume_diagnostics):
            try:
                consume_diagnostics()
            except Exception as exc:
                logger.debug(
                    "Failed to clear stale TTS diagnostics (%s)",
                    safe_exception_summary(exc),
                )
        try:
            audio_data = self._get_audio(
                request.text,
                request.voice,
                request.rate,
                request.volume,
            )
            decoded_audio: np.ndarray | None = None
            decoded_sample_rate = 0
            decode_started_at = time.monotonic()
            try:
                decoded_audio, decoded_sample_rate = self._decode_audio_data(audio_data)
                decoded_audio = np.ascontiguousarray(decoded_audio, dtype=np.float32)
            except Exception as exc:
                # Keep malformed engine output as a playback-stage failure, as
                # before. Valid WAV/MP3 output is decoded here so this CPU work
                # overlaps playback of the preceding sentence.
                logger.debug(
                    "TTS worker could not predecode audio; deferring to playback (%s)",
                    safe_exception_summary(exc),
                )
            decode_finished_at = time.monotonic()
            engine_diagnostics = self._consume_engine_diagnostics()
            return _TTSSynthesisResult(
                request=request,
                audio_data=audio_data,
                decoded_audio=decoded_audio,
                decoded_sample_rate=int(decoded_sample_rate or 0),
                synthesis_started_at=started_at,
                synthesis_finished_at=time.monotonic(),
                decode_started_at=decode_started_at,
                decode_finished_at=decode_finished_at,
                engine_diagnostics=engine_diagnostics,
            )
        except Exception as exc:
            engine_diagnostics = self._consume_engine_diagnostics()
            return _TTSSynthesisResult(
                request=request,
                error=exc,
                synthesis_started_at=started_at,
                synthesis_finished_at=time.monotonic(),
                engine_diagnostics=engine_diagnostics,
            )

    def _consume_engine_diagnostics(self) -> dict[str, object]:
        consume = getattr(
            self._engine,
            "consume_last_synthesis_diagnostics",
            None,
        )
        if not callable(consume):
            return {}
        try:
            diagnostics = consume()
        except Exception as exc:
            logger.debug(
                "Failed to consume TTS diagnostics (%s)",
                safe_exception_summary(exc),
            )
            return {}
        return dict(diagnostics) if isinstance(diagnostics, Mapping) else {}

    def _publish_synthesis_result(self, result: _TTSSynthesisResult) -> bool:
        condition = self._result_available
        request = result.request
        with condition:
            self._active_synthesis.pop(
                (request.generation, request.sequence),
                None,
            )
            if (
                not self._running
                or request.generation != self._generation
                or (request.generation, request.sequence)
                not in self._outstanding_requests
            ):
                return False
            result.published_at = time.monotonic()
            self._completed_synthesis[request.sequence] = result
            condition.notify_all()
            return True

    def _playback_worker_loop(self) -> None:
        while True:
            with self._result_available:
                result = self._completed_synthesis.pop(
                    self._next_playback_sequence,
                    None,
                )
                while result is None:
                    if not self._running:
                        return
                    result = self._expire_stalled_head_locked(time.monotonic())
                    if result is not None:
                        break
                    self._result_available.wait(timeout=0.1)
                    result = self._completed_synthesis.pop(
                        self._next_playback_sequence,
                        None,
                    )
                self._next_playback_sequence += 1

            if bool(
                (result.engine_diagnostics or {}).get("manager_wall_timeout")
            ):
                self._restore_synthesis_capacity()

            request = result.request
            if request.generation != self._generation:
                if self._finish_request_identity(request):
                    self._notify_request_callback(
                        request,
                        False,
                        tts_error_token("cancelled"),
                    )
                continue

            queue_wait_ms = max(
                0.0,
                result.synthesis_started_at - request.submitted_at,
            ) * 1000.0
            synthesis_ms = max(
                0.0,
                result.synthesis_finished_at - result.synthesis_started_at,
            ) * 1000.0
            if result.error is None:
                logger.info(
                    "TTS ordered playback ready (sequence=%d queue_wait_ms=%.0f synthesis_ms=%.0f)",
                    request.sequence,
                    queue_wait_ms,
                    synthesis_ms,
                )
            else:
                logger.info(
                    "TTS ordered synthesis completed with error "
                    "(sequence=%d queue_wait_ms=%.0f synthesis_ms=%.0f)",
                    request.sequence,
                    queue_wait_ms,
                    synthesis_ms,
                )
            self._process_synthesis_result(result)
            self._finish_request_identity(request)

    def _expire_stalled_head_locked(
        self,
        now: float,
    ) -> _TTSSynthesisResult | None:
        sequence = int(getattr(self, "_next_playback_sequence", 0))
        generation = int(getattr(self, "_generation", 0))
        identity = (generation, sequence)
        active = getattr(self, "_active_synthesis", {})
        active_entry = active.get(identity)
        if active_entry is None:
            return None
        request, started_at, worker = active_entry
        timeout_s = float(
            getattr(
                self,
                "_synthesis_wall_timeout_seconds",
                TTS_DEFAULT_SYNTHESIS_WALL_TIMEOUT_SECONDS,
            )
        )
        elapsed_s = max(0.0, now - started_at)
        if elapsed_s < timeout_s:
            return None

        active.pop(identity, None)
        self._outstanding_requests.discard(identity)
        self._retire_synthesis_worker(worker)
        logger.error(
            "TTS ordered synthesis wall timeout "
            "(engine=%s sequence=%d generation=%d elapsed_ms=%.0f limit_ms=%.0f); "
            "advancing ordered playback",
            self._engine_name,
            sequence,
            generation,
            elapsed_s * 1000.0,
            timeout_s * 1000.0,
        )
        return _TTSSynthesisResult(
            request=request,
            error=TimeoutError(
                "TTS synthesis exceeded the configured wall-clock timeout"
            ),
            synthesis_started_at=started_at,
            synthesis_finished_at=now,
            published_at=now,
            engine_diagnostics={
                "total_s": elapsed_s,
                "succeeded": False,
                "manager_wall_timeout": True,
                "dns_s": None,
                "tcp_s": None,
                "tls_s": None,
            },
        )

    def _process_synthesis_result(self, result: _TTSSynthesisResult) -> None:
        request = result.request
        ordered_processing_started_at = time.monotonic()
        playback_diagnostics: dict[str, object] = {}
        if result.error is not None:
            message = str(result.error)
            self._record_request_failure(message, stage="synthesis")
            code = tts_error_code(message)
            logger.error(
                "TTS synthesis failed (code=%s %s)",
                code,
                safe_exception_summary(result.error),
            )
            self._finish_request_identity(request)
            callback_started_at = time.monotonic()
            self._notify_request_callback(
                request,
                False,
                tts_error_token(code),
            )
            callback_finished_at = time.monotonic()
            self._log_request_latency(
                result,
                outcome=code,
                ordered_processing_started_at=ordered_processing_started_at,
                playback_diagnostics=playback_diagnostics,
                callback_started_at=callback_started_at,
                callback_finished_at=callback_finished_at,
            )
            return
        try:
            playback_context = getattr(self, "_playback_context", None)
            if playback_context is None:
                playback_context = threading.local()
                self._playback_context = playback_context
            playback_context.decoded_audio = (
                result.decoded_audio,
                result.decoded_sample_rate,
            )
            try:
                # Keep the one-argument playback contract used by plugins and
                # tests. The default implementation reads worker-decoded PCM
                # from this playback thread's local context.
                self._play_audio(result.audio_data)
            finally:
                playback_diagnostics = self._consume_playback_diagnostics()
                try:
                    del playback_context.decoded_audio
                except AttributeError:
                    pass
            if request.generation != self._generation:
                self._finish_request_identity(request)
                callback_started_at = time.monotonic()
                self._notify_request_callback(
                    request,
                    False,
                    tts_error_token("cancelled"),
                )
                callback_finished_at = time.monotonic()
                self._log_request_latency(
                    result,
                    outcome="cancelled",
                    ordered_processing_started_at=ordered_processing_started_at,
                    playback_diagnostics=playback_diagnostics,
                    callback_started_at=callback_started_at,
                    callback_finished_at=callback_finished_at,
                )
                return
            self._consecutive_failures = 0
            self._last_failure_message = ""
            self._last_failure_stage = ""
            self._suspended_until = 0.0
            self._finish_request_identity(request)
            callback_started_at = time.monotonic()
            self._notify_request_callback(request, True, "")
            callback_finished_at = time.monotonic()
            self._log_request_latency(
                result,
                outcome="success",
                ordered_processing_started_at=ordered_processing_started_at,
                playback_diagnostics=playback_diagnostics,
                callback_started_at=callback_started_at,
                callback_finished_at=callback_finished_at,
            )
        except _TTSPlaybackInterrupted as exc:
            logger.info(
                "TTS playback cancelled (%s)",
                safe_exception_summary(exc),
            )
            if not playback_diagnostics:
                playback_diagnostics = self._consume_playback_diagnostics()
            self._finish_request_identity(request)
            callback_started_at = time.monotonic()
            self._notify_request_callback(
                request,
                False,
                tts_error_token("cancelled"),
            )
            callback_finished_at = time.monotonic()
            self._log_request_latency(
                result,
                outcome="cancelled",
                ordered_processing_started_at=ordered_processing_started_at,
                playback_diagnostics=playback_diagnostics,
                callback_started_at=callback_started_at,
                callback_finished_at=callback_finished_at,
            )
        except Exception as exc:
            message = str(exc)
            self._record_request_failure(message, stage="playback")
            logger.error(
                "TTS playback failed (%s)",
                safe_exception_summary(exc),
            )
            if not playback_diagnostics:
                playback_diagnostics = self._consume_playback_diagnostics()
            self._finish_request_identity(request)
            callback_started_at = time.monotonic()
            self._notify_request_callback(
                request,
                False,
                tts_error_token("playback"),
            )
            callback_finished_at = time.monotonic()
            self._log_request_latency(
                result,
                outcome="playback",
                ordered_processing_started_at=ordered_processing_started_at,
                playback_diagnostics=playback_diagnostics,
                callback_started_at=callback_started_at,
                callback_finished_at=callback_finished_at,
            )

    def _consume_playback_diagnostics(self) -> dict[str, object]:
        playback_context = getattr(self, "_playback_context", None)
        diagnostics = (
            getattr(playback_context, "last_playback_diagnostics", None)
            if playback_context is not None
            else None
        )
        if playback_context is not None:
            try:
                del playback_context.last_playback_diagnostics
            except AttributeError:
                pass
        return dict(diagnostics) if isinstance(diagnostics, Mapping) else {}

    @staticmethod
    def _diagnostic_ms(value: object) -> str:
        if value is None:
            return "na"
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return "na"
        if not math.isfinite(seconds) or seconds < 0.0:
            return "na"
        return f"{seconds * 1000.0:.1f}"

    def _log_request_latency(
        self,
        result: _TTSSynthesisResult,
        *,
        outcome: str,
        ordered_processing_started_at: float,
        playback_diagnostics: Mapping[str, object],
        callback_started_at: float,
        callback_finished_at: float,
    ) -> None:
        request = result.request
        diagnostics = dict(result.engine_diagnostics or {})
        context = dict(request.context or {})

        decode_s = max(
            0.0,
            result.decode_finished_at - result.decode_started_at,
        ) if result.decode_started_at > 0.0 else 0.0
        try:
            engine_parse_s = max(
                0.0,
                float(diagnostics.get("parsing_postprocess_s", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            engine_parse_s = 0.0
        parsing_s = engine_parse_s + decode_s
        local_queue_s = max(
            0.0,
            result.synthesis_started_at - request.submitted_at,
        )
        reorder_s = max(
            0.0,
            ordered_processing_started_at - result.synthesis_finished_at,
        )
        playback_started_at = playback_diagnostics.get("started_at")
        try:
            playback_started = float(playback_started_at)
        except (TypeError, ValueError):
            playback_started = callback_started_at
        tts_wait_s = max(0.0, playback_started - request.submitted_at)
        first_playback_at = playback_diagnostics.get("first_audio_at")
        try:
            first_playback_s: float | None = max(
                0.0,
                float(first_playback_at) - request.submitted_at,
            )
        except (TypeError, ValueError):
            first_playback_s = None
        total_s = max(
            0.0,
            callback_finished_at
            - (request.admission_started_at or request.submitted_at),
        )
        upstream_started_at = context.get("upstream_started_at")
        try:
            upstream_total_s: float | None = max(
                0.0,
                callback_finished_at - float(upstream_started_at),
            )
        except (TypeError, ValueError):
            upstream_total_s = None

        source = str(context.get("source", "") or "-")[:64]
        session_id = str(context.get("session_id", "") or "-")[:64]
        upstream_sequence = context.get("sequence", "-")
        logger.info(
            "TTS latency engine=%s sequence=%d generation=%d source=%s "
            "session_id=%s upstream_sequence=%s outcome=%s queue_depth=%d "
            "queue_high_watermark=%d admission_ms=%s local_queue_ms=%s "
            "preprocessing_ms=%s connection_pool_wait_ms=%s dns_ms=%s tcp_ms=%s "
            "tls_ms=%s response_header_ms=%s provider_ms=%s provider_estimated=%s "
            "streaming_first_audio_ms=%s full_response_ms=%s audio_download_ms=%s "
            "parsing_postprocess_ms=%s reorder_wait_ms=%s ui_delivery_ms=%s "
            "device_prepare_ms=%s playback_first_audio_ms=%s playback_ms=%s "
            "callback_delivery_ms=%s osc_wait_ms=%s tts_wait_ms=%s "
            "total_wall_ms=%s upstream_total_ms=%s api_session_reused=%s "
            "audio_session_reused=%s api_attempts=%s audio_download_attempts=%s",
            self._engine_name,
            request.sequence,
            request.generation,
            source,
            session_id,
            upstream_sequence,
            outcome,
            request.queue_depth_at_admission,
            int(getattr(self, "_request_queue_high_watermark", 0)),
            self._diagnostic_ms(request.submitted_at - request.admission_started_at),
            self._diagnostic_ms(local_queue_s),
            self._diagnostic_ms(diagnostics.get("preprocessing_s")),
            self._diagnostic_ms(diagnostics.get("connection_pool_wait_s")),
            self._diagnostic_ms(diagnostics.get("dns_s")),
            self._diagnostic_ms(diagnostics.get("tcp_s")),
            self._diagnostic_ms(diagnostics.get("tls_s")),
            self._diagnostic_ms(diagnostics.get("api_response_header_s")),
            self._diagnostic_ms(diagnostics.get("api_provider_s")),
            diagnostics.get("api_provider_estimated", "na"),
            self._diagnostic_ms(diagnostics.get("first_audio_s")),
            self._diagnostic_ms(diagnostics.get("full_response_s")),
            self._diagnostic_ms(diagnostics.get("audio_download_full_s")),
            self._diagnostic_ms(parsing_s),
            self._diagnostic_ms(reorder_s),
            self._diagnostic_ms(context.get("ui_delivery_s")),
            self._diagnostic_ms(playback_diagnostics.get("device_prepare_s")),
            self._diagnostic_ms(first_playback_s),
            self._diagnostic_ms(playback_diagnostics.get("total_s")),
            self._diagnostic_ms(callback_finished_at - callback_started_at),
            self._diagnostic_ms(context.get("osc_wait_s")),
            self._diagnostic_ms(tts_wait_s),
            self._diagnostic_ms(total_s),
            self._diagnostic_ms(upstream_total_s),
            diagnostics.get("api_session_reused", "na"),
            diagnostics.get("audio_download_session_reused", "na"),
            diagnostics.get("api_attempts", "na"),
            diagnostics.get("audio_download_attempts", "na"),
        )

    def _finish_request_identity(self, request: TTSRequest) -> bool:
        pipeline_lock = getattr(self, "_pipeline_lock", None)
        if pipeline_lock is None:
            return False
        with pipeline_lock:
            outstanding = getattr(self, "_outstanding_requests", None)
            if outstanding is not None:
                identity = (request.generation, request.sequence)
                existed = identity in outstanding
                outstanding.discard(identity)
                active = getattr(self, "_active_synthesis", None)
                if active is not None:
                    active.pop(identity, None)
                return existed
        return False

    @staticmethod
    def _notify_request_callback(
        request: TTSRequest,
        success: bool,
        message: str,
    ) -> None:
        if request.callback is None:
            return
        try:
            request.callback(bool(success), str(message or ""))
        except Exception as exc:
            logger.error(
                "TTS callback error (%s)",
                safe_exception_summary(exc),
            )

    def _process_request(self, request: TTSRequest) -> None:
        """Process TTS request."""
        try:
            audio_data = self._get_audio(
                request.text,
                request.voice,
                request.rate,
                request.volume,
            )
        except Exception as exc:
            message = str(exc)
            self._record_request_failure(message, stage="synthesis")
            code = tts_error_code(message)
            logger.error(
                "TTS synthesis failed (code=%s %s)",
                code,
                safe_exception_summary(exc),
            )
            self._notify_request_callback(
                request,
                False,
                tts_error_token(code),
            )
            return

        try:
            self._play_audio(audio_data)

            self._consecutive_failures = 0
            self._last_failure_message = ""
            self._last_failure_stage = ""
            self._suspended_until = 0.0
            self._notify_request_callback(request, True, "")

        except Exception as exc:
            message = str(exc)
            self._record_request_failure(message, stage="playback")
            logger.error(
                "TTS playback failed (%s)",
                safe_exception_summary(exc),
            )
            self._notify_request_callback(
                request,
                False,
                tts_error_token("playback"),
            )

    @staticmethod
    def _suspension_message(stage: str, remaining_seconds: float) -> str:
        seconds = int(max(1, remaining_seconds))
        normalized_stage = str(stage or "").strip().lower()
        if normalized_stage == "synthesis":
            subject = "TTS synthesis"
        elif normalized_stage == "playback":
            subject = "TTS audio playback"
        else:
            subject = "TTS request processing"
        return (
            f"{subject} is temporarily paused after repeated failures. "
            f"Please try again in {seconds} seconds."
        )

    def _record_request_failure(self, message: str, *, stage: str = "request") -> None:
        clean_message = str(message or "").strip() or "Unknown TTS error"
        clean_stage = str(stage or "request").strip().lower() or "request"
        if (
            clean_message == self._last_failure_message
            and clean_stage == getattr(self, "_last_failure_stage", "")
        ):
            self._consecutive_failures += 1
        else:
            self._last_failure_message = clean_message
            self._last_failure_stage = clean_stage
            self._consecutive_failures = 1

        if self._consecutive_failures < TTS_FAILURE_SUSPEND_THRESHOLD:
            return

        self._suspended_until = time.monotonic() + TTS_FAILURE_SUSPEND_SECONDS
        stage_label = {
            "synthesis": "synthesis",
            "playback": "audio playback",
        }.get(clean_stage, "request processing")
        dropped = self._drop_pending_requests(
            f"TTS {stage_label} was paused after repeated failures.",
            error_code="suspended",
        )
        logger.warning(
            "TTS %s paused for %.0f seconds after %d repeated failures "
            "(dropped %d queued request(s) code=%s)",
            stage_label,
            TTS_FAILURE_SUSPEND_SECONDS,
            self._consecutive_failures,
            dropped,
            tts_error_code(clean_message),
        )

    def _drop_pending_requests(
        self,
        message: str,
        *,
        error_code: str | None = None,
    ) -> int:
        return self.clear_queue(message=message, error_code=error_code)

    def _get_audio(
        self,
        text: str,
        voice: str,
        rate: float,
        volume: float,
    ) -> bytes:
        """Get audio data (from cache or synthesize)."""
        # Generate cache key
        cache_key = self._generate_cache_key(
            text,
            voice,
            rate,
            volume,
        )

        cache_owner = False
        cache_event: threading.Event | None = None
        coalesce_wait_started_at = time.monotonic()
        # Try cache first and coalesce duplicate concurrent synthesis requests.
        if self._cache_enabled:
            while True:
                if self._closed:
                    raise RuntimeError("TTS manager stopped during synthesis")
                with self._cache_lock:
                    now = time.monotonic()
                    expired = [
                        key
                        for key, (_data, timestamp) in self._cache.items()
                        if now - timestamp > CACHE_TTL_SECONDS
                    ]
                    for key in expired:
                        data = self._cache.pop(key)
                        self._cache_size_bytes -= len(data[0])

                    cached = self._cache.get(cache_key)
                    if cached is not None:
                        logger.debug("TTS cache hit")
                        self._cache.move_to_end(cache_key)
                        return cached[0]

                    cache_event = self._cache_inflight.get(cache_key)
                    if cache_event is None:
                        cache_event = threading.Event()
                        self._cache_inflight[cache_key] = cache_event
                        cache_owner = True
                        break
                remaining = (
                    TTS_DUPLICATE_COALESCE_WAIT_SECONDS
                    - (time.monotonic() - coalesce_wait_started_at)
                )
                if remaining <= 0.0:
                    logger.warning(
                        "Duplicate TTS synthesis coalescing wait expired after %.1fs; "
                        "continuing independently",
                        TTS_DUPLICATE_COALESCE_WAIT_SECONDS,
                    )
                    break
                cache_event.wait(timeout=min(0.25, remaining))

        try:
            if self._engine is None:
                raise RuntimeError("TTS engine not available")
            logger.info(
                "TTS synthesis stage started (engine=%s voice=%s text_chars=%d)",
                self._engine_name,
                voice,
                len(text),
            )
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
            logger.info(
                "TTS synthesis stage finished (engine=%s output_size=%d bytes)",
                self._engine_name,
                len(audio_data),
            )

            if self._cache_enabled:
                self._add_to_cache(cache_key, audio_data)
            return audio_data
        finally:
            if self._cache_enabled and cache_owner and cache_event is not None:
                with self._cache_lock:
                    self._cache_inflight.pop(cache_key, None)
                    cache_event.set()

    def _generate_cache_key(
        self,
        text: str,
        voice: str,
        rate: float,
        volume: float,
    ) -> str:
        """Generate cache key."""
        key_str = (
            f"{text}|{voice}|{rate:.2f}|{volume:.2f}|"
            f"{self._engine_name}|{self._device}|{self._bert_language}|"
            f"{self._engine_cache_signature()}"
        )
        return hashlib.md5(
            key_str.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

    def _engine_cache_signature(self) -> str:
        def scrub(value: object) -> object:
            if isinstance(value, dict):
                return {
                    str(key): scrub(item)
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                    if str(key).lower() not in {"api_key", "secret", "token", "password"}
                }
            if isinstance(value, (list, tuple)):
                return [scrub(item) for item in value]
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            return str(value)

        try:
            return json.dumps(scrub(self._engine_config), sort_keys=True, separators=(",", ":"))
        except Exception:
            return repr(self._engine_config)

    def _add_to_cache(self, key: str, data: bytes) -> None:
        """Add audio data to cache."""
        with self._cache_lock:
            if self._closed:
                return
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
            default_id = inventory_default_output_device_index(
                force_refresh=False,
                sounddevice_module=sd,
            )
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
        playback_started_at = time.monotonic()
        playback_metrics: dict[str, object] = {
            "started_at": playback_started_at,
        }
        self._active_playback_metrics = playback_metrics
        with self._lifecycle_lock:
            playback_thread = getattr(self, "_playback_thread", None)
            if self._closed or (
                not self._running
                and playback_thread is not None
            ):
                self._active_playback_metrics = None
                raise _TTSPlaybackInterrupted(
                    "TTS playback was cancelled because the manager is stopping"
                )
            with self._playback_lock:
                playback_epoch = int(getattr(self, "_playback_stop_epoch", 0))
        route_started_at = time.monotonic()
        try:
            playback_device, playback_name = self._resolve_playback_device()
            monitor_device = self._resolve_monitor_playback_device(
                playback_device,
                playback_name,
            )
        except Exception:
            playback_metrics["device_route_s"] = max(
                0.0,
                time.monotonic() - route_started_at,
            )
            playback_metrics["total_s"] = max(
                0.0,
                time.monotonic() - playback_started_at,
            )
            playback_context = getattr(self, "_playback_context", None)
            if playback_context is not None:
                playback_context.last_playback_diagnostics = dict(playback_metrics)
            if getattr(self, "_active_playback_metrics", None) is playback_metrics:
                self._active_playback_metrics = None
            raise
        playback_metrics["device_route_s"] = max(
            0.0,
            time.monotonic() - route_started_at,
        )
        monitor_output = monitor_device is not None
        logger.info(
            "Starting audio playback (configured_device=%s, resolved_device=%s, resolved_name=%s, monitor_output=%s, data_size=%d bytes)",
            self._output_device,
            playback_device,
            playback_name or "default",
            monitor_output,
            len(audio_data),
        )

        # Ordered playback is serialized, so the previous stream has already
        # been closed and no fixed inter-sentence delay is necessary.
        stream_records: list[
            tuple[sd.OutputStream, threading.Event, str, bool, OutputDeviceRef]
        ] = []
        owned_playbacks: list[sd.OutputStream] = []
        registered_playback = False
        activity_event = threading.Event()
        self._playback_activity_event = activity_event

        try:
            decode_started_at = time.monotonic()
            predecoded = getattr(
                getattr(self, "_playback_context", None),
                "decoded_audio",
                None,
            )
            if (
                isinstance(predecoded, tuple)
                and len(predecoded) == 2
                and isinstance(predecoded[0], np.ndarray)
                and int(predecoded[1] or 0) > 0
            ):
                audio_array = predecoded[0]
                sample_rate = int(predecoded[1])
            else:
                audio_array, sample_rate = self._decode_audio_data(audio_data)
            playback_metrics["decode_s"] = max(
                0.0,
                time.monotonic() - decode_started_at,
            )
            logger.info(
                "Decoded TTS playback audio (%s, shape=%s, dtype=%s)",
                _audio_stats(audio_array, sample_rate),
                audio_array.shape,
                audio_array.dtype,
            )
            self._maybe_save_debug_audio(audio_data, "playback")
            audio_array = _append_tail_silence(audio_array, sample_rate)
            playback_metrics["audio_duration_s"] = (
                float(audio_array.shape[0]) / float(sample_rate)
                if sample_rate > 0 and audio_array.size > 0
                else 0.0
            )
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
                    owned_playbacks.append(playback)
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
                            "Host API error (WDM-KS may not support this operation) (%s)",
                            safe_exception_summary(exc),
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
                        owned_playbacks.append(fallback_playback)
                        return fallback_playback, fallback_done, "fallback-default", True, None
                    if required:
                        raise
                    logger.warning(
                        "TTS monitor output skipped (%s)",
                        safe_exception_summary(exc),
                    )
                    return None
                except Exception as exc:
                    if required:
                        raise
                    logger.warning(
                        "TTS monitor output skipped (%s)",
                        safe_exception_summary(exc),
                    )
                    return None

            device_prepare_started_at = time.monotonic()
            primary_record = build_stream(playback_device, playback_name or "default", True)
            if primary_record is not None:
                stream_records.append(primary_record)

            if monitor_device is not None and primary_record is not None:
                monitor_id, monitor_name = monitor_device
                monitor_record = build_stream(monitor_id, f"monitor-{monitor_name}", False)
                if monitor_record is not None:
                    stream_records.append(monitor_record)

            playback_metrics["device_prepare_s"] = max(
                0.0,
                time.monotonic() - device_prepare_started_at,
            )

            if not stream_records:
                raise RuntimeError("Audio playback failed: no output stream")

            self._register_preparing_playbacks(
                [record[0] for record in stream_records],
                playback_epoch,
            )

            started_records: list[
                tuple[sd.OutputStream, threading.Event, str, bool, OutputDeviceRef]
            ] = []
            stream_start_started_at = time.monotonic()
            for playback, done_event, label, required, target_device in stream_records:
                try:
                    self._start_preparing_playback(playback, playback_epoch)
                    started_records.append(
                        (playback, done_event, label, required, target_device)
                    )
                    logger.debug("Audio stream started (target=%s)", label)
                except sd.PortAudioError as exc:
                    self._unregister_preparing_playbacks([playback])
                    self._close_playbacks([playback])
                    error_code = _portaudio_error_code(exc)
                    if (
                        required
                        and error_code in _RECOVERABLE_MIXLINE_PORTAUDIO_ERRORS
                        and target_device is not None
                    ):
                        logger.warning(
                            "Recoverable host API error while starting output stream (%s)",
                            safe_exception_summary(exc),
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
                                owned_playbacks.append(alt_playback)
                                self._register_preparing_playbacks(
                                    [alt_playback],
                                    playback_epoch,
                                )
                                self._start_preparing_playback(
                                    alt_playback,
                                    playback_epoch,
                                )
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
                            except _TTSPlaybackInterrupted:
                                if alt_playback is not None:
                                    self._unregister_preparing_playbacks([alt_playback])
                                    self._close_playbacks([alt_playback])
                                raise
                            except Exception as alt_exc:
                                if alt_playback is not None:
                                    self._unregister_preparing_playbacks([alt_playback])
                                    self._close_playbacks([alt_playback])
                                logger.warning(
                                    "Fallback device %s failed (%s)",
                                    alt_device_id,
                                    safe_exception_summary(alt_exc),
                                )
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
                                self._register_preparing_playbacks(
                                    [fallback_playback],
                                    playback_epoch,
                                )
                                self._start_preparing_playback(
                                    fallback_playback,
                                    playback_epoch,
                                )
                            except Exception:
                                self._unregister_preparing_playbacks(
                                    [fallback_playback]
                                )
                                self._close_playbacks([fallback_playback])
                                raise
                            started_records.append(fallback_record)
                        continue
                    if required:
                        raise
                    logger.warning(
                        "TTS monitor output skipped while starting (%s)",
                        safe_exception_summary(exc),
                    )
                except Exception as exc:
                    self._unregister_preparing_playbacks([playback])
                    self._close_playbacks([playback])
                    if required:
                        raise
                    logger.warning(
                        "TTS monitor output skipped while starting (%s)",
                        safe_exception_summary(exc),
                    )

            stream_records = started_records
            playback_metrics["stream_start_s"] = max(
                0.0,
                time.monotonic() - stream_start_started_at,
            )
            if not stream_records:
                raise RuntimeError("Audio playback failed: no output stream started")

            playbacks = [record[0] for record in stream_records]
            with self._playback_lock:
                if playback_epoch != self._playback_stop_epoch:
                    raise _TTSPlaybackInterrupted(
                        "TTS playback was stopped while streams were starting"
                    )
                self._preparing_playbacks = [
                    preparing
                    for preparing in self._preparing_playbacks
                    if all(preparing is not playback for playback in playbacks)
                ]
                self._current_playbacks = playbacks
                self._current_playback = playbacks[0]
                self._current_playback_done = activity_event
                registered_playback = True

            logger.debug("Waiting for playback to complete...")
            audio_duration_s = float(
                playback_metrics.get("audio_duration_s", 0.0) or 0.0
            )
            playback_timeout_s = _playback_timeout_seconds(audio_duration_s)
            playback_metrics["timeout_s"] = playback_timeout_s
            deadline = time.monotonic() + playback_timeout_s
            while True:
                activity_event.clear()
                with self._playback_lock:
                    current_playbacks = list(
                        getattr(self, "_current_playbacks", [])
                    )
                    still_registered = any(
                        current is playback
                        for current in current_playbacks
                        for playback in playbacks
                    )
                if not still_registered:
                    logger.debug("Playback interrupted")
                    raise _TTSPlaybackInterrupted(
                        "TTS playback was stopped before completion"
                    )
                if all(record[1].is_set() for record in stream_records):
                    logger.debug("Playback finished successfully")
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    playback_metrics["timed_out"] = True
                    logger.warning(
                        "Audio playback timeout (audio_duration_s=%.2f limit_s=%.2f)",
                        audio_duration_s,
                        playback_timeout_s,
                    )
                    raise RuntimeError(
                        "TTS audio playback exceeded the wall-clock timeout"
                    )
                activity_event.wait(timeout=remaining)

        except _TTSPlaybackInterrupted:
            logger.info("TTS audio playback interrupted before/during stream start")
            raise
        except sd.PortAudioError as exc:
            logger.error(
                "PortAudio error during playback (%s)",
                safe_exception_summary(exc),
            )
            raise RuntimeError(f"Audio playback failed: {exc}")
        except Exception as exc:
            logger.error(
                "Audio playback failed (%s)",
                safe_exception_summary(exc),
            )
            raise
        finally:
            playbacks = list(owned_playbacks)
            self._unregister_preparing_playbacks(playbacks)
            if registered_playback:
                playbacks = self._release_current_playbacks(playbacks)
            self._close_playbacks(playbacks)
            if getattr(self, "_playback_activity_event", None) is activity_event:
                self._playback_activity_event = None
            playback_metrics["total_s"] = max(
                0.0,
                time.monotonic() - playback_started_at,
            )
            playback_context = getattr(self, "_playback_context", None)
            if playback_context is not None:
                playback_context.last_playback_diagnostics = dict(playback_metrics)
            if getattr(self, "_active_playback_metrics", None) is playback_metrics:
                self._active_playback_metrics = None

    def _probe_supported_sample_rates(
        self,
        device: OutputDeviceRef,
        channels: int = 1,
    ) -> list[int]:
        """Probe which sample rates the device supports.

        Args:
            device: Device ID or None for default device.

        Returns:
            List of supported sample rates in Hz.
        """
        channels = max(1, int(channels))
        cache_key = (device, channels)

        # Check cache first. Channel count is part of the PortAudio contract;
        # a rate accepted for mono is not necessarily accepted for stereo.
        with self._device_cache_lock:
            if cache_key in self._device_sample_rates_cache:
                return self._device_sample_rates_cache[cache_key]

        common_rates = [8000, 11025, 16000, 22050, 24000, 44100, 48000, 96000]
        supported = []

        for rate in common_rates:
            try:
                sd.check_output_settings(
                    device=device,
                    samplerate=rate,
                    channels=channels,
                )
                supported.append(rate)
            except Exception as exc:
                logger.debug(
                    "Output device sample rate probe failed "
                    "(device=%s rate=%s %s)",
                    device,
                    rate,
                    safe_exception_summary(exc),
                )

        # If probing is unavailable, use the device's advertised default rate.
        if not supported:
            fallback_rate = self._target_sample_rate(device, 48000)
            logger.warning(
                "Could not probe device sample rates for %d channel(s), assuming %d Hz",
                channels,
                fallback_rate,
            )
            supported = [fallback_rate]

        logger.debug(
            "Device supports sample rates (channels=%d): %s",
            channels,
            supported,
        )

        # Update cache
        with self._device_cache_lock:
            self._device_sample_rates_cache[cache_key] = supported

        return supported

    def _find_alternative_devices(self, failed_device: OutputDeviceRef) -> list[int]:
        """Find alternative MIXLINE devices when the primary device fails.

        Args:
            failed_device: The device ID that failed.

        Returns:
            List of alternative device IDs, sorted by priority.
        """
        devices = _iter_raw_output_devices()
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
        return decode_wav_bytes(data)

    @staticmethod
    def _decode_mp3(data: bytes) -> tuple[np.ndarray, int]:
        """Decode compressed/container audio data using PyAV."""
        try:
            import av
        except ImportError:
            raise RuntimeError(
                "PyAV is required for compressed TTS playback. Install with: pip install av"
            )

        # PyAV detects MP3, AAC, Ogg, FLAC, WebM, and MP4 from the byte stream.
        container = av.open(io.BytesIO(data))
        try:
            if not container.streams.audio:
                raise RuntimeError("Compressed TTS response contains no audio stream")
            audio_stream = container.streams.audio[0]

            audio_frames = [
                frame.to_ndarray()
                for frame in container.decode(audio_stream)
            ]
            if not audio_frames:
                raise RuntimeError("No audio data decoded from TTS response")

            audio_data = np.concatenate(audio_frames, axis=1)
            if audio_data.shape[0] == 1:
                audio_data = audio_data[0]
            else:
                audio_data = audio_data.T

            if np.issubdtype(audio_data.dtype, np.integer):
                limits = np.iinfo(audio_data.dtype)
                scale = float(max(abs(limits.min), abs(limits.max)))
                audio_data = audio_data.astype(np.float32) / scale
            else:
                audio_data = audio_data.astype(np.float32, copy=False)

            return audio_data, int(audio_stream.rate)
        finally:
            container.close()

    @staticmethod
    def _maybe_save_debug_audio(audio_data: bytes, label: str) -> None:
        if os.environ.get(TTS_DEBUG_AUDIO_ENV) != "1":
            return
        try:
            debug_dir = require_real_directory(
                app_temp_dir() / "tts_debug_audio"
            )
            safe_label = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "_",
                str(label or "audio"),
            ).strip("._-")
            safe_label = (safe_label[:48] or "audio").rstrip(" .")
            suffix = "wav" if audio_data.startswith(b"RIFF") else "audio"
            path = debug_dir / (
                f"{time.time_ns()}-{safe_label}-{secrets.token_hex(6)}.{suffix}"
            )
            atomic_write_bytes(path, audio_data, overwrite=False)
            logger.info("Saved debug TTS audio: %s", path)
        except Exception as exc:
            logger.warning(
                "Failed to save debug TTS audio (%s)",
                safe_exception_summary(exc),
            )

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
        follows_system_default = (
            self._output_device is None
            and not str(self._output_device_name or "").strip()
            and not self._prefer_virtual_output
        )
        resolved = resolve_output_device(
            self._output_device,
            self._output_device_name,
            prefer_virtual=self._prefer_virtual_output,
        )
        if resolved is None:
            return None, ""
        device_id, device_name = resolved
        if (
            not follows_system_default
            and (device_id != self._output_device or device_name != self._output_device_name)
        ):
            self._update_device_config(device_id, device_name)
        return device_id, device_name

    @staticmethod
    def _target_sample_rate(device: OutputDeviceRef, fallback_rate: int) -> int:
        """Return the output device's preferred sample rate when available."""
        try:
            device_info = sd.query_devices(device, kind="output")
        except Exception as exc:
            logger.debug(
                "Failed to query output device sample rate (%s)",
                safe_exception_summary(exc),
            )
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

        audio_float = np.ascontiguousarray(audio_array, dtype=np.float32)
        try:
            import soxr

            resampled = soxr.resample(
                audio_float,
                source_rate,
                target_rate,
                quality="HQ",
            )
        except Exception as soxr_exc:
            global _SOXR_RESAMPLE_FALLBACK_LOGGED
            if not _SOXR_RESAMPLE_FALLBACK_LOGGED:
                logger.warning(
                    "soxr resample unavailable; using scipy fallback (%s)",
                    safe_exception_summary(soxr_exc),
                )
                _SOXR_RESAMPLE_FALLBACK_LOGGED = True
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
                        "scipy resample failed, falling back to numpy interpolation (%s)",
                        safe_exception_summary(exc),
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
            (
                _find_output_device_by_id(configured_device_id, devices)
                or _find_raw_output_device_by_id(configured_device_id)
            )
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
        matched_by_id = (
            _find_output_device_by_id(device_id, devices)
            or _find_raw_output_device_by_id(device_id)
        )
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
    if not prefer_virtual:
        try:
            default_id = inventory_default_output_device_index(
                force_refresh=False,
                sounddevice_module=sd,
            )
        except Exception:
            default_id = None
        if default_id is not None:
            matched_default = _find_output_device_by_id(default_id, devices)
            if matched_default is not None:
                resolved_id, resolved_name, _hostapi_name = matched_default
                logger.info(
                    "No specific device matched; using resolved default output %s (%s)",
                    resolved_id,
                    resolved_name,
                )
                return resolved_id, resolved_name
    logger.info("No specific device matched, using PortAudio default output device")
    return None


def _iter_output_devices() -> list[tuple[int, str, str]]:
    try:
        snapshot = get_device_inventory(
            force_refresh=False,
            sounddevice_module=sd,
        )
        return [
            (endpoint.index, endpoint.name, endpoint.hostapi)
            for endpoint in snapshot.outputs
        ]
    except Exception as exc:
        logger.error(
            "Failed to list audio devices (%s)",
            safe_exception_summary(exc),
        )
        return []


def _iter_raw_output_devices() -> list[tuple[int, str, str]]:
    """Return backend-specific aliases for playback failover, not UI display."""

    try:
        raw_devices = list(sd.query_devices())
        try:
            hostapis = list(sd.query_hostapis())
        except Exception:
            hostapis = []
        result: list[tuple[int, str, str]] = []
        for index, device in enumerate(raw_devices):
            try:
                if int(device.get("max_output_channels", 0) or 0) <= 0:
                    continue
                name = str(device.get("name", "") or "").strip()
                normalized = _normalize_device_name(name)
                if not normalized or re.search(r"\(\s*\)\s*$", normalized):
                    continue
                if normalized in {
                    "microsoft sound mapper - output",
                    "primary sound driver",
                }:
                    continue
                hostapi_name = ""
                hostapi_index = int(device.get("hostapi", -1))
                if 0 <= hostapi_index < len(hostapis):
                    hostapi_name = str(hostapis[hostapi_index].get("name", "") or "")
                result.append((index, name, hostapi_name))
            except Exception as exc:
                logger.debug(
                    "Skipping malformed raw output endpoint (%s)",
                    safe_exception_summary(exc),
                )
        return result
    except Exception as exc:
        logger.debug(
            "Failed to enumerate raw output aliases (%s)",
            safe_exception_summary(exc),
        )
        return _iter_output_devices()


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


def _find_raw_output_device_by_id(
    device_id: int,
) -> tuple[int, str, str] | None:
    """Validate an explicitly saved backend endpoint omitted by UI deduplication."""

    try:
        try:
            device = sd.query_devices(int(device_id))
        except TypeError:
            device = list(sd.query_devices())[int(device_id)]
        if int(device.get("max_output_channels", 0) or 0) <= 0:
            return None
        name = str(device.get("name", "") or "").strip()
        if not name or re.search(r"\(\s*\)\s*$", name):
            return None
        hostapi_name = ""
        try:
            hostapis = sd.query_hostapis()
            hostapi_index = int(device.get("hostapi", -1))
            if 0 <= hostapi_index < len(hostapis):
                hostapi_name = str(hostapis[hostapi_index].get("name", "") or "")
        except Exception:
            pass
        return int(device_id), name, hostapi_name
    except Exception:
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
        default_id = inventory_default_output_device_index(
            force_refresh=False,
            sounddevice_module=sd,
        )
    except Exception:
        return ()
    if default_id is None:
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
