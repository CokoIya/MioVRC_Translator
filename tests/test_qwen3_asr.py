from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from src.asr.errors import ASRMissingAPIKeyError
from src.asr.qwen3_asr import Qwen3ASRProvider


class _FakeOpenAI:
    last_init_kwargs = None
    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeOpenAI.last_init_kwargs = kwargs
        self.kwargs = kwargs
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        _FakeOpenAI.last_kwargs = kwargs
        message = SimpleNamespace(content="Transcription: ありがとう、今どこにいる？")
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def test_qwen3_asr_requires_api_key():
    provider = Qwen3ASRProvider({"asr": {"qwen3_asr": {"api_key": ""}}})

    with pytest.raises(ASRMissingAPIKeyError):
        provider.load()


def test_qwen3_asr_sends_audio_and_cleans_text(monkeypatch):
    fake_module = SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    provider = Qwen3ASRProvider(
        {
            "asr": {
                "qwen3_asr": {
                    "api_key": "test-key",
                    "region": "singapore",
                    "model": "qwen3-asr-flash",
                    "language": "ja",
                }
            }
        }
    )

    text = provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)

    assert text == "ありがとう、今どこにいる？"
    assert _FakeOpenAI.last_init_kwargs["base_url"] == (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    assert _FakeOpenAI.last_init_kwargs["timeout"] == 25.0
    assert _FakeOpenAI.last_init_kwargs["max_retries"] == 0
    assert _FakeOpenAI.last_kwargs["model"] == "qwen3-asr-flash"
    content = _FakeOpenAI.last_kwargs["messages"][0]["content"][0]
    assert content["type"] == "input_audio"
    assert content["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert _FakeOpenAI.last_kwargs["extra_body"] == {
        "asr_options": {"enable_itn": False, "language": "ja"}
    }
