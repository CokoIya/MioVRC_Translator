from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from src.asr.errors import ASRMissingAPIKeyError
from src.asr.gemini_live_asr import GeminiLiveASRProvider


class _FakePart:
    @staticmethod
    def from_bytes(*, data, mime_type):
        return SimpleNamespace(data=data, mime_type=mime_type)


class _FakeBlob:
    def __init__(self, *, data, mime_type):
        self.data = data
        self.mime_type = mime_type


class _FakeLiveSession:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_realtime_input(self, **kwargs):
        self.sent.append(kwargs)

    def receive(self):
        return self._messages()

    async def _messages(self):
        yield SimpleNamespace(
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="Transcription: こんにちは"),
                turn_complete=False,
            )
        )
        yield SimpleNamespace(server_content=SimpleNamespace(turn_complete=True))


class _FakeLiveConnect:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeClient:
    last_init_kwargs = None
    last_generate_kwargs = None
    last_live_kwargs = None
    last_live_session = None

    def __init__(self, **kwargs):
        _FakeClient.last_init_kwargs = kwargs
        self.models = SimpleNamespace(generate_content=self._generate_content)
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self._connect_live))

    def _generate_content(self, **kwargs):
        _FakeClient.last_generate_kwargs = kwargs
        return SimpleNamespace(text="Transcription: ありがとう")

    def _connect_live(self, **kwargs):
        _FakeClient.last_live_kwargs = kwargs
        _FakeClient.last_live_session = _FakeLiveSession()
        return _FakeLiveConnect(_FakeClient.last_live_session)


def _install_fake_genai(monkeypatch):
    genai = SimpleNamespace(
        Client=_FakeClient,
        types=SimpleNamespace(Part=_FakePart, Blob=_FakeBlob),
    )
    google = ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)


def test_gemini_live_requires_api_key():
    provider = GeminiLiveASRProvider({"asr": {"gemini_live": {"api_key": ""}}})

    with pytest.raises(ASRMissingAPIKeyError):
        provider.load()


def test_gemini_generate_content_path_sends_wav_and_cleans_text(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {
            "asr": {
                "gemini_live": {
                    "api_key": "test-key",
                    "model": "gemini-3.5-flash",
                    "use_live_api": False,
                }
            }
        }
    )

    text = provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)

    assert text == "ありがとう"
    assert _FakeClient.last_init_kwargs == {"api_key": "test-key"}
    assert _FakeClient.last_generate_kwargs["model"] == "gemini-3.5-flash"
    part = _FakeClient.last_generate_kwargs["contents"][1]
    assert part.mime_type == "audio/wav"
    assert part.data.startswith(b"RIFF")


def test_gemini_live_path_sends_pcm_stream_and_collects_input_transcription(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {
            "asr": {
                "gemini_live": {
                    "api_key": "test-key",
                    "model": "gemini-3.5-flash",
                    "use_live_api": True,
                    "live_silence_duration_ms": 500,
                }
            }
        }
    )

    text = provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)

    assert text == "こんにちは"
    assert _FakeClient.last_live_kwargs["model"] == "gemini-3.1-flash-live-preview"
    config = _FakeClient.last_live_kwargs["config"]
    assert config["input_audio_transcription"] == {}
    assert config["realtime_input_config"]["automatic_activity_detection"]["silence_duration_ms"] == 500
    sent = _FakeClient.last_live_session.sent
    assert sent[0]["audio"].mime_type == "audio/pcm;rate=16000"
    assert sent[1]["audio_stream_end"] is True
