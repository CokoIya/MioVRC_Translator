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

_CERTIFICATE_ERROR_TOKENS = (
    "certificate verify failed",
    "hostname mismatch",
    "self signed certificate",
    "unknown ca",
)


def is_tts_authentication_error(error: object) -> bool:
    """Return whether an API-TTS failure represents rejected credentials."""

    message = str(error or "").strip().casefold()
    return bool(message) and any(token in message for token in _AUTH_ERROR_TOKENS)


def is_tts_network_error(error: object) -> bool:
    """Return whether a TTS failure is a transient network interruption.

    Authentication and certificate-validation failures need different user
    guidance, so they are deliberately excluded even when a transport wrapper
    adds generic connection wording around them.
    """

    message = str(error or "").strip().casefold()
    if not message or is_tts_authentication_error(message):
        return False
    if any(token in message for token in _CERTIFICATE_ERROR_TOKENS):
        return False
    return any(token in message for token in _NETWORK_ERROR_TOKENS)
