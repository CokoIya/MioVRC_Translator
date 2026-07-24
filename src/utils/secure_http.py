"""Small HTTPS helpers for bounded, allowlisted configuration downloads."""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import requests

from src.utils.provider_network import is_local_provider_address, is_local_provider_host

_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_REDIRECT_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization"}
)


def validate_api_base_url(
    url: str,
    *,
    label: str = "API",
    allow_private_http: bool = False,
) -> str:
    """Validate a base URL before attaching credentials to an API client.

    Public endpoints must use HTTPS. Plain HTTP is permitted for explicit
    loopback/private literal addresses when the caller has opted into a
    self-hosted endpoint. This supports LAN model servers, including servers
    that authenticate requests with their own API key, without trusting
    arbitrary hostnames as local destinations.
    """

    candidate = str(url or "").strip()
    if not candidate:
        raise ValueError(f"{label} base URL is not configured")
    if "\\" in candidate or any(char.isspace() or ord(char) < 32 for char in candidate):
        raise ValueError(f"{label} base URL is malformed")

    parsed = urllib.parse.urlsplit(candidate)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").rstrip(".").casefold()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} base URL has an invalid port") from exc

    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or port == 0
    ):
        raise ValueError(f"{label} base URL is malformed")

    if scheme == "http":
        is_loopback_name = host == "localhost" or host.endswith(".localhost")
        is_allowed_local = False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            is_loopback_name = address.is_loopback
            is_allowed_local = is_local_provider_address(
                address,
                include_shared=True,
            )
        elif allow_private_http:
            is_allowed_local = is_local_provider_host(host)
        if not is_loopback_name and not (allow_private_http and is_allowed_local):
            raise ValueError(
                f"{label} base URL must use HTTPS or an explicit loopback/private local HTTP host"
            )

    return urllib.parse.urlunsplit(parsed).rstrip("/")


def validate_trusted_https_url(
    url: str,
    *,
    trusted_hosts: Collection[str],
    label: str,
) -> str:
    """Validate an HTTPS URL against an explicit hostname allowlist."""

    candidate = str(url or "").strip()
    parsed = urllib.parse.urlsplit(candidate)
    host = (parsed.hostname or "").rstrip(".").casefold()
    allowed = {str(item or "").rstrip(".").casefold() for item in trusted_hosts}
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} URL has an invalid port") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or host not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise RuntimeError(f"{label} URL is not from a trusted HTTPS source: {candidate}")
    return urllib.parse.urlunsplit(parsed)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@contextmanager
def open_trusted_https_url(
    url: str,
    *,
    trusted_hosts: Collection[str],
    timeout: float,
    label: str,
    max_redirects: int = 5,
    headers: Mapping[str, str] | None = None,
) -> Iterator[Any]:
    """Open a trusted URL while validating every redirect before following it."""

    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    current = validate_trusted_https_url(
        url,
        trusted_hosts=trusted_hosts,
        label=label,
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request_headers = {str(key): str(value) for key, value in (headers or {}).items()}
    request_headers["Accept-Encoding"] = "identity"
    response = None
    try:
        for redirect_index in range(max_redirects + 1):
            try:
                request = urllib.request.Request(
                    current,
                    headers=_redirect_headers(
                        request_headers,
                        initial_url=url,
                        current_url=current,
                    ),
                )
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in _REDIRECT_STATUS_CODES:
                    raise
                location = exc.headers.get("Location")
                exc.close()
                if not location:
                    raise RuntimeError(f"{label} redirect is missing a Location header")
                if redirect_index >= max_redirects:
                    raise RuntimeError(f"{label} exceeded the redirect limit")
                current = validate_trusted_https_url(
                    urllib.parse.urljoin(current, location),
                    trusted_hosts=trusted_hosts,
                    label=f"{label} redirect",
                )
                continue

            final_url_getter = getattr(response, "geturl", None)
            final_url = (
                str(final_url_getter())
                if callable(final_url_getter)
                else current
            )
            validate_trusted_https_url(
                final_url,
                trusted_hosts=trusted_hosts,
                label=f"{label} response",
            )
            yield response
            return
        raise RuntimeError(f"{label} exceeded the redirect limit")
    finally:
        if response is not None:
            response.close()


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    items = getattr(headers, "items", None)
    if callable(items):
        target = name.casefold()
        for key, value in items():
            if str(key).casefold() == target:
                return str(value)
    return None


def _require_validated_url(
    url: str,
    *,
    validator: Callable[[str], bool],
    label: str,
    stage: str,
) -> str:
    candidate = str(url or "").strip()
    try:
        trusted = bool(validator(candidate))
    except Exception as exc:
        raise RuntimeError(f"{label} {stage} URL validation failed") from exc
    if not trusted:
        raise RuntimeError(f"{label} {stage} URL is not trusted")
    return candidate


def _redirect_headers(
    headers: Mapping[str, str] | None,
    *,
    initial_url: str,
    current_url: str,
) -> dict[str, str]:
    prepared = {str(key): str(value) for key, value in (headers or {}).items()}
    initial = urllib.parse.urlsplit(initial_url)
    current = urllib.parse.urlsplit(current_url)
    initial_origin = (
        initial.scheme.casefold(),
        (initial.hostname or "").rstrip(".").casefold(),
        initial.port or 443,
    )
    current_origin = (
        current.scheme.casefold(),
        (current.hostname or "").rstrip(".").casefold(),
        current.port or 443,
    )
    if current_origin != initial_origin:
        prepared = {
            key: value
            for key, value in prepared.items()
            if key.casefold() not in _SENSITIVE_REDIRECT_HEADERS
        }
    return prepared


@contextmanager
def open_validated_requests_response(
    url: str,
    *,
    url_validator: Callable[[str], bool],
    timeout: float | tuple[float, float],
    label: str,
    headers: Mapping[str, str] | None = None,
    stream: bool = True,
    max_redirects: int = 5,
    request_get: Callable[..., Any] | None = None,
) -> Iterator[Any]:
    """Issue a GET while validating every redirect before it is requested.

    ``requests`` normally follows a redirect before callers can inspect the next
    target.  This helper disables that behavior, validates each absolute target,
    enforces a redirect bound, strips sensitive headers on origin changes, and
    closes every intermediate and terminal response on all exit paths.
    """

    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    get = requests.get if request_get is None else request_get
    initial = _require_validated_url(
        url,
        validator=url_validator,
        label=label,
        stage="initial",
    )
    current = initial
    response = None
    try:
        for redirect_index in range(max_redirects + 1):
            response = get(
                current,
                headers=_redirect_headers(
                    headers,
                    initial_url=initial,
                    current_url=current,
                ),
                timeout=timeout,
                stream=stream,
                allow_redirects=False,
            )
            response_url = _require_validated_url(
                getattr(response, "url", None) or current,
                validator=url_validator,
                label=label,
                stage="response",
            )
            status_code = int(getattr(response, "status_code", 200) or 0)
            if status_code not in _REDIRECT_STATUS_CODES:
                yield response
                return

            location = _response_header(response, "Location")
            if not location:
                raise RuntimeError(f"{label} redirect is missing a Location header")
            if redirect_index >= max_redirects:
                raise RuntimeError(f"{label} exceeded the redirect limit")
            current = _require_validated_url(
                urllib.parse.urljoin(response_url, location),
                validator=url_validator,
                label=label,
                stage="redirect",
            )
            close = getattr(response, "close", None)
            if callable(close):
                close()
            response = None
        raise RuntimeError(f"{label} exceeded the redirect limit")
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                close()


def read_bounded_response(response: Any, *, limit: int, label: str) -> bytes:
    """Read at most ``limit`` bytes, rejecting dishonest or oversized responses."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    content_encoding = (_response_header(response, "Content-Encoding") or "").strip()
    if content_encoding and content_encoding.casefold() != "identity":
        raise RuntimeError(f"{label} uses an unsupported Content-Encoding")
    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if headers is not None else None
    declared_length: int | None = None
    if content_length not in (None, ""):
        try:
            declared_length = int(str(content_length).strip())
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} has an invalid Content-Length") from exc
        if declared_length < 0:
            raise RuntimeError(f"{label} has an invalid Content-Length")
        if declared_length > limit:
            raise RuntimeError(f"{label} exceeds the maximum allowed size")

    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError(f"{label} exceeds the maximum allowed size")
    if declared_length is not None and len(payload) != declared_length:
        raise RuntimeError(f"{label} body length does not match Content-Length")
    return payload


def read_bounded_requests_response(response: Any, *, limit: int, label: str) -> bytes:
    """Stream a bounded identity-encoded ``requests`` response into memory."""

    if limit < 0:
        raise ValueError("limit must be non-negative")

    content_encoding = (_response_header(response, "Content-Encoding") or "").strip()
    if content_encoding and content_encoding.casefold() != "identity":
        raise RuntimeError(f"{label} uses an unsupported Content-Encoding")

    content_length = (_response_header(response, "Content-Length") or "").strip()
    declared_length: int | None = None
    if content_length:
        if not re.fullmatch(r"[0-9]+", content_length):
            raise RuntimeError(f"{label} has an invalid Content-Length")
        declared_length = int(content_length)
        if declared_length > limit:
            raise RuntimeError(f"{label} exceeds the maximum allowed size")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=min(65_536, max(1, limit + 1))):
        if not chunk:
            continue
        payload = bytes(chunk)
        total += len(payload)
        if total > limit:
            raise RuntimeError(f"{label} exceeds the maximum allowed size")
        chunks.append(payload)

    if declared_length is not None and total != declared_length:
        raise RuntimeError(f"{label} body length does not match Content-Length")
    return b"".join(chunks)
