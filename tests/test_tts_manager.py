"""Tests for text-to-speech runtime helpers."""
from __future__ import annotations

import threading
import queue
from pathlib import Path
import sys

import pytest
import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tts.base import BaseTTS, TTSVoice
from src.tts.gtts_engine import GoogleTTS
from src.tts.manager import (
    TTSManager,
    TTSRequest,
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
    monkeypatch.setattr("src.tts.manager._style_bert_cuda_available", lambda: True)

    manager = TTSManager(
        engine_name="style_bert_vits2",
        cache_enabled=False,
        allow_fallback=False,
        sbv2_device="cuda",
        sbv2_bert_language="en",
    )

    assert manager.is_available() is True
    assert captured == [("style_bert_vits2", "cuda", "en")]


def test_tts_manager_falls_back_to_cpu_when_sbv2_cuda_is_unavailable(monkeypatch):
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
    monkeypatch.setattr("src.tts.manager._style_bert_cuda_available", lambda: False)

    manager = TTSManager(
        engine_name="style_bert_vits2",
        cache_enabled=False,
        allow_fallback=False,
        sbv2_device="cuda",
        sbv2_bert_language="en",
    )

    assert manager.is_available() is True
    assert captured == [("style_bert_vits2", "cpu", "en")]


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
