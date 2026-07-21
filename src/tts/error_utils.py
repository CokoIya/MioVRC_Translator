"""Helpers for classifying TTS errors without exposing credentials."""

from __future__ import annotations


_AUTH_ERROR_TOKENS = (
    "invalid api-key",
    "invalid api key",
    "invalidapikey",
    "invalid_api_key",
    "incorrect api key",
    "authentication failed",
    "authentication rejected",
    "authentication was rejected",
    "unauthorized",
    "http 401",
    "status 401",
    "401 unauthorized",
)

_RATE_LIMIT_ERROR_TOKENS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "http 429",
    "status 429",
    "429 too many requests",
    "throttl",
)

_UNSUPPORTED_MODEL_ERROR_TOKENS = (
    "unsupported model",
    "model is not supported",
    "model not supported",
    "model_not_found",
    "model not found",
    "invalid model",
)

_INVALID_ENDPOINT_ERROR_TOKENS = (
    "invalid endpoint",
    "endpoint not found",
    "unknown endpoint",
    "invalid base url",
    "base url is malformed",
    "base url is not configured",
    "api url must use https",
    "redirects are not allowed",
    "redirect is not allowed",
)

_NETWORK_ERROR_TOKENS = (
    "network connection was interrupted",
    "unexpected eof while reading",
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
    "remote end closed connection without response",
    "connection reset",
    "connection aborted",
    "broken pipe",
    "connect timeout",
    "connection timed out",
    "read timed out",
    "failed to establish a new connection",
    "temporary failure in name resolution",
    "name or service not known",
)

_TIMEOUT_ERROR_TOKENS = (
    "wall-clock timeout",
    "wall clock timeout",
    "deadline exceeded",
    "exceeded the configured timeout",
    "connect timeout",
    "connection timed out",
    "read timed out",
    "timed out",
)

_CERTIFICATE_ERROR_TOKENS = (
    "certificate verify failed",
    "hostname mismatch",
    "self signed certificate",
    "unknown ca",
)

TTS_ERROR_TOKEN_PREFIX = "tts_error:"
TTS_ERROR_CODES = frozenset(
    {
        "authentication",
        "configuration",
        "rate_limit",
        "unsupported_model",
        "invalid_endpoint",
        "safety",
        "network",
        "timeout",
        "cancelled",
        "stopped",
        "queue_full",
        "pipeline_full",
        "suspended",
        "playback",
        "provider",
        "invalid_input",
        "unavailable",
    }
)


def tts_error_token(code: object) -> str:
    """Return the stable UI-bound token for a TTS failure category."""

    normalized = str(code or "provider").strip().casefold().replace("-", "_")
    if normalized not in TTS_ERROR_CODES:
        normalized = "provider"
    return f"{TTS_ERROR_TOKEN_PREFIX}{normalized}"


def tts_error_code(error: object) -> str:
    """Extract a stable TTS error category from a token or technical error.

    Provider details belong in diagnostic logs. UI callbacks use the returned
    category to select localized copy without exposing arbitrary English (or
    provider-supplied) text directly to the player.
    """

    message = str(error or "").strip().casefold()
    if not message:
        return "provider"
    if message.startswith(TTS_ERROR_TOKEN_PREFIX):
        code = message[len(TTS_ERROR_TOKEN_PREFIX) :].split(None, 1)[0].strip()
        return code if code in TTS_ERROR_CODES else "provider"
    if "api key" in message and any(
        marker in message
        for marker in (
            "not configured",
            "still encrypted",
            "invalid characters",
            "cannot be used",
        )
    ):
        return "configuration"
    if any(
        token in message
        for token in (
            "data_inspection_failed",
            "content inspection",
            "content moderation",
            "safety policy",
            "content policy",
        )
    ):
        return "safety"
    if any(token in message for token in _AUTH_ERROR_TOKENS):
        return "authentication"
    if any(token in message for token in _RATE_LIMIT_ERROR_TOKENS):
        return "rate_limit"
    if any(token in message for token in _UNSUPPORTED_MODEL_ERROR_TOKENS):
        return "unsupported_model"
    if any(token in message for token in _INVALID_ENDPOINT_ERROR_TOKENS):
        return "invalid_endpoint"
    if "queue" in message and "full" in message:
        return "queue_full"
    if "pipeline" in message and "full" in message:
        return "pipeline_full"
    if "cancel" in message:
        return "cancelled"
    if "stopped" in message or "shutting down" in message:
        return "stopped"
    if "temporarily paused" in message or "suspended" in message:
        return "suspended"
    if any(token in message for token in _CERTIFICATE_ERROR_TOKENS):
        return "network"
    if any(token in message for token in _TIMEOUT_ERROR_TOKENS):
        return "timeout"
    if any(token in message for token in _NETWORK_ERROR_TOKENS):
        return "network"
    if "playback" in message or "portaudio" in message or "audio device" in message:
        return "playback"
    if any(
        token in message
        for token in (
            "invalid tts input",
            "text cannot be empty",
            "voice id cannot be empty",
            "voice cannot be empty",
        )
    ):
        return "invalid_input"
    if "not available" in message or "not configured" in message:
        return "unavailable"
    return "provider"


def is_tts_authentication_error(error: object) -> bool:
    """Return whether an API-TTS failure represents rejected credentials."""

    message = str(error or "").strip().casefold()
    return bool(message) and (
        tts_error_code(message) == "authentication"
        or any(token in message for token in _AUTH_ERROR_TOKENS)
    )


def is_tts_network_error(error: object) -> bool:
    """Return whether a TTS failure is a transient network interruption.

    Authentication and certificate-validation failures need different user
    guidance, so they are deliberately excluded even when a transport wrapper
    adds generic connection wording around them.
    """

    message = str(error or "").strip().casefold()
    if not message or is_tts_authentication_error(message):
        return False
    if message.startswith(TTS_ERROR_TOKEN_PREFIX):
        return tts_error_code(message) in {"network", "timeout"}
    if any(token in message for token in _CERTIFICATE_ERROR_TOKENS):
        return False
    return any(token in message for token in _NETWORK_ERROR_TOKENS)
