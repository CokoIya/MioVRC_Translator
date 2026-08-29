from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .api_tts_config import (
    get_cloned_voice_options,
    get_tts_api_voice_options,
    resolve_tts_api_config,
)
from .base import BaseTTS, TTSVoice
from .error_utils import is_tts_authentication_error
from .persona_instructions import qwen_tts_model_supports_instructions
from src.translators.factory import _float_setting, _int_setting
from src.utils.app_paths import read_secure_text, secure_file_path, writable_app_dir
from src.utils.http_session_pool import ThreadLocalSessionPool
from src.utils.provider_network import (
    SystemTrustHTTPAdapter,
    configure_requests_session_for_url,
    is_local_provider_address,
    is_local_provider_host,
    should_bypass_environment_proxies,
)
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_warmup import warmup_requests_session
from src.utils.qwen_endpoints import (
    QWEN_TOKYO_DASHSCOPE_PATH,
    require_qwen_tokyo_workspace_base_url,
)
from src.utils.secure_http import (
    open_validated_requests_response,
    read_bounded_requests_response,
    validate_api_base_url,
)

logger = logging.getLogger(__name__)

_PROTECTED_SECRET_PREFIX = "dpapi:v1:"
_MAX_AUDIO_BYTES = 32 * 1024 * 1024
_MAX_JSON_AUDIO_RESPONSE_BYTES = 48 * 1024 * 1024
_MAX_ERROR_RESPONSE_BYTES = 64 * 1024
_MAX_AUDIO_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_DASHSCOPE_RESULT_HOST_RE = re.compile(
    r"^dashscope-result-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com$",
    re.IGNORECASE,
)
# Mihomo/Clash and several desktop TUN clients synthesize answers for public
# DNS names in these ranges.  They are not routable provider addresses, but
# they are valid proxy interception targets.  Keep the exception scoped to
# the exact DashScope result-host pattern and continue to pin the connection
# and verify the original TLS hostname before downloading audio.
_DASHSCOPE_FAKE_IPV4_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DASHSCOPE_FAKE_IPV6_NETWORK = ipaddress.ip_network("fdfe:dcba:9876::/48")


def _is_dashscope_proxy_synthetic_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return bool(
        address in _DASHSCOPE_FAKE_IPV4_NETWORK
        or address in _DASHSCOPE_FAKE_IPV6_NETWORK
    )


_QWEN_SYNTHESIS_RETRY_DELAYS = (0.2,)
_QWEN_AUDIO_DOWNLOAD_RETRY_DELAYS = (0.25, 1.0, 2.0)
_AUDIO_DNS_RESOLVER_SLOTS = threading.BoundedSemaphore(4)
_REQUESTS_SESSION_CLASS = requests.sessions.Session
_QWEN_NETWORK_ERROR_MESSAGE = (
    "Qwen TTS network connection was interrupted. "
    "Please check the network connection and try again."
)
_TLS_EOF_ERROR_TOKENS = (
    "unexpected eof",
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
    "eof while reading",
    "remote end closed connection without response",
    "connection reset",
    "connection aborted",
    "broken pipe",
)
_NON_RETRYABLE_TLS_ERROR_TOKENS = (
    "certificate verify failed",
    "hostname mismatch",
    "self signed certificate",
    "unknown ca",
    "wrong version number",
)


class _QwenTransportRetriesExhausted(RuntimeError):
    """Internal marker preventing nested request/download retry multiplication."""


class _APITTSClientResponseError(RuntimeError):
    """Marker for a received 4xx response that must never be transport-retried."""


class _APITTSWallClockTimeout(TimeoutError):
    """A synthesis operation exhausted its absolute wall-clock budget."""


class _APITTSCancelled(RuntimeError):
    """A synthesis operation was interrupted by engine shutdown."""


class _AudioDNSResolutionError(requests.exceptions.ConnectionError):
    """A provider audio hostname could not be resolved to a usable address."""


@dataclass(frozen=True)
class _PinnedAudioTarget:
    scheme: str
    hostname: str
    port: int
    address: str
    mount_prefix: str
    host_header: str


class _PinnedAddressAdapter(SystemTrustHTTPAdapter):
    """Connect to one validated IP while preserving HTTP Host and TLS SNI."""

    def __init__(self, target: _PinnedAudioTarget) -> None:
        super().__init__(
            max_retries=0,
            pool_connections=1,
            pool_maxsize=2,
            pool_block=True,
        )
        self._target = target

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: object,
        proxies: Mapping[str, str] | None = None,
        cert: object = None,
    ):
        # Provider-supplied download URLs must be connected to the address that
        # was validated. A proxy would resolve the hostname again and recreate
        # the DNS-rebinding/SSRF boundary this adapter is designed to remove.
        del proxies
        _host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        if self._target.scheme == "https":
            pool_kwargs["assert_hostname"] = self._target.hostname
            pool_kwargs["server_hostname"] = self._target.hostname
        return self.poolmanager.connection_from_host(
            self._target.address,
            port=self._target.port,
            scheme=self._target.scheme,
            pool_kwargs=pool_kwargs,
        )

    def request_url(
        self,
        request: requests.PreparedRequest,
        proxies: Mapping[str, str] | None,
    ) -> str:
        del proxies
        return request.path_url

    def add_headers(self, request: requests.PreparedRequest, **kwargs: object) -> None:
        super().add_headers(request, **kwargs)
        request.headers["Host"] = self._target.host_header


class _TimedRequestsResponse:
    """Transparent response wrapper recording first-body-byte timing.

    The shared bounded-response reader remains responsible for all size and
    content-encoding checks. This wrapper only adds deadline/cancellation
    checks at body-chunk boundaries and keeps the original response API.
    """

    def __init__(
        self,
        response: requests.Response,
        *,
        engine: "_APITTSBase",
        metric_prefix: str,
        request_started_at: float,
        body_is_audio: bool = False,
    ) -> None:
        self._response = response
        self._engine = engine
        self._metric_prefix = metric_prefix
        self._request_started_at = request_started_at
        self._body_is_audio = body_is_audio

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def iter_content(self, chunk_size: int = 1, *args: Any, **kwargs: Any) -> Iterator[bytes]:
        first_payload = True
        for chunk in self._response.iter_content(chunk_size, *args, **kwargs):
            self._engine._check_request_active()
            if chunk and first_payload:
                first_payload = False
                now = time.monotonic()
                self._engine._set_diagnostic(
                    f"{self._metric_prefix}_first_body_byte_s",
                    max(0.0, now - self._request_started_at),
                )
                if self._body_is_audio:
                    self._engine._record_first_audio(now)
            yield chunk
        self._engine._check_request_active()


_HIDDEN_VOICES: set[str] | None = None


def _load_hidden_voices() -> set[str]:
    global _HIDDEN_VOICES
    if _HIDDEN_VOICES is not None:
        return _HIDDEN_VOICES

    import json

    _HIDDEN_VOICES = set()
    try:
        path = secure_file_path(writable_app_dir() / "seren.json")
        if path.exists():
            data = json.loads(
                read_secure_text(path, encoding="utf-8", max_bytes=1024 * 1024)
            )
            voices = data.get("hidden_voices", [])
            if isinstance(voices, list):
                for v in voices:
                    if isinstance(v, str):
                        _HIDDEN_VOICES.add(v)
    except Exception:
        pass

    if not _HIDDEN_VOICES:
        _HIDDEN_VOICES = set()

    if _HIDDEN_VOICES:
        logger.debug("Hidden voices loaded: %s", _HIDDEN_VOICES)
    return _HIDDEN_VOICES


def _nested_transport_exceptions(error: BaseException) -> tuple[BaseException, ...]:
    pending: list[BaseException] = [error]
    found: list[BaseException] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        found.append(current)
        for nested in (
            current.__cause__,
            current.__context__,
            getattr(current, "reason", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
        for argument in getattr(current, "args", ()):  # urllib3 nests errors here.
            if isinstance(argument, BaseException):
                pending.append(argument)
    return tuple(found)


def _is_retryable_qwen_transport_error(error: BaseException) -> bool:
    if isinstance(error, (_QwenTransportRetriesExhausted, _APITTSClientResponseError)):
        return False
    nested = _nested_transport_exceptions(error)
    message = " ".join(str(item) for item in nested).casefold()
    if any(token in message for token in _NON_RETRYABLE_TLS_ERROR_TOKENS):
        return False
    if any(isinstance(item, ssl.SSLEOFError) for item in nested):
        return True
    if any(isinstance(item, requests.exceptions.SSLError) for item in nested):
        return any(token in message for token in _TLS_EOF_ERROR_TOKENS)
    if any(isinstance(item, requests.exceptions.Timeout) for item in nested):
        return True
    if any(
        isinstance(item, requests.exceptions.ChunkedEncodingError)
        for item in nested
    ):
        return True
    if any(isinstance(item, requests.exceptions.ConnectionError) for item in nested):
        return True
    if any(
        isinstance(
            item,
            (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError),
        )
        for item in nested
    ):
        return True
    return any(token in message for token in _TLS_EOF_ERROR_TOKENS) and any(
        isinstance(item, OSError) for item in nested
    )


class _APITTSBase(BaseTTS):
    max_concurrent_synthesis = 2
    ENGINE_ID = ""
    ENGINE_LABEL = "API TTS"
    AUTH_HEADER_NAME = "Authorization"
    AUTH_HEADER_PREFIX = "Bearer "

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        resolved = resolve_tts_api_config(self.ENGINE_ID, config)
        self.api_key = str(resolved.get("api_key", "") or "").strip()
        self.region = str(resolved.get("region", "") or "").strip()
        raw_base_url = str(resolved.get("base_url", "") or "").strip()
        if self.ENGINE_ID == "qwen_tts" and self.region == "japan":
            raw_base_url = require_qwen_tokyo_workspace_base_url(
                raw_base_url,
                endpoint_path=QWEN_TOKYO_DASHSCOPE_PATH,
                label="Qwen TTS Tokyo workspace API",
            )
        self.base_url = (
            validate_api_base_url(
                raw_base_url,
                label=f"{self.ENGINE_LABEL} API",
                allow_private_http=should_bypass_environment_proxies(raw_base_url),
            )
            if raw_base_url
            else ""
        )
        self.model = str(resolved.get("model", "") or "").strip()
        self.default_voice = str(resolved.get("voice", "") or "").strip()
        self.language_type_hint = str(
            resolved.get("language_type") or resolved.get("language_hint") or ""
        ).strip()
        self.instructions = str(resolved.get("instructions", "") or "").strip()
        self.optimize_instructions = bool(resolved.get("optimize_instructions", True))
        self.timeout_seconds = _float_setting(
            resolved.get("timeout_seconds"), 30.0, minimum=3.0, maximum=120.0
        )
        self.connect_timeout_seconds = _float_setting(
            resolved.get("connect_timeout_seconds"),
            self.timeout_seconds,
            minimum=0.25,
            maximum=120.0,
        )
        self.read_timeout_seconds = _float_setting(
            resolved.get("read_timeout_seconds"),
            self.timeout_seconds,
            minimum=0.25,
            maximum=120.0,
        )
        self.wall_timeout_seconds = _float_setting(
            resolved.get("wall_timeout_seconds"),
            max(45.0, self.timeout_seconds),
            minimum=3.0,
            maximum=300.0,
        )
        self.max_retries = _int_setting(resolved.get("max_retries"), 0, minimum=0, maximum=3)
        self._diagnostics_local = threading.local()
        self._request_state_local = threading.local()
        self._close_requested = threading.Event()
        self._resolver_lock = threading.Lock()
        self._resolver_threads: set[threading.Thread] = set()
        self._session_pool = ThreadLocalSessionPool(self._create_session)

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        configure_requests_session_for_url(session, self.base_url)
        previous_https_adapter = getattr(session, "adapters", {}).get("https://")
        adapter = SystemTrustHTTPAdapter(
            max_retries=self._adapter_max_retries(),
            pool_connections=4,
            pool_maxsize=4,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        if previous_https_adapter is not None and previous_https_adapter is not adapter:
            previous_https_adapter.close()
        # Per-origin counters are diagnostic hints. A warm Session can still
        # reconnect after an idle timeout, so logs call this session reuse
        # rather than claiming socket reuse that requests cannot expose.
        try:
            session._mio_origin_request_counts = {}  # type: ignore[attr-defined]
        except Exception:
            pass
        return session

    def _adapter_max_retries(self) -> int:
        return self.max_retries

    @property
    def _session(self) -> requests.Session:
        """Compatibility accessor backed by the current thread's session."""

        return self._session_pool.get()

    def close_thread_context(self) -> None:
        self._session_pool.close_current()

    def request_close(self) -> None:
        self._close_requested.set()
        self._session_pool.close()

    def close(self) -> None:
        self._close_requested.set()
        self._session_pool.close()

    def background_work_count(self) -> int:
        """Return DNS resolver work that still owns this engine instance."""

        with self._resolver_lock:
            active = {
                thread
                for thread in self._resolver_threads
                if thread.ident is None or thread.is_alive()
            }
            self._resolver_threads.clear()
            self._resolver_threads.update(active)
            return len(active)

    def prewarm(self, voice: str = "") -> None:
        del voice
        self._check_request_active(require_request=False)
        if not self.is_available():
            return
        try:
            headers = self._auth_headers()
            result = warmup_requests_session(
                self._session_pool.get(),
                self._prewarm_url(),
                method="HEAD",
                headers=headers,
                timeout_s=min(self.connect_timeout_seconds, 3.0),
            )
        except Exception as exc:
            logger.warning(
                "%s prewarm failed (error_type=%s)",
                self.ENGINE_LABEL,
                exc.__class__.__name__,
            )
            return
        logger.log(
            logging.INFO if result.succeeded else logging.WARNING,
            "%s prewarm %s (status=%s elapsed_ms=%.0f error_type=%s)",
            self.ENGINE_LABEL,
            "finished" if result.succeeded else "failed",
            result.status_code if result.status_code is not None else "unknown",
            result.elapsed_s * 1000.0,
            result.error_type or "none",
        )

    def _prewarm_url(self) -> str:
        return self.base_url

    def consume_last_synthesis_diagnostics(self) -> Mapping[str, object]:
        diagnostics = getattr(self._diagnostics_local, "last", None)
        try:
            del self._diagnostics_local.last
        except AttributeError:
            pass
        return dict(diagnostics) if isinstance(diagnostics, Mapping) else {}

    def _begin_synthesis_diagnostics(self) -> float:
        started_at = time.monotonic()
        diagnostics: dict[str, object] = {
            "started_at": started_at,
            "connection_pool_wait_s": 0.0,
            "dns_s": None,
            "tcp_s": None,
            "tls_s": None,
            "retry_wait_s": 0.0,
            "api_attempts": 0,
            "audio_download_attempts": 0,
        }
        self._request_state_local.diagnostics = diagnostics
        self._request_state_local.started_at = started_at
        self._request_state_local.deadline = started_at + self.wall_timeout_seconds
        return started_at

    def _finish_synthesis_diagnostics(self, *, succeeded: bool) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is None:
            return
        now = time.monotonic()
        started_at = float(diagnostics.get("started_at", now) or now)
        diagnostics["total_s"] = max(0.0, now - started_at)
        diagnostics["succeeded"] = bool(succeeded)
        diagnostics["cancelled"] = self._close_requested.is_set()
        self._diagnostics_local.last = dict(diagnostics)
        for attribute in ("diagnostics", "started_at", "deadline"):
            try:
                delattr(self._request_state_local, attribute)
            except AttributeError:
                pass

    def _current_diagnostics(self) -> dict[str, object] | None:
        diagnostics = getattr(self._request_state_local, "diagnostics", None)
        return diagnostics if isinstance(diagnostics, dict) else None

    def _set_diagnostic(self, name: str, value: object) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is not None:
            diagnostics[str(name)] = value

    def _add_diagnostic_seconds(self, name: str, seconds: float) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is None:
            return
        try:
            previous = float(diagnostics.get(name, 0.0) or 0.0)
        except (TypeError, ValueError):
            previous = 0.0
        diagnostics[name] = previous + max(0.0, float(seconds))

    def _increment_diagnostic(self, name: str) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is None:
            return
        try:
            previous = int(diagnostics.get(name, 0) or 0)
        except (TypeError, ValueError):
            previous = 0
        diagnostics[name] = previous + 1

    def _check_request_active(self, *, require_request: bool = True) -> float | None:
        if self._close_requested.is_set():
            raise _APITTSCancelled(f"{self.ENGINE_LABEL} request was cancelled during shutdown")
        deadline = getattr(self._request_state_local, "deadline", None)
        if deadline is None:
            if require_request:
                return None
            return None
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0.0:
            raise _APITTSWallClockTimeout(
                f"{self.ENGINE_LABEL} request exceeded the configured wall-clock timeout"
            )
        return remaining

    def _requests_timeout(self) -> float | tuple[float, float]:
        remaining = self._check_request_active()
        connect_timeout = self.connect_timeout_seconds
        read_timeout = self.read_timeout_seconds
        if remaining is not None:
            connect_timeout = min(connect_timeout, max(0.05, remaining))
            read_timeout = min(read_timeout, max(0.05, remaining))
        if abs(connect_timeout - read_timeout) < 1e-9:
            return connect_timeout
        return connect_timeout, read_timeout

    def _resolve_audio_addresses_bounded(
        self,
        host: str,
        port: int,
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        try:
            return (ipaddress.ip_address(host),)
        except ValueError:
            pass

        self._check_request_active()
        if not _AUDIO_DNS_RESOLVER_SLOTS.acquire(blocking=False):
            raise _AudioDNSResolutionError(
                f"{self.ENGINE_LABEL} DNS resolver capacity is temporarily unavailable"
            )

        finished = threading.Event()
        result: dict[str, object] = {}

        resolver: threading.Thread

        def resolve() -> None:
            try:
                result["addresses"] = _addresses_from_getaddrinfo(
                    socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                )
            except OSError as exc:
                result["error"] = exc
            finally:
                try:
                    _AUDIO_DNS_RESOLVER_SLOTS.release()
                finally:
                    finished.set()
                    with self._resolver_lock:
                        self._resolver_threads.discard(
                            threading.current_thread()
                        )

        resolver = None
        try:
            resolver = threading.Thread(
                target=resolve,
                daemon=True,
                name="qwen-tts-dns-resolver",
            )
            with self._resolver_lock:
                self._resolver_threads.add(resolver)
            resolver.start()
        except Exception:
            if resolver is not None:
                with self._resolver_lock:
                    self._resolver_threads.discard(resolver)
            _AUDIO_DNS_RESOLVER_SLOTS.release()
            finished.set()
            raise
        while not finished.is_set():
            remaining = self._check_request_active()
            wait_s = 0.05 if remaining is None else min(0.05, remaining)
            finished.wait(max(0.001, wait_s))
        self._check_request_active()
        resolution_error = result.get("error")
        if isinstance(resolution_error, BaseException):
            raise _AudioDNSResolutionError(
                f"{self.ENGINE_LABEL} audio hostname resolution failed"
            ) from resolution_error
        addresses = result.get("addresses")
        if not isinstance(addresses, tuple) or not addresses:
            raise _AudioDNSResolutionError(
                f"{self.ENGINE_LABEL} audio hostname returned no usable addresses"
            )
        return addresses

    def _prepare_pinned_audio_url(
        self,
        session: requests.Session,
        candidate: str,
    ) -> bool:
        # Test doubles and third-party injected sessions do not use requests'
        # adapter transport. Preserve their validator contract without
        # pretending they provide DNS pinning.
        if not isinstance(session, _REQUESTS_SESSION_CLASS):
            return self._is_safe_audio_url(candidate)

        try:
            parsed_candidate = urlsplit(str(candidate or "").strip())
        except (TypeError, ValueError):
            return False
        if parsed_candidate.fragment or parsed_candidate.scheme.casefold() not in {
            "http",
            "https",
        }:
            return False

        adapters = getattr(session, "_mio_pinned_audio_adapters", None)
        if not isinstance(adapters, dict):
            adapters = {}
            session._mio_pinned_audio_adapters = adapters  # type: ignore[attr-defined]

        candidate_origin = _url_origin(candidate)
        if candidate_origin is None:
            return False
        candidate_mount_prefix = _requests_adapter_mount_prefix(candidate)
        if candidate_mount_prefix is None:
            return False
        origin_key = candidate_origin
        existing = adapters.get(origin_key)
        if isinstance(existing, tuple) and len(existing) == 2:
            target, adapter = existing
            if isinstance(target, _PinnedAudioTarget) and isinstance(
                adapter,
                _PinnedAddressAdapter,
            ):
                # Equivalent spellings of the same origin (for example an
                # explicit default port or a trailing DNS dot) have different
                # Requests adapter prefixes. Mount the already-pinned adapter
                # for every validated spelling so none can fall through to the
                # generic adapter and trigger a second DNS lookup.
                session.mount(candidate_mount_prefix, adapter)
                return True

        try:
            target = _validated_audio_download_target(
                candidate,
                api_base_url=self.base_url,
                resolver=self._resolve_audio_addresses_bounded,
            )
        except _AudioDNSResolutionError:
            self._set_diagnostic("audio_url_validation", "dns_resolution_failed")
            scheme, hostname, port = _audio_url_origin_for_log(candidate)
            logger.warning(
                "%s audio URL validation deferred "
                "(reason=dns_resolution_failed scheme=%s host=%s port=%s)",
                self.ENGINE_LABEL,
                scheme,
                hostname,
                port,
            )
            raise
        if target is None:
            self._set_diagnostic("audio_url_validation", "rejected")
            scheme, hostname, port = _audio_url_origin_for_log(candidate)
            logger.warning(
                "%s audio URL rejected "
                "(reason=security_policy scheme=%s host=%s port=%s)",
                self.ENGINE_LABEL,
                scheme,
                hostname,
                port,
            )
            return False
        adapter = _PinnedAddressAdapter(target)
        previous = adapters.get(origin_key)
        if isinstance(previous, tuple) and len(previous) == 2:
            close = getattr(previous[1], "close", None)
            if callable(close):
                close()
        adapters[origin_key] = (target, adapter)
        session.mount(candidate_mount_prefix, adapter)
        return True

    def _record_first_audio(self, timestamp: float | None = None) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is None or "first_audio_s" in diagnostics:
            return
        now = time.monotonic() if timestamp is None else float(timestamp)
        started_at = float(diagnostics.get("started_at", now) or now)
        diagnostics["first_audio_s"] = max(0.0, now - started_at)

    @staticmethod
    def _session_reuse_hint(session: requests.Session, url: str) -> bool:
        origin = _url_origin(url)
        key = origin or ("", str(url or ""), 0)
        counts = getattr(session, "_mio_origin_request_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            try:
                session._mio_origin_request_counts = counts  # type: ignore[attr-defined]
            except Exception:
                pass
        previous = int(counts.get(key, 0) or 0)
        counts[key] = previous + 1
        return previous > 0

    def _record_response_headers(
        self,
        response: requests.Response,
        *,
        prefix: str,
        request_started_at: float,
        session_reused: bool,
    ) -> None:
        now = time.monotonic()
        header_s = max(0.0, now - request_started_at)
        self._set_diagnostic(f"{prefix}_response_header_s", header_s)
        self._set_diagnostic(f"{prefix}_session_reused", session_reused)
        elapsed = getattr(response, "elapsed", None)
        elapsed_seconds = None
        total_seconds = getattr(elapsed, "total_seconds", None)
        if callable(total_seconds):
            try:
                elapsed_seconds = max(0.0, float(total_seconds()))
            except (TypeError, ValueError):
                elapsed_seconds = None
        # requests exposes time-to-headers but not portable DNS/TCP/TLS
        # breakdowns. Preserve the approximation and explicitly leave those
        # lower-level fields unmeasured rather than inventing precision.
        self._set_diagnostic(
            f"{prefix}_provider_s",
            header_s if elapsed_seconds is None else elapsed_seconds,
        )
        self._set_diagnostic(f"{prefix}_provider_estimated", True)

    def is_available(self) -> bool:
        return bool(self.base_url and self.model)

    def get_available_voices(self) -> list[TTSVoice]:
        voices: list[TTSVoice] = []
        hidden = _load_hidden_voices()
        for voice_id, name, language, gender, locale in get_tts_api_voice_options(self.ENGINE_ID):
            if voice_id in hidden:
                continue
            voices.append(
                TTSVoice(
                    id=voice_id,
                    name=name,
                    language=language,
                    gender=gender,
                    locale=locale,
                )
            )
        return voices

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/*, application/json",
            "Accept-Encoding": "identity",
        }
        if not self.api_key and should_bypass_environment_proxies(self.base_url):
            return headers
        self._require_api_key()
        auth_value = (
            f"{self.AUTH_HEADER_PREFIX}{self.api_key}"
            if self.AUTH_HEADER_PREFIX
            else self.api_key
        )
        headers[self.AUTH_HEADER_NAME] = auth_value
        return headers

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(f"{self.ENGINE_LABEL} API Key is not configured")
        if self.api_key.startswith(_PROTECTED_SECRET_PREFIX):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} API Key is still encrypted and cannot be used"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in self.api_key):
            raise RuntimeError(f"{self.ENGINE_LABEL} API Key contains invalid characters")

    def _request_json_audio(self, url: str, payload: Mapping[str, object]) -> bytes:
        if not _is_safe_api_request_url(
            url,
            allow_private_http=should_bypass_environment_proxies(self.base_url),
        ):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} API URL must use HTTPS or local HTTP"
            )
        response = self._post_json_response(
            url,
            payload,
        )
        try:
            self._check_request_active()
            status_code = int(getattr(response, "status_code", 200) or 0)
            if status_code in _REDIRECT_STATUS_CODES:
                raise RuntimeError(
                    f"{self.ENGINE_LABEL} API redirects are not allowed"
                )
            content_type = str(
                response.headers.get("content-type", "") or ""
            ).lower()
            if status_code >= 400:
                try:
                    content = read_bounded_requests_response(
                        _TimedRequestsResponse(
                            response,
                            engine=self,
                            metric_prefix="api",
                            request_started_at=float(
                                (self._current_diagnostics() or {}).get(
                                    "api_request_started_at",
                                    time.monotonic(),
                                )
                            ),
                        ),
                        limit=_MAX_ERROR_RESPONSE_BYTES,
                        label=f"{self.ENGINE_LABEL} API error response",
                    )
                except Exception as exc:
                    if 400 <= status_code < 500:
                        raise _APITTSClientResponseError(
                            self._api_request_failure_message(status_code, "")
                        ) from exc
                    raise
                try:
                    response.raise_for_status()
                except Exception as exc:
                    detail = _response_error_detail(content, content_type)
                    message = self._api_request_failure_message(
                        status_code,
                        detail,
                    )
                    raise RuntimeError(message) from exc
                raise RuntimeError(f"{self.ENGINE_LABEL} API request failed")

            response_limit = (
                _MAX_AUDIO_BYTES
                if content_type.startswith("audio/")
                else _MAX_JSON_AUDIO_RESPONSE_BYTES
            )
            content = read_bounded_requests_response(
                _TimedRequestsResponse(
                    response,
                    engine=self,
                    metric_prefix="api",
                    request_started_at=float(
                        (self._current_diagnostics() or {}).get(
                            "api_request_started_at",
                            time.monotonic(),
                        )
                    ),
                    body_is_audio=content_type.startswith("audio/"),
                ),
                limit=response_limit,
                label=f"{self.ENGINE_LABEL} API response",
            )
            diagnostics = self._current_diagnostics()
            if diagnostics is not None:
                request_started_at = float(
                    diagnostics.get("api_request_started_at", time.monotonic())
                    or time.monotonic()
                )
                diagnostics["api_full_response_s"] = max(
                    0.0,
                    time.monotonic() - request_started_at,
                )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if _looks_like_audio(content, content_type):
            self._record_first_audio()
            self._set_full_response_timing()
            return content

        parsing_started_at = time.monotonic()
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned non-audio data") from exc
        if not isinstance(data, Mapping):
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned invalid JSON data")
        audio = self._extract_audio_from_payload(data)
        if not audio:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned no audio data")
        parsing_elapsed = max(0.0, time.monotonic() - parsing_started_at)
        diagnostics = self._current_diagnostics()
        download_s = 0.0
        if diagnostics is not None:
            try:
                download_s = float(diagnostics.get("audio_download_full_s", 0.0) or 0.0)
            except (TypeError, ValueError):
                download_s = 0.0
        self._set_diagnostic(
            "parsing_postprocess_s",
            max(0.0, parsing_elapsed - download_s),
        )
        self._record_first_audio()
        self._set_full_response_timing()
        return audio

    def _set_full_response_timing(self) -> None:
        diagnostics = self._current_diagnostics()
        if diagnostics is None:
            return
        now = time.monotonic()
        started_at = float(diagnostics.get("started_at", now) or now)
        diagnostics["full_response_s"] = max(0.0, now - started_at)

    def _post_json_response(
        self,
        url: str,
        payload: Mapping[str, object],
    ) -> requests.Response:
        self._check_request_active()
        pool_started_at = time.monotonic()
        session = self._session_pool.get()
        self._add_diagnostic_seconds(
            "connection_pool_wait_s",
            time.monotonic() - pool_started_at,
        )
        request_started_at = time.monotonic()
        self._set_diagnostic("api_request_started_at", request_started_at)
        self._increment_diagnostic("api_attempts")
        session_reused = self._session_reuse_hint(session, url)
        try:
            response = session.post(
                url,
                headers=self._auth_headers(),
                json=dict(payload),
                timeout=self._requests_timeout(),
                stream=True,
                allow_redirects=False,
            )
        except Exception:
            self._set_diagnostic(
                "api_failed_after_s",
                max(0.0, time.monotonic() - request_started_at),
            )
            raise
        self._record_response_headers(
            response,
            prefix="api",
            request_started_at=request_started_at,
            session_reused=session_reused,
        )
        return response

    def _api_request_failure_message(self, status_code: int, detail: str) -> str:
        del status_code
        message = f"{self.ENGINE_LABEL} API request failed"
        if detail:
            message = f"{message}: {detail}"
        return message

    def _extract_audio_from_payload(self, payload: Mapping[str, object]) -> bytes:
        data_value = _first_path_value(
            payload,
            (
                ("choices", 0, "message", "audio", "data"),
                ("choices", 0, "message", "audio"),
                ("output", "audio", "data"),
                ("audio", "data"),
                ("data",),
            ),
        )
        audio = _decode_audio_data(data_value)
        if audio:
            if not _looks_like_audio(audio, ""):
                raise RuntimeError(
                    f"{self.ENGINE_LABEL} API returned an unsupported audio format"
                )
            return audio

        url_value = _first_path_value(
            payload,
            (
                ("choices", 0, "message", "audio", "url"),
                ("output", "audio", "url"),
                ("output", "url"),
                ("audio", "url"),
                ("url",),
            ),
        )
        audio_url = str(url_value or "").strip()
        if audio_url.startswith(("http://", "https://")):
            return self._download_audio(audio_url)
        return b""

    def _download_audio(self, url: str) -> bytes:
        url = self._normalize_audio_download_url(url)
        self._check_request_active()
        download_started_at = time.monotonic()
        pool_started_at = time.monotonic()
        session = self._session_pool.get()
        self._add_diagnostic_seconds(
            "connection_pool_wait_s",
            time.monotonic() - pool_started_at,
        )

        def timed_get(current_url: str, **kwargs: Any) -> requests.Response:
            self._check_request_active()
            request_started_at = time.monotonic()
            self._increment_diagnostic("audio_download_attempts")
            session_reused = self._session_reuse_hint(session, current_url)
            kwargs["timeout"] = self._requests_timeout()
            try:
                response = session.get(current_url, **kwargs)
            except Exception:
                self._set_diagnostic(
                    "audio_download_failed_after_s",
                    max(0.0, time.monotonic() - request_started_at),
                )
                raise
            self._set_diagnostic("audio_download_request_started_at", request_started_at)
            self._record_response_headers(
                response,
                prefix="audio_download",
                request_started_at=request_started_at,
                session_reused=session_reused,
            )
            return response

        def timed_url_validator(candidate: str) -> bool:
            self._check_request_active()
            started_at = time.monotonic()
            hostname_requires_dns = False
            try:
                parsed = urlsplit(str(candidate or "").strip())
                host = str(parsed.hostname or "")
                try:
                    ipaddress.ip_address(host)
                    hostname_requires_dns = False
                except ValueError:
                    hostname_requires_dns = bool(host)
                return self._prepare_pinned_audio_url(session, candidate)
            finally:
                elapsed = max(0.0, time.monotonic() - started_at)
                if hostname_requires_dns:
                    self._add_diagnostic_seconds("dns_s", elapsed)
                    self._add_diagnostic_seconds("audio_download_dns_s", elapsed)
                self._check_request_active()

        with open_validated_requests_response(
            url,
            url_validator=timed_url_validator,
            timeout=self._requests_timeout(),
            label=f"{self.ENGINE_LABEL} audio download",
            headers={
                "Accept": "audio/*, application/octet-stream",
                "Accept-Encoding": "identity",
            },
            stream=True,
            max_redirects=_MAX_AUDIO_REDIRECTS,
            request_get=timed_get,
        ) as response:
            self._check_request_active()
            status_code = int(getattr(response, "status_code", 200) or 0)
            content_type = str(
                response.headers.get("content-type", "") or ""
            ).lower()
            if status_code >= 400:
                content = read_bounded_requests_response(
                    response,
                    limit=_MAX_ERROR_RESPONSE_BYTES,
                    label=f"{self.ENGINE_LABEL} audio download error response",
                )
                try:
                    response.raise_for_status()
                except Exception as exc:
                    detail = _response_error_detail(content, content_type)
                    message = f"{self.ENGINE_LABEL} audio download failed"
                    if detail:
                        message = f"{message}: {detail}"
                    raise RuntimeError(message) from exc
                raise RuntimeError(f"{self.ENGINE_LABEL} audio download failed")
            audio = read_bounded_requests_response(
                _TimedRequestsResponse(
                    response,
                    engine=self,
                    metric_prefix="audio_download",
                    request_started_at=float(
                        (self._current_diagnostics() or {}).get(
                            "audio_download_request_started_at",
                            download_started_at,
                        )
                    ),
                    body_is_audio=True,
                ),
                limit=_MAX_AUDIO_BYTES,
                label=f"{self.ENGINE_LABEL} audio download",
            )
        self._set_diagnostic(
            "audio_download_full_s",
            max(0.0, time.monotonic() - download_started_at),
        )
        if not audio:
            raise RuntimeError(f"{self.ENGINE_LABEL} audio download returned empty audio")
        if not _looks_like_audio(audio, content_type):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} audio download returned an unsupported format"
            )
        return audio

    def _is_safe_audio_url(self, url: str) -> bool:
        return _is_safe_audio_download_url(url, api_base_url=self.base_url)

    def _normalize_audio_download_url(self, url: str) -> str:
        return str(url or "").strip()


class MimoTTS(_APITTSBase):
    ENGINE_ID = "mimo_tts"
    ENGINE_LABEL = "MiMo TTS"
    AUTH_HEADER_NAME = "api-key"
    AUTH_HEADER_PREFIX = ""

    def _prewarm_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")
        clean_voice = str(voice or self.default_voice or "").strip()
        if not clean_voice:
            raise ValueError("Voice ID cannot be empty")

        payload = {
            "model": self.model,
            "modalities": ["text", "audio"],
            "audio": {"voice": clean_voice, "format": "wav"},
            "messages": [
                {
                    "role": "user",
                    "content": "Read the following assistant message aloud exactly.",
                },
                {"role": "assistant", "content": clean_text},
            ],
        }
        try:
            return self._request_json_audio(f"{self.base_url}/chat/completions", payload)
        except Exception as exc:
            logger.error(
                "MiMo TTS synthesis failed (%s)",
                safe_exception_summary(exc),
            )
            raise RuntimeError(f"MiMo TTS synthesis failed: {exc}") from exc


class QwenTTS(_APITTSBase):
    ENGINE_ID = "qwen_tts"
    ENGINE_LABEL = "Qwen TTS"

    def _prewarm_url(self) -> str:
        return (
            f"{self.base_url}/services/aigc/"
            "multimodal-generation/generation"
        )

    def _with_transient_transport_retry(
        self,
        operation: Callable[[], Any],
        *,
        operation_label: str,
        retry_delays: tuple[float, ...],
    ) -> Any:
        for retry_index in range(len(retry_delays) + 1):
            try:
                self._check_request_active()
                return operation()
            except Exception as exc:
                if isinstance(exc, (_APITTSWallClockTimeout, _APITTSCancelled)):
                    raise
                if not _is_retryable_qwen_transport_error(exc):
                    raise
                self._session_pool.close_current()
                if retry_index >= len(retry_delays):
                    raise _QwenTransportRetriesExhausted(
                        _QWEN_NETWORK_ERROR_MESSAGE
                    ) from exc
                delay = retry_delays[retry_index]
                logger.warning(
                    "Qwen TTS %s hit a transient transport failure; "
                    "retrying with a fresh HTTPS session (%d/%d)",
                    operation_label,
                    retry_index + 1,
                    len(retry_delays),
                )
                wait_started_at = time.monotonic()
                remaining = self._check_request_active()
                deadline_limited = remaining is not None and delay >= remaining
                bounded_delay = (
                    delay
                    if remaining is None
                    else min(delay, max(0.0, remaining))
                )
                self._close_requested.wait(bounded_delay)
                self._add_diagnostic_seconds(
                    "retry_wait_s",
                    time.monotonic() - wait_started_at,
                )
                self._check_request_active()
                if deadline_limited:
                    raise _APITTSWallClockTimeout(
                        f"{self.ENGINE_LABEL} request exceeded the configured "
                        "wall-clock timeout"
                    )
        raise RuntimeError(_QWEN_NETWORK_ERROR_MESSAGE)

    def _adapter_max_retries(self) -> int:
        # The synthesis POST is not idempotent.  Keep urllib3 from applying
        # additional implicit retries underneath the single, explicit TLS
        # recovery attempt below, even for legacy configs with max_retries > 0.
        return 0

    def _post_json_response(
        self,
        url: str,
        payload: Mapping[str, object],
    ) -> requests.Response:
        post = super()._post_json_response
        return self._with_transient_transport_retry(
            lambda: post(url, payload),
            operation_label="API request",
            retry_delays=_QWEN_SYNTHESIS_RETRY_DELAYS,
        )

    def _download_audio(self, url: str) -> bytes:
        download = super()._download_audio
        return self._with_transient_transport_retry(
            lambda: download(url),
            operation_label="audio download",
            retry_delays=_QWEN_AUDIO_DOWNLOAD_RETRY_DELAYS,
        )

    def _api_request_failure_message(self, status_code: int, detail: str) -> str:
        message = super()._api_request_failure_message(status_code, detail)
        detail_lower = str(detail or "").casefold()
        if status_code == 429:
            return f"Qwen TTS API rate limit exceeded (HTTP 429): {detail}".rstrip(": ")
        if status_code == 404:
            return f"Qwen TTS API endpoint not found (HTTP 404): {detail}".rstrip(": ")
        if status_code == 400 and any(
            token in detail_lower
            for token in (
                "unsupported model",
                "model is not supported",
                "model_not_found",
                "model not found",
            )
        ):
            return f"Qwen TTS unsupported model: {detail}".rstrip(": ")
        if status_code >= 500:
            return f"Qwen TTS provider failure (HTTP {status_code}): {detail}".rstrip(": ")
        if status_code not in {401, 403} and not is_tts_authentication_error(
            f"status {status_code} {detail}"
        ):
            return message
        region = self.region or "selected"
        separator = " " if message.endswith((".", "!", "?")) else ". "
        return (
            f"{message}{separator}Authentication was rejected for service region "
            f"'{region}'; the API key may be invalid, revoked, or belong to "
            "another service region"
        )

    def _normalize_audio_download_url(self, url: str) -> str:
        """Upgrade DashScope's signed OSS result URLs to encrypted transport.

        DashScope has historically returned ``http://dashscope-result-...``
        URLs even though the same signed object is available over HTTPS. The
        shared downloader intentionally rejects public HTTP, so upgrade only
        Alibaba-controlled DashScope result hosts before validation.
        """

        candidate = super()._normalize_audio_download_url(url)
        try:
            parsed = urlsplit(candidate)
            host = str(parsed.hostname or "").rstrip(".").casefold()
            port = parsed.port
        except (TypeError, ValueError):
            return candidate
        if (
            parsed.scheme.casefold() != "http"
            or port not in (None, 80)
            or parsed.username is not None
            or parsed.password is not None
            or not _DASHSCOPE_RESULT_HOST_RE.fullmatch(host)
        ):
            return candidate

        logger.info("Upgrading Qwen TTS audio download URL to HTTPS")
        return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        del rate, volume
        self._begin_synthesis_diagnostics()
        succeeded = False
        try:
            preprocessing_started_at = time.monotonic()
            clean_text = str(text or "").strip()
            if not clean_text:
                raise ValueError("Text cannot be empty")
            clean_voice = str(voice or self.default_voice or "").strip()
            if not clean_voice:
                raise ValueError("Voice ID cannot be empty")

            language_type = _qwen_language_type(
                clean_text,
                self.language_type_hint,
            )
            payload = {
                "model": self.model,
                "input": {
                    "text": clean_text,
                    "voice": clean_voice,
                    "language_type": language_type,
                },
            }
            if self.instructions and qwen_tts_model_supports_instructions(self.model):
                payload["input"]["instructions"] = self.instructions
                payload["input"]["optimize_instructions"] = self.optimize_instructions
            self._set_diagnostic(
                "preprocessing_s",
                max(0.0, time.monotonic() - preprocessing_started_at),
            )
            self._set_diagnostic("language_type", language_type)
            audio = self._request_json_audio(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                payload,
            )
            succeeded = True
            return audio
        except _APITTSWallClockTimeout as exc:
            logger.error("Qwen TTS synthesis exceeded its wall-clock deadline")
            raise RuntimeError(
                "Qwen TTS synthesis failed: request exceeded the configured wall-clock timeout"
            ) from exc
        except _APITTSCancelled as exc:
            logger.info("Qwen TTS synthesis cancelled during shutdown")
            raise RuntimeError("Qwen TTS synthesis was cancelled during shutdown") from exc
        except requests.RequestException as exc:
            logger.error("Qwen TTS synthesis failed: network request failed")
            raise RuntimeError(
                f"Qwen TTS synthesis failed: {_QWEN_NETWORK_ERROR_MESSAGE}"
            ) from exc
        except Exception as exc:
            logger.error(
                "Qwen TTS synthesis failed (%s)",
                safe_exception_summary(exc),
            )
            raise RuntimeError(f"Qwen TTS synthesis failed: {exc}") from exc
        finally:
            self._finish_synthesis_diagnostics(succeeded=succeeded)


class QwenVoiceCloneTTS(QwenTTS):
    """Qwen TTS driven by a voice the player cloned from their own recording.

    Synthesis is byte-for-byte the same request as preset-voice Qwen TTS; only
    the model and the voice id differ, so everything is inherited.  What this
    subclass adds is resolving the voice list from the player's stored
    enrollments instead of the built-in catalog.
    """

    ENGINE_ID = "qwen_vc"
    ENGINE_LABEL = "Qwen Voice Cloning"

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._cloned_voices = get_cloned_voice_options(
            config if isinstance(config, Mapping) else {}
        )

    def is_available(self) -> bool:
        # A cloned voice is required: without one there is nothing to speak
        # with, and reporting availability would only defer the failure to the
        # first utterance in the middle of a conversation.
        return bool(super().is_available() and self._resolved_default_voice())

    def _resolved_default_voice(self) -> str:
        if self.default_voice:
            return self.default_voice
        if self._cloned_voices:
            return self._cloned_voices[0][0]
        return ""

    def get_available_voices(self) -> list[TTSVoice]:
        return [
            TTSVoice(
                id=voice_id,
                name=name,
                language=language,
                gender=gender,
                locale=locale,
            )
            for voice_id, name, language, gender, locale in self._cloned_voices
        ]

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        resolved_voice = str(voice or "").strip() or self._resolved_default_voice()
        if not resolved_voice:
            raise RuntimeError(
                "Qwen Voice Cloning has no cloned voice yet. Record one in "
                "Settings before enabling this engine."
            )
        return super().synthesize(text, resolved_voice, rate, volume)


def _looks_like_audio(content: bytes, content_type: str) -> bool:
    if not content:
        return False
    del content_type
    return (
        (content.startswith(b"RIFF") and content[8:12] == b"WAVE")
        or content.startswith(b"ID3")
        or content.startswith(b"OggS")
        or content.startswith(b"fLaC")
        or content.startswith(b"\x1aE\xdf\xa3")
        or (len(content) >= 12 and content[4:8] == b"ftyp")
        or (
        len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
        )
    )


def _first_path_value(payload: Any, paths: tuple[tuple[object, ...], ...]) -> Any:
    for path in paths:
        current = payload
        found = True
        for part in path:
            if isinstance(part, int):
                if isinstance(current, list) and 0 <= part < len(current):
                    current = current[part]
                else:
                    found = False
                    break
            elif isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _decode_audio_data(value: Any) -> bytes:
    if isinstance(value, bytes):
        if len(value) > _MAX_AUDIO_BYTES:
            raise RuntimeError("API TTS audio data exceeds the maximum allowed size")
        return value
    if isinstance(value, bytearray):
        return _decode_audio_data(bytes(value))
    if isinstance(value, Mapping):
        return _decode_audio_data(value.get("data") or value.get("audio"))
    text = str(value or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return b""
    if text.startswith("data:"):
        metadata, separator, text = text.partition(",")
        if not separator or ";base64" not in metadata.casefold():
            return b""
    max_encoded_chars = ((_MAX_AUDIO_BYTES + 2) // 3) * 4
    if len(text) > max_encoded_chars:
        raise RuntimeError("API TTS audio data exceeds the maximum allowed size")
    try:
        encoded = text.encode("ascii")
        audio = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return b""
    if len(audio) > _MAX_AUDIO_BYTES:
        raise RuntimeError("API TTS audio data exceeds the maximum allowed size")
    return audio


def _response_error_detail(content: bytes, content_type: str = "") -> str:
    del content_type
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return content.decode("utf-8", "replace").strip()[:500]
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("code") or error).strip()
        if error:
            return str(error).strip()
        for key in ("message", "msg", "code", "request_id"):
            value = payload.get(key)
            if value:
                return str(value).strip()
    return str(payload).strip()[:500]


def _url_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(str(url or "").strip())
        host = str(parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if not host or parsed.username is not None or parsed.password is not None:
        return None
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), host, port or default_port


def _audio_url_origin_for_log(url: str) -> tuple[str, str, object]:
    """Return path/query-free provider URL fields suitable for diagnostics."""

    try:
        parsed = urlsplit(str(url or "").strip())
        scheme = str(parsed.scheme or "invalid").casefold()
        hostname = str(parsed.hostname or "invalid-host").rstrip(".").casefold()
        port: object = parsed.port
    except (TypeError, ValueError):
        return "invalid", "invalid-host", "invalid"
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else "default"
    return scheme, hostname, port


def _requests_adapter_mount_prefix(url: str) -> str | None:
    """Return the origin prefix Requests will use after URL preparation."""

    try:
        prepared = requests.Request("GET", str(url or "").strip()).prepare()
        parsed = urlsplit(str(prepared.url or ""))
    except (requests.RequestException, TypeError, ValueError):
        return None
    if not parsed.netloc or parsed.scheme.casefold() not in {"http", "https"}:
        return None
    return f"{parsed.scheme.casefold()}://{parsed.netloc}/"


def _addresses_from_getaddrinfo(
    records: object,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    if not isinstance(records, (list, tuple)):
        return ()
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        except (IndexError, TypeError, ValueError):
            return ()
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _resolved_ip_addresses(
    host: str,
    port: int,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return ()
        return _addresses_from_getaddrinfo(records)
    return (literal,)


def _validated_audio_download_target(
    url: str,
    *,
    api_base_url: str,
    resolver: Callable[
        [str, int],
        tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ] = _resolved_ip_addresses,
) -> _PinnedAudioTarget | None:
    """Validate and resolve one audio URL into a DNS-pinned connection target."""

    candidate = str(url or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    origin = _url_origin(candidate)
    api_origin = _url_origin(api_base_url)
    if (
        origin is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.scheme.casefold() not in {"http", "https"}
    ):
        return None

    scheme, host, effective_port = origin
    addresses = resolver(host, effective_port)
    if not addresses:
        return None

    trusted_qwen_result_host = bool(_DASHSCOPE_RESULT_HOST_RE.fullmatch(host))
    is_local = all(
        is_local_provider_address(address)
        and not _is_dashscope_proxy_synthetic_address(address)
        for address in addresses
    )
    api_is_local = False
    api_addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] = ()
    if is_local and api_origin is not None:
        api_addresses = (
            addresses
            if api_origin == origin
            else resolver(api_origin[1], api_origin[2])
        )
        api_is_local = bool(api_addresses) and all(
            is_local_provider_address(address) for address in api_addresses
        )
    if is_local:
        safe = bool(
            api_is_local
            and api_origin is not None
            and scheme == api_origin[0]
            and effective_port == api_origin[2]
            and bool(set(addresses).intersection(api_addresses))
        )
    else:
        addresses_are_publicly_routable = all(
            (
                address.is_global
                and not _is_dashscope_proxy_synthetic_address(address)
            )
            or (
                trusted_qwen_result_host
                and not address.is_private
                and not address.is_loopback
                and not address.is_link_local
                and not address.is_multicast
                and not address.is_unspecified
                and not address.is_reserved
            )
            or (
                trusted_qwen_result_host
                and (
                    _is_dashscope_proxy_synthetic_address(address)
                )
            )
            for address in addresses
        )
        safe = bool(
            scheme == "https"
            and port in (None, 443)
            and addresses_are_publicly_routable
        )
    if not safe:
        return None

    mount_prefix = _requests_adapter_mount_prefix(candidate)
    if mount_prefix is None:
        return None

    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if scheme == "https" else 80
    host_header = (
        display_host
        if effective_port == default_port
        else f"{display_host}:{effective_port}"
    )
    return _PinnedAudioTarget(
        scheme=scheme,
        hostname=host,
        port=effective_port,
        address=str(addresses[0]),
        mount_prefix=mount_prefix,
        host_header=host_header,
    )


def _is_safe_audio_download_url(url: str, *, api_base_url: str) -> bool:
    """Reject credentials, insecure public URLs, and SSRF-capable destinations."""

    return _validated_audio_download_target(
        url,
        api_base_url=api_base_url,
    ) is not None


def _is_safe_api_request_url(
    url: str,
    *,
    allow_private_http: bool = False,
) -> bool:
    candidate = str(url or "").strip()
    try:
        parsed = urlsplit(candidate)
    except (TypeError, ValueError):
        return False
    origin = _url_origin(candidate)
    if origin is None or parsed.fragment:
        return False
    scheme, host, port = origin
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    addresses = _resolved_ip_addresses(host, port)
    explicit_local_host = is_local_provider_host(host)
    return bool(addresses) and all(
        address.is_loopback
        or (
            allow_private_http
            and (
                is_local_provider_address(address)
                or (
                    explicit_local_host
                    and is_local_provider_address(address, include_shared=True)
                )
            )
        )
        for address in addresses
    )


def _normalize_qwen_language_type_hint(value: object) -> str:
    hint = str(value or "").strip().lower().replace("_", "-")
    if hint in {"japanese", "ja", "jp", "日本語", "日文", "日语"}:
        return "Japanese"
    if hint in {"chinese", "zh", "zh-cn", "cn", "中文", "简体中文", "中国語"}:
        return "Chinese"
    if hint in {"korean", "ko", "kr", "한국어", "韩语", "韓国語"}:
        return "Korean"
    if hint in {"english", "en", "en-us", "en-gb", "英文", "英语"}:
        return "English"
    return ""


def _qwen_language_type(text: str, language_hint: object = "") -> str:
    value = str(text or "")
    hint = _normalize_qwen_language_type_hint(language_hint)
    if any("\u3040" <= char <= "\u30ff" for char in value):
        return "Japanese"
    if any("\uac00" <= char <= "\ud7af" for char in value):
        return "Korean"
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        if hint in {"Chinese", "Japanese", "Korean"}:
            return hint
        return "Chinese"
    stripped = value.strip()
    if stripped and all(ord(char) < 128 for char in stripped):
        return "English"
    return "Auto"
