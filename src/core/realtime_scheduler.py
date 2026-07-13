# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors

"""Bounded staged scheduling for final realtime speech segments.

The scheduler deliberately separates concurrent ASR, per-source recognition
reordering, translation, and ordered delivery.  A slow translation therefore
cannot stop capture or ASR admission for the next sentence, while a faster ASR
request cannot push a later sentence into translation before the preceding
source sentence has finished recognition.  It owns every worker thread it
creates and never evicts a final task that has already been admitted.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)


class AdmissionStatus(str, Enum):
    """Result of trying to admit a final sentence."""

    ACCEPTED = "accepted"
    FULL = "full"
    STOPPED = "stopped"
    INVALID_SOURCE = "invalid_source"


class SchedulerHealth(str, Enum):
    """Coarse realtime pipeline health for diagnostics and support logs."""

    HEALTHY = "healthy"
    BUSY = "busy"
    BACKLOGGED = "backlogged"
    STALLED = "stalled"
    STOPPED = "stopped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RealtimeTask:
    """Immutable identity and input snapshot for one admitted sentence."""

    source: str
    session_id: int
    sequence: int
    provider_key: Hashable
    payload: Any
    submitted_at: float
    provider_concurrency: int = 1


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    status: AdmissionStatus
    task: RealtimeTask | None = None

    @property
    def accepted(self) -> bool:
        return self.status is AdmissionStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class RealtimeCompletion:
    """A terminal marker.  Every admitted task reaches this state unless stopped."""

    task: RealtimeTask
    recognized_text: str = ""
    rewritten_text: str = ""
    result: Any = None
    asr_error: BaseException | None = None
    rewrite_error: BaseException | None = None
    translation_error: BaseException | None = None
    empty_asr: bool = False
    asr_queue_wait_s: float = 0.0
    asr_duration_s: float = 0.0
    rewrite_queue_wait_s: float = 0.0
    rewrite_duration_s: float = 0.0
    translation_queue_wait_s: float = 0.0
    translation_duration_s: float = 0.0
    completed_at: float = 0.0
    cancelled: bool = False

    @property
    def error(self) -> BaseException | None:
        return self.translation_error or self.asr_error

    @property
    def successful(self) -> bool:
        return not self.cancelled and self.error is None and not self.empty_asr


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    accepting: bool
    cancelled: bool
    ingress_pending: Mapping[str, int]
    outstanding: Mapping[str, int]
    asr_claimed: int
    asr_running: int
    provider_claimed: Mapping[Hashable, int]
    recognized_pending: Mapping[str, int]
    rewrite_pending: int
    rewrite_running: int
    rewritten_pending: Mapping[str, int]
    translation_pending: int
    translation_running: int
    translation_pending_by_source: Mapping[str, int]
    translation_running_by_source: Mapping[str, int]
    delivery_pending: int
    delivery_running: int
    accepted: int
    rejected_full: int
    rejected_stopped: int
    health: SchedulerHealth
    oldest_task_age_s: float
    oldest_by_source: Mapping[str, float]
    capacity_utilization: float

    @property
    def pending(self) -> int:
        return (
            sum(self.ingress_pending.values())
            + sum(self.recognized_pending.values())
            + self.rewrite_pending
            + sum(self.rewritten_pending.values())
            + self.translation_pending
            + self.delivery_pending
        )

    @property
    def running(self) -> int:
        return (
            self.asr_running
            + self.rewrite_running
            + self.translation_running
            + self.delivery_running
        )


@dataclass(frozen=True, slots=True)
class _RecognizedWork:
    task: RealtimeTask
    text: str
    recognized_text: str
    error: BaseException | None
    rewrite_error: BaseException | None
    asr_queue_wait_s: float
    asr_duration_s: float
    rewrite_queue_wait_s: float
    rewrite_duration_s: float
    enqueued_at: float


ASRHandler = Callable[[RealtimeTask, threading.Event], str | None]
RewriteHandler = Callable[[RealtimeTask, str, Any, threading.Event], str | None]
RewriteRequired = Callable[[RealtimeTask, str], bool]
TranslationHandler = Callable[[RealtimeTask, str, Any, threading.Event], Any]
DeliveryHandler = Callable[[RealtimeCompletion], None]
RewriteStateFactory = Callable[[int], Any]
RewriteStateFinalizer = Callable[[Any], None]
TranslationStateFactory = Callable[[int], Any]
TranslationStateFinalizer = Callable[[Any], None]


class RealtimeScheduler:
    """Cancellation-safe, bounded staged realtime sentence pipeline."""

    def __init__(
        self,
        *,
        sources: Sequence[str],
        asr_handler: ASRHandler,
        rewrite_handler: RewriteHandler | None = None,
        rewrite_required: RewriteRequired | None = None,
        translation_handler: TranslationHandler,
        delivery_handler: DeliveryHandler,
        rewrite_state_factory: RewriteStateFactory | None = None,
        rewrite_state_finalizer: RewriteStateFinalizer | None = None,
        translation_state_factory: TranslationStateFactory | None = None,
        translation_state_finalizer: TranslationStateFinalizer | None = None,
        ingress_limits: Mapping[str, int] | None = None,
        outstanding_limits: Mapping[str, int] | None = None,
        rewrite_queue_size: int = 8,
        translation_queue_size: int = 8,
        asr_concurrency: int = 2,
        rewrite_concurrency: int = 1,
        translation_concurrency: int = 2,
        priority_source: str | None = None,
        priority_burst: int = 3,
        thread_name_prefix: str = "realtime",
        clock: Callable[[], float] = time.monotonic,
        stale_task_age_s: float = 30.0,
        backlog_ratio: float = 0.75,
        health_check_interval_s: float = 1.0,
    ) -> None:
        normalized_sources = tuple(dict.fromkeys(str(source) for source in sources if str(source)))
        if not normalized_sources:
            raise ValueError("At least one realtime source is required")
        if asr_concurrency < 1 or translation_concurrency < 1:
            raise ValueError("Worker concurrency must be at least one")
        if rewrite_handler is not None and rewrite_concurrency < 1:
            raise ValueError("Rewrite worker concurrency must be at least one")
        if rewrite_queue_size < 1:
            raise ValueError("Rewrite queue size must be at least one")
        if translation_queue_size < 1:
            raise ValueError("Translation queue size must be at least one")
        if priority_burst < 1:
            raise ValueError("Priority burst must be at least one")
        if stale_task_age_s <= 0 or health_check_interval_s <= 0:
            raise ValueError("Realtime health timing values must be positive")
        if not 0 < backlog_ratio <= 1:
            raise ValueError("backlog_ratio must be within (0, 1]")
        if not callable(asr_handler) or not callable(translation_handler) or not callable(delivery_handler):
            raise TypeError("Realtime stage handlers must be callable")
        if rewrite_handler is not None and not callable(rewrite_handler):
            raise TypeError("Realtime rewrite handler must be callable")
        if rewrite_required is not None and not callable(rewrite_required):
            raise TypeError("Realtime rewrite predicate must be callable")

        limits = dict(ingress_limits or {})
        configured_outstanding_limits = dict(outstanding_limits or {})
        self._sources = normalized_sources
        self._ingress_limits: dict[str, int] = {}
        self._outstanding_limits: dict[str, int] = {}
        for source in self._sources:
            ingress_limit = int(limits.get(source, 8))
            if ingress_limit < 1:
                raise ValueError(f"Ingress limit for {source!r} must be at least one")
            self._ingress_limits[source] = ingress_limit
            default_outstanding_limit = (
                ingress_limit
                + int(asr_concurrency)
                + (int(rewrite_queue_size) + int(rewrite_concurrency) if rewrite_handler else 0)
                + int(translation_queue_size)
                + int(translation_concurrency)
            )
            outstanding_limit = int(
                configured_outstanding_limits.get(source, default_outstanding_limit)
            )
            if outstanding_limit < 1:
                raise ValueError(
                    f"Outstanding limit for {source!r} must be at least one"
                )
            self._outstanding_limits[source] = outstanding_limit

        self._asr_handler = asr_handler
        self._rewrite_handler = rewrite_handler
        self._rewrite_required = rewrite_required
        self._translation_handler = translation_handler
        self._delivery_handler = delivery_handler
        self._rewrite_state_factory = rewrite_state_factory or (lambda _index: None)
        self._rewrite_state_finalizer = rewrite_state_finalizer
        self._translation_state_factory = translation_state_factory or (lambda _index: None)
        self._translation_state_finalizer = translation_state_finalizer
        self._asr_concurrency = int(asr_concurrency)
        self._rewrite_concurrency = int(rewrite_concurrency) if rewrite_handler else 0
        self._translation_concurrency = int(translation_concurrency)
        self._priority_source = priority_source if priority_source in self._sources else None
        self._priority_burst = int(priority_burst)
        self._thread_name_prefix = str(thread_name_prefix or "realtime")
        self._clock = clock
        self._stale_task_age_s = float(stale_task_age_s)
        self._backlog_ratio = float(backlog_ratio)
        self._health_check_interval_s = float(health_check_interval_s)

        self._cancel_event = threading.Event()
        self._state_lock = threading.RLock()
        self._work_available = threading.Condition(self._state_lock)
        self._recognized_available = threading.Condition(self._state_lock)
        self._translation_available = threading.Condition(self._state_lock)
        self._delivery_available = threading.Condition(self._state_lock)
        self._state_changed = threading.Condition(self._state_lock)
        self._delivery_gate = threading.RLock()

        self._ingress: dict[str, deque[RealtimeTask]] = {
            source: deque() for source in self._sources
        }
        self._next_sequence = {source: 0 for source in self._sources}
        self._next_recognized_sequence = {source: 0 for source in self._sources}
        self._next_translation_sequence = {source: 0 for source in self._sources}
        self._next_delivery_sequence = {source: 0 for source in self._sources}
        self._outstanding = {source: 0 for source in self._sources}
        self._admitted_tasks: dict[str, dict[int, RealtimeTask]] = {
            source: {} for source in self._sources
        }
        self._completed: dict[str, dict[int, RealtimeCompletion]] = {
            source: {} for source in self._sources
        }
        self._recognized: dict[str, dict[int, _RecognizedWork]] = {
            source: {} for source in self._sources
        }
        self._rewritten: dict[str, dict[int, _RecognizedWork]] = {
            source: {} for source in self._sources
        }
        self._rewrite_queue: queue.Queue[_RecognizedWork] | None = (
            queue.Queue(maxsize=int(rewrite_queue_size))
            if rewrite_handler is not None
            else None
        )
        self._translation_queues: dict[str, deque[_RecognizedWork]] = {
            source: deque() for source in self._sources
        }
        self._translation_queue_size = int(translation_queue_size)
        self._translation_reserved_priority_slots = (
            1
            if self._priority_source is not None and self._translation_queue_size > 1
            else 0
        )
        self._translation_running_by_source = {
            source: 0 for source in self._sources
        }
        self._cancelled_sequences: dict[str, set[int]] = {
            source: set() for source in self._sources
        }
        self._asr_inflight_sequences: set[tuple[str, int]] = set()
        self._rewrite_pending_sequences: set[tuple[str, int]] = set()
        self._translation_active_sequences: set[tuple[str, int]] = set()

        self._provider_claimed: dict[Hashable, int] = {}
        self._provider_active_limits: dict[Hashable, list[int]] = {}
        self._asr_claimed = 0
        self._asr_running = 0
        self._rewrite_running = 0
        self._translation_running = 0
        self._delivery_running = 0
        self._priority_streak = 0
        self._round_robin_index = 0
        self._recognized_round_robin_index = 0
        self._rewritten_round_robin_index = 0
        self._translation_round_robin_index = 0
        self._translation_priority_streak = 0
        self._delivery_round_robin_index = 0

        self._accepted_count = 0
        self._rejected_full_count = 0
        self._rejected_stopped_count = 0
        self._started = False
        self._accepting = False
        self._threads: list[threading.Thread] = []

    @property
    def cancellation_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def threads(self) -> tuple[threading.Thread, ...]:
        with self._state_lock:
            return tuple(self._threads)

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            if self._cancel_event.is_set():
                raise RuntimeError("A stopped realtime scheduler cannot be restarted")
            self._started = True
            self._accepting = True

            threads: list[threading.Thread] = []
            for index in range(self._asr_concurrency):
                threads.append(
                    threading.Thread(
                        target=self._asr_worker,
                        args=(index,),
                        daemon=True,
                        name=f"{self._thread_name_prefix}-asr-{index + 1}",
                    )
                )
            for source in self._sources:
                threads.append(
                    threading.Thread(
                        target=self._recognized_feeder,
                        args=(source,),
                        daemon=True,
                        name=(
                            f"{self._thread_name_prefix}-rewrite-feed-{source}"
                            if self._rewrite_handler is not None
                            else f"{self._thread_name_prefix}-translation-feed-{source}"
                        ),
                    )
                )
            if self._rewrite_handler is not None:
                for index in range(self._rewrite_concurrency):
                    threads.append(
                        threading.Thread(
                            target=self._rewrite_worker,
                            args=(index,),
                            daemon=True,
                            name=f"{self._thread_name_prefix}-rewrite-{index + 1}",
                        )
                    )
                for source in self._sources:
                    threads.append(
                        threading.Thread(
                            target=self._rewritten_feeder,
                            args=(source,),
                            daemon=True,
                            name=f"{self._thread_name_prefix}-translation-feed-{source}",
                        )
                    )
            for index in range(self._translation_concurrency):
                threads.append(
                    threading.Thread(
                        target=self._translation_worker,
                        args=(index,),
                        daemon=True,
                        name=f"{self._thread_name_prefix}-translation-{index + 1}",
                    )
                )
            threads.append(
                threading.Thread(
                    target=self._delivery_worker,
                    daemon=True,
                    name=f"{self._thread_name_prefix}-delivery",
                )
            )
            threads.append(
                threading.Thread(
                    target=self._health_monitor,
                    daemon=True,
                    name=f"{self._thread_name_prefix}-health",
                )
            )
            self._threads = threads

        for thread in threads:
            thread.start()

    def submit(
        self,
        *,
        source: str,
        session_id: int,
        provider_key: Hashable,
        payload: Any,
        provider_concurrency: int = 1,
    ) -> AdmissionResult:
        source = str(source)
        try:
            hash(provider_key)
        except Exception as exc:
            raise TypeError("provider_key must be hashable") from exc
        try:
            provider_limit = int(provider_concurrency)
        except (TypeError, ValueError):
            provider_limit = 1
        provider_limit = max(1, min(provider_limit, self._asr_concurrency))

        with self._work_available:
            if not self._accepting or self._cancel_event.is_set():
                self._rejected_stopped_count += 1
                logger.debug("Realtime admission rejected: scheduler stopped source=%s", source)
                return AdmissionResult(AdmissionStatus.STOPPED)
            source_queue = self._ingress.get(source)
            if source_queue is None:
                return AdmissionResult(AdmissionStatus.INVALID_SOURCE)
            ingress_full = len(source_queue) >= self._ingress_limits[source]
            outstanding_full = (
                self._outstanding[source] >= self._outstanding_limits[source]
            )
            if ingress_full or outstanding_full:
                self._rejected_full_count += 1
                logger.warning(
                    "Realtime admission backpressure source=%s ingress_depth=%d "
                    "ingress_limit=%d outstanding=%d outstanding_limit=%d",
                    source,
                    len(source_queue),
                    self._ingress_limits[source],
                    self._outstanding[source],
                    self._outstanding_limits[source],
                )
                return AdmissionResult(AdmissionStatus.FULL)

            sequence = self._next_sequence[source]
            task = RealtimeTask(
                source=source,
                session_id=int(session_id),
                sequence=sequence,
                provider_key=provider_key,
                payload=payload,
                submitted_at=self._clock(),
                provider_concurrency=provider_limit,
            )
            source_queue.append(task)
            self._admitted_tasks[source][sequence] = task
            self._next_sequence[source] = sequence + 1
            self._outstanding[source] += 1
            self._accepted_count += 1
            depth = len(source_queue)
            self._work_available.notify()
            self._state_changed.notify_all()

        logger.debug(
            "Realtime task admitted source=%s sequence=%d ingress_depth=%d",
            source,
            sequence,
            depth,
        )
        return AdmissionResult(AdmissionStatus.ACCEPTED, task)

    def cancel_source(self, source: str, *, session_id: int | None = None) -> int:
        """Cancel admitted work for one source without stopping other sources.

        Cancellation publishes ordered terminal markers, so later sentences from
        the same source cannot deadlock behind a removed sequence. Work already
        inside a provider call cannot be forcefully interrupted, but its eventual
        result is ignored and never replaces the cancellation marker.
        """

        source = str(source)
        with self._delivery_available:
            admitted = self._admitted_tasks.get(source)
            if admitted is None:
                return 0
            tasks = [
                task
                for task in admitted.values()
                if session_id is None or task.session_id == int(session_id)
            ]
            if not tasks:
                return 0

            cancelled_sequences = {task.sequence for task in tasks}
            self._cancelled_sequences[source].update(cancelled_sequences)
            self._ingress[source] = deque(
                task
                for task in self._ingress[source]
                if task.sequence not in cancelled_sequences
            )
            for sequence in cancelled_sequences:
                self._recognized[source].pop(sequence, None)
                self._rewritten[source].pop(sequence, None)
            self._translation_queues[source] = deque(
                work
                for work in self._translation_queues[source]
                if work.task.sequence not in cancelled_sequences
            )

            completed_at = self._clock()
            for task in tasks:
                if task.sequence < self._next_delivery_sequence[source]:
                    continue
                self._completed[source][task.sequence] = RealtimeCompletion(
                    task=task,
                    completed_at=completed_at,
                    cancelled=True,
                )

            self._work_available.notify_all()
            self._recognized_available.notify_all()
            self._translation_available.notify_all()
            self._delivery_available.notify_all()
            self._state_changed.notify_all()

        logger.info(
            "Cancelled realtime source work source=%s session_id=%s count=%d",
            source,
            session_id,
            len(tasks),
        )
        return len(tasks)

    def should_yield_partial(self, provider_key: Hashable | None = None) -> bool:
        """Return true when partial ASR would contend with admitted final work."""

        with self._state_lock:
            if not self._accepting or self._cancel_event.is_set():
                return True
            if provider_key is None:
                return self._asr_claimed > 0 or any(self._ingress.values())
            if self._provider_claimed.get(provider_key, 0) > 0:
                return True
            return any(
                task.provider_key == provider_key
                for source_queue in self._ingress.values()
                for task in source_queue
            )

    def snapshot(self) -> SchedulerSnapshot:
        with self._state_lock:
            now = self._clock()
            ingress = MappingProxyType(
                {source: len(source_queue) for source, source_queue in self._ingress.items()}
            )
            provider_claimed = MappingProxyType(dict(self._provider_claimed))
            outstanding = MappingProxyType(dict(self._outstanding))
            recognized_pending = MappingProxyType(
                {
                    source: len(items)
                    for source, items in self._recognized.items()
                }
            )
            rewritten_pending = MappingProxyType(
                {
                    source: len(items)
                    for source, items in self._rewritten.items()
                }
            )
            translation_pending_by_source = MappingProxyType(
                {
                    source: len(items)
                    for source, items in self._translation_queues.items()
                }
            )
            translation_running_by_source = MappingProxyType(
                dict(self._translation_running_by_source)
            )
            oldest_by_source_values: dict[str, float] = {}
            for source, tasks in self._admitted_tasks.items():
                oldest_submitted = min(
                    (task.submitted_at for task in tasks.values()),
                    default=now,
                )
                oldest_by_source_values[source] = (
                    max(0.0, now - oldest_submitted) if tasks else 0.0
                )
            oldest_by_source = MappingProxyType(oldest_by_source_values)
            oldest_task_age = max(oldest_by_source_values.values(), default=0.0)
            delivery_pending = sum(len(items) for items in self._completed.values())
            capacity_ratios = [
                len(self._ingress[source]) / self._ingress_limits[source]
                for source in self._sources
            ]
            capacity_ratios.extend(
                self._outstanding[source] / self._outstanding_limits[source]
                for source in self._sources
            )
            translation_pending = self._translation_pending_count_locked()
            capacity_ratios.append(
                translation_pending / self._translation_queue_size
            )
            if self._rewrite_queue is not None:
                capacity_ratios.append(
                    self._rewrite_queue.qsize() / self._rewrite_queue.maxsize
                )
            capacity_utilization = max(capacity_ratios, default=0.0)
            if self._cancel_event.is_set():
                health = SchedulerHealth.CANCELLED
            elif not self._accepting:
                health = SchedulerHealth.STOPPED
            elif oldest_task_age >= self._stale_task_age_s:
                health = SchedulerHealth.STALLED
            elif capacity_utilization >= self._backlog_ratio:
                health = SchedulerHealth.BACKLOGGED
            elif any(self._outstanding.values()):
                health = SchedulerHealth.BUSY
            else:
                health = SchedulerHealth.HEALTHY
            return SchedulerSnapshot(
                accepting=self._accepting,
                cancelled=self._cancel_event.is_set(),
                ingress_pending=ingress,
                outstanding=outstanding,
                asr_claimed=self._asr_claimed,
                asr_running=self._asr_running,
                provider_claimed=provider_claimed,
                recognized_pending=recognized_pending,
                rewrite_pending=(
                    self._rewrite_queue.qsize()
                    if self._rewrite_queue is not None
                    else 0
                ),
                rewrite_running=self._rewrite_running,
                rewritten_pending=rewritten_pending,
                translation_pending=translation_pending,
                translation_running=self._translation_running,
                translation_pending_by_source=translation_pending_by_source,
                translation_running_by_source=translation_running_by_source,
                delivery_pending=delivery_pending,
                delivery_running=self._delivery_running,
                accepted=self._accepted_count,
                rejected_full=self._rejected_full_count,
                rejected_stopped=self._rejected_stopped_count,
                health=health,
                oldest_task_age_s=oldest_task_age,
                oldest_by_source=oldest_by_source,
                capacity_utilization=capacity_utilization,
            )

    def _health_monitor(self) -> None:
        previous: SchedulerHealth | None = None
        last_stalled_report = 0.0
        while not self._cancel_event.wait(self._health_check_interval_s):
            snapshot = self.snapshot()
            health = snapshot.health
            now = self._clock()
            repeated_stall = (
                health is SchedulerHealth.STALLED
                and now - last_stalled_report >= 10.0
            )
            if health is not previous or repeated_stall:
                if health in {SchedulerHealth.BACKLOGGED, SchedulerHealth.STALLED}:
                    logger.warning(
                        "Realtime pipeline health=%s oldest_ms=%.0f utilization=%.0f%% "
                        "pending=%d running=%d outstanding=%s translation_pending=%s "
                        "translation_running=%s",
                        health.value,
                        snapshot.oldest_task_age_s * 1000.0,
                        snapshot.capacity_utilization * 100.0,
                        snapshot.pending,
                        snapshot.running,
                        dict(snapshot.outstanding),
                        dict(snapshot.translation_pending_by_source),
                        dict(snapshot.translation_running_by_source),
                    )
                    if health is SchedulerHealth.STALLED:
                        last_stalled_report = now
                elif previous in {
                    SchedulerHealth.BACKLOGGED,
                    SchedulerHealth.STALLED,
                }:
                    logger.info(
                        "Realtime pipeline recovered health=%s oldest_ms=%.0f",
                        health.value,
                        snapshot.oldest_task_age_s * 1000.0,
                    )
                previous = health

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = self._clock() + max(float(timeout), 0.0)
        with self._state_changed:
            while not self._is_idle_locked():
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._state_changed.wait(timeout=min(remaining, 0.1))
            return True

    def stop(self, timeout: float | None = 5.0) -> bool:
        """Cancel pending work, suppress future delivery, and join owned threads."""

        deadline = None if timeout is None else self._clock() + max(float(timeout), 0.0)
        with self._state_lock:
            self._accepting = False
            self._cancel_event.set()
            for source_queue in self._ingress.values():
                source_queue.clear()
            for completed in self._completed.values():
                completed.clear()
            for recognized in self._recognized.values():
                recognized.clear()
            for rewritten in self._rewritten.values():
                rewritten.clear()
            for source in self._sources:
                self._outstanding[source] = 0
                self._admitted_tasks[source].clear()
                self._translation_queues[source].clear()
                self._cancelled_sequences[source].clear()
            self._asr_inflight_sequences.clear()
            self._rewrite_pending_sequences.clear()
            self._translation_active_sequences.clear()
            self._drain_stage_queue_locked(self._rewrite_queue)
            self._work_available.notify_all()
            self._recognized_available.notify_all()
            self._translation_available.notify_all()
            self._delivery_available.notify_all()
            self._state_changed.notify_all()
            threads = tuple(self._threads)

        # Synchronize with a callback that may already have passed its final
        # cancellation check.  Respect the caller's timeout even if a callback
        # itself is stuck; cancellation prevents any later callback from starting.
        if deadline is None:
            delivery_quiesced = self._delivery_gate.acquire()
        else:
            delivery_quiesced = self._delivery_gate.acquire(
                timeout=max(0.0, deadline - self._clock())
            )
        if delivery_quiesced:
            self._delivery_gate.release()

        current = threading.current_thread()
        for thread in threads:
            if thread is current:
                continue
            remaining = None if deadline is None else max(0.0, deadline - self._clock())
            thread.join(timeout=remaining)

        alive = tuple(thread for thread in threads if thread is not current and thread.is_alive())
        if alive or not delivery_quiesced:
            details = ", ".join(thread.name for thread in alive) or "delivery callback"
            logger.warning(
                "Realtime scheduler stop timed out; workers still alive: %s",
                details,
            )
        with self._state_lock:
            self._state_changed.notify_all()
        return delivery_quiesced and not alive

    @staticmethod
    def _drain_stage_queue_locked(
        stage_queue: queue.Queue[_RecognizedWork] | None,
    ) -> None:
        if stage_queue is None:
            return
        while True:
            try:
                stage_queue.get_nowait()
            except queue.Empty:
                return
            else:
                stage_queue.task_done()

    def _translation_pending_count_locked(self) -> int:
        return sum(len(items) for items in self._translation_queues.values())

    def _translation_pending_count(self) -> int:
        with self._state_lock:
            return self._translation_pending_count_locked()

    def _task_cancelled_locked(self, task: RealtimeTask) -> bool:
        return task.sequence in self._cancelled_sequences[task.source]

    def _task_cancelled(self, task: RealtimeTask) -> bool:
        with self._state_lock:
            return self._task_cancelled_locked(task)

    def _prune_cancelled_sequences_locked(self, source: str) -> None:
        cancelled = self._cancelled_sequences[source]
        if not cancelled:
            return

        recognized_floor = self._next_recognized_sequence[source]
        translation_floor = self._next_translation_sequence[source]
        delivery_floor = self._next_delivery_sequence[source]
        admitted = self._admitted_tasks[source]
        completed = self._completed[source]
        recognized = self._recognized[source]
        rewritten = self._rewritten[source]
        queued_for_translation = {
            work.task.sequence for work in self._translation_queues[source]
        }
        queued_at_ingress = {task.sequence for task in self._ingress[source]}

        for sequence in tuple(cancelled):
            key = (source, sequence)
            if sequence >= recognized_floor or sequence >= delivery_floor:
                continue
            if self._rewrite_handler is not None and sequence >= translation_floor:
                continue
            if (
                sequence in admitted
                or sequence in completed
                or sequence in recognized
                or sequence in rewritten
                or sequence in queued_for_translation
                or sequence in queued_at_ingress
                or key in self._asr_inflight_sequences
                or key in self._rewrite_pending_sequences
                or key in self._translation_active_sequences
            ):
                continue
            cancelled.discard(sequence)

    def _finish_asr_pipeline_task(self, task: RealtimeTask) -> None:
        with self._state_lock:
            self._asr_inflight_sequences.discard((task.source, task.sequence))
            self._prune_cancelled_sequences_locked(task.source)
            self._state_changed.notify_all()

    def _finish_rewrite_pipeline_task(self, work: _RecognizedWork) -> None:
        with self._state_lock:
            self._rewrite_pending_sequences.discard(
                (work.task.source, work.task.sequence)
            )
            self._prune_cancelled_sequences_locked(work.task.source)
            self._state_changed.notify_all()

    def _is_idle_locked(self) -> bool:
        return bool(
            not any(self._ingress.values())
            and not any(self._recognized.values())
            and not any(self._rewritten.values())
            and not any(self._outstanding.values())
            and self._asr_claimed == 0
            and (self._rewrite_queue is None or self._rewrite_queue.empty())
            and self._rewrite_running == 0
            and self._translation_pending_count_locked() == 0
            and self._translation_running == 0
            and not any(self._completed.values())
            and self._delivery_running == 0
            and not any(self._cancelled_sequences.values())
            and not self._asr_inflight_sequences
            and not self._rewrite_pending_sequences
            and not self._translation_active_sequences
        )

    def _select_source_locked(self) -> str | None:
        dispatchable = [
            source
            for source in self._sources
            if self._ingress[source]
            and self._provider_can_dispatch_locked(self._ingress[source][0])
        ]
        if not dispatchable:
            return None

        priority = self._priority_source
        others = [source for source in dispatchable if source != priority]
        if priority is not None and priority in dispatchable and others:
            if self._priority_streak < self._priority_burst:
                self._priority_streak += 1
                return priority
            self._priority_streak = 0
            return self._select_round_robin_locked(others)
        if priority is not None and priority in dispatchable:
            self._priority_streak = 0
            return priority

        self._priority_streak = 0
        return self._select_round_robin_locked(dispatchable)

    def _provider_can_dispatch_locked(self, task: RealtimeTask) -> bool:
        active_limits = self._provider_active_limits.get(task.provider_key, ())
        effective_limit = min(
            (task.provider_concurrency, *active_limits),
            default=task.provider_concurrency,
        )
        return self._provider_claimed.get(task.provider_key, 0) < effective_limit

    def _select_round_robin_locked(self, candidates: Sequence[str]) -> str:
        candidate_set = set(candidates)
        for offset in range(len(self._sources)):
            index = (self._round_robin_index + offset) % len(self._sources)
            source = self._sources[index]
            if source in candidate_set:
                self._round_robin_index = (index + 1) % len(self._sources)
                return source
        return candidates[0]

    def _take_asr_task(self) -> RealtimeTask | None:
        with self._work_available:
            while not self._cancel_event.is_set():
                source = self._select_source_locked()
                if source is not None:
                    task = self._ingress[source].popleft()
                    self._asr_inflight_sequences.add((task.source, task.sequence))
                    self._asr_claimed += 1
                    self._provider_claimed[task.provider_key] = (
                        self._provider_claimed.get(task.provider_key, 0) + 1
                    )
                    self._provider_active_limits.setdefault(
                        task.provider_key,
                        [],
                    ).append(task.provider_concurrency)
                    self._state_changed.notify_all()
                    return task
                self._work_available.wait(timeout=0.1)
            return None

    def _finish_asr_claim(self, task: RealtimeTask, *, was_running: bool) -> None:
        with self._work_available:
            if was_running:
                self._asr_running = max(0, self._asr_running - 1)
            self._asr_claimed = max(0, self._asr_claimed - 1)
            provider_key = task.provider_key
            remaining = self._provider_claimed.get(provider_key, 0) - 1
            if remaining > 0:
                self._provider_claimed[provider_key] = remaining
            else:
                self._provider_claimed.pop(provider_key, None)
            active_limits = self._provider_active_limits.get(provider_key)
            if active_limits is not None:
                try:
                    active_limits.remove(task.provider_concurrency)
                except ValueError:
                    active_limits.clear()
                if not active_limits:
                    self._provider_active_limits.pop(provider_key, None)
            self._work_available.notify_all()
            self._state_changed.notify_all()

    def _asr_worker(self, worker_index: int) -> None:
        del worker_index
        while not self._cancel_event.is_set():
            task = self._take_asr_task()
            if task is None:
                return

            running = False
            text = ""
            error: BaseException | None = None
            started_at = self._clock()
            queue_wait = max(0.0, started_at - task.submitted_at)
            try:
                if self._cancel_event.is_set() or self._task_cancelled(task):
                    self._finish_asr_pipeline_task(task)
                    continue
                with self._state_lock:
                    self._asr_running += 1
                    running = True
                    self._state_changed.notify_all()
                started_at = self._clock()
                logger.debug(
                    "Realtime ASR started source=%s sequence=%d queue_wait_ms=%.1f",
                    task.source,
                    task.sequence,
                    queue_wait * 1000.0,
                )
                raw_text = self._asr_handler(task, self._cancel_event)
                text = str(raw_text or "").strip()
            except Exception as exc:
                error = exc
                logger.debug(
                    "Realtime ASR failed source=%s sequence=%d",
                    task.source,
                    task.sequence,
                    exc_info=True,
                )
            finally:
                finished_at = self._clock()
                self._finish_asr_claim(task, was_running=running)

            if self._cancel_event.is_set() or self._task_cancelled(task):
                self._finish_asr_pipeline_task(task)
                continue
            duration = max(0.0, finished_at - started_at)
            work = _RecognizedWork(
                task=task,
                text=text,
                recognized_text=text,
                error=error,
                rewrite_error=None,
                asr_queue_wait_s=queue_wait,
                asr_duration_s=duration,
                rewrite_queue_wait_s=0.0,
                rewrite_duration_s=0.0,
                enqueued_at=self._clock(),
            )
            logger.debug(
                "Realtime ASR finished source=%s sequence=%d duration_ms=%.1f translation_depth=%d",
                task.source,
                task.sequence,
                duration * 1000.0,
                self._translation_pending_count(),
            )
            try:
                self._store_recognized_work(work)
            finally:
                self._finish_asr_pipeline_task(task)

    def _store_recognized_work(self, work: _RecognizedWork) -> bool:
        """Publish one ASR terminal marker without blocking an ASR worker."""

        with self._recognized_available:
            if self._cancel_event.is_set() or self._task_cancelled_locked(work.task):
                return False
            if work.task.sequence < self._next_recognized_sequence[work.task.source]:
                return False
            pending = self._recognized[work.task.source]
            pending[work.task.sequence] = work
            self._recognized_available.notify()
            self._state_changed.notify_all()
            return True

    def _peek_next_recognized_work_locked(self) -> _RecognizedWork | None:
        for offset in range(len(self._sources)):
            index = (
                self._recognized_round_robin_index + offset
            ) % len(self._sources)
            source = self._sources[index]
            expected = self._next_recognized_sequence[source]
            while expected in self._cancelled_sequences[source]:
                expected += 1
            self._next_recognized_sequence[source] = expected
            self._prune_cancelled_sequences_locked(source)
            work = self._recognized[source].get(expected)
            if work is not None:
                self._recognized_round_robin_index = (index + 1) % len(
                    self._sources
                )
                return work
        return None

    def _take_next_recognized_work(self, source: str | None = None) -> _RecognizedWork | None:
        with self._recognized_available:
            if source is None:
                work = self._peek_next_recognized_work_locked()
            else:
                expected = self._next_recognized_sequence[source]
                while expected in self._cancelled_sequences[source]:
                    expected += 1
                self._next_recognized_sequence[source] = expected
                self._prune_cancelled_sequences_locked(source)
                work = self._recognized[source].get(expected)
            while work is None and not self._cancel_event.is_set():
                self._recognized_available.wait(timeout=0.1)
                if source is None:
                    work = self._peek_next_recognized_work_locked()
                else:
                    expected = self._next_recognized_sequence[source]
                    while expected in self._cancelled_sequences[source]:
                        expected += 1
                    self._next_recognized_sequence[source] = expected
                    self._prune_cancelled_sequences_locked(source)
                    work = self._recognized[source].get(expected)
            return work

    def _mark_recognized_work_enqueued(self, work: _RecognizedWork) -> None:
        with self._recognized_available:
            pending = self._recognized[work.task.source]
            if pending.get(work.task.sequence) is work:
                pending.pop(work.task.sequence, None)
                self._next_recognized_sequence[work.task.source] = (
                    work.task.sequence + 1
                )
            self._prune_cancelled_sequences_locked(work.task.source)
            self._recognized_available.notify_all()
            self._state_changed.notify_all()

    def _recognized_feeder(self, source: str | None = None) -> None:
        """Feed ASR results to the next stage in strict per-source order."""

        while not self._cancel_event.is_set():
            work = self._take_next_recognized_work(source)
            if work is None:
                return
            if self._rewrite_queue is not None:
                rewrite_required = True
                predicate = self._rewrite_required
                if predicate is not None:
                    try:
                        rewrite_required = bool(predicate(work.task, work.text))
                    except Exception:
                        logger.exception(
                            "Realtime rewrite predicate failed; preserving rewrite "
                            "source=%s sequence=%d",
                            work.task.source,
                            work.task.sequence,
                        )
                if rewrite_required:
                    queued = self._put_stage_work(
                        self._rewrite_queue,
                        work,
                        stage_name="rewrite",
                        reserve_priority=True,
                    )
                else:
                    # A bypassed item still enters the rewritten reorder buffer.
                    # Publishing it straight to translation could let a later
                    # bypassed sequence overtake an earlier provider rewrite.
                    self._store_rewritten_work(work)
                    queued = True
            else:
                queued = self._put_translation_work(work)
            if not queued:
                return
            self._mark_recognized_work_enqueued(work)

    def _put_stage_work(
        self,
        stage_queue: queue.Queue[_RecognizedWork],
        work: _RecognizedWork,
        *,
        stage_name: str,
        reserve_priority: bool = False,
    ) -> bool:
        logged_backpressure = False
        while not self._cancel_event.is_set():
            with self._state_lock:
                if self._task_cancelled_locked(work.task):
                    return True
            reserved_capacity = (
                1
                if reserve_priority
                and self._priority_source is not None
                and work.task.source != self._priority_source
                and stage_queue.maxsize > 1
                else 0
            )
            if stage_queue.qsize() >= stage_queue.maxsize - reserved_capacity:
                if not logged_backpressure:
                    logged_backpressure = True
                    logger.warning(
                        "Realtime %s backpressure source=%s sequence=%d depth=%d",
                        stage_name,
                        work.task.source,
                        work.task.sequence,
                        stage_queue.qsize(),
                    )
                self._cancel_event.wait(0.05)
                continue
            try:
                with self._state_lock:
                    if self._cancel_event.is_set():
                        return False
                    if self._task_cancelled_locked(work.task):
                        return True
                    self._rewrite_pending_sequences.add(
                        (work.task.source, work.task.sequence)
                    )
                    stage_queue.put_nowait(work)
                    self._state_changed.notify_all()
                return True
            except queue.Full:
                with self._state_lock:
                    self._rewrite_pending_sequences.discard(
                        (work.task.source, work.task.sequence)
                    )
                if not logged_backpressure:
                    logged_backpressure = True
                    logger.warning(
                        "Realtime %s backpressure source=%s sequence=%d depth=%d",
                        stage_name,
                        work.task.source,
                        work.task.sequence,
                        stage_queue.qsize(),
                    )
        return False

    def _put_translation_work(self, work: _RecognizedWork) -> bool:
        logged_backpressure = False
        source = work.task.source
        with self._translation_available:
            while not self._cancel_event.is_set():
                if self._task_cancelled_locked(work.task):
                    return True
                pending = self._translation_pending_count_locked()
                capacity = self._translation_queue_size
                if (
                    self._priority_source is not None
                    and source != self._priority_source
                    and not self._translation_queues[self._priority_source]
                ):
                    capacity -= self._translation_reserved_priority_slots
                if pending < max(capacity, 1):
                    self._translation_queues[source].append(work)
                    self._translation_available.notify_all()
                    self._state_changed.notify_all()
                    return True
                if not logged_backpressure:
                    logged_backpressure = True
                    logger.warning(
                        "Realtime translation backpressure source=%s sequence=%d "
                        "source_depth=%d total_depth=%d capacity=%d",
                        source,
                        work.task.sequence,
                        len(self._translation_queues[source]),
                        pending,
                        capacity,
                    )
                self._translation_available.wait(timeout=0.05)
        return False

    def _rewrite_worker(self, worker_index: int) -> None:
        rewrite_queue = self._rewrite_queue
        rewrite_handler = self._rewrite_handler
        if rewrite_queue is None or rewrite_handler is None:
            return

        state_error: BaseException | None = None
        state_initialized = False
        try:
            worker_state = self._rewrite_state_factory(worker_index)
            state_initialized = True
        except Exception as exc:
            worker_state = None
            state_error = exc
            logger.exception("Realtime rewrite worker state initialization failed")

        try:
            while True:
                if self._cancel_event.is_set() and rewrite_queue.empty():
                    return
                try:
                    work = rewrite_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                running = False
                try:
                    if self._cancel_event.is_set() or self._task_cancelled(work.task):
                        continue
                    queue_started = self._clock()
                    queue_wait = max(0.0, queue_started - work.enqueued_at)
                    with self._state_lock:
                        self._rewrite_running += 1
                        running = True
                        self._state_changed.notify_all()

                    rewritten_text = work.text
                    rewrite_error: BaseException | None = None
                    rewrite_started = self._clock()
                    empty_asr = work.error is None and not work.text
                    try:
                        if work.error is not None or empty_asr:
                            pass
                        elif state_error is not None:
                            raise state_error
                        else:
                            candidate = rewrite_handler(
                                work.task,
                                work.text,
                                worker_state,
                                self._cancel_event,
                            )
                            rewritten_text = str(candidate or "").strip() or work.text
                    except Exception as exc:
                        rewrite_error = exc
                        rewritten_text = work.text
                        logger.warning(
                            "Realtime ASR rewrite failed open source=%s sequence=%d: %s",
                            work.task.source,
                            work.task.sequence,
                            exc,
                        )
                        logger.debug(
                            "Realtime ASR rewrite traceback",
                            exc_info=True,
                        )
                    finished_at = self._clock()
                    rewritten = _RecognizedWork(
                        task=work.task,
                        text=rewritten_text,
                        recognized_text=work.recognized_text,
                        error=work.error,
                        rewrite_error=rewrite_error,
                        asr_queue_wait_s=work.asr_queue_wait_s,
                        asr_duration_s=work.asr_duration_s,
                        rewrite_queue_wait_s=queue_wait,
                        rewrite_duration_s=max(0.0, finished_at - rewrite_started),
                        enqueued_at=finished_at,
                    )
                    if not self._cancel_event.is_set():
                        self._store_rewritten_work(rewritten)
                    logger.debug(
                        "Realtime ASR rewrite finished source=%s sequence=%d "
                        "queue_wait_ms=%.1f duration_ms=%.1f changed=%s",
                        work.task.source,
                        work.task.sequence,
                        queue_wait * 1000.0,
                        rewritten.rewrite_duration_s * 1000.0,
                        rewritten.text != rewritten.recognized_text,
                    )
                finally:
                    with self._state_lock:
                        if running:
                            self._rewrite_running = max(0, self._rewrite_running - 1)
                        self._state_changed.notify_all()
                    rewrite_queue.task_done()
                    self._finish_rewrite_pipeline_task(work)
        finally:
            finalizer = self._rewrite_state_finalizer
            if state_initialized and callable(finalizer):
                try:
                    finalizer(worker_state)
                except Exception:
                    logger.exception("Realtime rewrite worker state cleanup failed")

    def _store_rewritten_work(self, work: _RecognizedWork) -> None:
        with self._recognized_available:
            if self._cancel_event.is_set() or self._task_cancelled_locked(work.task):
                return
            if work.task.sequence < self._next_translation_sequence[work.task.source]:
                return
            self._rewritten[work.task.source][work.task.sequence] = work
            self._recognized_available.notify_all()
            self._state_changed.notify_all()

    def _peek_next_rewritten_work_locked(self) -> _RecognizedWork | None:
        for offset in range(len(self._sources)):
            index = (
                self._rewritten_round_robin_index + offset
            ) % len(self._sources)
            source = self._sources[index]
            expected = self._next_translation_sequence[source]
            while expected in self._cancelled_sequences[source]:
                expected += 1
            self._next_translation_sequence[source] = expected
            self._prune_cancelled_sequences_locked(source)
            work = self._rewritten[source].get(expected)
            if work is not None:
                self._rewritten_round_robin_index = (index + 1) % len(
                    self._sources
                )
                return work
        return None

    def _take_next_rewritten_work(self, source: str | None = None) -> _RecognizedWork | None:
        with self._recognized_available:
            if source is None:
                work = self._peek_next_rewritten_work_locked()
            else:
                expected = self._next_translation_sequence[source]
                while expected in self._cancelled_sequences[source]:
                    expected += 1
                self._next_translation_sequence[source] = expected
                self._prune_cancelled_sequences_locked(source)
                work = self._rewritten[source].get(expected)
            while work is None and not self._cancel_event.is_set():
                self._recognized_available.wait(timeout=0.1)
                if source is None:
                    work = self._peek_next_rewritten_work_locked()
                else:
                    expected = self._next_translation_sequence[source]
                    while expected in self._cancelled_sequences[source]:
                        expected += 1
                    self._next_translation_sequence[source] = expected
                    self._prune_cancelled_sequences_locked(source)
                    work = self._rewritten[source].get(expected)
            return work

    def _mark_rewritten_work_enqueued(self, work: _RecognizedWork) -> None:
        with self._recognized_available:
            pending = self._rewritten[work.task.source]
            if pending.get(work.task.sequence) is work:
                pending.pop(work.task.sequence, None)
                self._next_translation_sequence[work.task.source] = (
                    work.task.sequence + 1
                )
            self._prune_cancelled_sequences_locked(work.task.source)
            self._recognized_available.notify_all()
            self._state_changed.notify_all()

    def _rewritten_feeder(self, source: str | None = None) -> None:
        """Feed completed rewrites to translation in strict source order."""

        while not self._cancel_event.is_set():
            work = self._take_next_rewritten_work(source)
            if work is None:
                return
            if not self._put_translation_work(work):
                return
            self._mark_rewritten_work_enqueued(work)

    def _select_translation_source_locked(self) -> str | None:
        for source, pending in self._translation_queues.items():
            while pending and self._task_cancelled_locked(pending[0].task):
                pending.popleft()

        nonpriority_running = sum(
            count
            for source, count in self._translation_running_by_source.items()
            if source != self._priority_source
        )
        nonpriority_limit = (
            self._translation_concurrency
            if self._priority_source is None or self._translation_concurrency <= 1
            else self._translation_concurrency - 1
        )
        dispatchable = [
            source
            for source in self._sources
            if self._translation_queues[source]
            and (
                source == self._priority_source
                or nonpriority_running < nonpriority_limit
            )
        ]
        if not dispatchable:
            return None

        priority = self._priority_source
        others = [source for source in dispatchable if source != priority]
        if priority is not None and priority in dispatchable and others:
            if self._translation_priority_streak < self._priority_burst:
                self._translation_priority_streak += 1
                return priority
            self._translation_priority_streak = 0
        elif priority is not None and priority in dispatchable:
            self._translation_priority_streak = 0
            return priority
        else:
            self._translation_priority_streak = 0

        candidate_set = set(others or dispatchable)
        for offset in range(len(self._sources)):
            index = (self._translation_round_robin_index + offset) % len(self._sources)
            source = self._sources[index]
            if source in candidate_set:
                self._translation_round_robin_index = (index + 1) % len(self._sources)
                return source
        return next(iter(candidate_set), None)

    def _take_translation_work(self) -> _RecognizedWork | None:
        with self._translation_available:
            while not self._cancel_event.is_set():
                source = self._select_translation_source_locked()
                if source is not None:
                    work = self._translation_queues[source].popleft()
                    self._translation_active_sequences.add(
                        (work.task.source, work.task.sequence)
                    )
                    self._translation_running += 1
                    self._translation_running_by_source[source] += 1
                    self._translation_available.notify_all()
                    self._state_changed.notify_all()
                    return work
                self._translation_available.wait(timeout=0.1)
            return None

    def _finish_translation_work(self, work: _RecognizedWork) -> None:
        with self._translation_available:
            source = work.task.source
            self._translation_running = max(0, self._translation_running - 1)
            self._translation_running_by_source[source] = max(
                0,
                self._translation_running_by_source[source] - 1,
            )
            self._translation_active_sequences.discard(
                (work.task.source, work.task.sequence)
            )
            self._prune_cancelled_sequences_locked(source)
            self._translation_available.notify_all()
            self._state_changed.notify_all()

    def _translation_worker(self, worker_index: int) -> None:
        state_error: BaseException | None = None
        state_initialized = False
        try:
            worker_state = self._translation_state_factory(worker_index)
            state_initialized = True
        except Exception as exc:
            worker_state = None
            state_error = exc
            logger.exception("Realtime translation worker state initialization failed")

        try:
            while True:
                work = self._take_translation_work()
                if work is None:
                    return

                try:
                    if self._cancel_event.is_set() or self._task_cancelled(work.task):
                        continue
                    queue_started = self._clock()
                    queue_wait = max(0.0, queue_started - work.enqueued_at)

                    result: Any = None
                    translation_error: BaseException | None = None
                    translation_started = self._clock()
                    empty_asr = work.error is None and not work.text
                    try:
                        if work.error is not None or empty_asr:
                            pass
                        elif state_error is not None:
                            raise state_error
                        else:
                            result = self._translation_handler(
                                work.task,
                                work.text,
                                worker_state,
                                self._cancel_event,
                            )
                    except Exception as exc:
                        translation_error = exc
                        logger.debug(
                            "Realtime translation failed source=%s sequence=%d",
                            work.task.source,
                            work.task.sequence,
                            exc_info=True,
                        )
                    finished_at = self._clock()
                    completion = RealtimeCompletion(
                        task=work.task,
                        recognized_text=work.recognized_text,
                        rewritten_text=work.text,
                        result=result,
                        asr_error=work.error,
                        rewrite_error=work.rewrite_error,
                        translation_error=translation_error,
                        empty_asr=empty_asr,
                        asr_queue_wait_s=work.asr_queue_wait_s,
                        asr_duration_s=work.asr_duration_s,
                        rewrite_queue_wait_s=work.rewrite_queue_wait_s,
                        rewrite_duration_s=work.rewrite_duration_s,
                        translation_queue_wait_s=queue_wait,
                        translation_duration_s=max(0.0, finished_at - translation_started),
                        completed_at=finished_at,
                    )
                    if not self._cancel_event.is_set():
                        self._store_completion(completion)
                    logger.debug(
                        "Realtime translation finished source=%s sequence=%d queue_wait_ms=%.1f duration_ms=%.1f",
                        work.task.source,
                        work.task.sequence,
                        queue_wait * 1000.0,
                        completion.translation_duration_s * 1000.0,
                    )
                finally:
                    self._finish_translation_work(work)
        finally:
            finalizer = self._translation_state_finalizer
            if state_initialized and callable(finalizer):
                try:
                    finalizer(worker_state)
                except Exception:
                    logger.exception("Realtime translation worker state cleanup failed")

    def _store_completion(self, completion: RealtimeCompletion) -> None:
        with self._delivery_available:
            if self._cancel_event.is_set():
                return
            source = completion.task.source
            sequence = completion.task.sequence
            if sequence < self._next_delivery_sequence[source]:
                return
            existing = self._completed[source].get(sequence)
            if existing is not None and existing.cancelled:
                return
            if self._task_cancelled_locked(completion.task) and not completion.cancelled:
                return
            self._completed[source][sequence] = completion
            self._delivery_available.notify()
            self._state_changed.notify_all()

    def _pop_deliverable_locked(self) -> RealtimeCompletion | None:
        for offset in range(len(self._sources)):
            index = (self._delivery_round_robin_index + offset) % len(self._sources)
            source = self._sources[index]
            expected = self._next_delivery_sequence[source]
            completion = self._completed[source].pop(expected, None)
            if completion is not None:
                self._next_delivery_sequence[source] = expected + 1
                self._delivery_round_robin_index = (index + 1) % len(self._sources)
                return completion
        return None

    def _delivery_worker(self) -> None:
        while not self._cancel_event.is_set():
            with self._delivery_available:
                completion = self._pop_deliverable_locked()
                while completion is None and not self._cancel_event.is_set():
                    self._delivery_available.wait(timeout=0.1)
                    completion = self._pop_deliverable_locked()
                if completion is None:
                    return
                self._delivery_running += 1
                self._state_changed.notify_all()

            try:
                delivery_started_at = self._clock()
                ordered_wait_s = max(
                    0.0,
                    delivery_started_at - completion.completed_at,
                )
                with self._delivery_gate:
                    if self._cancel_event.is_set():
                        return
                    if not completion.cancelled:
                        self._delivery_handler(completion)
                logger.debug(
                    "Realtime delivery source=%s sequence=%d cancelled=%s end_to_end_ms=%.1f",
                    completion.task.source,
                    completion.task.sequence,
                    completion.cancelled,
                    max(0.0, self._clock() - completion.task.submitted_at) * 1000.0,
                )
            except Exception:
                logger.exception(
                    "Realtime delivery callback failed source=%s sequence=%d",
                    completion.task.source,
                    completion.task.sequence,
                )
            finally:
                delivery_finished_at = self._clock()
                logger.info(
                    "Realtime latency source=%s sequence=%d cancelled=%s "
                    "asr_queue_ms=%.1f asr_ms=%.1f rewrite_queue_ms=%.1f "
                    "rewrite_ms=%.1f translation_queue_ms=%.1f "
                    "translation_ms=%.1f ordered_wait_ms=%.1f "
                    "delivery_ms=%.1f end_to_end_ms=%.1f",
                    completion.task.source,
                    completion.task.sequence,
                    completion.cancelled,
                    completion.asr_queue_wait_s * 1000.0,
                    completion.asr_duration_s * 1000.0,
                    completion.rewrite_queue_wait_s * 1000.0,
                    completion.rewrite_duration_s * 1000.0,
                    completion.translation_queue_wait_s * 1000.0,
                    completion.translation_duration_s * 1000.0,
                    ordered_wait_s * 1000.0,
                    max(0.0, delivery_finished_at - delivery_started_at) * 1000.0,
                    max(
                        0.0,
                        delivery_finished_at - completion.task.submitted_at,
                    )
                    * 1000.0,
                )
                with self._state_lock:
                    self._delivery_running = max(0, self._delivery_running - 1)
                    self._outstanding[completion.task.source] = max(
                        0, self._outstanding[completion.task.source] - 1
                    )
                    self._admitted_tasks[completion.task.source].pop(
                        completion.task.sequence,
                        None,
                    )
                    self._prune_cancelled_sequences_locked(
                        completion.task.source
                    )
                    self._state_changed.notify_all()
