import queue

from src.ui_qt.main_window import (
    DESKTOP_FINAL_TASK_QUEUE_MAXSIZE,
    DESKTOP_SOURCE,
    FINAL_TASK_QUEUE_MAXSIZE,
    MIC_SOURCE,
    MainWindow,
)


def _window_for_mute(muted: bool = True):
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._listen_session = 7
    window._mic_muted = muted
    window._partial_task_queues = {
        MIC_SOURCE: queue.Queue(maxsize=1),
        DESKTOP_SOURCE: queue.Queue(maxsize=1),
    }
    window._final_task_queues = {
        MIC_SOURCE: queue.Queue(maxsize=FINAL_TASK_QUEUE_MAXSIZE),
        DESKTOP_SOURCE: queue.Queue(maxsize=DESKTOP_FINAL_TASK_QUEUE_MAXSIZE),
    }
    window._current_asr_lang = "ja"
    window._current_src_lang = "ja"
    window._listen_asr_language = lambda: "en"
    window._listen_source_language = lambda: "en"
    window._reset_streaming_state = lambda source=None: setattr(
        window,
        "_last_reset_source",
        source,
    )
    return window


def test_mic_mute_drops_mic_segments_but_allows_reverse_translation():
    window = _window_for_mute(muted=True)

    window._on_audio_segment("private mic audio", MIC_SOURCE)

    assert window._final_task_queues[MIC_SOURCE].empty()
    assert window._last_reset_source == MIC_SOURCE

    window._on_audio_segment("desktop audio", DESKTOP_SOURCE)

    payload = window._final_task_queues[DESKTOP_SOURCE].get_nowait()
    assert payload[-1] == DESKTOP_SOURCE


def test_reverse_translation_final_queue_keeps_latest_segment():
    window = _window_for_mute(muted=False)

    window._on_audio_segment("old desktop audio", DESKTOP_SOURCE)
    window._on_audio_segment("new desktop audio", DESKTOP_SOURCE)

    payload = window._final_task_queues[DESKTOP_SOURCE].get_nowait()
    assert payload[0] == "new desktop audio"
    assert window._final_task_queues[DESKTOP_SOURCE].empty()


def test_mic_final_queue_keeps_latest_segment_under_backpressure():
    window = _window_for_mute(muted=False)

    window._on_audio_segment("old mic audio", MIC_SOURCE)
    window._on_audio_segment("new mic audio", MIC_SOURCE)

    payload = window._final_task_queues[MIC_SOURCE].get_nowait()
    assert payload[0] == "new mic audio"
    assert window._final_task_queues[MIC_SOURCE].empty()


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
        "status_running": "监听中...",
        "status_speaking": "说话中...",
    }.get(key, key)

    def record_status(text, color="default", key=None):
        window._status_key = key
        status_events.append((key, text, color))

    window._set_status = record_status

    window._handle_mic_vad_state(True)
    window._handle_mic_vad_state(False)

    assert status_events[0] == ("status_speaking", "说话中...", "accent")
    assert status_events[-1] == ("status_running", "监听中...", "accent")


def test_stop_workers_replaces_full_queues_with_stop_sentinel():
    window = MainWindow.__new__(MainWindow)
    partial_queue = queue.Queue(maxsize=1)
    final_queue = queue.Queue(maxsize=1)
    partial_queue.put_nowait("stale partial")
    final_queue.put_nowait("stale final")
    window._partial_task_queues = {MIC_SOURCE: partial_queue}
    window._final_task_queues = {MIC_SOURCE: final_queue}
    window._partial_workers = {"partial": object()}
    window._final_workers = {"final": object()}

    window._stop_workers()

    assert partial_queue.get_nowait() is None
    assert final_queue.get_nowait() is None
    assert window._partial_workers == {}
    assert window._final_workers == {}


def test_reverse_translation_yields_while_mic_is_speaking():
    window = _window_for_mute(muted=False)
    shared_asr = object()
    window._asr = shared_asr
    window._listen_asr = shared_asr
    window._mic_in_speech = True

    window._on_audio_segment("desktop audio", DESKTOP_SOURCE)

    assert window._final_task_queues[DESKTOP_SOURCE].empty()


def test_reverse_translation_does_not_yield_to_mic_with_independent_asr():
    window = _window_for_mute(muted=False)
    window._asr = object()
    window._listen_asr = object()
    window._mic_in_speech = True

    window._on_audio_segment("desktop audio", DESKTOP_SOURCE)

    payload = window._final_task_queues[DESKTOP_SOURCE].get_nowait()
    assert payload[0] == "desktop audio"


def test_mic_mute_drops_already_queued_final_mic_audio():
    window = _window_for_mute(muted=True)
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
