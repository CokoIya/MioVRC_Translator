from __future__ import annotations

import pytest

from src.tts.error_utils import (
    is_tts_authentication_error,
    is_tts_network_error,
    tts_error_code,
    tts_error_token,
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


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("Model is not supported", "unsupported_model"),
        ("Endpoint not found", "invalid_endpoint"),
        ("Request exceeded the configured wall-clock timeout", "timeout"),
        ("Audio playback failed", "playback"),
        ("TTS synthesis queue is full", "queue_full"),
    ),
)
def test_tts_error_code_exposes_stable_localization_categories(message, expected):
    assert tts_error_code(message) == expected
    assert tts_error_code(tts_error_token(expected)) == expected


def test_tts_error_code_classifies_local_credential_configuration():
    assert (
        tts_error_code("Qwen TTS API Key is still encrypted and cannot be used")
        == "configuration"
    )
    assert (
        tts_error_code("Qwen TTS API Key contains invalid characters")
        == "configuration"
    )


def test_tts_error_code_classifies_provider_safety_rejection():
    assert tts_error_code("data_inspection_failed") == "safety"
    assert (
        tts_error_code(
            "HTTP 403 Authentication was rejected: data_inspection_failed"
        )
        == "safety"
    )


def test_tts_error_code_classifies_endpoint_and_voice_validation():
    assert tts_error_code("API redirects are not allowed") == "invalid_endpoint"
    assert tts_error_code("Voice ID cannot be empty") == "invalid_input"
