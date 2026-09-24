"""VAD calibration measures without listening running and never posts the test."""

from __future__ import annotations

import pytest

from src.ui_qt.main_window import DESKTOP_SOURCE, MIC_SOURCE, MainWindow


class _Meter:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.is_running = False
        _Meter.instances.append(self)

    def start(self):
        self.started = True
        self.is_running = True

    def stop(self):
        self.stopped = True
        self.is_running = False

    def diagnostics_snapshot(self):
        return {"last_frame_rms": 0.03}


@pytest.fixture
def window(monkeypatch):
    _Meter.instances = []
    monkeypatch.setattr("src.audio.recorder.AudioRecorder", _Meter)
    monkeypatch.setattr("src.audio.desktop_recorder.DesktopAudioRecorder", _Meter)
    win = MainWindow.__new__(MainWindow)
    win._config = {"audio": {"vad_min_rms": 0.012}, "vrc_listen": {}}
    win._recorder = None
    win._listen_recorder = None
    win._devices = {"Mic": 3}
    win._desktop_devices = {"Speakers": 1}
    win._vad_calibration_windows = {}
    win._active_mic_input_device_name = None
    win._active_listen_output_device_name = None
    win._running = False
    monkeypatch.setattr(win, "_resolve_mic_input_device_name", lambda refresh=False: "Mic")
    monkeypatch.setattr(win, "_match_mic_input_device_name", lambda name: name)
    monkeypatch.setattr(win, "_effective_mic_tail_silence_s", lambda cfg: 0.65)
    monkeypatch.setattr(win, "_desktop_output_device_name", lambda: "Speakers")
    monkeypatch.setattr(win, "_listen_tail_silence_s", lambda: 0.8)
    return win


def test_a_stopped_pipeline_gets_a_meter_of_its_own(window):
    MainWindow._begin_vad_calibration(window, MIC_SOURCE)

    meter = _Meter.instances[-1]
    assert meter.started
    assert meter.kwargs["input_device"] == 3
    snapshot = MainWindow.audio_diagnostics_snapshot(window, MIC_SOURCE)
    assert snapshot["last_frame_rms"] == 0.03
    assert snapshot["running"] is True

    MainWindow._on_vad_calibration_closed(window, MIC_SOURCE)

    assert meter.stopped
    assert not MainWindow._calibration_in_progress(window, MIC_SOURCE)


def test_the_listen_side_can_be_measured_too(window):
    MainWindow._begin_vad_calibration(window, DESKTOP_SOURCE)

    assert _Meter.instances[-1].kwargs["output_device_name"] == "Speakers"


def test_a_running_recorder_is_used_as_it_is(window):
    window._recorder = _Meter()
    _Meter.instances.clear()

    MainWindow._begin_vad_calibration(window, MIC_SOURCE)

    assert _Meter.instances == []
    assert MainWindow._calibration_in_progress(window, MIC_SOURCE)


def test_the_speech_sample_is_never_translated(window, monkeypatch):
    window._running = True
    reset: list = []
    monkeypatch.setattr(window, "_reset_streaming_state", reset.append)
    MainWindow._begin_vad_calibration(window, MIC_SOURCE)

    assert MainWindow._on_audio_segment(window, b"audio", MIC_SOURCE) is None
    assert reset == [MIC_SOURCE]
    assert MainWindow._on_audio_chunk(window, b"audio", MIC_SOURCE) is None
