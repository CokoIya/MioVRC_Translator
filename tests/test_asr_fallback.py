from __future__ import annotations

import numpy as np
import pytest

from src.asr.base import ASRProvider
from src.asr.errors import ASRMissingAPIKeyError, ASRTemporaryUnavailableError
from src.asr.fallback_asr import FallbackASR


class _MissingKeyASR(ASRProvider):
    provider_id = "missing"
    supports_partial = False

    def load(self, progress_callback=None) -> None:
        raise ASRMissingAPIKeyError("missing key")

    def transcribe(self, audio, sample_rate=16000, language=None, is_final=True) -> str:
        raise ASRMissingAPIKeyError("missing key")


class _FallbackASR(ASRProvider):
    provider_id = "fallback"
    supports_partial = True

    def __init__(self) -> None:
        self.loaded = False

    def load(self, progress_callback=None) -> None:
        self.loaded = True

    def transcribe(self, audio, sample_rate=16000, language=None, is_final=True) -> str:
        self.loaded = True
        return "fallback text"


def test_fallback_asr_uses_fallback_when_primary_load_fails():
    fallback = _FallbackASR()
    asr = FallbackASR(_MissingKeyASR(), fallback, auto_fallback=True)

    asr.load()

    assert fallback.loaded is True
    assert asr.supports_partial is True


def test_fallback_asr_uses_fallback_for_transcribe_failure():
    fallback = _FallbackASR()
    asr = FallbackASR(_MissingKeyASR(), fallback, auto_fallback=True)

    text = asr.transcribe(np.zeros(1600, dtype=np.float32))

    assert text == "fallback text"
    assert fallback.loaded is True


def test_fallback_asr_forwards_browser_capture_control():
    events: list[tuple[str, bool]] = []
    primary = _FallbackASR()
    fallback = _FallbackASR()
    primary.set_capture_enabled = lambda enabled: events.append(("primary", enabled))
    fallback.set_capture_enabled = lambda enabled: events.append(("fallback", enabled))
    asr = FallbackASR(primary, fallback)

    asr.set_capture_enabled(False)
    asr.set_capture_enabled(True)

    assert events == [
        ("primary", False),
        ("fallback", False),
        ("primary", True),
        ("fallback", True),
    ]


def test_realtime_timeout_fails_fast_without_loading_slow_local_fallback():
    class TemporaryFailureASR(_FallbackASR):
        def transcribe(self, audio, sample_rate=16000, language=None, is_final=True):
            raise ASRTemporaryUnavailableError("hard timeout")

    fallback = _FallbackASR()
    asr = FallbackASR(TemporaryFailureASR(), fallback, auto_fallback=True)

    with pytest.raises(ASRTemporaryUnavailableError, match="hard timeout"):
        asr.transcribe_realtime(np.zeros(1600, dtype=np.float32))

    assert fallback.loaded is False
    assert asr._using_fallback is False


def test_fallback_asr_forwards_pending_request_cancellation_once_per_provider():
    events: list[str] = []
    primary = _FallbackASR()
    fallback = _FallbackASR()
    primary.cancel_pending_requests = lambda: events.append("primary")
    fallback.cancel_pending_requests = lambda: events.append("fallback")
    asr = FallbackASR(primary, fallback)

    asr.cancel_pending_requests()

    assert events == ["primary", "fallback"]
