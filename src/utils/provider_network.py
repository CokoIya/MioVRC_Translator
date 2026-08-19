"""Shared connection policy for provider HTTP clients.

Public provider endpoints continue to honor environment/system proxy settings.
Explicit local endpoints connect directly so a desktop proxy cannot intercept
or delay loopback and private-LAN model traffic.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
import ssl
import threading
from urllib.parse import urlsplit

import requests

try:
    import truststore as _truststore
except ImportError:  # pragma: no cover - release builds require this dependency.
    _truststore = None


_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429})
_SHARED_LOCAL_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_SPECIAL_LOCAL_HOSTS = frozenset(
    {
        "host.docker.internal",
        "gateway.docker.internal",
    }
)
_LOOPBACK_HOSTS = frozenset({"localhost"})


class SystemTrustHTTPAdapter(requests.adapters.HTTPAdapter):
    """Requests adapter that verifies HTTPS with the native system trust store.

    Requests normally supplies urllib3 with Certifi's bundled roots. That is a
    useful portable default, but on Windows it bypasses the certificate chain
    engine used by the operating system and therefore cannot retrieve a
    missing intermediate certificate or honor enterprise roots installed by
    an administrator. Truststore keeps hostname and certificate verification
    enabled while delegating chain construction to the platform.

    Explicit ``verify=/path/to/ca.pem`` values are loaded in addition to the
    system roots, so private PKI and the standard Requests CA-bundle
    environment variables continue to work.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._mio_ssl_contexts: dict[tuple[str, str], ssl.SSLContext] = {}
        self._mio_ssl_contexts_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def build_connection_pool_key_attributes(
        self,
        request: requests.PreparedRequest,
        verify: object,
        cert: object = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        try:
            scheme = urlsplit(str(request.url or "")).scheme.casefold()
        except (TypeError, ValueError):
            scheme = ""
        if scheme != "https" or verify is False:
            return host_params, pool_kwargs

        ca_file, ca_dir = _requests_ca_locations(verify)
        cache_key = (ca_file, ca_dir)
        with self._mio_ssl_contexts_lock:
            context = self._mio_ssl_contexts.get(cache_key)
            if context is None:
                context = _create_verified_ssl_context(
                    ca_file=ca_file,
                    ca_dir=ca_dir,
                )
                self._mio_ssl_contexts[cache_key] = context

        # The context already contains both native roots and any explicit CA
        # locations. Removing the path fields avoids loading one bundle into a
        # shared context again for every new pooled connection.
        pool_kwargs.pop("ca_certs", None)
        pool_kwargs.pop("ca_cert_dir", None)
        pool_kwargs["cert_reqs"] = "CERT_REQUIRED"
        pool_kwargs["ssl_context"] = context
        return host_params, pool_kwargs

    def cert_verify(
        self,
        conn: object,
        url: str,
        verify: object,
        cert: object,
    ) -> None:
        super().cert_verify(conn, url, verify, cert)
        if str(url or "").casefold().startswith("https://") and verify is not False:
            # ``HTTPAdapter.cert_verify`` otherwise re-attaches Certifi after
            # pool selection. Verification is performed by the explicit
            # system-trust SSLContext stored in the pool key above.
            conn.ca_certs = None
            conn.ca_cert_dir = None
            conn.cert_reqs = "CERT_REQUIRED"


def is_local_provider_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    include_shared: bool = False,
) -> bool:
    """Return whether an IP literal belongs to a local/private model route."""

    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or (include_shared and address in _SHARED_LOCAL_NETWORK)
    )


def is_special_local_provider_host(host: object) -> bool:
    """Return whether ``host`` is a reserved desktop-container gateway name."""

    normalized = str(host or "").strip().rstrip(".").casefold()
    return normalized in _SPECIAL_LOCAL_HOSTS


def resolve_pinnable_local_provider_addresses(
    host: object,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Return safe addresses for local hostnames that can be request-pinned.

    ``localhost`` has a standards-defined loopback meaning, so using the
    IPv4 literal avoids repeated Windows resolver/IPv6 fallback delays without
    trusting DNS. Docker gateway names retain the stricter all-results-local
    resolution policy below. Arbitrary hostnames are never resolved here.
    """

    normalized = str(host or "").strip().rstrip(".").casefold()
    if normalized in _LOOPBACK_HOSTS or normalized.endswith(".localhost"):
        return (ipaddress.ip_address("127.0.0.1"),)
    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        literal = None
    if literal is not None and literal.is_loopback:
        return (literal,)
    return resolve_special_local_provider_addresses(normalized)


def resolve_special_local_provider_addresses(
    host: object,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a reserved container gateway only when every result stays local.

    Docker Desktop provides ``host.docker.internal`` and
    ``gateway.docker.internal`` through its local resolver. Treating those
    names as local without checking their answers would let a poisoned or
    rebound DNS result turn an explicitly permitted HTTP endpoint into a
    public credential destination. Returning no addresses is deliberately
    fail-closed for resolution errors, malformed answers, mixed local/public
    answers, and entirely public answers.
    """

    normalized = str(host or "").strip().rstrip(".").casefold()
    if normalized not in _SPECIAL_LOCAL_HOSTS:
        return ()
    try:
        resolved = socket.getaddrinfo(
            normalized,
            0,
            type=socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError):
        return ()

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _socktype, _protocol, _canonical_name, sockaddr in resolved:
        if family not in {socket.AF_INET, socket.AF_INET6} or not sockaddr:
            return ()
        try:
            address_text = str(sockaddr[0])
            if (
                family == socket.AF_INET6
                and len(sockaddr) >= 4
                and sockaddr[3]
                and "%" not in address_text
            ):
                address_text = f"{address_text}%{sockaddr[3]}"
            address = ipaddress.ip_address(address_text)
        except ValueError:
            return ()
        if not is_local_provider_address(address, include_shared=True):
            return ()
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    return tuple(addresses)


def is_local_provider_host(host: object) -> bool:
    """Recognize explicit local-service hosts without trusting arbitrary DNS."""

    normalized = str(host or "").strip().rstrip(".").casefold()
    if not normalized:
        return False
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized in _SPECIAL_LOCAL_HOSTS:
        return bool(resolve_special_local_provider_addresses(normalized))
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return is_local_provider_address(address, include_shared=True)


def should_bypass_environment_proxies(url: object) -> bool:
    """Return whether an explicit local endpoint should connect directly."""

    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").rstrip(".").casefold()
    except (TypeError, ValueError):
        return False
    return is_local_provider_host(host)


def configure_requests_session_for_url(
    session: requests.Session,
    url: object,
) -> requests.Session:
    """Apply system trust and direct-local routing to a Requests session.

    ``requests.Session.trust_env = False`` is the reliable way to prevent a
    localhost/private-LAN request from being detoured through environment or
    system proxies. It also disables Requests' normal CA-bundle environment
    lookup, so copy that setting onto the session before disabling it.
    """

    bypass = should_bypass_environment_proxies(url)
    session.trust_env = not bypass
    if bypass:
        ca_bundle = _first_environment_value(
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        )
        if ca_bundle:
            session.verify = ca_bundle
    if isinstance(session, requests.sessions.Session):
        _mount_system_trust_adapter(session)
    return session


def direct_connection_ssl_context(url: object) -> ssl.SSLContext | None:
    """Return an environment-aware, system-trust context for provider HTTPS.

    HTTPX also couples proxy discovery and CA environment handling behind
    ``trust_env``. Supplying an explicit verified context lets local endpoints
    disable proxy discovery without losing private PKI support, and lets public
    custom endpoints use the same native certificate chain engine as Windows.
    Proxy behavior remains controlled independently by ``trust_env``.
    """

    try:
        scheme = urlsplit(str(url or "").strip()).scheme.casefold()
    except (TypeError, ValueError):
        return None
    if scheme != "https":
        return None

    ca_file = _first_environment_value(
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    )
    ca_dir = _first_environment_value("SSL_CERT_DIR")
    if ca_file and Path(ca_file).is_dir() and not ca_dir:
        ca_dir, ca_file = ca_file, ""
    return _create_verified_ssl_context(
        ca_file=ca_file,
        ca_dir=ca_dir,
    )


def _mount_system_trust_adapter(session: requests.Session) -> None:
    existing = session.adapters.get("https://")
    if isinstance(existing, SystemTrustHTTPAdapter):
        return

    adapter_kwargs: dict[str, object] = {}
    if isinstance(existing, requests.adapters.HTTPAdapter):
        adapter_kwargs = {
            "max_retries": existing.max_retries,
            "pool_connections": existing._pool_connections,
            "pool_maxsize": existing._pool_maxsize,
            "pool_block": existing._pool_block,
        }
    replacement = SystemTrustHTTPAdapter(**adapter_kwargs)
    session.mount("https://", replacement)
    if existing is not None:
        existing.close()


def _requests_ca_locations(verify: object) -> tuple[str, str]:
    if isinstance(verify, (str, os.PathLike)):
        location = str(verify).strip()
        if location and Path(location).is_dir():
            return "", location
        return location, ""

    ca_file = _first_environment_value(
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    )
    ca_dir = _first_environment_value("SSL_CERT_DIR")
    if ca_file and Path(ca_file).is_dir() and not ca_dir:
        ca_dir, ca_file = ca_file, ""
    return ca_file, ca_dir


def _create_verified_ssl_context(
    *,
    ca_file: str = "",
    ca_dir: str = "",
) -> ssl.SSLContext:
    if _truststore is None:
        # Keep source checkouts usable before dependencies are installed. The
        # locked release environment requires Truststore, because the stdlib
        # fallback cannot use the Windows chain engine to retrieve a missing
        # intermediate certificate.
        context = ssl.create_default_context()
        if ca_file or ca_dir:
            # ``create_default_context(cafile=...)`` replaces the platform
            # defaults. Load explicit bundles afterwards so they remain
            # additive even in a source environment without Truststore.
            context.load_verify_locations(
                cafile=ca_file or None,
                capath=ca_dir or None,
            )
        return context

    context = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca_file or ca_dir:
        context.load_verify_locations(
            cafile=ca_file or None,
            capath=ca_dir or None,
        )
    return context


def should_retry_requests_attempt(
    error: BaseException,
    *,
    status_code: object = None,
) -> bool:
    """Classify a failed ``requests`` attempt without retrying permanent errors.

    Realtime translation should recover from transport failures, request
    timeouts, explicit throttling, and provider-side failures. Authentication,
    validation, endpoint, and other permanent 4xx responses are returned to the
    user immediately instead of repeating the same request and adding latency.
    """

    parsed_status = _http_status_code(status_code)
    if parsed_status is None:
        response = getattr(error, "response", None)
        parsed_status = _http_status_code(getattr(response, "status_code", None))
    if parsed_status is not None:
        return bool(
            parsed_status in _RETRYABLE_HTTP_STATUS_CODES
            or 500 <= parsed_status <= 599
        )

    # Certificate and hostname validation failures will not recover on an
    # immediate retry. Other TLS interruptions (EOF, reset, temporary provider
    # handshake failure) are transport errors and can recover on another route.
    if isinstance(error, requests.exceptions.SSLError):
        return not _is_permanent_tls_error(error)
    return isinstance(
        error,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    )


def _http_status_code(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 100 <= parsed <= 599 else None


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _is_permanent_tls_error(error: BaseException) -> bool:
    pending: list[object] = [error]
    seen: set[int] = set()
    messages: list[str] = []
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(current, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return True
        if isinstance(current, BaseException):
            messages.append(str(current).casefold())
            pending.extend(current.args)
            cause = getattr(current, "__cause__", None)
            context = getattr(current, "__context__", None)
            if cause is not None:
                pending.append(cause)
            if context is not None:
                pending.append(context)
        elif current is not None:
            messages.append(str(current).casefold())

    detail = " ".join(messages)
    permanent_markers = (
        "certificate verify failed",
        "certificate has expired",
        "certificate expired",
        "hostname mismatch",
        "doesn't match",
        "does not match",
        "self signed certificate",
        "unable to get local issuer certificate",
        "unknown ca",
        "wrong version number",
    )
    return any(marker in detail for marker in permanent_markers)
