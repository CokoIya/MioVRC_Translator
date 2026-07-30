from __future__ import annotations

import pytest

from src.asr.errors import ASRUnsupportedRuntimeError
from src.asr.factory import _create_ready_sensevoice_fallback, create_asr
from src.asr.fallback_asr import FallbackASR
from src.asr.text_corrections import LayeredASRCorrector
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


def test_automatic_local_fallback_never_downloads_missing_model(monkeypatch):
    config = {"asr": {"engine": "qwen3-asr"}}
    monkeypatch.setattr(
        "src.asr.model_manager.existing_model_path",
        lambda _spec: None,
    )
    monkeypatch.setattr(
        "src.asr.factory._create_sensevoice",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing fallback model must not be constructed")
        ),
    )

    with pytest.raises(ASRUnsupportedRuntimeError, match="not installed"):
        _create_ready_sensevoice_fallback(
            config,
            LayeredASRCorrector(config),
        )
