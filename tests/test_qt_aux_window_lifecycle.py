from __future__ import annotations

from src.ui_qt.audio_diagnostics_window import AudioDiagnosticsWindow
from src.ui_qt.sponsor_window import SponsorWindow
from src.ui_qt.vad_calibration_window import VadCalibrationWindow


def test_audio_diagnostics_timer_stops_while_closed_and_restarts_on_show(qtbot):
    window = AudioDiagnosticsWindow(
        None,
        target="mic",
        snapshot_provider=lambda _target: {},
        ui_language="en",
    )
    qtbot.addWidget(window)
    window.show()
    assert window._timer.isActive() is True

    window.close()
    assert window._timer.isActive() is False

    window.show()
    assert window._timer.isActive() is True
    window.close()


def test_vad_calibration_timer_stops_when_window_closes(qtbot):
    window = VadCalibrationWindow(
        None,
        target="mic",
        snapshot_provider=lambda _target: {},
        apply_callback=lambda _target, _result: None,
        ui_language="en",
    )
    qtbot.addWidget(window)
    window._start_phase("noise")
    assert window._tick_timer.isActive() is True

    window.close()

    assert window._tick_timer.isActive() is False
    assert window._phase == "idle"


def test_sponsor_fetch_callback_is_ignored_after_window_close(qtbot, monkeypatch):
    callbacks = []
    monkeypatch.setattr(
        "src.ui_qt.sponsor_window.get_sponsors",
        lambda callback, **_kwargs: callbacks.append(callback),
    )
    window = SponsorWindow(None, ui_language="en")
    qtbot.addWidget(window)
    assert len(callbacks) == 1

    window.close()
    callbacks[0]({"version": 2, "sponsors": [{"name": "late"}]})

    assert window._data_loaded is False
    assert window._last_data == {}
