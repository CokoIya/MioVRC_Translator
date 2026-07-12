"""Tests for text-to-speech runtime helpers."""
from __future__ import annotations

import io
import os
import struct
import subprocess
import threading
import time
import queue
import logging
import wave
from pathlib import Path
import sys

import pytest
import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tts import manager as manager_module
from src.tts.base import BaseTTS, TTSVoice
from src.tts.gtts_engine import GoogleTTS
from src.tts.manager import (
    TTSManager,
    TTSRequest,
    _append_tail_silence,
    _portaudio_error_code,
    _virtual_output_score,
    find_best_virtual_output_device,
    resolve_output_device,
)


class FakeTTS(BaseTTS):
    """Small fake engine for exercising manager queueing without audio devices."""

    def __init__(self):
        self.requests: list[tuple[str, str, float, float]] = []

    def is_available(self) -> bool:
        return True

    def get_available_voices(self) -> list[TTSVoice]:
        return [
            TTSVoice(
                id="fake-voice",
                name="Fake Voice",
                language="en",
                gender="Neutral",
                locale="en-US",
            )
        ]

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        self.requests.append((text, voice, rate, volume))
        return b"RIFF-fake"


def _pcm_wav_bytes(sample_width: int, frames: bytes, *, channels: int = 1, sample_rate: int = 8000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return output.getvalue()


def _raw_wav_bytes(
    *,
    format_tag: int,
    bits_per_sample: int,
    frames: bytes,
    channels: int = 1,
    sample_rate: int = 8000,
) -> bytes:
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        format_tag,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def test_tts_manager_queues_and_invokes_playback(monkeypatch):
    """Manager should synthesize queued speech and invoke the callback."""
    fake_engine = FakeTTS()
    played: list[bytes] = []
    done = threading.Event()
    callback_result: list[tuple[bool, str]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )
    monkeypatch.setattr(manager, "_play_audio", lambda audio: played.append(audio))

    manager.start()
    try:
        accepted = manager.speak(
            "hello",
            "fake-voice",
            1.2,
            0.7,
            callback=lambda success, message: (
                callback_result.append((success, message)),
                done.set(),
            ),
        )

        assert accepted is True
        assert done.wait(2.0)
        assert fake_engine.requests == [("hello", "fake-voice", 1.2, 0.7)]
        assert played == [b"RIFF-fake"]
        assert callback_result == [(True, "")]
    finally:
        manager.stop()


def test_tts_manager_close_releases_engine_once_and_cannot_restart(monkeypatch):
    class ClosableTTS(FakeTTS):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    engine = ClosableTTS()
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )

    manager.start()
    manager.close()
    manager.close()
    manager.start()

    assert engine.close_calls == 1
    assert manager._engine is None
    assert manager._running is False


def test_tts_manager_prewarm_runs_on_synthesis_worker(monkeypatch):
    class PrewarmTTS(FakeTTS):
        def __init__(self):
            super().__init__()
            self.prewarm_calls = []
            self.prewarmed = threading.Event()

        def prewarm(self, voice=""):
            self.prewarm_calls.append((voice, threading.current_thread().name))
            self.prewarmed.set()

    engine = PrewarmTTS()
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )

    manager.start()
    try:
        assert manager.prewarm("voice") is True
        assert manager.prewarm("voice") is True
        assert engine.prewarmed.wait(timeout=1)
        assert engine.prewarm_calls == [("voice", "tts-synthesis-1")]
    finally:
        manager.stop()


def test_tts_synthesizes_next_sentence_while_previous_audio_is_playing(monkeypatch):
    class OverlapTTS(FakeTTS):
        def __init__(self):
            super().__init__()
            self.second_synthesized = threading.Event()

        def synthesize(self, text, voice, rate=1.0, volume=1.0):
            audio = super().synthesize(text, voice, rate, volume)
            if text == "second":
                self.second_synthesized.set()
            return audio + text.encode("utf-8")

    engine = OverlapTTS()
    playback_started = threading.Event()
    release_playback = threading.Event()
    played: list[bytes] = []
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )

    def play(audio: bytes) -> None:
        played.append(audio)
        if len(played) == 1:
            playback_started.set()
            release_playback.wait(timeout=2)

    monkeypatch.setattr(manager, "_play_audio", play)
    manager.start()
    try:
        assert manager.speak("first", "voice")
        assert playback_started.wait(timeout=1)
        assert manager.speak("second", "voice")
        assert engine.second_synthesized.wait(timeout=1)
        assert len(played) == 1
        release_playback.set()
        deadline = time.monotonic() + 2
        while len(played) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert played[0].endswith(b"first")
        assert played[1].endswith(b"second")
    finally:
        release_playback.set()
        manager.stop()


def test_tts_predecodes_next_sentence_while_previous_audio_is_playing(monkeypatch):
    class ValidWavTTS(FakeTTS):
        def synthesize(self, text, voice, rate=1.0, volume=1.0):
            self.requests.append((text, voice, rate, volume))
            sample = 1000 if text == "first" else 2000
            return _pcm_wav_bytes(2, struct.pack("<h", sample))

    engine = ValidWavTTS()
    first_playback_started = threading.Event()
    release_first_playback = threading.Event()
    second_decoded = threading.Event()
    decoded_count = 0
    decoded_lock = threading.Lock()
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )
    original_decode = manager._decode_audio_data

    def decode(audio):
        nonlocal decoded_count
        result = original_decode(audio)
        with decoded_lock:
            decoded_count += 1
            if decoded_count == 2:
                second_decoded.set()
        return result

    def play(_audio):
        if not first_playback_started.is_set():
            first_playback_started.set()
            release_first_playback.wait(timeout=2)

    monkeypatch.setattr(manager, "_decode_audio_data", decode)
    monkeypatch.setattr(manager, "_play_audio", play)
    manager.start()
    try:
        assert manager.speak("first", "voice")
        assert first_playback_started.wait(timeout=1)
        assert manager.speak("second", "voice")
        assert second_decoded.wait(timeout=1)
    finally:
        release_first_playback.set()
        manager.stop()


def test_tts_resampling_prefers_soxr(monkeypatch):
    calls = []

    class FakeSoxr:
        @staticmethod
        def resample(audio, source_rate, target_rate, quality):
            calls.append((audio.copy(), source_rate, target_rate, quality))
            return np.array([0.25, -0.25], dtype=np.float32)

    monkeypatch.setitem(sys.modules, "soxr", FakeSoxr)

    result = TTSManager._resample_audio(
        np.array([0.0, 1.0], dtype=np.float32),
        24000,
        48000,
    )

    assert len(calls) == 1
    assert calls[0][1:] == (24000, 48000, "HQ")
    np.testing.assert_array_equal(result, [0.25, -0.25])


def test_concurrent_tts_synthesis_still_plays_in_sentence_order(monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()

    class OutOfOrderTTS(FakeTTS):
        max_concurrent_synthesis = 2

        def synthesize(self, text, voice, rate=1.0, volume=1.0):
            self.requests.append((text, voice, rate, volume))
            if text == "first":
                first_started.set()
                release_first.wait(timeout=2)
            return b"RIFF-" + text.encode("ascii")

    engine = OutOfOrderTTS()
    played: list[bytes] = []
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        synthesis_concurrency=2,
    )
    monkeypatch.setattr(manager, "_play_audio", played.append)
    manager.start()
    try:
        assert manager.speak("first", "voice")
        assert first_started.wait(timeout=1)
        assert manager.speak("second", "voice")
        deadline = time.monotonic() + 1
        while len(engine.requests) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(engine.requests) == 2
        assert played == []
        release_first.set()
        deadline = time.monotonic() + 2
        while len(played) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert played == [b"RIFF-first", b"RIFF-second"]
    finally:
        release_first.set()
        manager.stop()


def test_tts_generation_clear_suppresses_stale_synthesis_result(monkeypatch):
    old_started = threading.Event()
    release_old = threading.Event()
    callbacks: list[tuple[str, bool]] = []

    class GenerationTTS(FakeTTS):
        def synthesize(self, text, voice, rate=1.0, volume=1.0):
            self.requests.append((text, voice, rate, volume))
            if text == "old":
                old_started.set()
                release_old.wait(timeout=2)
            return b"RIFF-" + text.encode("ascii")

    engine = GenerationTTS()
    played: list[bytes] = []
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: engine,
    )
    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )
    monkeypatch.setattr(manager, "_play_audio", played.append)
    manager.start()
    try:
        assert manager.speak(
            "old",
            "voice",
            callback=lambda success, _message: callbacks.append(("old", success)),
        )
        assert old_started.wait(timeout=1)
        manager.clear_queue()
        assert manager.speak(
            "new",
            "voice",
            callback=lambda success, _message: callbacks.append(("new", success)),
        )
        release_old.set()
        deadline = time.monotonic() + 2
        while not any(name == "new" for name, _success in callbacks) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert played == [b"RIFF-new"]
        assert callbacks.count(("old", False)) == 1
        assert callbacks.count(("new", True)) == 1
    finally:
        release_old.set()
        manager.stop()


def test_tts_manager_cache_returns_audio_bytes(monkeypatch):
    """Cached TTS requests should return the original audio bytes, not metadata."""
    fake_engine = FakeTTS()

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=True,
        allow_fallback=False,
    )

    first = manager._get_audio("hello", "fake-voice", 1.0, 0.8)
    second = manager._get_audio("hello", "fake-voice", 1.0, 0.8)

    assert first == b"RIFF-fake"
    assert second == b"RIFF-fake"
    assert fake_engine.requests == [("hello", "fake-voice", 1.0, 0.8)]


def test_tts_manager_cache_key_includes_runtime_engine_config(monkeypatch):
    fake_engine = FakeTTS()
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="xtts",
        cache_enabled=True,
        allow_fallback=False,
        engine_config={"language": "en", "api_key": "first"},
    )

    key_en = manager._generate_cache_key("hello", "voice", 1.0, 0.8)
    manager._engine_config["api_key"] = "second"
    key_secret_changed = manager._generate_cache_key("hello", "voice", 1.0, 0.8)
    manager._engine_config["language"] = "ja"
    key_ja = manager._generate_cache_key("hello", "voice", 1.0, 0.8)

    assert key_secret_changed == key_en
    assert key_ja != key_en


def test_decode_wav_handles_unsigned_8_bit_pcm():
    audio, sample_rate = TTSManager._decode_wav(_pcm_wav_bytes(1, bytes([0, 128, 255])))

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-1.0, 0.0, 127.0 / 128.0], rtol=0, atol=1e-6)


@pytest.mark.parametrize(
    "payload",
    [
        b"OggS-container",
        b"fLaC-container",
        b"\x1aE\xdf\xa3-webm",
        b"\x00\x00\x00\x18ftypisom-container",
    ],
)
def test_decode_audio_routes_supported_containers_through_pyav(payload):
    manager = TTSManager.__new__(TTSManager)
    decoded = (np.array([0.25], dtype=np.float32), 24000)
    calls = []
    manager._decode_mp3 = lambda data: calls.append(data) or decoded

    assert manager._decode_audio_data(payload) == decoded
    assert calls == [payload]


def test_decode_wav_handles_signed_24_bit_pcm():
    frames = b"\x00\x00\x80" + b"\x00\x00\x00" + b"\xff\xff\x7f"

    audio, sample_rate = TTSManager._decode_wav(_pcm_wav_bytes(3, frames))

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-1.0, 0.0, 8388607.0 / 8388608.0], rtol=0, atol=1e-6)


def test_decode_wav_handles_signed_32_bit_pcm_without_clipping_to_noise():
    frames = struct.pack("<iii", -2147483648, 0, 2147483647)

    audio, sample_rate = TTSManager._decode_wav(_pcm_wav_bytes(4, frames))

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-1.0, 0.0, 2147483647.0 / 2147483648.0], rtol=0, atol=1e-6)


def test_decode_wav_handles_ieee_float_wav():
    frames = struct.pack("<fff", -0.5, 0.0, 0.5)

    audio, sample_rate = TTSManager._decode_wav(
        _raw_wav_bytes(format_tag=3, bits_per_sample=32, frames=frames)
    )

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-0.5, 0.0, 0.5], rtol=0, atol=1e-6)


def test_decode_wav_ignores_trailing_bytes_after_audio_data():
    frames = struct.pack("<hhh", -32768, 0, 32767)
    data = _pcm_wav_bytes(2, frames) + b"TAIL" + struct.pack("<I", 999999)

    audio, sample_rate = TTSManager._decode_wav(data)

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-1.0, 0.0, 32767.0 / 32768.0], rtol=0, atol=1e-6)


def test_decode_wav_accepts_overstated_final_data_chunk_size():
    frames = struct.pack("<hhh", -32768, 0, 32767)
    data = bytearray(_pcm_wav_bytes(2, frames))
    data_chunk_offset = data.index(b"data")
    data[data_chunk_offset + 4 : data_chunk_offset + 8] = struct.pack("<I", 0xFFFFFFFF)

    audio, sample_rate = TTSManager._decode_wav(bytes(data))

    assert sample_rate == 8000
    np.testing.assert_allclose(audio, [-1.0, 0.0, 32767.0 / 32768.0], rtol=0, atol=1e-6)


def test_tts_manager_rejects_non_byte_audio(monkeypatch):
    class FloatTTS(FakeTTS):
        def synthesize(self, *args, **kwargs):
            return 1.0

    fake_engine = FloatTTS()
    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )

    with pytest.raises(RuntimeError, match="invalid audio data"):
        manager._get_audio("hello", "fake-voice", 1.0, 0.8)


def test_tts_manager_monitor_output_flag_can_be_toggled(monkeypatch):
    fake_engine = FakeTTS()

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        monitor_output=True,
    )

    assert manager._monitor_output is True

    manager.set_monitor_output(False)

    assert manager._monitor_output is False


def test_tts_manager_passes_sbv2_device_to_engine_factory(monkeypatch):
    fake_engine = FakeTTS()
    captured: list[tuple[str, str, str]] = []

    def fake_create_engine(engine_name, **kwargs):
        captured.append(
            (
                engine_name,
                kwargs.get("device"),
                kwargs.get("bert_language"),
            )
        )
        return fake_engine

    monkeypatch.setattr("src.tts.manager.create_tts_engine", fake_create_engine)
    manager = TTSManager(
        engine_name="style_bert_vits2",
        cache_enabled=False,
        allow_fallback=False,
        sbv2_device="cuda",
        sbv2_bert_language="en",
    )

    assert manager.is_available() is True
    assert captured == [("style_bert_vits2", "cuda", "en")]


def test_tts_manager_passes_api_engine_config_to_factory(monkeypatch):
    fake_engine = FakeTTS()
    captured: list[dict[str, object]] = []

    def fake_create_engine(engine_name, **kwargs):
        captured.append({"engine_name": engine_name, **kwargs})
        return fake_engine

    monkeypatch.setattr("src.tts.manager.create_tts_engine", fake_create_engine)

    manager = TTSManager(
        engine_name="mimo_tts",
        cache_enabled=False,
        allow_fallback=False,
        engine_config={
            "api_key": "mimo-key",
            "region": "global",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5-tts",
        },
    )

    assert manager.is_available() is True
    assert captured[0]["engine_name"] == "mimo_tts"
    assert captured[0]["config"]["api_key"] == "mimo-key"
    assert captured[0]["config"]["model"] == "mimo-v2.5-tts"


def test_tts_manager_keeps_requested_cuda_for_engine_factory(monkeypatch):
    fake_engine = FakeTTS()
    captured: list[tuple[str, str, str]] = []

    def fake_create_engine(engine_name, **kwargs):
        captured.append(
            (
                engine_name,
                kwargs.get("device"),
                kwargs.get("bert_language"),
            )
        )
        return fake_engine

    monkeypatch.setattr("src.tts.manager.create_tts_engine", fake_create_engine)
    manager = TTSManager(
        engine_name="style_bert_vits2",
        cache_enabled=False,
        allow_fallback=False,
        sbv2_device="cuda",
        sbv2_bert_language="en",
    )

    assert manager.is_available() is True
    assert captured == [("style_bert_vits2", "cuda", "en")]


def test_tts_manager_pauses_after_repeated_failures(monkeypatch):
    class FailingTTS(FakeTTS):
        def synthesize(self, *args, **kwargs):
            raise RuntimeError("offline resource download failed")

    fake_engine = FailingTTS()
    callback_results: list[tuple[bool, str]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )
    monkeypatch.setattr("src.tts.manager.TTS_FAILURE_SUSPEND_THRESHOLD", 2)
    monkeypatch.setattr("src.tts.manager.TTS_FAILURE_SUSPEND_SECONDS", 60.0)

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
    )

    request = lambda: TTSRequest(
        text="hello",
        voice="fake-voice",
        rate=1.0,
        volume=1.0,
        callback=lambda success, message: callback_results.append((success, message)),
    )

    manager._process_request(request())
    manager._process_request(request())

    assert manager._suspended_until > 0
    assert len(callback_results) == 2
    assert all(success is False for success, _message in callback_results)

    manager._running = True
    accepted = manager.speak(
        "hello",
        "fake-voice",
        callback=lambda success, message: callback_results.append((success, message)),
    )

    assert accepted is False
    assert "temporarily paused" in callback_results[-1][1]


def test_play_audio_can_mirror_to_monitor_output_when_not_routing_to_vrchat(monkeypatch):
    fake_engine = FakeTTS()
    playback_log: list[tuple[str, str, object]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        output_device=12,
        monitor_output=True,
    )
    monkeypatch.setattr(manager, "stop_playback", lambda: None)
    monkeypatch.setattr(manager, "_resolve_playback_device", lambda: (12, "Headphones"))
    monkeypatch.setattr(manager, "_resolve_monitor_playback_device", lambda _device, _name: (24, "Speakers"))
    monkeypatch.setattr(
        manager,
        "_decode_audio_data",
        lambda _audio_data: (np.ones(4, dtype=np.float32), 24000),
    )
    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )

    def fake_create_output_stream(audio_array, sample_rate, playback_device, label):
        done = threading.Event()

        class FakePlayback:
            def start(self):
                playback_log.append(("start", label, playback_device))
                done.set()

            def stop(self):
                playback_log.append(("stop", label, playback_device))

            def close(self):
                playback_log.append(("close", label, playback_device))

        playback_log.append(("create", label, playback_device))
        return FakePlayback(), done

    monkeypatch.setattr(manager, "_create_output_stream", fake_create_output_stream)

    manager._play_audio(b"RIFF-fake")

    created = [(entry[1], entry[2]) for entry in playback_log if entry[0] == "create"]
    assert created == [("Headphones", 12), ("monitor-Speakers", 24)]
    assert manager._current_playback is None


def test_play_audio_monitors_to_local_device_when_routing_to_vrchat(monkeypatch):
    fake_engine = FakeTTS()
    playback_log: list[tuple[str, str, object]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        output_device=12,
        prefer_virtual_output=True,
        monitor_output=True,
    )
    monkeypatch.setattr(manager, "stop_playback", lambda: None)
    monkeypatch.setattr(manager, "_resolve_playback_device", lambda: (12, "MixLine Input"))
    monkeypatch.setattr(manager, "_resolve_monitor_playback_device", lambda _device, _name: (24, "Headphones"))
    monkeypatch.setattr(
        manager,
        "_decode_audio_data",
        lambda _audio_data: (np.ones(4, dtype=np.float32), 24000),
    )
    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )

    def fake_create_output_stream(audio_array, sample_rate, playback_device, label):
        done = threading.Event()

        class FakePlayback:
            def start(self):
                playback_log.append(("start", label, playback_device))
                done.set()

            def stop(self):
                playback_log.append(("stop", label, playback_device))

            def close(self):
                playback_log.append(("close", label, playback_device))

        playback_log.append(("create", label, playback_device))
        return FakePlayback(), done

    monkeypatch.setattr(manager, "_create_output_stream", fake_create_output_stream)

    manager._play_audio(b"RIFF-fake")

    created = [(entry[1], entry[2]) for entry in playback_log if entry[0] == "create"]
    assert created == [("MixLine Input", 12), ("monitor-Headphones", 24)]
    assert manager._current_playback is None


def test_play_audio_retries_next_mixline_endpoint_when_start_fails(monkeypatch):
    fake_engine = FakeTTS()
    playback_log: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        output_device=62,
        prefer_virtual_output=True,
    )
    monkeypatch.setattr(manager, "stop_playback", lambda: None)
    monkeypatch.setattr(
        manager,
        "_resolve_playback_device",
        lambda: (62, "Speakers (MIXLINE Wave Speaker)"),
    )
    monkeypatch.setattr(
        manager,
        "_resolve_monitor_playback_device",
        lambda _device, _name: None,
    )
    monkeypatch.setattr(
        manager,
        "_decode_audio_data",
        lambda _audio_data: (np.ones(4, dtype=np.float32), 44100),
    )
    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )

    def fake_alternatives(device_id: int) -> list[int]:
        if device_id == 62:
            return [30, 24]
        if device_id == 30:
            return [24]
        return []

    monkeypatch.setattr(manager, "_find_alternative_devices", fake_alternatives)

    def fake_create_output_stream(audio_array, sample_rate, playback_device, label):
        done = threading.Event()

        class FakePlayback:
            def start(self):
                playback_log.append(("start", playback_device))
                if playback_device in {62, 30}:
                    raise sd.PortAudioError("failed to start", -9999)
                done.set()

            def stop(self):
                playback_log.append(("stop", playback_device))

            def close(self):
                playback_log.append(("close", playback_device))

        playback_log.append(("create", playback_device))
        return FakePlayback(), done

    monkeypatch.setattr(manager, "_create_output_stream", fake_create_output_stream)

    manager._play_audio(b"RIFF-fake")

    assert [entry for entry in playback_log if entry[0] == "start"] == [
        ("start", 62),
        ("start", 30),
        ("start", 24),
    ]


def test_play_audio_retries_next_mixline_endpoint_when_device_is_invalid(monkeypatch):
    fake_engine = FakeTTS()
    playback_log: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        output_device=62,
        prefer_virtual_output=True,
    )
    monkeypatch.setattr(manager, "stop_playback", lambda: None)
    monkeypatch.setattr(
        manager,
        "_resolve_playback_device",
        lambda: (62, "Speakers (MIXLINE Wave Speaker)"),
    )
    monkeypatch.setattr(
        manager,
        "_resolve_monitor_playback_device",
        lambda _device, _name: None,
    )
    monkeypatch.setattr(
        manager,
        "_decode_audio_data",
        lambda _audio_data: (np.ones(4, dtype=np.float32), 44100),
    )
    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )

    def fake_alternatives(device_id: int) -> list[int]:
        if device_id == 62:
            return [30, 24]
        if device_id == 30:
            return [24]
        return []

    monkeypatch.setattr(manager, "_find_alternative_devices", fake_alternatives)

    def fake_create_output_stream(audio_array, sample_rate, playback_device, label):
        done = threading.Event()

        class FakePlayback:
            def start(self):
                playback_log.append(("start", playback_device))
                if playback_device in {62, 30}:
                    raise sd.PortAudioError("failed to start", -9996)
                done.set()

            def stop(self):
                playback_log.append(("stop", playback_device))

            def close(self):
                playback_log.append(("close", playback_device))

        playback_log.append(("create", playback_device))
        return FakePlayback(), done

    monkeypatch.setattr(manager, "_create_output_stream", fake_create_output_stream)

    manager._play_audio(b"RIFF-fake")

    assert [entry for entry in playback_log if entry[0] == "start"] == [
        ("start", 62),
        ("start", 30),
        ("start", 24),
    ]


def test_create_output_stream_retries_mixline_endpoint_on_insufficient_memory(monkeypatch):
    manager = TTSManager.__new__(TTSManager)
    playback_log: list[tuple[str, object]] = []

    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )

    def fake_alternatives(device_id: int) -> list[int]:
        if device_id == 62:
            return [30]
        if device_id == 30:
            return [24]
        return []

    monkeypatch.setattr(manager, "_find_alternative_devices", fake_alternatives)

    class FakePlayback:
        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    def fake_output_stream(*, device, **_kwargs):
        playback_log.append(("open", device))
        if device in {62, 30}:
            raise sd.PortAudioError("insufficient memory", -9992)
        return FakePlayback()

    monkeypatch.setattr("src.tts.manager.sd.OutputStream", fake_output_stream)

    playback, done_event = manager._create_output_stream(
        np.ones(4, dtype=np.float32),
        48000,
        62,
        "MixLine",
    )

    assert playback is not None
    assert done_event.is_set() is False
    assert playback_log == [("open", 62), ("open", 30), ("open", 24)]


def test_create_output_stream_finishes_once_and_zeros_tail(monkeypatch, caplog):
    manager = TTSManager.__new__(TTSManager)
    captured: dict[str, object] = {}

    class FakePlayback:
        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    def fake_output_stream(**kwargs):
        captured.update(kwargs)
        return FakePlayback()

    monkeypatch.setattr("src.tts.manager.sd.OutputStream", fake_output_stream)

    _playback, done_event = manager._create_output_stream(
        np.array([0.25, 0.5, -0.25], dtype=np.float32),
        48000,
        None,
        "default",
    )
    callback = captured["callback"]
    finished_callback = captured["finished_callback"]

    outdata = np.full((4, 1), 9.0, dtype=np.float32)
    with pytest.raises(sd.CallbackStop):
        callback(outdata, 4, None, None)

    np.testing.assert_allclose(outdata[:3, 0], [0.25, 0.5, -0.25])
    assert outdata[3, 0] == 0
    assert done_event.is_set() is False

    repeated_outdata = np.full((4, 1), 9.0, dtype=np.float32)
    with pytest.raises(sd.CallbackStop):
        callback(repeated_outdata, 4, None, None)
    assert np.all(repeated_outdata == 0)

    with caplog.at_level(logging.INFO, logger="src.tts.manager"):
        finished_callback()
        finished_callback()

    assert done_event.is_set() is True
    assert caplog.text.count("Audio playback completed (target=default") == 1


def test_append_tail_silence_extends_audio_without_changing_source_frames():
    audio = np.array([[0.25, -0.25], [0.5, -0.5]], dtype=np.float32)

    padded = _append_tail_silence(audio, 1000)

    assert padded.shape == (82, 2)
    np.testing.assert_allclose(padded[:2], audio)
    assert np.all(padded[2:] == 0)


def test_play_audio_does_not_fall_back_to_default_when_mixline_required(monkeypatch):
    fake_engine = FakeTTS()
    playback_log: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "src.tts.manager.create_tts_engine",
        lambda _engine_name, **_kwargs: fake_engine,
    )

    manager = TTSManager(
        engine_name="fake",
        cache_enabled=False,
        allow_fallback=False,
        output_device=62,
        prefer_virtual_output=True,
    )
    monkeypatch.setattr(
        manager,
        "_resolve_playback_device",
        lambda: (62, "Speakers (MIXLINE)"),
    )
    monkeypatch.setattr(
        manager,
        "_resolve_monitor_playback_device",
        lambda _device, _name: None,
    )
    monkeypatch.setattr(
        manager,
        "_decode_audio_data",
        lambda _audio_data: (np.ones(4, dtype=np.float32), 48000),
    )
    monkeypatch.setattr(
        manager,
        "_prepare_audio_for_device",
        lambda audio_array, sample_rate, _device: (audio_array, sample_rate),
    )
    monkeypatch.setattr(manager, "_find_alternative_devices", lambda _device_id: [])

    def fake_create_output_stream(audio_array, sample_rate, playback_device, label):
        done = threading.Event()

        class FakePlayback:
            def start(self):
                playback_log.append(("start", playback_device))
                raise sd.PortAudioError("insufficient memory", -9992)

            def stop(self):
                playback_log.append(("stop", playback_device))

            def close(self):
                playback_log.append(("close", playback_device))

        playback_log.append(("create", playback_device))
        return FakePlayback(), done

    monkeypatch.setattr(manager, "_create_output_stream", fake_create_output_stream)

    with pytest.raises(RuntimeError, match="MixLine output device error"):
        manager._play_audio(b"RIFF-fake")

    assert ("create", None) not in playback_log


def test_portaudio_error_code_reads_numeric_arg():
    assert _portaudio_error_code(sd.PortAudioError("failed", -9999)) == -9999


def test_monitor_output_avoids_primary_and_virtual_devices(monkeypatch):
    fake_devices = [
        {"name": "Microphone (MIXLINE Record)", "max_output_channels": 0, "hostapi": 0},
        {"name": "Speakers (MIXLINE)", "max_output_channels": 2, "hostapi": 0},
        {"name": "Headphones", "max_output_channels": 2, "hostapi": 1},
    ]
    fake_hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager.sd.default.device", [None, 1])

    manager = TTSManager.__new__(TTSManager)
    manager._monitor_output = True

    assert manager._resolve_monitor_playback_device(1, "Speakers (MIXLINE)") == (2, "Headphones")


def test_tts_stop_signal_does_not_block_when_queue_is_full():
    manager = TTSManager.__new__(TTSManager)
    manager._request_queue = queue.Queue(maxsize=1)
    manager._request_queue.put_nowait(TTSRequest("old", "fake", 1.0, 1.0))

    manager._signal_worker_stop()

    assert manager._request_queue.get_nowait() is None


def test_stop_playback_closes_stream_and_wakes_waiter():
    class FakePlayback:
        def __init__(self):
            self.stopped = False
            self.closed = False

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    playback = FakePlayback()
    done = threading.Event()
    manager = TTSManager.__new__(TTSManager)
    manager._playback_lock = threading.Lock()
    manager._current_playback = playback
    manager._current_playback_done = done

    manager.stop_playback()

    assert playback.stopped is True
    assert playback.closed is True
    assert done.is_set()
    assert manager._current_playback is None
    assert manager._current_playback_done is None


def test_resolve_output_device_prefers_saved_name_over_stale_id(monkeypatch):
    """Saved device names should recover when the numeric ID now points elsewhere."""
    fake_devices = [
        {"name": "Headphones (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 0},
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)

    resolved = resolve_output_device(
        0,
        "MixLine Input (Logitech G MixLine)",
        prefer_virtual=True,
    )

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_resolve_output_device_falls_back_when_saved_id_is_not_virtual(monkeypatch):
    """VRChat output should not keep using an ID that now points to a physical device."""
    fake_devices = [
        {"name": "Microphone Monitor (USB Mic)", "max_output_channels": 2, "hostapi": 0},
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)

    resolved = resolve_output_device(0, None, prefer_virtual=True)

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_resolve_output_device_rejects_saved_non_mixline_virtual_device(monkeypatch):
    """Saved VoiceMeeter/VB-CABLE devices must not be reused for VRChat output."""
    fake_devices = [
        {
            "name": "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
        {
            "name": "CABLE Input (VB-Audio Virtual Cable)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
    ]
    fake_hostapis = [{"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)

    resolved = resolve_output_device(
        0,
        "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
        prefer_virtual=True,
    )

    assert resolved is None


def test_resolve_output_device_replaces_saved_non_mixline_with_mixline(monkeypatch):
    fake_devices = [
        {
            "name": "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
    ]
    fake_hostapis = [{"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = resolve_output_device(
        0,
        "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
        prefer_virtual=True,
    )

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_find_best_virtual_output_device_prefers_mixline_input(monkeypatch):
    """Among multiple MixLine endpoints, choose the input endpoint."""
    fake_devices = [
        {
            "name": "MixLine Output (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = find_best_virtual_output_device()

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_find_best_virtual_output_device_prefers_mixline_stream(monkeypatch):
    """MIXLINE Stream is the endpoint that pairs with VRChat's virtual mic."""
    fake_devices = [
        {
            "name": "Speakers (MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Stream)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = find_best_virtual_output_device()

    assert resolved == (1, "Speakers (MIXLINE Stream)")


def test_resolve_output_device_switches_generic_mixline_to_stream(monkeypatch):
    fake_devices = [
        {
            "name": "Speakers (MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Stream)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = resolve_output_device(
        0,
        "Speakers (MIXLINE)",
        prefer_virtual=True,
    )

    assert resolved == (1, "Speakers (MIXLINE Stream)")


def test_find_best_virtual_output_device_ignores_non_mixline_virtual_devices(monkeypatch):
    fake_devices = [
        {"name": "Generic Virtual Mixer", "max_output_channels": 2, "hostapi": 0},
        {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_output_channels": 2, "hostapi": 0},
        {"name": "Voicemeeter Input (VB-Audio Voicemeeter VAIO)", "max_output_channels": 2, "hostapi": 0},
        {"name": "Speakers (Realtek Audio)", "max_output_channels": 2, "hostapi": 0},
    ]
    fake_hostapis = [{"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)

    assert find_best_virtual_output_device(avoid_default_output=False) is None


def test_find_alternative_devices_accepts_localized_mixline_names(monkeypatch):
    fake_devices = [
        {"name": "Speakers (MIXLINE Wave Speaker)", "max_output_channels": 2, "hostapi": 0},
        {"name": "旦疋奈市奈 (MIXLINE)", "max_output_channels": 2, "hostapi": 1},
        {"name": "Headphones", "max_output_channels": 2, "hostapi": 1},
    ]
    fake_hostapis = [{"name": "Windows WDM-KS"}, {"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)

    manager = TTSManager.__new__(TTSManager)

    assert manager._find_alternative_devices(0) == [1]


def test_virtual_output_score_rejects_mixline_wave_speaker():
    wave_score = _virtual_output_score(
        "Speakers (MIXLINE Wave Speaker)",
        "Windows WASAPI",
    )
    generic_score = _virtual_output_score(
        "スピーカー (MIXLINE)",
        "Windows WASAPI",
    )

    assert wave_score == 0
    assert generic_score > wave_score


def test_find_best_virtual_output_device_ignores_mixline_wave_speaker(monkeypatch):
    fake_devices = [
        {
            "name": "スピーカー (MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = find_best_virtual_output_device()

    assert resolved == (0, "スピーカー (MIXLINE)")


def test_find_best_virtual_output_device_avoids_default_playback_bus(monkeypatch):
    """TTS should avoid the system default playback bus when another virtual input exists."""
    fake_devices = [
        {
            "name": "MixLine Output (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
    ]
    fake_hostapis = [{"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr(
        "src.tts.manager._default_output_device_names",
        lambda _devices: ("MixLine Output (Logitech G MixLine)",),
    )

    resolved = find_best_virtual_output_device()

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_find_best_virtual_output_device_prefers_wasapi_mixline_over_wdmks_wave(monkeypatch):
    fake_devices = [
        {
            "name": "銈广償銉笺偒銉?(MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 2,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager.sd.default.device", [None, 0])

    resolved = find_best_virtual_output_device()

    assert resolved == (0, "銈广償銉笺偒銉?(MIXLINE)")


def test_resolve_output_device_avoids_saved_default_playback_bus(monkeypatch):
    """A saved MixLine output should resolve to the MixLine input endpoint."""
    fake_devices = [
        {
            "name": "MixLine Output (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 0,
        },
    ]
    fake_hostapis = [{"name": "Windows WASAPI"}]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr(
        "src.tts.manager._default_output_device_names",
        lambda _devices: ("MixLine Output (Logitech G MixLine)",),
    )

    resolved = resolve_output_device(
        0,
        "MixLine Output (Logitech G MixLine)",
        prefer_virtual=True,
    )

    assert resolved == (1, "MixLine Input (Logitech G MixLine)")


def test_resolve_output_device_switches_from_wdmks_to_safer_virtual_device(monkeypatch):
    """Prefer a non-WDM-KS virtual endpoint when one is available."""
    fake_devices = [
        {
            "name": "MixLine Input (Logitech G MixLine)",
            "max_output_channels": 2,
            "hostapi": 2,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 3,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows DirectSound"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = resolve_output_device(
        1,
        "Speakers (MIXLINE Wave Speaker)",
        prefer_virtual=True,
    )

    assert resolved == (0, "MixLine Input (Logitech G MixLine)")


def test_resolve_output_device_switches_from_wdmks_wave_to_wasapi_mixline(monkeypatch):
    fake_devices = [
        {
            "name": "銈广償銉笺偒銉?(MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 2,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = resolve_output_device(
        1,
        "Speakers (MIXLINE Wave Speaker)",
        prefer_virtual=True,
    )

    assert resolved == (0, "銈广償銉笺偒銉?(MIXLINE)")


def test_resolve_output_device_preserves_saved_same_name_host_api(monkeypatch):
    fake_devices = [
        {
            "name": "Speakers (MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 2,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows DirectSound"},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    resolved = resolve_output_device(
        1,
        "Speakers (MIXLINE)",
        prefer_virtual=True,
    )

    assert resolved == (1, "Speakers (MIXLINE)")


def test_unconfigured_tts_output_uses_canonical_wasapi_default(monkeypatch):
    fake_devices = [
        {
            "name": "Speakers (USB DAC)",
            "max_output_channels": 2,
            "max_input_channels": 0,
            "hostapi": 0,
        },
        {
            "name": "Speakers (USB DAC)",
            "max_output_channels": 2,
            "max_input_channels": 0,
            "hostapi": 1,
        },
    ]
    fake_hostapis = [
        {"name": "MME", "default_input_device": -1, "default_output_device": 0},
        {"name": "Windows WASAPI", "default_input_device": -1, "default_output_device": 1},
    ]
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager.sd.default.device", [-1, 0])

    assert resolve_output_device(None, None) == (1, "Speakers (USB DAC)")


def test_follow_default_tts_route_does_not_persist_transient_device_id(monkeypatch):
    manager = TTSManager.__new__(TTSManager)
    manager._output_device = None
    manager._output_device_name = ""
    manager._prefer_virtual_output = False
    manager._update_device_config = lambda *_args: (_ for _ in ()).throw(
        AssertionError("automatic default resolution must not become a fixed device")
    )
    monkeypatch.setattr(
        "src.tts.manager.resolve_output_device",
        lambda *_args, **_kwargs: (40, "Speakers (USB DAC)"),
    )

    assert manager._resolve_playback_device() == (40, "Speakers (USB DAC)")
    assert manager._output_device is None
    assert manager._output_device_name == ""


def test_tts_manager_persists_recovered_virtual_output(monkeypatch):
    fake_devices = [
        {
            "name": "銈广償銉笺偒銉?(MIXLINE)",
            "max_output_channels": 2,
            "hostapi": 1,
        },
        {
            "name": "Speakers (MIXLINE Wave Speaker)",
            "max_output_channels": 2,
            "hostapi": 2,
        },
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    saved: list[tuple[object, str]] = []
    monkeypatch.setattr("src.tts.manager.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("src.tts.manager.sd.query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr("src.tts.manager._default_output_device_names", lambda _devices: ())

    manager = TTSManager.__new__(TTSManager)
    manager._output_device = 1
    manager._output_device_name = "Speakers (MIXLINE Wave Speaker)"
    manager._prefer_virtual_output = True
    manager._config_save_callback = lambda device_id, name: saved.append((device_id, name))

    resolved = manager._resolve_playback_device()

    assert resolved == (0, "銈广償銉笺偒銉?(MIXLINE)")
    assert manager._output_device == 0
    assert manager._output_device_name == "銈广償銉笺偒銉?(MIXLINE)"
    assert saved == [(0, "銈广償銉笺偒銉?(MIXLINE)")]


def test_google_tts_voice_loading_is_local():
    """gTTS voice listing should be available without a network voice fetch."""
    engine = GoogleTTS()
    if not engine.is_available():
        pytest.skip("gTTS is not installed")

    voices = engine.get_available_voices()

    assert any(voice.id == "zh-CN" for voice in voices)
    assert any(voice.id == "en" for voice in voices)



def test_debug_audio_filename_sanitizes_untrusted_label(monkeypatch, tmp_path):
    monkeypatch.setenv(manager_module.TTS_DEBUG_AUDIO_ENV, "1")
    monkeypatch.setattr(manager_module, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module.time, "time_ns", lambda: 123)
    monkeypatch.setattr(manager_module.secrets, "token_hex", lambda _size: "abcdef123456")

    TTSManager._maybe_save_debug_audio(b"RIFF-data", "..\\evil/voice :?../")

    files = list((tmp_path / "tts_debug_audio").iterdir())
    assert [path.name for path in files] == [
        "123-evil_voice-abcdef123456.wav"
    ]
    assert files[0].parent == tmp_path / "tts_debug_audio"


def test_debug_audio_uses_unique_names_even_at_same_timestamp(monkeypatch, tmp_path):
    tokens = iter(["000000000001", "000000000002"])
    monkeypatch.setenv(manager_module.TTS_DEBUG_AUDIO_ENV, "1")
    monkeypatch.setattr(manager_module, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module.time, "time_ns", lambda: 456)
    monkeypatch.setattr(manager_module.secrets, "token_hex", lambda _size: next(tokens))

    TTSManager._maybe_save_debug_audio(b"first", "sample")
    TTSManager._maybe_save_debug_audio(b"second", "sample")

    files = sorted((tmp_path / "tts_debug_audio").iterdir())
    assert [path.name for path in files] == [
        "456-sample-000000000001.audio",
        "456-sample-000000000002.audio",
    ]
    assert [path.read_bytes() for path in files] == [b"first", b"second"]


def test_debug_audio_rejects_redirected_output_directory(monkeypatch, tmp_path, caplog):
    app_temp = tmp_path / "temp"
    outside = tmp_path / "outside"
    app_temp.mkdir()
    outside.mkdir()
    redirected = app_temp / "tts_debug_audio"
    try:
        os.symlink(outside, redirected, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(redirected), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("directory symlink and junction creation are unavailable")

    monkeypatch.setenv(manager_module.TTS_DEBUG_AUDIO_ENV, "1")
    monkeypatch.setattr(manager_module, "app_temp_dir", lambda: app_temp)
    try:
        with caplog.at_level(logging.WARNING, logger="src.tts.manager"):
            TTSManager._maybe_save_debug_audio(b"blocked", "sample")

        assert list(outside.iterdir()) == []
        assert "Failed to save debug TTS audio" in caplog.text
    finally:
        if os.path.lexists(redirected):
            if os.name == "nt" and not redirected.is_symlink():
                os.rmdir(redirected)
            else:
                redirected.unlink()
