"""Tests for VOICEVOX-compatible local TTS clients."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json

import pytest

from src.tts.voicevox_compatible_engine import VoicevoxCompatibleTTS
from src.tts.voicevox_engine import VoicevoxTTS


@dataclass
class FakeResponse:
    payload: object | None = None
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def __post_init__(self):
        if self.payload is not None and not self.content:
            self.content = json.dumps(self.payload).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        del chunk_size
        if self.content:
            yield self.content

    def close(self):
        self.closed = True


def test_voicevox_compatible_engine_flattens_speaker_styles(monkeypatch):
    engine = VoicevoxCompatibleTTS()

    monkeypatch.setattr(
        engine._session,
        "get",
        lambda url, timeout, stream: FakeResponse(
            payload=[
                {
                    "name": "Speaker A",
                    "styles": [
                        {"id": 1, "name": "Normal"},
                        {"id": 2147483646, "name": "Warm"},
                    ],
                }
            ]
        ),
    )

    voices = engine.get_available_voices()

    assert [(voice.id, voice.name) for voice in voices] == [
        ("1", "Speaker A / Normal"),
        ("2147483646", "Speaker A / Warm"),
    ]
    assert all(voice.language == "ja" for voice in voices)


def test_voicevox_compatible_engine_synthesizes_with_runtime_controls(monkeypatch):
    engine = VoicevoxCompatibleTTS()
    requests_seen: list[tuple[str, dict | None, dict | None]] = []

    def fake_post(url, params=None, json=None, timeout=None, stream=None):
        assert stream is True
        requests_seen.append((url, params, json))
        if url.endswith("/audio_query"):
            return FakeResponse(payload={"speedScale": 1.0, "volumeScale": 1.0})
        return FakeResponse(content=b"RIFF-wave")

    monkeypatch.setattr(engine._session, "post", fake_post)

    audio = engine.synthesize("hello", "77", rate=1.6, volume=0.35)

    assert audio == b"RIFF-wave"
    assert requests_seen[0][1] == {"text": "hello", "speaker": "77"}
    assert requests_seen[1][1] == {"speaker": "77"}
    assert requests_seen[1][2] == {"speedScale": 1.6, "volumeScale": 0.35}


def test_local_tts_engines_use_expected_default_ports():
    assert VoicevoxTTS()._base_url == "http://127.0.0.1:50021"


def test_voicevox_compatible_endpoint_is_limited_to_local_or_private_hosts():
    assert VoicevoxCompatibleTTS(host="192.168.1.50")._base_url == (
        "http://192.168.1.50:50021"
    )
    assert VoicevoxCompatibleTTS(host="::1")._base_url == "http://[::1]:50021"

    with pytest.raises(ValueError, match="loopback"):
        VoicevoxCompatibleTTS(host="public.example")
    with pytest.raises(ValueError):
        VoicevoxCompatibleTTS(host="user@127.0.0.1")
