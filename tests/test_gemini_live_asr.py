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
        _FakeClient.live_exit_count += 1
        return False


class _FakeClient:
    last_init_kwargs = None
    last_generate_kwargs = None
    last_live_kwargs = None
    last_live_session = None
    live_connect_count = 0
    live_exit_count = 0

    def __init__(self, **kwargs):
        _FakeClient.last_init_kwargs = kwargs
        self.models = SimpleNamespace(generate_content=self._generate_content)
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self._connect_live))

    def _generate_content(self, **kwargs):
        _FakeClient.last_generate_kwargs = kwargs
        return SimpleNamespace(text="Transcription: ありがとう")

    def _connect_live(self, **kwargs):
        _FakeClient.live_connect_count += 1
        _FakeClient.last_live_kwargs = kwargs
        _FakeClient.last_live_session = _FakeLiveSession()
        return _FakeLiveConnect(_FakeClient.last_live_session)


def _install_fake_genai(monkeypatch):
    _FakeClient.live_connect_count = 0
    _FakeClient.live_exit_count = 0
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
    provider.close()


def test_gemini_live_reuses_event_loop_and_websocket_session(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {
            "asr": {
                "gemini_live": {
                    "api_key": "test-key",
                    "use_live_api": True,
                }
            }
        }
    )

    first = provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)
    runner = provider._async_runner
    second = provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)

    assert first == second
    assert provider._async_runner is runner
    assert _FakeClient.live_connect_count == 1
    assert len(_FakeClient.last_live_session.sent) == 4

    provider.close()
    assert _FakeClient.live_exit_count == 1


def test_explicit_load_prewarms_live_session_for_first_utterance(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {"asr": {"gemini_live": {"api_key": "test-key", "use_live_api": True}}}
    )

    provider.load()
    provider._prewarm_thread.join(timeout=2)
    assert _FakeClient.live_connect_count == 1

    provider.transcribe(np.zeros(1600, dtype=np.float32), sample_rate=16000)
    assert _FakeClient.live_connect_count == 1

    provider.close()


def test_generic_prewarm_initializes_runtime_without_opening_live_session(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {"asr": {"gemini_live": {"api_key": "test-key", "use_live_api": True}}}
    )

    assert provider.prewarm() is True
    assert provider.is_loaded is True
    assert _FakeClient.last_init_kwargs == {"api_key": "test-key"}
    assert _FakeClient.live_connect_count == 0
    assert provider._prewarm_thread is None


def test_explicit_load_after_generic_prewarm_prepares_live_session(monkeypatch):
    _install_fake_genai(monkeypatch)
    provider = GeminiLiveASRProvider(
        {"asr": {"gemini_live": {"api_key": "test-key", "use_live_api": True}}}
    )
    opened: list[bool] = []

    monkeypatch.setattr(
        provider,
        "_start_live_prewarm",
        lambda runner: opened.append(runner is provider._async_runner),
    )

    assert provider.prewarm() is True
    assert opened == []

    provider.load()

    assert opened == [True]

    provider.close()
