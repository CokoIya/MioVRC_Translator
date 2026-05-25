from __future__ import annotations

import numpy as np

from src.asr.base import ASRProvider
from src.asr.errors import ASRMissingAPIKeyError
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
