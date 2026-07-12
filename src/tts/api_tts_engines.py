from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .api_tts_config import (
    get_tts_api_voice_options,
    resolve_tts_api_config,
)
from .base import BaseTTS, TTSVoice
from .error_utils import is_tts_authentication_error
from .persona_instructions import qwen_tts_model_supports_instructions
from src.translators.factory import _float_setting, _int_setting
from src.utils.app_paths import read_secure_text, secure_file_path, writable_app_dir
from src.utils.http_session_pool import ThreadLocalSessionPool
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
_QWEN_SYNTHESIS_RETRY_DELAYS = (0.2,)
_QWEN_AUDIO_DOWNLOAD_RETRY_DELAYS = (0.2, 0.6)
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
        self.base_url = (
            validate_api_base_url(
                raw_base_url,
                label=f"{self.ENGINE_LABEL} API",
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
        self.max_retries = _int_setting(resolved.get("max_retries"), 0, minimum=0, maximum=3)
        self._session_pool = ThreadLocalSessionPool(self._create_session)

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            max_retries=self._adapter_max_retries(),
            pool_connections=4,
            pool_maxsize=4,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _adapter_max_retries(self) -> int:
        return self.max_retries

    @property
    def _session(self) -> requests.Session:
        """Compatibility accessor backed by the current thread's session."""

        return self._session_pool.get()

    def close_thread_context(self) -> None:
        self._session_pool.close_current()

    def close(self) -> None:
        self._session_pool.close()

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
        self._require_api_key()
        auth_value = (
            f"{self.AUTH_HEADER_PREFIX}{self.api_key}"
            if self.AUTH_HEADER_PREFIX
            else self.api_key
        )
        return {
            self.AUTH_HEADER_NAME: auth_value,
            "Content-Type": "application/json",
            "Accept": "audio/*, application/json",
            "Accept-Encoding": "identity",
        }

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(f"{self.ENGINE_LABEL} API Key is not configured")
        if self.api_key.startswith(_PROTECTED_SECRET_PREFIX):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} API Key is still encrypted and cannot be used"
            )

    def _request_json_audio(self, url: str, payload: Mapping[str, object]) -> bytes:
        if not _is_safe_api_request_url(url):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} API URL must use HTTPS or loopback HTTP"
            )
        response = self._post_json_response(
            url,
            payload,
        )
        try:
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
                        response,
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
                response,
                limit=response_limit,
                label=f"{self.ENGINE_LABEL} API response",
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if _looks_like_audio(content, content_type):
            return content

        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned non-audio data") from exc
        if not isinstance(data, Mapping):
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned invalid JSON data")
        audio = self._extract_audio_from_payload(data)
        if not audio:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned no audio data")
        return audio

    def _post_json_response(
        self,
        url: str,
        payload: Mapping[str, object],
    ) -> requests.Response:
        session = self._session_pool.get()
        return session.post(
            url,
            headers=self._auth_headers(),
            json=dict(payload),
            timeout=self.timeout_seconds,
            stream=True,
            allow_redirects=False,
        )

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
        session = self._session_pool.get()
        with open_validated_requests_response(
            url,
            url_validator=self._is_safe_audio_url,
            timeout=self.timeout_seconds,
            label=f"{self.ENGINE_LABEL} audio download",
            headers={
                "Accept": "audio/*, application/octet-stream",
                "Accept-Encoding": "identity",
            },
            stream=True,
            max_redirects=_MAX_AUDIO_REDIRECTS,
            request_get=session.get,
        ) as response:
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
                response,
                limit=_MAX_AUDIO_BYTES,
                label=f"{self.ENGINE_LABEL} audio download",
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
            logger.error("MiMo TTS synthesis failed: %s", exc)
            raise RuntimeError(f"MiMo TTS synthesis failed: {exc}") from exc


class QwenTTS(_APITTSBase):
    ENGINE_ID = "qwen_tts"
    ENGINE_LABEL = "Qwen TTS"

    def _with_transient_transport_retry(
        self,
        operation: Callable[[], Any],
        *,
        operation_label: str,
        retry_delays: tuple[float, ...],
    ) -> Any:
        for retry_index in range(len(retry_delays) + 1):
            try:
                return operation()
            except Exception as exc:
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
                time.sleep(delay)
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
        if not is_tts_authentication_error(f"status {status_code} {detail}"):
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
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")
        clean_voice = str(voice or self.default_voice or "").strip()
        if not clean_voice:
            raise ValueError("Voice ID cannot be empty")

        payload = {
            "model": self.model,
            "input": {
                "text": clean_text,
                "voice": clean_voice,
                "language_type": _qwen_language_type(clean_text, self.language_type_hint),
            },
        }
        if self.instructions and qwen_tts_model_supports_instructions(self.model):
            payload["input"]["instructions"] = self.instructions
            payload["input"]["optimize_instructions"] = self.optimize_instructions
        try:
            return self._request_json_audio(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                payload,
            )
        except requests.RequestException as exc:
            logger.error("Qwen TTS synthesis failed: network request failed")
            raise RuntimeError(
                f"Qwen TTS synthesis failed: {_QWEN_NETWORK_ERROR_MESSAGE}"
            ) from exc
        except Exception as exc:
            logger.error("Qwen TTS synthesis failed: %s", exc)
            raise RuntimeError(f"Qwen TTS synthesis failed: {exc}") from exc


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


def _resolved_ip_addresses(host: str, port: int) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
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
    return (literal,)


def _is_safe_audio_download_url(url: str, *, api_base_url: str) -> bool:
    """Reject credentials, insecure public URLs, and SSRF-capable destinations."""

    candidate = str(url or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    origin = _url_origin(candidate)
    api_origin = _url_origin(api_base_url)
    if (
        origin is None
        or parsed.fragment
        or parsed.scheme.casefold() not in {"http", "https"}
    ):
        return False

    scheme, host, effective_port = origin
    addresses = _resolved_ip_addresses(host, effective_port)
    if not addresses:
        return False

    is_loopback = all(address.is_loopback for address in addresses)
    api_is_local = False
    if api_origin is not None:
        api_addresses = (
            addresses
            if api_origin == origin
            else _resolved_ip_addresses(api_origin[1], api_origin[2])
        )
        api_is_local = bool(api_addresses) and all(
            address.is_loopback for address in api_addresses
        )
    if is_loopback:
        return bool(
            api_is_local
            and api_origin is not None
            and effective_port == api_origin[2]
        )

    return bool(
        scheme == "https"
        and port in (None, 443)
        and all(address.is_global for address in addresses)
    )


def _is_safe_api_request_url(url: str) -> bool:
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
    return bool(addresses) and all(address.is_loopback for address in addresses)


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
