"""Retry rules shared by the HTTP translators."""

from __future__ import annotations

import pytest

from src.translators import web_translator_base as base_module
from src.translators.web_translator_base import WebRateLimited, WebTranslatorBase


class _Response:
    def __init__(self, status: int, payload=None, headers=None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Probe(WebTranslatorBase):
    PROVIDER_LABEL = "Probe"

    def __init__(self, responses, max_retries: int = 2) -> None:
        super().__init__()
        self._timeout_s = 5.0
        self._max_retries = max_retries
        self.responses = list(responses)
        self.sent = 0
        self._init_session_pool("https://probe.example")

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        return self.request()

    def request(self) -> str:
        def send(_session):
            self.sent += 1
            return self.responses.pop(0)

        return self._send_with_retries(send, lambda response: response.json()["text"])


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(base_module.time, "sleep", recorded.append)
    return recorded


def test_a_transient_failure_is_retried_with_bounded_jittered_backoff(sleeps):
    probe = _Probe([_Response(503), _Response(200, {"text": "ok"})])
    try:
        assert probe.request() == "ok"
    finally:
        probe.close()

    assert probe.sent == 2
    assert len(sleeps) == 1
    assert 0.2 <= sleeps[0] <= 0.3


def test_a_rate_limit_asking_for_longer_than_the_cap_is_not_retried(sleeps):
    probe = _Probe([_Response(429, headers={"Retry-After": "30"}), _Response(200, {"text": "late"})])
    try:
        with pytest.raises(RuntimeError, match="Probe translation failed: Probe rate limit reached"):
            probe.request()
    finally:
        probe.close()

    assert probe.sent == 1
    assert sleeps == []


def test_a_short_retry_after_is_honoured(sleeps):
    probe = _Probe([_Response(429, headers={"Retry-After": "0.9"}), _Response(200, {"text": "ok"})])
    try:
        assert probe.request() == "ok"
    finally:
        probe.close()

    assert sleeps and sleeps[0] >= 0.9 * 0.8


def test_retries_stop_at_the_configured_count(sleeps):
    probe = _Probe([_Response(500)] * 5, max_retries=1)
    try:
        with pytest.raises(RuntimeError):
            probe.request()
    finally:
        probe.close()

    assert probe.sent == 2


def test_rate_limit_exception_carries_the_hint():
    exc = WebRateLimited("slow down", 2.5)
    assert exc.retry_after_s == 2.5
    assert isinstance(exc, RuntimeError)
