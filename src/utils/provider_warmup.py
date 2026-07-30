"""Credential-safe, non-inference provider connection warm-up helpers.

The helpers in this module deliberately operate on caller-owned HTTP clients.
That lets a provider pay DNS/TCP/TLS and connection-pool setup on the same
client that will serve its first real request.  Warm-up requests are restricted
to ``HEAD`` and ``GET`` and never follow redirects. ``GET`` responses are
drained only up to a small fixed limit so ordinary metadata responses can
return their socket to the caller's pool without allowing unbounded reads.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


_ALLOWED_METHODS = frozenset({"GET", "HEAD"})
_MIN_TIMEOUT_SECONDS = 0.1
_MAX_TIMEOUT_SECONDS = 5.0
_MAX_DRAIN_BYTES = 256 * 1024
_DNS_RESOLVER_SLOTS = threading.BoundedSemaphore(8)


@dataclass(frozen=True)
class ProviderWarmupResult:
    """Sanitized outcome of a provider transport warm-up attempt."""

    attempted: bool
    succeeded: bool
    elapsed_s: float
    status_code: int | None = None
    error_type: str = ""
    connection_reusable: bool = False

    def __bool__(self) -> bool:
        return self.succeeded


def _bounded_timeout(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 2.0
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        parsed = 2.0
    return max(_MIN_TIMEOUT_SECONDS, min(parsed, _MAX_TIMEOUT_SECONDS))


def _validated_http_url(url: object) -> str:
    candidate = str(url or "").strip()
    if not candidate or len(candidate) > 4096:
        raise ValueError("Provider warm-up URL is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise ValueError("Provider warm-up URL is invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Provider warm-up URL is invalid") from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("Provider warm-up URL is invalid")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Provider warm-up URL is invalid")
    return candidate


def _validated_method(method: object) -> str:
    normalized = str(method or "HEAD").strip().upper()
    if normalized not in _ALLOWED_METHODS:
        raise ValueError("Provider warm-up only supports HEAD and GET")
    return normalized


def _safe_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}
    prepared: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name or "").strip()
        value = str(raw_value or "")
        if (
            not name
            or any(ord(char) < 33 or ord(char) == 127 for char in name)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            continue
        if name.casefold() in {
            "connection",
            "content-length",
            "host",
            "proxy-authorization",
            "transfer-encoding",
        }:
            continue
        prepared[name] = value
    return prepared


def _failure(started_at: float, exc: BaseException, *, attempted: bool) -> ProviderWarmupResult:
    return ProviderWarmupResult(
        attempted=attempted,
        succeeded=False,
        elapsed_s=max(0.0, time.monotonic() - started_at),
        error_type=exc.__class__.__name__,
    )


def _drain_requests_response(response: object, method: str) -> bool:
    if method == "HEAD":
        return True
    iter_content = getattr(response, "iter_content", None)
    if not callable(iter_content):
        return False
    consumed = 0
    for chunk in iter_content(chunk_size=8192):
        consumed += len(chunk or b"")
        if consumed > _MAX_DRAIN_BYTES:
            return False
    return True


def _drain_httpx_response(response: object, method: str) -> bool:
    if method == "HEAD":
        return True
    iter_bytes = getattr(response, "iter_bytes", None)
    if not callable(iter_bytes):
        return False
    consumed = 0
    for chunk in iter_bytes(chunk_size=8192):
        consumed += len(chunk or b"")
        if consumed > _MAX_DRAIN_BYTES:
            return False
    return True


async def _drain_async_httpx_response(response: object, method: str) -> bool:
    if method == "HEAD":
        return True
    aiter_bytes = getattr(response, "aiter_bytes", None)
    if not callable(aiter_bytes):
        return False
    consumed = 0
    async for chunk in aiter_bytes(chunk_size=8192):
        consumed += len(chunk or b"")
        if consumed > _MAX_DRAIN_BYTES:
            return False
    return True


def warmup_requests_session(
    session: object,
    url: str,
    *,
    method: str = "HEAD",
    headers: Mapping[str, object] | None = None,
    timeout_s: float = 2.0,
) -> ProviderWarmupResult:
    """Warm a caller-owned ``requests.Session``."""

    started_at = time.monotonic()
    try:
        target = _validated_http_url(url)
        request_method = _validated_method(method)
        timeout = _bounded_timeout(timeout_s)
    except Exception as exc:
        return _failure(started_at, exc, attempted=False)

    response = None
    try:
        request = getattr(session, "request")
        response = request(
            request_method,
            target,
            headers=_safe_headers(headers),
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        status_code = int(getattr(response, "status_code", 0) or 0) or None
        connection_reusable = _drain_requests_response(response, request_method)
        return ProviderWarmupResult(
            attempted=True,
            succeeded=True,
            elapsed_s=max(0.0, time.monotonic() - started_at),
            status_code=status_code,
            connection_reusable=connection_reusable,
        )
    except Exception as exc:
        return _failure(started_at, exc, attempted=True)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def warmup_httpx_client(
    client: object,
    url: str,
    *,
    method: str = "HEAD",
    headers: Mapping[str, object] | None = None,
    timeout_s: float = 2.0,
) -> ProviderWarmupResult:
    """Warm a caller-owned synchronous ``httpx.Client``."""

    started_at = time.monotonic()
    try:
        target = _validated_http_url(url)
        request_method = _validated_method(method)
        timeout = _bounded_timeout(timeout_s)
    except Exception as exc:
        return _failure(started_at, exc, attempted=False)

    try:
        stream = getattr(client, "stream")
        with stream(
            request_method,
            target,
            headers=_safe_headers(headers),
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status_code = int(getattr(response, "status_code", 0) or 0) or None
            connection_reusable = _drain_httpx_response(
                response,
                request_method,
            )
        return ProviderWarmupResult(
            attempted=True,
            succeeded=True,
            elapsed_s=max(0.0, time.monotonic() - started_at),
            status_code=status_code,
            connection_reusable=connection_reusable,
        )
    except Exception as exc:
        return _failure(started_at, exc, attempted=True)


async def warmup_async_httpx_client(
    client: object,
    url: str,
    *,
    method: str = "HEAD",
    headers: Mapping[str, object] | None = None,
    timeout_s: float = 2.0,
) -> ProviderWarmupResult:
    """Warm a caller-owned ``httpx.AsyncClient``."""

    started_at = time.monotonic()
    try:
        target = _validated_http_url(url)
        request_method = _validated_method(method)
        timeout = _bounded_timeout(timeout_s)
    except Exception as exc:
        return _failure(started_at, exc, attempted=False)

    try:
        stream = getattr(client, "stream")
        async with stream(
            request_method,
            target,
            headers=_safe_headers(headers),
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status_code = int(getattr(response, "status_code", 0) or 0) or None
            connection_reusable = await _drain_async_httpx_response(
                response,
                request_method,
            )
        return ProviderWarmupResult(
            attempted=True,
            succeeded=True,
            elapsed_s=max(0.0, time.monotonic() - started_at),
            status_code=status_code,
            connection_reusable=connection_reusable,
        )
    except Exception as exc:
        return _failure(started_at, exc, attempted=True)


def warmup_dns_origin(
    url: str,
    *,
    timeout_s: float = 2.0,
) -> ProviderWarmupResult:
    """Bound DNS preparation for clients, such as gTTS, with no reusable pool."""

    started_at = time.monotonic()
    try:
        target = _validated_http_url(url)
        parsed = urlsplit(target)
        hostname = str(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        timeout = _bounded_timeout(timeout_s)
    except Exception as exc:
        return _failure(started_at, exc, attempted=False)

    if not _DNS_RESOLVER_SLOTS.acquire(blocking=False):
        return ProviderWarmupResult(
            attempted=False,
            succeeded=False,
            elapsed_s=max(0.0, time.monotonic() - started_at),
            error_type="ResolverCapacityError",
        )

    completed = threading.Event()
    outcome: dict[str, object] = {}

    def resolve() -> None:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
            outcome["succeeded"] = bool(records)
        except Exception as exc:
            outcome["error_type"] = exc.__class__.__name__
        finally:
            _DNS_RESOLVER_SLOTS.release()
            completed.set()

    resolver = threading.Thread(
        target=resolve,
        daemon=True,
        name="provider-warmup-dns",
    )
    try:
        resolver.start()
    except Exception as exc:
        _DNS_RESOLVER_SLOTS.release()
        return _failure(started_at, exc, attempted=False)

    if not completed.wait(timeout):
        return ProviderWarmupResult(
            attempted=True,
            succeeded=False,
            elapsed_s=max(0.0, time.monotonic() - started_at),
            error_type="TimeoutError",
        )
    return ProviderWarmupResult(
        attempted=True,
        succeeded=bool(outcome.get("succeeded")),
        elapsed_s=max(0.0, time.monotonic() - started_at),
        error_type=str(outcome.get("error_type") or ""),
    )
