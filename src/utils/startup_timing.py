"""Low-overhead startup timing instrumentation.

The entry point begins collecting timing data before file logging is ready.
Events are buffered until :func:`enable_startup_timing_logging` is called, then
new events are emitted immediately.  The module intentionally has no project
imports so it is safe to use on the earliest startup path.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math
import threading
import time
from typing import Iterator


@dataclass(frozen=True, slots=True)
class StartupTimingEvent:
    """A completed startup stage measured against the process entry point."""

    stage: str
    duration_ms: float
    total_ms: float
    thread: str
    outcome: str


_LOCK = threading.RLock()
_EMIT_LOCK = threading.Lock()
_ORIGIN = time.perf_counter()
_ORIGIN_EXPLICIT = False
_EVENTS: list[StartupTimingEvent] = []
_PENDING_EVENTS: list[StartupTimingEvent] = []
_LOGGING_ENABLED = False
_LOGGER = logging.getLogger("mio.startup")


def initialize_startup_timing(origin: float | None = None) -> None:
    """Set the earliest known monotonic startup timestamp.

    The first explicit origin wins so importing a UI module later cannot move
    the process origin forward.  Passing ``None`` simply preserves the module's
    import timestamp.
    """

    global _ORIGIN, _ORIGIN_EXPLICIT
    if origin is None:
        return
    normalized = float(origin)
    if not math.isfinite(normalized):
        raise ValueError("startup timing origin must be finite")
    with _LOCK:
        if _ORIGIN_EXPLICIT:
            return
        _ORIGIN = normalized
        _ORIGIN_EXPLICIT = True


def record_startup_stage(
    stage: str,
    *,
    started_at: float | None = None,
    finished_at: float | None = None,
    outcome: str = "ok",
) -> StartupTimingEvent:
    """Record a completed stage and emit it when logging is available."""

    stage_name = str(stage or "").strip()
    if not stage_name:
        raise ValueError("startup timing stage must not be empty")
    outcome_name = str(outcome or "unknown").strip() or "unknown"
    finished = time.perf_counter() if finished_at is None else float(finished_at)
    with _LOCK:
        origin = _ORIGIN
        start = finished if started_at is None else float(started_at)
        event = StartupTimingEvent(
            stage=stage_name,
            duration_ms=max(0.0, (finished - start) * 1000.0),
            total_ms=max(0.0, (finished - origin) * 1000.0),
            thread=threading.current_thread().name,
            outcome=outcome_name,
        )
        _EVENTS.append(event)
        logging_enabled = _LOGGING_ENABLED
        if not logging_enabled:
            _PENDING_EVENTS.append(event)
    if logging_enabled:
        with _EMIT_LOCK:
            _emit_event(event)
    return event


@contextmanager
def startup_stage(stage: str) -> Iterator[None]:
    """Measure a startup stage and record failures without swallowing them."""

    started_at = time.perf_counter()
    try:
        yield
    except BaseException:
        record_startup_stage(stage, started_at=started_at, outcome="error")
        raise
    else:
        record_startup_stage(stage, started_at=started_at)


def enable_startup_timing_logging(
    logger: logging.Logger | None = None,
) -> None:
    """Enable structured timing logs and flush all buffered early events."""

    global _LOGGING_ENABLED, _LOGGER
    with _EMIT_LOCK:
        with _LOCK:
            if logger is not None:
                _LOGGER = logger
            _LOGGING_ENABLED = True
            pending = tuple(_PENDING_EVENTS)
            _PENDING_EVENTS.clear()
        for event in pending:
            _emit_event(event)


def get_startup_timing_events() -> tuple[StartupTimingEvent, ...]:
    """Return an immutable snapshot for diagnostics and focused tests."""

    with _LOCK:
        return tuple(_EVENTS)


def _emit_event(event: StartupTimingEvent) -> None:
    _LOGGER.info(
        "Startup timing stage=%s duration_ms=%.1f total_ms=%.1f thread=%s outcome=%s",
        event.stage,
        event.duration_ms,
        event.total_ms,
        event.thread,
        event.outcome,
    )


def _reset_startup_timing_for_tests(origin: float | None = None) -> None:
    """Reset global state.  Intended only for isolated unit tests."""

    global _ORIGIN, _ORIGIN_EXPLICIT, _LOGGING_ENABLED, _LOGGER
    with _EMIT_LOCK:
        with _LOCK:
            _ORIGIN = time.perf_counter() if origin is None else float(origin)
            _ORIGIN_EXPLICIT = origin is not None
            _EVENTS.clear()
            _PENDING_EVENTS.clear()
            _LOGGING_ENABLED = False
            _LOGGER = logging.getLogger("mio.startup")
