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
    get_tts_api_voice_options,
    normalize_cloned_voices,
    resolve_tts_api_config,
    usable_cloned_voice_options,
)
from .base import BaseTTS, TTSVoice
from .error_utils import is_tts_authentication_error
from .persona_instructions import qwen_tts_model_supports_instructions
from .wav_utils import (
    concat_pcm16_wavs,
    scale_wav_volume_pcm16,
    time_stretch_pcm16_wav,
)
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
# DashScope documents 600 characters per qwen3-tts request; longer text is
# answered with InvalidParameter, so it is split at sentence boundaries.
QWEN_TTS_MAX_TEXT_CHARS = 600
# Qwen-Audio-TTS (cloned voices) uses the SpeechSynthesizer service instead of
# multimodal-generation, and accepts a native speaking rate.
QWEN_AUDIO_TTS_SYNTHESIS_PATH = "/services/audio/tts/SpeechSynthesizer"
QWEN_AUDIO_TTS_SAMPLE_RATE = 24000
QWEN_AUDIO_TTS_MIN_RATE = 0.5
QWEN_AUDIO_TTS_MAX_RATE = 2.0
# ``language_hints`` values Qwen-Audio-TTS accepts, keyed by the
# ``language_type`` the shared Qwen language detection produces.
_QWEN_AUDIO_LANGUAGE_HINTS = {
    "Chinese": "zh",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "German": "de",
    "French": "fr",
    "Russian": "ru",
    "Italian": "it",
    "Spanish": "es",
    "Portuguese": "pt",
}
# CJK sentence enders split anywhere; ASCII ones only before whitespace so
# decimals and abbreviations ("3.5", "e.g.") stay intact.
# Provider error codes are identifiers such as "AccessDenied.Unpurchased";
# anything else in a ``code`` field is not repeated into the error detail.
_PROVIDER_ERROR_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；\n])|(?<=[.!?;])(?=\s)")
_SOFT_SPLIT_RE = re.compile(r"(?<=[，,、\s])")
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
        # The probe is a HEAD against a POST-only route, so any HTTP answer
        # (typically 400) proves DNS/TCP/TLS work.  401/403 are the exception:
        # the transport is fine but the key or region is wrong, and saying
        # "finished" here hid that until the first sentence failed.
        credentials_rejected = result.status_code in (401, 403)
        logger.log(
            logging.WARNING if credentials_rejected or not result.succeeded else logging.INFO,
            "%s prewarm %s (status=%s transport_ok=%s credentials_rejected=%s "
            "elapsed_ms=%.0f error_type=%s)",
            self.ENGINE_LABEL,
            "finished" if result.succeeded and not credentials_rejected else "failed",
            result.status_code if result.status_code is not None else "unknown",
            result.succeeded,
            credentials_rejected,
            result.elapsed_s * 1000.0,
            result.error_type or "none",
        )

    @staticmethod
    def _apply_local_rate_volume(audio: bytes, rate: object, volume: object) -> bytes:
        """Apply the player's rate/volume to WAV audio the service returned.

        Neither DashScope qwen3-tts nor MiMo exposes a speed or gain
        parameter, so the sliders are honoured here: WSOLA time-stretch for
        the rate and a plain gain for the volume.  Non-WAV payloads are left
        untouched.
        """
        try:
            speed = float(rate)
        except (TypeError, ValueError):
            speed = 1.0
        try:
            loudness = float(volume)
        except (TypeError, ValueError):
            loudness = 1.0
        speed = speed if speed > 0 else 1.0
        loudness = max(0.0, min(1.0, loudness)) if loudness == loudness else 1.0
        if abs(speed - 1.0) < 0.01 and abs(loudness - 1.0) < 0.01:
            return audio
        if not (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"):
            logger.debug("Skipping local rate/volume processing for non-WAV audio")
            return audio
        try:
            processed = audio
            if abs(speed - 1.0) >= 0.01:
                processed = time_stretch_pcm16_wav(processed, speed)
            if abs(loudness - 1.0) >= 0.01:
                processed = scale_wav_volume_pcm16(processed, loudness)
            return processed
        except Exception as exc:
            logger.warning(
                "Local TTS rate/volume processing failed (%s); playing the original audio",
                safe_exception_summary(exc),
            )
            return audio

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
        # Diagnostics also arm the wall-clock deadline; without them the
        # configured wall timeout was ignored for MiMo.
        self._begin_synthesis_diagnostics()
        succeeded = False
        try:
            audio = self._request_json_audio(f"{self.base_url}/chat/completions", payload)
            audio = self._apply_local_rate_volume(audio, rate, volume)
            succeeded = True
            return audio
        except _APITTSWallClockTimeout as exc:
            logger.error("MiMo TTS synthesis exceeded its wall-clock deadline")
            raise RuntimeError(
                "MiMo TTS synthesis failed: request exceeded the configured wall-clock timeout"
            ) from exc
        except _APITTSCancelled as exc:
            logger.info("MiMo TTS synthesis cancelled during shutdown")
            raise RuntimeError("MiMo TTS synthesis was cancelled during shutdown") from exc
        except Exception as exc:
            logger.error(
                "MiMo TTS synthesis failed (%s)",
                safe_exception_summary(exc),
            )
            raise RuntimeError(f"MiMo TTS synthesis failed: {exc}") from exc
        finally:
            self._finish_synthesis_diagnostics(succeeded=succeeded)


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
        if status_code == 403 and any(
            token in detail_lower
            for token in (
                "arrearage",
                "unpurchased",
                "insufficient balance",
                "overdue",
                # Model.AccessDenied / Workspace.AccessDenied: the key is valid
                # but lacks permission for this model or workspace;
                # AllocationQuota.FreeTierOnly: the free quota is used up and
                # paid usage is switched off.
                "accessdenied",
                "access denied",
                # AccessDenied.Unpurchased (Model Studio is not activated),
                # DashScope's most common 403, also when only its prose
                # survives a gateway.
                "access to model denied",
                "eligible for using the model",
                "freetieronly",
                "free tier",
            )
        ):
            # DashScope uses 403 for an unpaid balance, an unsubscribed model
            # or missing model permission as well; telling the player to check
            # the key is wrong.
            separator = " " if str(detail).endswith((".", "!", "?")) else ". "
            return (
                f"Qwen TTS account access denied (HTTP 403): {detail}{separator}"
                "The DashScope account balance or model subscription needs attention"
            )
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

    def _synthesis_url(self) -> str:
        return f"{self.base_url}/services/aigc/multimodal-generation/generation"

    def _synthesis_payload(
        self,
        chunk: str,
        voice: str,
        *,
        language_type: str,
        rate: object,
    ) -> dict[str, Any]:
        del rate  # qwen3-tts has no speed parameter; it is applied locally.
        payload: dict[str, Any] = {
            "model": self.model,
            "input": {
                "text": chunk,
                "voice": voice,
                "language_type": language_type,
            },
        }
        if self.instructions and qwen_tts_model_supports_instructions(self.model):
            payload["input"]["instructions"] = self.instructions
            payload["input"]["optimize_instructions"] = self.optimize_instructions
        return payload

    def _local_rate_factor(self, rate: object) -> object:
        """Speed change still to apply locally after the service's own."""

        return rate

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
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
            chunks = split_text_for_synthesis(clean_text, QWEN_TTS_MAX_TEXT_CHARS)
            self._set_diagnostic(
                "preprocessing_s",
                max(0.0, time.monotonic() - preprocessing_started_at),
            )
            self._set_diagnostic("language_type", language_type)
            self._set_diagnostic("text_chunks", len(chunks))
            parts: list[bytes] = []
            for chunk in chunks:
                parts.append(
                    self._request_json_audio(
                        self._synthesis_url(),
                        self._synthesis_payload(
                            chunk,
                            clean_voice,
                            language_type=language_type,
                            rate=rate,
                        ),
                    )
                )
            audio = parts[0] if len(parts) == 1 else concat_pcm16_wavs(parts)
            audio = self._apply_local_rate_volume(
                audio,
                self._local_rate_factor(rate),
                volume,
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


def _qwen_audio_native_rate(rate: object) -> float | None:
    """The speaking rate to send to Qwen-Audio-TTS, or None for the default."""

    try:
        speed = float(rate)
    except (TypeError, ValueError):
        return None
    if speed != speed or speed <= 0 or abs(speed - 1.0) < 0.01:
        return None
    return round(
        max(QWEN_AUDIO_TTS_MIN_RATE, min(QWEN_AUDIO_TTS_MAX_RATE, speed)),
        3,
    )


class QwenVoiceCloneTTS(QwenTTS):
    """Qwen-Audio-TTS driven by a voice the player cloned from a recording.

    Cloned voices are enrolled with ``voice-enrollment`` and spoken through
    the SpeechSynthesizer service (not multimodal-generation). The response
    shape, result download and error handling match preset-voice Qwen TTS, so
    only the request differs. The voice list comes from the player's stored
    enrollments instead of the built-in catalog.
    """

    ENGINE_ID = "qwen_vc"
    ENGINE_LABEL = "Qwen Voice Cloning"

    def _prewarm_url(self) -> str:
        return f"{self.base_url}{QWEN_AUDIO_TTS_SYNTHESIS_PATH}"

    def _synthesis_url(self) -> str:
        return f"{self.base_url}{QWEN_AUDIO_TTS_SYNTHESIS_PATH}"

    def _synthesis_payload(
        self,
        chunk: str,
        voice: str,
        *,
        language_type: str,
        rate: object,
    ) -> dict[str, Any]:
        input_payload: dict[str, Any] = {
            "text": chunk,
            "voice": voice,
            # WAV keeps multi-chunk joins and the local volume step lossless.
            "format": "wav",
            "sample_rate": QWEN_AUDIO_TTS_SAMPLE_RATE,
        }
        native_rate = _qwen_audio_native_rate(rate)
        if native_rate is not None:
            input_payload["rate"] = native_rate
        language_hint = _QWEN_AUDIO_LANGUAGE_HINTS.get(language_type)
        if language_hint:
            input_payload["language_hints"] = [language_hint]
        return {"model": self.model, "input": input_payload}

    def _local_rate_factor(self, rate: object) -> object:
        native_rate = _qwen_audio_native_rate(rate)
        if native_rate is None:
            return rate
        try:
            # Only the part of the request beyond the service's 0.5-2.0
            # range is left for the local time-stretch.
            return float(rate) / native_rate
        except (TypeError, ValueError, ZeroDivisionError):
            return 1.0

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        engine_config = config if isinstance(config, Mapping) else {}
        # Only voices enrolled for this synthesis model and service region
        # are usable; DashScope rejects every other combination with an
        # opaque HTTP 400.
        self._cloned_voices = usable_cloned_voice_options(engine_config)
        self._unusable_cloned_voices = {
            voice["voice_id"]: voice
            for voice in normalize_cloned_voices(engine_config.get("custom_voices"))
            if voice["voice_id"] not in {entry[0] for entry in self._cloned_voices}
        }

    def is_available(self) -> bool:
        # A cloned voice is required: without one there is nothing to speak
        # with, and reporting availability would only defer the failure to the
        # first utterance in the middle of a conversation.  Voices that exist
        # but belong to another model/region keep the engine "available" so
        # the mismatch is reported as a specific error instead of silence.
        return bool(
            super().is_available()
            and (self._resolved_default_voice() or self._unusable_cloned_voices)
        )

    def _resolved_default_voice(self) -> str:
        default = str(self.default_voice or "").strip()
        usable_ids = {entry[0] for entry in self._cloned_voices}
        if default and default in usable_ids:
            return default
        if self._cloned_voices:
            return self._cloned_voices[0][0]
        if default and default not in self._unusable_cloned_voices:
            # A voice id the player entered by hand (not in the local list).
            return default
        return ""

    def _raise_for_unusable_voice(self, voice_id: str) -> None:
        unusable = self._unusable_cloned_voices.get(voice_id)
        if unusable is None:
            return
        registered_model = unusable.get("target_model") or "another model"
        registered_region = unusable.get("region") or ""
        where = f" in region '{registered_region}'" if registered_region else ""
        raise RuntimeError(
            f"The cloned voice '{unusable.get('display_name') or voice_id}' "
            f"was registered for model '{registered_model}'{where}; it cannot be "
            f"used with model '{self.model}' in region '{self.region}'. Record "
            "the voice again for the current model, or switch the model back."
        )

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
            # Explain a stored-but-unusable voice before falling back to the
            # generic "record one first" message.
            default = str(self.default_voice or "").strip()
            self._raise_for_unusable_voice(
                default or next(iter(self._unusable_cloned_voices), "")
            )
            raise RuntimeError(
                "Qwen Voice Cloning has no cloned voice yet. Record one in "
                "Settings before enabling this engine."
            )
        self._raise_for_unusable_voice(resolved_voice)
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
        message = next(
            (str(payload[key]).strip() for key in ("message", "msg") if payload.get(key)),
            "",
        )
        code = payload.get("code")
        code = code.strip() if isinstance(code, str) else ""
        if message and _PROVIDER_ERROR_CODE_RE.fullmatch(code):
            # DashScope's prose is often generic ("Access to model denied.
            # Please make sure you are eligible for using the model."); its
            # code (AccessDenied.Unpurchased, Arrearage, ...) says why, and
            # dropping it made an inactive account read as a rejected key.
            if code.casefold() in message.casefold():
                return message
            return f"{code}: {message}"
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


# DashScope qwen3-tts ``language_type`` values and the target-language
# spellings the app uses for them.  Anything else is left to "Auto".
_QWEN_LANGUAGE_TYPE_ALIASES: dict[str, frozenset[str]] = {
    "Japanese": frozenset({"japanese", "ja", "jp", "ja-jp", "日本語", "日文", "日语"}),
    "Chinese": frozenset(
        {"chinese", "zh", "zh-cn", "zh-hans", "zh-tw", "zh-hant", "cn", "中文", "简体中文", "中国語"}
    ),
    "Korean": frozenset({"korean", "ko", "kr", "ko-kr", "한국어", "韩语", "韓国語"}),
    "English": frozenset({"english", "en", "en-us", "en-gb", "英文", "英语", "英語"}),
    "German": frozenset({"german", "de", "de-de", "deutsch", "德语", "ドイツ語"}),
    "Italian": frozenset({"italian", "it", "it-it", "italiano", "意大利语", "イタリア語"}),
    "Portuguese": frozenset(
        {"portuguese", "pt", "pt-br", "pt-pt", "português", "葡萄牙语", "ポルトガル語"}
    ),
    "Spanish": frozenset({"spanish", "es", "es-es", "es-mx", "español", "西班牙语", "スペイン語"}),
    "French": frozenset({"french", "fr", "fr-fr", "français", "法语", "フランス語"}),
    "Russian": frozenset({"russian", "ru", "ru-ru", "русский", "俄语", "ロシア語"}),
}


def _normalize_qwen_language_type_hint(value: object) -> str:
    hint = str(value or "").strip().lower().replace("_", "-")
    if not hint:
        return ""
    for language_type, aliases in _QWEN_LANGUAGE_TYPE_ALIASES.items():
        if hint == language_type.lower() or hint in aliases:
            return language_type
    return ""


def qwen_tts_language_type_from_target(target_language: object) -> str:
    """Map an app target-language code to a DashScope ``language_type``.

    Returns an empty string for languages DashScope has no value for; the
    engine then sends ``Auto`` so the service detects the language itself.
    """
    return _normalize_qwen_language_type_hint(target_language)


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
    # Latin-script text follows the translation target.  It used to be
    # forced to English whenever the text was plain ASCII, which read
    # Spanish/German/... sentences with English phonology.
    return hint or "Auto"


def split_text_for_synthesis(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most ``limit`` characters at natural breaks."""
    clean = str(text or "").strip()
    limit = max(1, int(limit))
    if len(clean) <= limit:
        return [clean] if clean else []

    def _pack(units: list[str]) -> list[str]:
        packed: list[str] = []
        current = ""
        for unit in units:
            if not unit:
                continue
            if current and len(current) + len(unit) > limit:
                packed.append(current.strip())
                current = unit
            else:
                current += unit
        if current.strip():
            packed.append(current.strip())
        return packed

    chunks: list[str] = []
    for sentence_chunk in _pack(_SENTENCE_SPLIT_RE.split(clean)):
        if len(sentence_chunk) <= limit:
            chunks.append(sentence_chunk)
            continue
        for soft_chunk in _pack(_SOFT_SPLIT_RE.split(sentence_chunk)):
            if len(soft_chunk) <= limit:
                chunks.append(soft_chunk)
                continue
            chunks.extend(
                soft_chunk[index : index + limit].strip()
                for index in range(0, len(soft_chunk), limit)
            )
    return [chunk for chunk in chunks if chunk]
