from __future__ import annotations

from src.asr.factory import create_asr
from src.asr.fallback_asr import FallbackASR
from src.asr.webspeech_asr import WebSpeechASRProvider


def test_webspeech_factory_does_not_wrap_local_fallback_by_default():
    provider = create_asr(
        {
            "asr": {
                "engine": "webspeech",
                "auto_fallback": True,
                "webspeech": {"auto_open_browser": False},
            }
        }
    )

    try:
        assert isinstance(provider, WebSpeechASRProvider)
    finally:
        provider.close()


def test_webspeech_factory_can_opt_into_local_fallback():
    provider = create_asr(
        {
            "asr": {
                "engine": "webspeech",
                "auto_fallback": True,
                "webspeech": {
                    "auto_open_browser": False,
                    "auto_fallback": True,
                },
            }
        }
    )

    try:
        assert isinstance(provider, FallbackASR)
        assert isinstance(provider.primary, WebSpeechASRProvider)
    finally:
        provider.close()
