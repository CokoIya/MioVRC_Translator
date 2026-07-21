from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


_TRACE_EXTENSION_KEY = "mio_provider_http_trace"


@dataclass
class _RequestTrace:
    request_started_at: float
    first_transport_event_at: float | None = None
    response_headers_at: float | None = None
    event_started_at: dict[str, float] = field(default_factory=dict)
    event_durations_s: dict[str, float] = field(default_factory=dict)
    event_names: set[str] = field(default_factory=set)

    def record(self, name: str, _info: dict[str, Any]) -> None:
        """Record credential-free httpcore trace timestamps."""

        event_name = str(name or "")
        now = time.perf_counter()
        if self.first_transport_event_at is None:
            self.first_transport_event_at = now
        self.event_names.add(event_name)

        if event_name.endswith(".started"):
            self.event_started_at[event_name[: -len(".started")]] = now
            return

        for suffix in (".complete", ".failed"):
            if not event_name.endswith(suffix):
                continue
            base_name = event_name[: -len(suffix)]
            started_at = self.event_started_at.pop(base_name, None)
            if started_at is not None:
                self.event_durations_s[base_name] = (
                    self.event_durations_s.get(base_name, 0.0)
                    + max(0.0, now - started_at)
                )
            if base_name.endswith("receive_response_headers"):
                self.response_headers_at = now
            return


class HttpTimingCapture:
    """Aggregate one logical SDK call that may perform multiple HTTP attempts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: list[_RequestTrace] = []

    def add_request(self, trace: _RequestTrace) -> None:
        with self._lock:
            self._requests.append(trace)

    def metrics(self) -> dict[str, object]:
        with self._lock:
            requests = tuple(self._requests)

        if not requests:
            return {
                "pool_wait_s": None,
                "dns_s": None,
                "tcp_s": None,
                "tls_s": None,
                "response_headers_s": None,
                "provider_processing_s": None,
                "connection_reused": None,
                "http_request_count": 0,
            }

        pool_waits = [
            max(0.0, trace.first_transport_event_at - trace.request_started_at)
            for trace in requests
            if trace.first_transport_event_at is not None
        ]
        header_elapsed = [
            max(0.0, trace.response_headers_at - trace.request_started_at)
            for trace in requests
            if trace.response_headers_at is not None
        ]
        tcp_durations = _matching_durations(requests, "connect_tcp")
        tls_durations = _matching_durations(requests, "start_tls")
        provider_header_waits = _matching_durations(
            requests,
            "receive_response_headers",
        )

        transport_traces = [
            trace
            for trace in requests
            if any(name.endswith("send_request_headers.started") for name in trace.event_names)
        ]
        if transport_traces:
            connection_reused: bool | None = all(
                not any("connect_tcp" in name for name in trace.event_names)
                for trace in transport_traces
            )
        else:
            connection_reused = None

        return {
            "pool_wait_s": sum(pool_waits) if pool_waits else None,
            # httpcore's sync connect_tcp event includes resolver work on the
            # standard backend, so DNS cannot be split out without replacing
            # the transport. Keep DNS explicitly unavailable and expose the
            # measurable connect event as tcp_s.
            "dns_s": None,
            "tcp_s": sum(tcp_durations) if tcp_durations else 0.0,
            "tls_s": sum(tls_durations) if tls_durations else 0.0,
            "response_headers_s": sum(header_elapsed) if header_elapsed else None,
            "provider_processing_s": (
                sum(provider_header_waits) if provider_header_waits else None
            ),
            "connection_reused": connection_reused,
            "http_request_count": len(requests),
        }


def _matching_durations(
    requests: tuple[_RequestTrace, ...],
    event_suffix: str,
) -> list[float]:
    return [
        duration
        for trace in requests
        for name, duration in trace.event_durations_s.items()
        if name.endswith(event_suffix)
    ]


class ProviderHttpTimingHooks:
    """httpx hooks that attach httpcore timing to the active provider call."""

    def __init__(self) -> None:
        self._active_capture: contextvars.ContextVar[HttpTimingCapture | None] = (
            contextvars.ContextVar(
                f"mio_provider_http_timing_{id(self)}",
                default=None,
            )
        )

    @contextmanager
    def capture(self) -> Iterator[HttpTimingCapture]:
        capture = HttpTimingCapture()
        token = self._active_capture.set(capture)
        try:
            yield capture
        finally:
            self._active_capture.reset(token)

    def on_request(self, request: Any) -> None:
        capture = self._active_capture.get()
        if capture is None:
            return
        trace = _RequestTrace(request_started_at=time.perf_counter())
        capture.add_request(trace)

        extensions = getattr(request, "extensions", None)
        if not isinstance(extensions, dict):
            return
        existing_trace = extensions.get("trace")

        def record(name: str, info: dict[str, Any]) -> None:
            trace.record(name, info)
            if callable(existing_trace):
                existing_trace(name, info)

        extensions["trace"] = record
        extensions[_TRACE_EXTENSION_KEY] = trace

    @staticmethod
    def on_response(response: Any) -> None:
        request = getattr(response, "request", None)
        extensions = getattr(request, "extensions", None)
        if not isinstance(extensions, dict):
            return
        trace = extensions.get(_TRACE_EXTENSION_KEY)
        if isinstance(trace, _RequestTrace) and trace.response_headers_at is None:
            trace.response_headers_at = time.perf_counter()
