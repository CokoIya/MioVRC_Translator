from __future__ import annotations

import pytest

from src.tts.error_utils import (
    is_tts_authentication_error,
    is_tts_network_error,
)


@pytest.mark.parametrize(
    "message",
    (
        "Qwen TTS network connection was interrupted. Please try again.",
        "SSLError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol",
        "Remote end closed connection without response",
        "HTTPSConnectionPool: Read timed out.",
        "Connection reset by peer",
        "Failed to establish a new connection",
    ),
)
def test_tts_network_error_recognizes_transient_transport_failures(message):
    assert is_tts_network_error(message) is True


@pytest.mark.parametrize(
    "message",
    (
        "Qwen TTS API request failed: Invalid API-key provided.",
        "HTTPS connection failed: certificate verify failed",
        "HTTP 400: invalid request",
        "Audio playback failed",
        "",
        None,
    ),
)
def test_tts_network_error_excludes_failures_with_different_guidance(message):
    assert is_tts_network_error(message) is False


def test_tts_authentication_error_remains_distinct_from_network_error():
    message = "HTTPS request failed with status 401 Unauthorized"

    assert is_tts_authentication_error(message) is True
    assert is_tts_network_error(message) is False
