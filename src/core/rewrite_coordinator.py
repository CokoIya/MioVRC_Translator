from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar


REWRITE_PRIORITY_REALTIME = "realtime"
REWRITE_PRIORITY_TYPED = "typed"
_VALID_PRIORITIES = frozenset(
    {REWRITE_PRIORITY_REALTIME, REWRITE_PRIORITY_TYPED}
)

_ResultT = TypeVar("_ResultT")
_RewriteKey = tuple[str, str, str, str]


class RewriteCoordinatorError(RuntimeError):
    """Base class for local rewrite admission failures."""


class RewriteCoordinatorFullError(RewriteCoordinatorError):
    """Raised when the bounded leader queue has no remaining capacity."""


class RewriteCoordinatorTimeoutError(RewriteCoordinatorError, TimeoutError):
    """Raised when a caller cannot be served within its local wait budget."""


class RewriteCoordinatorClosedError(RewriteCoordinatorError):
    """Raised after the coordinator has begun shutting down."""


@dataclass(frozen=True, slots=True)
class RewriteCoordinatorSnapshot:
    active_calls: int
    pending_realtime: int
    pending_typed: int
    inflight: int
    cache_entries: int
    closed: bool


@dataclass(slots=True, eq=False)
class _Flight:
    key: _RewriteKey
    priority: str
    state: str = "pending"
    done: bool = False
    result: Any = None
    error: BaseException | None = None
    followers: int = 0


def _update_runtime_signature(hasher: Any, value: object) -> None:
    """Hash configuration data without retaining a plaintext serialization."""

    if isinstance(value, Mapping):
        hasher.update(b"{")
        entries = sorted(
            ((str(key), item) for key, item in value.items()),
            key=lambda entry: entry[0],
        )
        for key, item in entries:
            _update_runtime_signature(hasher, key)
            _update_runtime_signature(hasher, item)
        hasher.update(b"}")
        return
    if isinstance(value, (list, tuple)):
        hasher.update(b"[")
        for item in value:
            _update_runtime_signature(hasher, item)
        hasher.update(b"]")
        return
    if isinstance(value, (set, frozenset)):
        hasher.update(b"<")
        item_hashes: list[bytes] = []
        for item in value:
            item_hasher = hashlib.sha256()
            _update_runtime_signature(item_hasher, item)
            item_hashes.append(item_hasher.digest())
        for digest in sorted(item_hashes):
            hasher.update(digest)
        hasher.update(b">")
        return
    if isinstance(value, bytes):
        hasher.update(b"b")
        hasher.update(value)
        hasher.update(b";")
        return
    if isinstance(value, bytearray):
        hasher.update(b"b")
        hasher.update(bytes(value))
        hasher.update(b";")
        return
    if value is None:
        hasher.update(b"n;")
        return
    if isinstance(value, bool):
        hasher.update(b"t;" if value else b"f;")
        return
    if isinstance(value, int):
        hasher.update(f"i{value};".encode("ascii"))
        return
    if isinstance(value, float):
        number = value if math.isfinite(value) else str(value)
        hasher.update(f"d{number};".encode("ascii"))
        return

    text = str(value)
    encoded = text.encode("utf-8", errors="surrogatepass")
    hasher.update(f"s{len(encoded)}:".encode("ascii"))
    hasher.update(encoded)
    hasher.update(b";")


def runtime_translation_config_signature(config: Mapping[str, object] | object) -> str:
    """Return a credential-safe digest of the active translation configuration."""

    translation: object = {}
    if isinstance(config, Mapping):
        candidate = config.get("translation", {})
        translation = candidate if isinstance(candidate, Mapping) else {}
    hasher = hashlib.sha256()
    _update_runtime_signature(hasher, translation)
    return hasher.hexdigest()


def provider_runtime_config_signature(
    config: Mapping[str, object] | object,
) -> str:
    """Digest only settings that can change provider/client construction."""

    translation: Mapping[str, object] = {}
    if isinstance(config, Mapping):
        candidate = config.get("translation", {})
        if isinstance(candidate, Mapping):
            translation = candidate

    backend = str(translation.get("backend", "") or "").strip()
    raw_fallbacks = translation.get("fallback_backends", ())
    if isinstance(raw_fallbacks, str):
        fallbacks = tuple(
            item.strip()
            for item in raw_fallbacks.replace(";", ",").split(",")
            if item.strip()
        )
    elif isinstance(raw_fallbacks, (list, tuple)):
        fallbacks = tuple(str(item or "").strip() for item in raw_fallbacks if str(item or "").strip())
    else:
        fallbacks = ()

    provider_config: dict[str, object] = {
        "backend": backend,
        "fallback_backends": fallbacks,
        "realtime_timeout_s": translation.get("realtime_timeout_s"),
    }
    for provider in dict.fromkeys((backend, *fallbacks)):
        section = translation.get(provider, {})
        provider_config[provider] = section if isinstance(section, Mapping) else {}

    hasher = hashlib.sha256()
    _update_runtime_signature(hasher, provider_config)
    return hasher.hexdigest()


def _rewrite_key(
    *,
    config: Mapping[str, object] | object,
    style: object,
    language_hint: object,
    text: object,
) -> _RewriteKey:
    source_digest = hashlib.sha256(
        str(text or "").encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return (
        provider_runtime_config_signature(config),
        str(style or "").strip().lower(),
        str(language_hint or "auto").strip().lower() or "auto",
        source_digest,
    )


class RewriteCallCoordinator:
    """Bound, prioritize, cache, and coalesce caller-executed rewrite calls.

    The coordinator deliberately owns no worker pool. A caller admitted as the
    leader executes its provider operation on its existing worker thread, which
    preserves provider-client confinement and connection reuse.
    """

    def __init__(
        self,
        *,
        max_active_calls: int = 2,
        max_pending_leaders: int = 8,
        cache_size: int = 256,
        realtime_burst: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_active_calls < 1:
            raise ValueError("max_active_calls must be at least one")
        if max_pending_leaders < 1:
            raise ValueError("max_pending_leaders must be at least one")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        if realtime_burst < 1:
            raise ValueError("realtime_burst must be at least one")

        self._max_active_calls = int(max_active_calls)
        self._max_pending_leaders = int(max_pending_leaders)
        self._cache_size = int(cache_size)
        self._realtime_burst = int(realtime_burst)
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[str, deque[_Flight]] = {
            REWRITE_PRIORITY_REALTIME: deque(),
            REWRITE_PRIORITY_TYPED: deque(),
        }
        self._flights: dict[_RewriteKey, _Flight] = {}
        self._cache: OrderedDict[_RewriteKey, Any] = OrderedDict()
        self._active_calls = 0
        self._realtime_streak = 0
        self._closed = False

    def snapshot(self) -> RewriteCoordinatorSnapshot:
        with self._condition:
            return RewriteCoordinatorSnapshot(
                active_calls=self._active_calls,
                pending_realtime=len(self._pending[REWRITE_PRIORITY_REALTIME]),
                pending_typed=len(self._pending[REWRITE_PRIORITY_TYPED]),
                inflight=len(self._flights),
                cache_entries=len(self._cache),
                closed=self._closed,
            )

    def execute(
        self,
        *,
        priority: str,
        config: Mapping[str, object] | object,
        style: object,
        language_hint: object,
        text: object,
        operation: Callable[[], _ResultT],
        wait_timeout_s: float | None,
    ) -> _ResultT:
        normalized_priority = str(priority or "").strip().lower()
        if normalized_priority not in _VALID_PRIORITIES:
            raise ValueError(f"Unsupported rewrite priority: {priority!r}")
        if not callable(operation):
            raise TypeError("operation must be callable")
        deadline = self._deadline(wait_timeout_s)
        key = _rewrite_key(
            config=config,
            style=style,
            language_hint=language_hint,
            text=text,
        )

        with self._condition:
            self._raise_if_closed_locked()
            cached = self._cache_get_locked(key)
            if cached[0]:
                return cached[1]

            flight = self._flights.get(key)
            if flight is not None:
                return self._wait_for_follower_locked(flight, deadline)

            self._admit_locked()
            if self._pending_count_locked() >= self._max_pending_leaders:
                raise RewriteCoordinatorFullError(
                    "The rewrite request queue is full"
                )
            flight = _Flight(key=key, priority=normalized_priority)
            self._flights[key] = flight
            self._pending[normalized_priority].append(flight)
            self._admit_locked()

            while flight.state == "pending" and not flight.done:
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    error = RewriteCoordinatorTimeoutError(
                        "Timed out waiting for rewrite provider capacity"
                    )
                    self._fail_pending_locked(flight, error)
                    raise error
                self._condition.wait(timeout=remaining)

            if flight.done:
                self._raise_flight_error(flight)
                return flight.result
            self._raise_if_closed_locked()
            flight.state = "running"

        try:
            result = operation()
        except BaseException as exc:
            self._finish_leader(flight, error=exc)
            raise
        return self._finish_leader(flight, result=result)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            error = RewriteCoordinatorClosedError("Rewrite coordinator is closed")
            self._cache.clear()
            for queue_ in self._pending.values():
                queue_.clear()
            for flight in tuple(self._flights.values()):
                if flight.done:
                    continue
                flight.done = True
                flight.error = error
                flight.state = "done"
            self._flights.clear()
            self._condition.notify_all()

    def _finish_leader(
        self,
        flight: _Flight,
        *,
        result: _ResultT | None = None,
        error: BaseException | None = None,
    ) -> _ResultT:
        with self._condition:
            self._active_calls = max(0, self._active_calls - 1)
            if not flight.done:
                flight.result = result
                flight.error = error
                flight.done = True
                flight.state = "done"
                if self._flights.get(flight.key) is flight:
                    self._flights.pop(flight.key, None)
                if error is None and not self._closed:
                    self._cache_put_locked(flight.key, result)
            self._admit_locked()
            self._condition.notify_all()
            self._raise_flight_error(flight)
            return flight.result

    def _wait_for_follower_locked(
        self,
        flight: _Flight,
        deadline: float | None,
    ) -> _ResultT:
        flight.followers += 1
        try:
            while not flight.done:
                if self._closed:
                    raise RewriteCoordinatorClosedError(
                        "Rewrite coordinator is closed"
                    )
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    raise RewriteCoordinatorTimeoutError(
                        "Timed out waiting for an in-flight rewrite result"
                    )
                self._condition.wait(timeout=remaining)
            self._raise_flight_error(flight)
            return flight.result
        finally:
            flight.followers = max(0, flight.followers - 1)

    def _admit_locked(self) -> None:
        if self._closed:
            return
        admitted = False
        while self._active_calls < self._max_active_calls:
            flight = self._next_pending_locked()
            if flight is None:
                break
            flight.state = "admitted"
            self._active_calls += 1
            admitted = True
        if admitted:
            self._condition.notify_all()

    def _next_pending_locked(self) -> _Flight | None:
        realtime = self._pending[REWRITE_PRIORITY_REALTIME]
        typed = self._pending[REWRITE_PRIORITY_TYPED]
        if realtime and typed:
            if self._realtime_streak < self._realtime_burst:
                self._realtime_streak += 1
                return realtime.popleft()
            self._realtime_streak = 0
            return typed.popleft()
        if realtime:
            self._realtime_streak = 0
            return realtime.popleft()
        if typed:
            self._realtime_streak = 0
            return typed.popleft()
        return None

    def _fail_pending_locked(
        self,
        flight: _Flight,
        error: BaseException,
    ) -> None:
        if flight.state == "pending":
            try:
                self._pending[flight.priority].remove(flight)
            except ValueError:
                pass
        flight.done = True
        flight.error = error
        flight.state = "done"
        if self._flights.get(flight.key) is flight:
            self._flights.pop(flight.key, None)
        self._admit_locked()
        self._condition.notify_all()

    def _cache_get_locked(self, key: _RewriteKey) -> tuple[bool, Any]:
        try:
            value = self._cache.pop(key)
        except KeyError:
            return False, None
        self._cache[key] = value
        return True, value

    def _cache_put_locked(self, key: _RewriteKey, value: object) -> None:
        if self._cache_size <= 0:
            return
        self._cache.pop(key, None)
        self._cache[key] = value
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _pending_count_locked(self) -> int:
        return sum(len(queue_) for queue_ in self._pending.values())

    def _raise_if_closed_locked(self) -> None:
        if self._closed:
            raise RewriteCoordinatorClosedError("Rewrite coordinator is closed")

    @staticmethod
    def _raise_flight_error(flight: _Flight) -> None:
        if flight.error is not None:
            raise flight.error

    def _deadline(self, wait_timeout_s: float | None) -> float | None:
        if wait_timeout_s is None:
            return None
        try:
            timeout = max(float(wait_timeout_s), 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("wait_timeout_s must be a number or None") from exc
        return self._clock() + timeout

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock())
