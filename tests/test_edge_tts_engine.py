from __future__ import annotations

import pytest

from src.tts.edge_tts_engine import EdgeTTS


def test_edge_tts_retries_no_audio_with_safe_parameters():
    attempts: list[tuple[str, str, str, str]] = []

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str, volume: str):
            attempts.append((text, voice, rate, volume))

        async def stream(self):
            if len(attempts) == 1:
                raise RuntimeError("No audio was received. Please verify parameters.")
            yield {"type": "audio", "data": b"ID3-audio"}

    class FakeEdgeTts:
        Communicate = FakeCommunicate

    engine = EdgeTTS.__new__(EdgeTTS)
    engine._edge_tts = FakeEdgeTts

    audio = engine.synthesize("嗯", "zh-CN-XiaoxiaoNeural", rate=1.0, volume=0.8)

    assert audio == b"ID3-audio"
    assert attempts == [
        ("嗯", "zh-CN-XiaoxiaoNeural", "+0%", "-20%"),
        ("嗯。", "zh-CN-XiaoxiaoNeural", "+0%", "+0%"),
    ]


def test_edge_tts_raises_when_safe_retry_still_has_no_audio():
    class FakeCommunicate:
        def __init__(self, *_args, **_kwargs):
            pass

        async def stream(self):
            if False:
                yield {}

    class FakeEdgeTts:
        Communicate = FakeCommunicate

    engine = EdgeTTS.__new__(EdgeTTS)
    engine._edge_tts = FakeEdgeTts

    with pytest.raises(RuntimeError, match="No audio"):
        engine.synthesize("hello", "en-US-JennyNeural")
