"""After a network failure the fallback is temporary: the primary is tried
again every couple of minutes and takes over as soon as it answers."""

from __future__ import annotations

import numpy as np

from src.asr.base import ASRProvider
from src.asr.errors import ASRNetworkError
from src.asr.fallback_asr import FallbackASR


class _Flaky(ASRProvider):
    provider_id = "edge-stt"
    supports_partial = False

    def __init__(self):
        self.down = True
        self.calls = 0

    def load(self, progress_callback=None) -> None:
        return None

    def transcribe(self, audio, sample_rate=16000, language=None, is_final=True) -> str:
        self.calls += 1
        if self.down:
            raise ASRNetworkError("route is down")
        return "online text"


class _Local(ASRProvider):
    provider_id = "sensevoice-small"
    supports_partial = False

    def __init__(self):
        self.calls = 0

    def load(self, progress_callback=None) -> None:
        return None

    def transcribe(self, audio, sample_rate=16000, language=None, is_final=True) -> str:
        self.calls += 1
        return "local text"


def _audio():
    return np.zeros(1600, dtype=np.float32)


def test_the_primary_is_retried_after_the_interval_and_taken_back_when_it_answers():
    now = {"t": 0.0}
    primary, local = _Flaky(), _Local()
    asr = FallbackASR(primary, local, auto_fallback=True, primary_retry_seconds=120.0, clock=lambda: now["t"])
    switches: list = []
    asr.on_switch = lambda mode, reason: switches.append(mode)

    assert asr.transcribe(_audio()) == "local text"
    assert asr.using_fallback and switches == ["fallback"]

    # Inside the interval the primary is left alone.
    now["t"] = 60.0
    assert asr.transcribe(_audio()) == "local text"
    assert primary.calls == 1

    # Interval over, still down: one probe, then straight back to local.
    now["t"] = 121.0
    assert asr.transcribe(_audio()) == "local text"
    assert primary.calls == 2 and asr.using_fallback
    # ... and the next probe waits another full interval.
    now["t"] = 180.0
    assert asr.transcribe(_audio()) == "local text"
    assert primary.calls == 2

    # The route is back: the probe succeeds and recognition returns online.
    primary.down = False
    now["t"] = 242.0
    assert asr.transcribe(_audio()) == "online text"
    assert not asr.using_fallback
    assert switches == ["fallback", "primary"]
    assert asr.transcribe(_audio()) == "online text"
    # Four local sentences: the first failure, one inside the interval, one
    # after the failed probe and one more while waiting for the next probe.
    assert local.calls == 4


def test_realtime_path_recovers_the_same_way():
    now = {"t": 0.0}
    primary, local = _Flaky(), _Local()
    asr = FallbackASR(primary, local, auto_fallback=True, primary_retry_seconds=30.0, clock=lambda: now["t"])

    assert asr.transcribe_realtime(_audio()) == "local text"
    primary.down = False
    now["t"] = 31.0

    assert asr.transcribe_realtime(_audio()) == "online text"
    assert not asr.using_fallback


def test_a_zero_interval_keeps_the_old_behaviour_of_never_returning():
    primary, local = _Flaky(), _Local()
    asr = FallbackASR(primary, local, auto_fallback=True, primary_retry_seconds=0.0)

    asr.transcribe(_audio())
    primary.down = False

    assert asr.transcribe(_audio()) == "local text"
    assert asr.using_fallback


def test_a_broken_switch_listener_does_not_break_recognition():
    primary, local = _Flaky(), _Local()
    asr = FallbackASR(primary, local, auto_fallback=True)
    asr.on_switch = lambda mode, reason: (_ for _ in ()).throw(RuntimeError("ui gone"))

    assert asr.transcribe(_audio()) == "local text"
