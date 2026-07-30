import pytest
import numpy as np
import time

import src.audio.desktop_recorder as desktop_recorder
from src.audio.desktop_recorder import DesktopAudioRecorder, auto_select_virtual_device, list_output_devices


class _FakePyAudio:
    def __init__(self, *, supported=True):
        self.supported = supported
        self.format_checks = []
        self.open_called = False

    def is_format_supported(self, rate, **kwargs):
        self.format_checks.append((rate, kwargs))
        if isinstance(self.supported, BaseException):
            raise self.supported
        return self.supported

    def open(self, **_kwargs):
        self.open_called = True
        raise AssertionError("validation must not open or read a stream")


class _FakePa:
    paFloat32 = 1


def test_desktop_stream_validation_does_not_read_from_loopback_device():
    fake = _FakePyAudio()

    DesktopAudioRecorder._validate_stream_config(
        fake,
        _FakePa,
        device_index=12,
        rate=48000,
        channels=2,
        blocksize=1440,
    )

    assert fake.format_checks == [
        (
            48000,
            {
                "input_device": 12,
                "input_channels": 2,
                "input_format": _FakePa.paFloat32,
            },
        )
    ]
    assert fake.open_called is False


def test_desktop_stream_validation_rejects_unsupported_format():
    fake = _FakePyAudio(supported=ValueError("unsupported format"))

    with pytest.raises(RuntimeError, match="unsupported format"):
        DesktopAudioRecorder._validate_stream_config(
            fake,
            _FakePa,
            device_index=12,
            rate=48000,
            channels=2,
            blocksize=1440,
        )


def test_desktop_capture_candidates_limit_high_rate_and_channels():
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)

    candidates = recorder._candidate_stream_configs(native_rate=384000, max_channels=8)

    assert candidates == [
        (384000, 2),
        (384000, 1),
        (48000, 2),
        (48000, 1),
        (44100, 2),
        (44100, 1),
        (16000, 2),
        (16000, 1),
    ]


def test_desktop_capture_treats_unanticipated_host_error_as_transient():
    assert DesktopAudioRecorder._is_transient_capture_error(
        OSError(-9999, "Unanticipated host error")
    )
    assert DesktopAudioRecorder._is_transient_capture_error(
        RuntimeError("Unanticipated host error [PaErrorCode -9999]")
    )
    assert not DesktopAudioRecorder._is_transient_capture_error(
        RuntimeError("Invalid sample rate")
    )


def test_desktop_start_thread_failure_releases_lifecycle_state(monkeypatch):
    class FailingThread:
        def start(self):
            raise RuntimeError("thread start failed")

        def is_alive(self):
            return False

        def join(self, timeout=None):
            del timeout

    monkeypatch.setattr(
        desktop_recorder.threading,
        "Thread",
        lambda *args, **kwargs: FailingThread(),
    )
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)

    with pytest.raises(RuntimeError, match="thread start failed"):
        recorder.start()

    assert recorder.is_running is False
    assert recorder._worker_thread is None
    assert recorder._capture_thread is None
    assert recorder._frame_queue.empty()


def test_desktop_prepare_frame_avoids_stereo_phase_cancellation():
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)
    recorder._capture_dtype = "float32"
    recorder._capture_rate = recorder.sample_rate
    tone = (np.sin(np.linspace(0.0, np.pi * 6.0, 480)) * 0.4).astype(np.float32)
    stereo = np.column_stack([tone, -tone])

    prepared = recorder._prepare_frame(stereo)

    assert prepared.shape == tone.shape
    assert float(np.sqrt(np.mean(np.square(prepared)))) > 0.25
    snapshot = recorder.diagnostics_snapshot()
    assert snapshot["last_prepared_rms"] > 0.25
    assert "vad_min_rms" in snapshot
    assert "vad_in_speech" in snapshot
    assert snapshot["running"] is False


def test_desktop_prepare_frame_keeps_normal_stereo_mix():
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)
    recorder._capture_dtype = "float32"
    recorder._capture_rate = recorder.sample_rate
    tone = (np.sin(np.linspace(0.0, np.pi * 6.0, 480)) * 0.4).astype(np.float32)
    stereo = np.column_stack([tone, tone])

    prepared = recorder._prepare_frame(stereo)

    assert np.allclose(prepared, tone, atol=1e-6)


def test_auto_select_virtual_device_prefers_mixline(monkeypatch):
    monkeypatch.setattr(
        "src.audio.desktop_recorder.list_output_devices",
        lambda: [
            {"name": "MixLine Input (Logitech G MixLine)"},
            {"name": "Speakers (Realtek Audio)"},
        ],
    )

    assert auto_select_virtual_device() == "MixLine Input (Logitech G MixLine)"


def test_auto_select_virtual_device_ignores_non_mixline_virtual_devices(monkeypatch):
    monkeypatch.setattr(
        "src.audio.desktop_recorder.list_output_devices",
        lambda: [
            {"name": "CABLE Output (VB-Audio Virtual Cable)"},
            {"name": "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)"},
            {"name": "Generic Virtual Mixer"},
            {"name": "Speakers (Realtek Audio)"},
        ],
    )

    assert auto_select_virtual_device() is None


def test_resolve_loopback_device_info_uses_sounddevice_output_list(monkeypatch):
    recorder = DesktopAudioRecorder(
        on_segment=lambda _audio: None,
        output_device_name="Headphones (Realtek(R) Audio)",
    )
    monkeypatch.setattr(
        "src.audio.desktop_recorder.list_output_devices",
        lambda: [
            {"index": 1, "name": "Speakers (Realtek Audio)", "is_default": False},
            {"index": 12, "name": "Headphones (Realtek(R) Audio)", "is_default": True},
        ],
    )
    monkeypatch.setattr(
        "src.audio.desktop_recorder._default_output_device_name_from_sounddevice",
        lambda: "",
    )

    resolved = recorder._resolve_loopback_device_info()

    assert resolved["index"] == 12
    assert resolved["name"] == "Headphones (Realtek(R) Audio)"


class _FakeSoundCardDevice:
    def __init__(self, name, *, device_id="", channels=2, isloopback=True):
        self.name = name
        self.id = device_id
        self.channels = channels
        self.isloopback = isloopback


class _FakeSoundCardModule:
    def __init__(self, devices):
        self.devices = devices

    def all_microphones(self, include_loopback=False):
        assert include_loopback is True
        return self.devices

    def default_speaker(self):
        return self.devices[0]


def _missing_soundcard():
    try:
        raise ImportError("missing soundcard")
    except ImportError as exc:
        raise RuntimeError("soundcard unavailable") from exc


def test_list_output_devices_prefers_soundcard_loopbacks(monkeypatch):
    fake_sc = _FakeSoundCardModule(
        [
            _FakeSoundCardDevice(
                "Headphones (Realtek(R) Audio)",
                device_id="{default}",
                channels=2,
                isloopback=True,
            ),
            _FakeSoundCardDevice(
                "Microphone (Realtek(R) Audio)",
                device_id="{mic}",
                channels=1,
                isloopback=False,
            ),
        ]
    )
    monkeypatch.setattr("src.audio.desktop_recorder._import_soundcard", lambda: fake_sc)
    monkeypatch.setattr(
        "src.audio.desktop_recorder._import_pyaudio",
        lambda: (_ for _ in ()).throw(AssertionError("PyAudio fallback should not run")),
    )

    devices = list_output_devices()

    assert len(devices) == 1
    assert devices[0]["name"] == "Headphones (Realtek(R) Audio)"
    assert devices[0]["id"] == "{default}"
    assert devices[0]["is_default"] is True
    assert devices[0]["backend"] == "soundcard"


def test_list_output_devices_falls_back_to_direct_wasapi_scan(monkeypatch):
    class FakePyAudio:
        def get_loopback_device_info_generator(self):
            raise AssertionError("generator should not be used")

        def get_host_api_info_by_type(self, _api):
            return {"index": 3}

        def get_device_count(self):
            return 2

        def get_device_info_by_index(self, index):
            if index == 0:
                return {
                    "index": 0,
                    "hostApi": 3,
                    "name": "Headphones (Realtek(R) Audio) [Loopback]",
                    "defaultSampleRate": 48000,
                    "maxInputChannels": 2,
                }
            return {
                "index": 1,
                "hostApi": 3,
                "name": "Microphone (Realtek(R) Audio)",
                "defaultSampleRate": 48000,
                "maxInputChannels": 1,
            }

        def terminate(self):
            pass

    class FakePaModule:
        paWASAPI = 13

        @staticmethod
        def PyAudio():
            return FakePyAudio()

    monkeypatch.setattr("src.audio.desktop_recorder._import_pyaudio", lambda: FakePaModule)
    monkeypatch.setattr("src.audio.desktop_recorder._import_soundcard", _missing_soundcard)
    monkeypatch.setattr(
        "src.audio.desktop_recorder._default_output_device_name_from_sounddevice",
        lambda: "",
    )

    assert list_output_devices() == [
        {
            "index": 0,
            "hostApi": 3,
            "name": "Headphones (Realtek(R) Audio)",
            "raw_name": "Headphones (Realtek(R) Audio) [Loopback]",
            "defaultSampleRate": 48000,
            "maxInputChannels": 2,
            "is_default": False,
        }
    ]


def test_desktop_start_uses_soundcard_loopback_stream(monkeypatch):
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)
    records = []

    class FakeRecorder:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def record(self, numframes):
            time.sleep(0.01)
            records.append(numframes)
            return np.zeros((numframes, 2), dtype=np.float32)

    class FakeLoopback(_FakeSoundCardDevice):
        def recorder(self, samplerate, channels=None, blocksize=None, exclusive_mode=False):
            del exclusive_mode
            assert samplerate == 48000
            assert channels == 2
            assert blocksize == 1440
            return FakeRecorder()

    fake_sc = _FakeSoundCardModule(
        [
            FakeLoopback(
                "Headphones (Realtek(R) Audio)",
                device_id="{default}",
                channels=2,
                isloopback=True,
            )
        ]
    )
    monkeypatch.setattr("src.audio.desktop_recorder._import_soundcard", lambda: fake_sc)
    monkeypatch.setattr(
        "src.audio.desktop_recorder._import_pyaudio",
        lambda: (_ for _ in ()).throw(AssertionError("PyAudio fallback should not run")),
    )
    monkeypatch.setattr(
        "src.audio.desktop_recorder._default_output_device_name_from_sounddevice",
        lambda: "",
    )

    recorder.start()

    assert recorder._loopback_device_info["name"] == "Headphones (Realtek(R) Audio)"
    assert recorder.input_device == 0
    assert recorder.extra_settings is None
    assert recorder._capture_rate == 48000
    assert recorder._capture_channels == 2

    recorder.stop()
    assert records


def test_desktop_start_can_fall_back_to_pyaudiowpatch_loopback_stream(monkeypatch):
    recorder = DesktopAudioRecorder(on_segment=lambda _audio: None)
    open_calls = []

    class FakeStream:
        def is_active(self):
            return True

        def read(self, frame_count, exception_on_overflow=False):
            del exception_on_overflow
            time.sleep(0.01)
            return np.zeros(frame_count * 2, dtype=np.float32).tobytes()

        def stop_stream(self):
            pass

        def close(self):
            pass

    class FakePyAudio:
        def get_host_api_info_by_type(self, _api):
            return {"index": 3}

        def get_device_count(self):
            return 1

        def get_device_info_by_index(self, _index):
            return {
                "index": 12,
                "hostApi": 3,
                "name": "Headphones (Realtek(R) Audio) [Loopback]",
                "defaultSampleRate": 48000,
                "maxInputChannels": 2,
            }

        def is_format_supported(self, rate, **kwargs):
            assert rate == 48000
            assert kwargs["input_device"] == 12
            return True

        def open(self, **kwargs):
            open_calls.append(kwargs)
            return FakeStream()

        def terminate(self):
            pass

    class FakePaModule:
        paWASAPI = 13
        paFloat32 = 1
        paContinue = 0
        paComplete = 1

        @staticmethod
        def PyAudio():
            return FakePyAudio()

    monkeypatch.setattr("src.audio.desktop_recorder._import_pyaudio", lambda: FakePaModule)
    monkeypatch.setattr("src.audio.desktop_recorder._import_soundcard", _missing_soundcard)
    monkeypatch.setattr(
        "src.audio.desktop_recorder._default_output_device_name_from_sounddevice",
        lambda: "",
    )

    recorder.start()

    assert open_calls[0]["input_device_index"] == 12
    assert open_calls[0]["channels"] == 2
    assert open_calls[0]["rate"] == 48000
    assert recorder._loopback_device_info["name"] == "Headphones (Realtek(R) Audio)"
    assert recorder.input_device == 12
    assert recorder.extra_settings is None

    recorder.stop()


def test_empty_soundcard_scan_falls_back_to_pyaudiowpatch(monkeypatch):
    desktop_recorder._reset_loopback_device_cache_for_tests()

    class EmptySoundCard:
        @staticmethod
        def all_microphones(include_loopback=False):
            assert include_loopback is True
            return []

        @staticmethod
        def default_speaker():
            return None

    class FakePyAudio:
        @staticmethod
        def get_host_api_info_by_type(_api):
            return {"index": 2}

        @staticmethod
        def get_device_count():
            return 1

        @staticmethod
        def get_device_info_by_index(_index):
            return {
                "index": 9,
                "hostApi": 2,
                "name": "Speakers (Fallback DAC) [Loopback]",
                "defaultSampleRate": 48000,
                "maxInputChannels": 2,
            }

        @staticmethod
        def terminate():
            pass

    class FakePaModule:
        paWASAPI = 13
        PyAudio = FakePyAudio

    monkeypatch.setattr(desktop_recorder, "_import_soundcard", lambda: EmptySoundCard)
    monkeypatch.setattr(desktop_recorder, "_import_pyaudio", lambda: FakePaModule)
    monkeypatch.setattr(desktop_recorder, "_windows_render_endpoints", lambda: [])
    monkeypatch.setattr(
        desktop_recorder,
        "_default_output_device_name_from_sounddevice",
        lambda: "Speakers (Fallback DAC)",
    )

    devices = list_output_devices()

    assert [item["name"] for item in devices] == ["Speakers (Fallback DAC)"]
    assert devices[0]["is_default"] is True


def test_generic_speakers_prefix_does_not_mark_multiple_defaults(monkeypatch):
    default_speaker = _FakeSoundCardDevice("Speakers", device_id="{default-speaker}")

    class FakeSoundCard:
        @staticmethod
        def all_microphones(include_loopback=False):
            assert include_loopback is True
            return [
                _FakeSoundCardDevice("Speakers (USB DAC)", device_id="{dac}"),
                _FakeSoundCardDevice("Speakers (VR Headset)", device_id="{vr}"),
            ]

        @staticmethod
        def default_speaker():
            return default_speaker

    monkeypatch.setattr(desktop_recorder, "_windows_render_endpoints", lambda: [])
    monkeypatch.setattr(
        desktop_recorder,
        "_default_output_device_name_from_sounddevice",
        lambda: "",
    )

    devices = desktop_recorder._enumerate_soundcard_loopback_devices(FakeSoundCard)

    assert len(devices) == 2
    assert not any(item["is_default"] for item in devices)


def test_busy_pyaudio_enumeration_returns_last_good_devices(monkeypatch):
    desktop_recorder._reset_loopback_device_cache_for_tests()
    cached = [
        {
            "index": 2,
            "name": "Speakers (Cached DAC)",
            "is_default": True,
            "backend": "soundcard",
        }
    ]
    desktop_recorder._cache_loopback_devices(cached, backend="soundcard")
    monkeypatch.setattr(desktop_recorder, "_import_soundcard", _missing_soundcard)
    monkeypatch.setattr(
        desktop_recorder,
        "_refresh_cached_loopbacks_from_sounddevice",
        lambda devices: devices,
    )

    desktop_recorder._pyaudio_lock.acquire()
    try:
        devices = list_output_devices()
    finally:
        desktop_recorder._pyaudio_lock.release()

    assert [item["name"] for item in devices] == ["Speakers (Cached DAC)"]
    diagnostics = desktop_recorder.loopback_device_diagnostics()
    assert diagnostics["from_cache"] is True
    assert "capture stream is active" in str(diagnostics["cache_reason"])


def test_inactive_soundcard_endpoint_is_excluded(monkeypatch):
    endpoint_id = "{inactive-endpoint}"
    fake_sc = _FakeSoundCardModule(
        [
            _FakeSoundCardDevice(
                "Speakers (Disconnected DAC)",
                device_id=endpoint_id,
                isloopback=True,
            )
        ]
    )
    monkeypatch.setattr(
        desktop_recorder,
        "_windows_render_endpoints",
        lambda: [
            {
                "id": endpoint_id,
                "name": "Speakers (Disconnected DAC)",
                "flow": "render",
                "active": False,
                "state_name": "unplugged",
            }
        ],
    )

    assert desktop_recorder._enumerate_soundcard_loopback_devices(fake_sc) == []


def test_missing_packaged_loopback_backends_fail_cleanly_with_diagnostics(monkeypatch):
    desktop_recorder._reset_loopback_device_cache_for_tests()

    def missing_pyaudio():
        raise RuntimeError(
            "Desktop audio capture component is unavailable in the packaged runtime"
        )

    monkeypatch.setattr(desktop_recorder, "_import_soundcard", _missing_soundcard)
    monkeypatch.setattr(desktop_recorder, "_import_pyaudio", missing_pyaudio)

    assert list_output_devices() == []
    diagnostics = desktop_recorder.loopback_device_diagnostics()
    assert diagnostics["backend"] == "unavailable"
    assert diagnostics["device_count"] == 0
    assert any("SoundCard" in error for error in diagnostics["errors"])
    assert any("packaged runtime" in error for error in diagnostics["errors"])


def test_empty_loopback_scan_is_negatively_cached_with_backoff(monkeypatch):
    desktop_recorder._reset_loopback_device_cache_for_tests()
    now = [100.0]
    soundcard_calls: list[float] = []
    pyaudio_calls: list[float] = []

    def missing_soundcard():
        soundcard_calls.append(now[0])
        raise RuntimeError("soundcard unavailable")

    def missing_pyaudio():
        pyaudio_calls.append(now[0])
        raise RuntimeError("pyaudio unavailable")

    monkeypatch.setattr(desktop_recorder.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(desktop_recorder, "_import_soundcard", missing_soundcard)
    monkeypatch.setattr(desktop_recorder, "_import_pyaudio", missing_pyaudio)

    assert list_output_devices() == []
    assert list_output_devices() == []
    assert soundcard_calls == [100.0]
    assert pyaudio_calls == [100.0]
    assert desktop_recorder.loopback_device_diagnostics()["from_cache"] is True

    now[0] += desktop_recorder._EMPTY_LOOPBACK_BACKOFF_BASE_S + 0.1
    assert list_output_devices() == []
    assert soundcard_calls == [100.0, now[0]]
    assert pyaudio_calls == [100.0, now[0]]


def test_hotplug_refresh_updates_cached_pyaudio_names_without_reopening_portaudio(monkeypatch):
    monkeypatch.setattr(
        desktop_recorder,
        "list_sounddevice_output_devices",
        lambda **_kwargs: [
            {
                "index": 40,
                "name": "Speakers (New USB DAC)",
                "is_default": True,
                "hostapi": "Windows WASAPI",
            }
        ],
    )

    refreshed = desktop_recorder._refresh_cached_loopbacks_from_sounddevice(
        [
            {
                "index": 9,
                "name": "Speakers (Removed USB DAC)",
                "is_default": True,
                "backend": "pyaudiowpatch",
            }
        ]
    )

    assert refreshed == [
        {
            "index": 40,
            "name": "Speakers (New USB DAC)",
            "is_default": True,
            "backend": "sounddevice_inventory",
            "enumeration_only": True,
        }
    ]
