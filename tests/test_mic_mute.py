from __future__ import annotations

import queue
from types import SimpleNamespace

from src.core.realtime_scheduler import AdmissionResult, AdmissionStatus
from src.ui_qt.main_window import (
    DESKTOP_SOURCE,
    MIC_SOURCE,
    WORKER_STOP_TIMEOUT_S,
    MainWindow,
)


class _FakeScheduler:
    def __init__(self, statuses: list[AdmissionStatus] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.submissions: list[dict] = []
        self.stop_calls: list[float | None] = []
        self.threads: tuple = ()

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        status = self.statuses.pop(0) if self.statuses else AdmissionStatus.ACCEPTED
        task = SimpleNamespace(**kwargs) if status is AdmissionStatus.ACCEPTED else None
        return AdmissionResult(status=status, task=task)

    def stop(self, timeout=WORKER_STOP_TIMEOUT_S):
        self.stop_calls.append(timeout)
        return True


def _window_for_mute(
    muted: bool = True,
    *,
    statuses: list[AdmissionStatus] | None = None,
):
    window = MainWindow.__new__(MainWindow)
    scheduler = _FakeScheduler(statuses)
    shared_asr = object()
    window._running = True
    window._destroying = False
    window._listen_session = 7
    window._mic_muted = muted
    window._mic_in_speech = False
    window._asr = shared_asr
    window._listen_asr = shared_asr
    window._realtime_scheduler = scheduler
    window._realtime_config_snapshot = {}
    window._config = {}
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._current_tgt_lang_3 = ""
    window._current_asr_lang = "ja"
    window._current_src_lang = "ja"
    window._listen_asr_language = lambda: "en"
    window._listen_source_language = lambda: "en"
    window._listen_target_language = lambda: "ja"
    window._listen_send_to_chatbox_enabled = lambda: False
    window._mic_send_to_chatbox_enabled = lambda: True
    window._listen_tts_echo_suppress_active = lambda: False
    window._copy = lambda key: key
    window._call_in_ui = lambda callback: callback()
    window._bottom_events = []
    window._set_bottom = lambda *args: window._bottom_events.append(args)
    window._reset_streaming_state = lambda source=None: setattr(
        window,
        "_last_reset_source",
        source,
    )
    return window, scheduler


def test_mic_mute_drops_mic_segments_but_allows_reverse_translation():
    window, scheduler = _window_for_mute(muted=True)

    assert window._on_audio_segment("private mic audio", MIC_SOURCE) is None
    assert scheduler.submissions == []
    assert window._last_reset_source == MIC_SOURCE

    admission = window._on_audio_segment("desktop audio", DESKTOP_SOURCE)

    assert admission.status is AdmissionStatus.ACCEPTED
    assert [item["source"] for item in scheduler.submissions] == [DESKTOP_SOURCE]
    assert scheduler.submissions[0]["payload"].audio == "desktop audio"


def test_final_sentences_are_submitted_in_original_order_without_replacement():
    window, scheduler = _window_for_mute(muted=False)

    window._on_audio_segment("first desktop sentence", DESKTOP_SOURCE)
    window._on_audio_segment("second desktop sentence", DESKTOP_SOURCE)
    window._on_audio_segment("first mic sentence", MIC_SOURCE)
    window._on_audio_segment("second mic sentence", MIC_SOURCE)

    assert [
        (item["source"], item["payload"].audio)
        for item in scheduler.submissions
    ] == [
        (DESKTOP_SOURCE, "first desktop sentence"),
        (DESKTOP_SOURCE, "second desktop sentence"),
        (MIC_SOURCE, "first mic sentence"),
        (MIC_SOURCE, "second mic sentence"),
    ]


def test_desktop_final_is_submitted_while_shared_microphone_asr_is_active():
    window, scheduler = _window_for_mute(muted=False)
    window._mic_in_speech = True

    admission = window._on_audio_segment("desktop audio", DESKTOP_SOURCE)

    assert admission.status is AdmissionStatus.ACCEPTED
    assert scheduler.submissions[0]["source"] == DESKTOP_SOURCE


def test_final_backpressure_is_exposed_without_evicting_prior_work():
    window, scheduler = _window_for_mute(
        muted=False,
        statuses=[AdmissionStatus.ACCEPTED, AdmissionStatus.FULL],
    )

    first = window._on_audio_segment("first", MIC_SOURCE)
    second = window._on_audio_segment("second", MIC_SOURCE)

    assert first.status is AdmissionStatus.ACCEPTED
    assert second.status is AdmissionStatus.FULL
    assert [item["payload"].audio for item in scheduler.submissions] == ["first", "second"]
    assert window._bottom_events == [("realtime_queue_full", "warning")]


def test_mic_vad_state_reports_speaking_status():
    window = MainWindow.__new__(MainWindow)
    status_events: list[tuple[str | None, str, str]] = []

    window._running = True
    window._mic_muted = False
    window._mic_in_speech = False
    window._status_label = object()
    window._status_key = "status_running"
    window._translating = False
    window._t = lambda key, **_kwargs: {
        "status_running": "???...",
        "status_speaking": "???...",
    }.get(key, key)

    def record_status(text, color="default", key=None):
        window._status_key = key
        status_events.append((key, text, color))

    window._set_status = record_status

    window._handle_mic_vad_state(True)
    window._handle_mic_vad_state(False)

    assert status_events[0] == ("status_speaking", "???...", "accent")
    assert status_events[-1] == ("status_running", "???...", "accent")


def test_stop_workers_stops_scheduler_and_joins_partial_workers():
    class _PartialWorker:
        name = "partial-test"

        def __init__(self) -> None:
            self.join_calls: list[float | None] = []

        def join(self, timeout=None):
            self.join_calls.append(timeout)

        def is_alive(self):
            return False

    window = MainWindow.__new__(MainWindow)
    partial_queue = queue.Queue(maxsize=1)
    partial_queue.put_nowait("stale partial")
    partial_worker = _PartialWorker()
    scheduler = _FakeScheduler()
    window._partial_task_queues = {MIC_SOURCE: partial_queue}
    window._final_task_queues = {MIC_SOURCE: queue.Queue(maxsize=1)}
    window._partial_workers = {MIC_SOURCE: partial_worker}
    window._final_workers = {MIC_SOURCE: object()}
    window._realtime_scheduler = scheduler

    barrier = window._stop_workers()

    assert barrier is None
    assert partial_queue.get_nowait() is None
    assert scheduler.stop_calls == [WORKER_STOP_TIMEOUT_S]
    assert len(partial_worker.join_calls) == 1
    assert window._realtime_scheduler is None
    assert window._partial_workers == {}
    assert window._final_workers == {}
    assert window._partial_task_queues == {}
    assert window._final_task_queues == {}


def test_mic_mute_drops_already_admitted_final_mic_audio():
    window, _scheduler = _window_for_mute(muted=True)
    window._asr_for_source = lambda _source: (_ for _ in ()).throw(
        AssertionError("muted mic audio should not reach ASR")
    )

    window._process_final_audio_segment(
        "private mic audio",
        None,
        None,
        window._listen_session,
        MIC_SOURCE,
    )


def test_mic_mute_syncs_avatar_muted_and_speaking_state():
    window = MainWindow.__new__(MainWindow)
    events: list[tuple[str, bool, bool]] = []

    class _Sender:
        def send_avatar_bool(self, name, value, *, force=False):
            events.append((name, value, force))
            return True

    window._mic_muted = False
    window._mic_in_speech = True
    window._desktop_in_speech = False
    window._config = {
        "osc": {
            "avatar_sync": {
                "enabled": True,
                "params": {
                    "muted": "MioMuted",
                    "speaking": "MioSpeaking",
                },
            }
        }
    }
    window._refresh_mic_mute_button = lambda: None
    window._set_bottom = lambda *args, **kwargs: None
    window._copy = lambda key: key
    window._ensure_sender = lambda: _Sender()

    window._toggle_mic_mute()

    assert ("MioMuted", True, True) in events
    assert ("MioSpeaking", False, True) in events


def test_vrchat_mute_pauses_and_unmute_resumes_physical_microphone_capture():
    window = MainWindow.__new__(MainWindow)
    capture_events: list[str] = []
    bottom_events: list[str] = []
    window._config = {"osc": {"sync_mute_self": True}}
    window._running = True
    window._destroying = False
    window._mic_muted = False
    window._mic_capture_paused_for_mute = False
    window._recorder = object()
    asr_capture_events: list[bool] = []

    class _ASR:
        def set_capture_enabled(self, enabled: bool):
            asr_capture_events.append(enabled)

    window._asr = _ASR()
    window._refresh_mic_mute_button = lambda: None
    window._set_bottom = lambda message, *_args, **_kwargs: bottom_events.append(message)
    window._copy = lambda key: key
    window._sync_avatar_muted_state = lambda **_kwargs: None
    window._sync_avatar_speaking_state = lambda **_kwargs: None
    window._reset_streaming_state = lambda _source=None: capture_events.append("reset")

    def stop_capture():
        capture_events.append("stop")
        window._recorder = None

    def start_capture():
        capture_events.append("start")
        window._recorder = object()

    window._stop_microphone_capture = stop_capture
    window._start_microphone_capture = start_capture

    window._handle_vrchat_mute_self(True)
    window._handle_vrchat_mute_self(True)
    window._handle_vrchat_mute_self(False)

    assert window._mic_muted is False
    assert window._mic_capture_paused_for_mute is False
    assert capture_events == ["reset", "stop", "start"]
    assert asr_capture_events == [False, False, True]
    assert window._realtime_source_generations[MIC_SOURCE] == 1
    assert bottom_events == ["mic_mute_on", "mic_mute_off"]


def test_microphone_start_is_suppressed_while_muted():
    window = MainWindow.__new__(MainWindow)
    window._mic_muted = True
    window._mic_capture_paused_for_mute = False
    window._resolve_mic_input_device_name = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("muted microphone must not be opened")
    )

    window._start_microphone_capture()

    assert window._mic_capture_paused_for_mute is True
