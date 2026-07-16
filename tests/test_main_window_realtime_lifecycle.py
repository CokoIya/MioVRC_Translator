from __future__ import annotations

import gc
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from src.asr.errors import ASRTemporaryUnavailableError
from src.core.realtime_pipelines import RealtimeTranslationResult
from src.core.realtime_scheduler import (
    AdmissionStatus,
    RealtimeCompletion,
    RealtimeTask,
)
from src.translators.base import TranslationContextStore
from src.ui_qt import main_window
from src.ui_qt.main_window import (
    DESKTOP_SOURCE,
    MIC_SOURCE,
    MainWindow,
    _RealtimeAudioPayload,
    _RealtimeRewriteWorkerState,
    _RealtimeTranslationWorkerState,
    _freeze_snapshot_value,
)


def _realtime_window_for_submission():
    class CaptureScheduler:
        def __init__(self) -> None:
            self.submissions: list[dict] = []

        def submit(self, **kwargs):
            self.submissions.append(kwargs)
            return SimpleNamespace(status=AdmissionStatus.ACCEPTED)

    window = MainWindow.__new__(MainWindow)
    scheduler = CaptureScheduler()
    provider = object()
    window._running = True
    window._destroying = False
    window._listen_session = 3
    window._mic_muted = False
    window._asr = provider
    window._listen_asr = provider
    window._realtime_scheduler = scheduler
    window._realtime_config_snapshot = {}
    window._config = {}
    window._current_asr_lang = None
    window._current_src_lang = None
    window._current_tgt_lang = "ja"
    window._current_tgt_lang_2 = "en"
    window._current_tgt_lang_3 = ""
    window._listen_source_language = lambda: "en"
    window._listen_target_language = lambda: "ja"
    window._listen_send_to_chatbox_enabled = lambda: False
    window._mic_send_to_chatbox_enabled = lambda: False
    window._listen_tts_echo_suppress_active = lambda: False
    window._reset_streaming_state = lambda _source=None: None
    window._copy = lambda key: key
    return window, scheduler


def test_final_numpy_audio_snapshot_is_owned_contiguous_and_read_only():
    np = pytest.importorskip("numpy")
    window, scheduler = _realtime_window_for_submission()
    recorder_storage = np.arange(24, dtype=np.float32).reshape(4, 6)
    non_contiguous_view = recorder_storage[:, ::2]
    expected = non_contiguous_view.copy()

    window._on_audio_segment(non_contiguous_view, MIC_SOURCE)

    snapshot = scheduler.submissions[0]["payload"].audio
    assert snapshot.flags.c_contiguous
    assert not snapshot.flags.writeable
    assert not np.shares_memory(snapshot, recorder_storage)
    assert np.array_equal(snapshot, expected)

    recorder_storage.fill(-1)
    assert np.array_equal(snapshot, expected)
    with pytest.raises(ValueError):
        snapshot[0, 0] = 99


def test_only_admitted_microphone_partial_lane_receives_chunk_callback(monkeypatch):
    from src.audio import desktop_recorder as desktop_recorder_module
    from src.audio import recorder as recorder_module

    created: dict[str, dict] = {}

    class FakeMicRecorder:
        active_input_device_name = "Microphone"

        def __init__(self, **kwargs) -> None:
            created["mic"] = kwargs

        def start(self) -> None:
            pass

    class FakeDesktopRecorder:
        def __init__(self, **kwargs) -> None:
            created["desktop"] = kwargs

        def start(self) -> None:
            pass

    monkeypatch.setattr(recorder_module, "AudioRecorder", FakeMicRecorder)
    monkeypatch.setattr(
        desktop_recorder_module,
        "DesktopAudioRecorder",
        FakeDesktopRecorder,
    )

    window = MainWindow.__new__(MainWindow)
    chunks: list[tuple[str, object]] = []
    window._config = {
        "audio": {},
        "asr": {
            "streaming": {
                "chunk_interval_ms": 600,
                "chunk_window_s": 2.0,
                "ring_buffer_s": 5.0,
                "recent_speech_hold_s": 0.4,
            }
        },
        "vrc_listen": {},
    }
    window._partial_task_queues = {MIC_SOURCE: object()}
    window._devices = {}
    window._desktop_devices = {"Speakers": 0}
    window._recorder = None
    window._listen_recorder = None
    window._resolve_mic_input_device_name = lambda refresh=True: None
    window._on_audio_segment = lambda *_args, **_kwargs: None
    window._on_audio_chunk = lambda audio, source=MIC_SOURCE: chunks.append((source, audio))
    window._call_in_ui = lambda callback: callback()
    window._handle_mic_vad_state = lambda _active: None
    window._handle_listen_vad_state = lambda _active: None
    window._handle_desktop_capture_runtime_error = lambda _message: None
    window._log_listen_environment = lambda _phase: None
    window._desktop_output_device_name = lambda: "Speakers"
    window._listen_segment_duration_s = lambda: 1.0
    window._listen_tail_silence_s = lambda: 0.6
    window._copy = lambda key: key
    window._refresh_floating_window_status = lambda _active: None

    window._start_microphone_capture()
    window._start_listen()

    assert callable(created["mic"]["on_chunk"])
    assert created["desktop"]["on_chunk"] is None
    assert created["mic"]["chunk_interval_ms"] == 600
    assert created["mic"]["chunk_window_s"] == 2.0
    assert created["mic"]["ring_buffer_s"] == 5.0
    assert created["mic"]["recent_speech_hold_s"] == 0.4
    created["mic"]["on_chunk"]("mic chunk")
    assert chunks == [(MIC_SOURCE, "mic chunk")]


def test_recorders_skip_partial_audio_copying_without_partial_worker(monkeypatch):
    from src.audio import desktop_recorder as desktop_recorder_module
    from src.audio import recorder as recorder_module

    created: dict[str, dict] = {}

    class FakeMicRecorder:
        active_input_device_name = "Microphone"

        def __init__(self, **kwargs) -> None:
            created["mic"] = kwargs

        def start(self) -> None:
            pass

    class FakeDesktopRecorder:
        def __init__(self, **kwargs) -> None:
            created["desktop"] = kwargs

        def start(self) -> None:
            pass

    monkeypatch.setattr(recorder_module, "AudioRecorder", FakeMicRecorder)
    monkeypatch.setattr(
        desktop_recorder_module,
        "DesktopAudioRecorder",
        FakeDesktopRecorder,
    )

    window = MainWindow.__new__(MainWindow)
    window._config = {"audio": {}, "asr": {}, "vrc_listen": {}}
    window._partial_task_queues = {}
    window._devices = {}
    window._desktop_devices = {"Speakers": 0}
    window._recorder = None
    window._listen_recorder = None
    window._resolve_mic_input_device_name = lambda refresh=True: None
    window._on_audio_segment = lambda *_args, **_kwargs: None
    window._call_in_ui = lambda callback: callback()
    window._handle_mic_vad_state = lambda _active: None
    window._handle_listen_vad_state = lambda _active: None
    window._handle_desktop_capture_runtime_error = lambda _message: None
    window._log_listen_environment = lambda _phase: None
    window._desktop_output_device_name = lambda: "Speakers"
    window._listen_segment_duration_s = lambda: 1.0
    window._listen_tail_silence_s = lambda: 0.6
    window._copy = lambda key: key
    window._refresh_floating_window_status = lambda _active: None

    window._start_microphone_capture()
    window._start_listen()

    assert created["mic"]["on_chunk"] is None
    assert created["desktop"]["on_chunk"] is None


def test_simultaneous_mode_uses_low_latency_vad_chunking_limits():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "app_mode": "simultaneous",
        "simul_mode": {
            "vad_silence_ms": 300,
            "aggressive_chunking": False,
        },
    }
    audio_cfg = {
        "vad_silence_threshold": 0.65,
        "max_segment_s": 6.0,
    }

    assert window._effective_mic_tail_silence_s(audio_cfg) == 0.3
    assert window._effective_mic_max_segment_s(audio_cfg) == 4.0

    window._config["simul_mode"]["aggressive_chunking"] = True
    assert window._effective_mic_tail_silence_s(audio_cfg) == 0.22
    assert window._effective_mic_max_segment_s(audio_cfg) == 2.5


def test_translation_worker_concurrency_is_provider_aware():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "performance": {"profile": "balanced"},
        "translation": {
            "backend": "qianwen",
            "qianwen": {"base_url": "https://dashscope.aliyuncs.com/v1"},
        },
    }
    assert window._realtime_translation_worker_concurrency() == 3

    window._config["translation"] = {
        "backend": "openai_compatible",
        "openai_compatible": {"base_url": "http://127.0.0.1:8000/v1"},
    }
    assert window._realtime_translation_worker_concurrency() == 1

    window._config["translation"]["openai_compatible"][
        "max_concurrent_requests"
    ] = 4
    assert window._realtime_translation_worker_concurrency() == 4


def test_distinct_asr_providers_close_exactly_once():
    class Provider:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    window = MainWindow.__new__(MainWindow)
    mic = Provider()
    desktop = Provider()
    window._asr = mic
    window._listen_asr = desktop

    window._close_asr_providers()
    window._close_asr_providers(mic, desktop)

    assert mic.close_count == 1
    assert desktop.close_count == 1
    assert window._asr is None
    assert window._listen_asr is None


def test_shared_asr_provider_closes_exactly_once():
    class Provider:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    window = MainWindow.__new__(MainWindow)
    shared = Provider()
    window._asr = shared
    window._listen_asr = shared

    window._close_asr_providers()
    window._close_asr_providers(shared)

    assert shared.close_count == 1
    assert window._asr is None
    assert window._listen_asr is None


def test_closed_asr_registry_does_not_retain_replaced_provider():
    class Provider:
        def close(self) -> None:
            pass

    window = MainWindow.__new__(MainWindow)
    provider = Provider()
    provider_ref = weakref.ref(provider)
    window._asr = provider
    window._listen_asr = provider

    window._close_asr_providers()

    del provider
    gc.collect()
    assert provider_ref() is None
    assert not any(
        reference() is not None
        for reference in window._closed_asr_provider_refs
    )


def test_clear_cached_translator_closes_distinct_owned_clients():
    class Translator:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    class Controller:
        def __init__(self, translator) -> None:
            self._translator = translator

        @property
        def translator(self):
            return self._translator

        @translator.setter
        def translator(self, value) -> None:
            previous = self._translator
            self._translator = value
            if previous is not None:
                previous.close()

    realtime = Translator()
    manual = Translator()
    window = MainWindow.__new__(MainWindow)
    window._translator = realtime
    window._manual_translation_controller = Controller(manual)

    window._clear_cached_translator()

    assert realtime.close_count == 1
    assert manual.close_count == 1
    assert window._translator is None
    assert window._manual_translation_controller.translator is None


def test_clear_cached_translator_closes_shared_client_once():
    class Translator:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    class Controller:
        def __init__(self, translator) -> None:
            self._translator = translator

        @property
        def translator(self):
            return self._translator

        @translator.setter
        def translator(self, value) -> None:
            previous = self._translator
            self._translator = value
            if previous is not None:
                previous.close()

    translator = Translator()
    window = MainWindow.__new__(MainWindow)
    window._translator = translator
    window._manual_translation_controller = Controller(translator)

    window._clear_cached_translator()

    assert translator.close_count == 1


def test_provider_close_waits_for_deferred_worker_shutdown():
    release_workers = threading.Event()
    provider_closed = threading.Event()

    class Scheduler:
        threads: tuple = ()

        def __init__(self) -> None:
            self.stop_calls: list[float | None] = []

        def stop(self, timeout=5.0):
            self.stop_calls.append(timeout)
            if timeout is None:
                assert release_workers.wait(timeout=2)
                return True
            return False

    class Provider:
        def close(self) -> None:
            provider_closed.set()

    scheduler = Scheduler()
    provider = Provider()
    window = MainWindow.__new__(MainWindow)
    window._realtime_scheduler = scheduler
    window._partial_task_queues = {}
    window._partial_workers = {}
    window._final_task_queues = {}
    window._final_workers = {}
    window._asr = provider
    window._listen_asr = provider

    barrier = window._stop_workers()
    assert barrier is not None
    window._close_asr_providers(wait_for=barrier)
    assert not provider_closed.wait(timeout=0.1)

    release_workers.set()
    assert provider_closed.wait(timeout=1)
    assert barrier.is_set()
    assert scheduler.stop_calls[0] is not None
    assert scheduler.stop_calls[-1] is None


def test_scheduler_completion_is_applied_as_one_ordered_ui_transaction():
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 11
    calls = []
    window._dispatch_output_message = (
        lambda message, *, sinks: calls.append(("dispatch", message, sinks))
    )
    window._send_chatbox_payload = lambda *_args, **_kwargs: calls.append(("chatbox",))
    window._restore_runtime_status = lambda *keys: calls.append(("restore", keys))

    payload = _RealtimeAudioPayload(
        audio=b"audio",
        asr_provider=object(),
        asr_language=None,
        source_language="en",
        target_language="ja",
        second_target_language="",
        third_target_language="",
        listen_target_language="ja",
        listen_prefix="",
        send_to_chatbox=False,
        config_snapshot={},
    )
    task = RealtimeTask(
        source=MIC_SOURCE,
        session_id=11,
        sequence=0,
        provider_key="provider",
        payload=payload,
        submitted_at=time.monotonic(),
    )
    message = object()
    completion = RealtimeCompletion(
        task=task,
        recognized_text="hello",
        result=RealtimeTranslationResult(
            original_text="hello",
            translated_text="translated",
            output_message=message,
        ),
    )

    window._deliver_scheduler_completion_ui(completion)

    assert calls == [
        ("dispatch", message, ("ui", "overlay")),
        ("dispatch", message, ("tts",)),
        ("restore", ("status_speaking", "status_translating")),
    ]


def test_qwen_asr_timeout_uses_localized_asr_failure_instead_of_translation_error():
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 11
    window._copy = lambda key, **_kwargs: {
        "asr_temporary_failure": "ASR is recovering",
    }.get(key, key)
    bottom = []
    window._set_bottom = lambda message, color="default", **kwargs: bottom.append(
        (message, color, kwargs.get("key"))
    )
    window._pulse_avatar_error = lambda: None
    window._restore_runtime_status = lambda *_keys: None
    window._format_translation_error = lambda _error: (_ for _ in ()).throw(
        AssertionError("ASR failures must not use translation error formatting")
    )

    payload = _RealtimeAudioPayload(
        audio=b"audio",
        asr_provider=object(),
        asr_language=None,
        source_language="en",
        target_language="ja",
        second_target_language="",
        third_target_language="",
        listen_target_language="ja",
        listen_prefix="",
        send_to_chatbox=False,
        config_snapshot={},
    )
    task = RealtimeTask(
        source=MIC_SOURCE,
        session_id=11,
        sequence=3,
        provider_key="qwen3-asr",
        payload=payload,
        submitted_at=time.monotonic(),
    )
    completion = RealtimeCompletion(
        task=task,
        asr_error=ASRTemporaryUnavailableError("hard timeout"),
    )

    window._deliver_scheduler_completion_ui(completion)

    assert bottom == [
        ("ASR is recovering", "warning", "asr_temporary_failure")
    ]


def test_stale_asr_marker_without_audio_payload_reports_localized_failure():
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 11
    window._copy = lambda key, **_kwargs: {
        "asr_queue_expired": "ASR queue expired",
    }.get(key, key)
    bottom = []
    window._set_bottom = lambda message, color="default", **kwargs: bottom.append(
        (message, color, kwargs.get("key"))
    )
    window._pulse_avatar_error = lambda: None

    task = RealtimeTask(
        source=MIC_SOURCE,
        session_id=11,
        sequence=4,
        provider_key="qwen3-asr",
        payload=None,
        submitted_at=time.monotonic(),
    )
    completion = RealtimeCompletion(
        task=task,
        asr_error=TimeoutError("expired before provider admission"),
        stale_asr=True,
    )

    window._deliver_scheduler_completion_ui(completion)

    assert bottom == [
        ("ASR queue expired", "warning", "asr_queue_expired")
    ]


def test_ordered_delivery_holds_scheduler_slot_until_ui_acknowledges():
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 12
    window._ui_thread_id = -1
    window._realtime_delivery_cancel_event = threading.Event()
    queued = threading.Event()
    callbacks = []
    delivered = []

    def call_in_ui(callback, **_kwargs):
        callbacks.append(callback)
        queued.set()
        return True

    window._call_in_ui = call_in_ui
    window._deliver_scheduler_completion_ui = delivered.append
    completion = SimpleNamespace(task=SimpleNamespace(session_id=12))
    worker = threading.Thread(
        target=window._scheduler_delivery_stage,
        args=(completion,),
    )

    worker.start()
    assert queued.wait(timeout=1)
    assert worker.is_alive()
    assert delivered == []

    callbacks.pop()()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert delivered == [completion]


def test_ordered_delivery_wait_is_cancellation_safe_when_ui_is_stalled():
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 13
    window._ui_thread_id = -1
    cancel_event = threading.Event()
    window._realtime_delivery_cancel_event = cancel_event
    queued = threading.Event()
    callbacks = []

    def call_in_ui(callback, **_kwargs):
        callbacks.append(callback)
        queued.set()
        return True

    window._call_in_ui = call_in_ui
    delivered = []
    window._deliver_scheduler_completion_ui = delivered.append
    completion = SimpleNamespace(task=SimpleNamespace(session_id=13))
    worker = threading.Thread(
        target=window._scheduler_delivery_stage,
        args=(completion,),
    )

    worker.start()
    assert queued.wait(timeout=1)
    assert worker.is_alive()

    cancel_event.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(callbacks) == 1
    callbacks[0]()
    assert delivered == []


def test_stop_workers_cancels_pending_ui_delivery_before_joining_scheduler():
    from queue import Queue

    cancel_event = threading.Event()
    pending_cancelled = threading.Event()
    provider_cancelled = threading.Event()

    class Provider:
        provider_id = "qwen3-asr"

        def cancel_pending_requests(self):
            provider_cancelled.set()

    class Scheduler:
        threads: tuple = ()

        def stop(self, timeout=5.0):
            assert cancel_event.is_set()
            assert provider_cancelled.is_set()
            return True

    window = MainWindow.__new__(MainWindow)
    window._realtime_delivery_cancel_event = cancel_event
    window._ui_priority_callback_queue = Queue(maxsize=1)
    window._ui_priority_callback_queue.put_nowait(
        (
            0,
            lambda: pending_cancelled.set() if cancel_event.is_set() else None,
        )
    )
    window._realtime_scheduler = Scheduler()
    window._asr = Provider()
    window._listen_asr = window._asr
    window._partial_task_queues = {}
    window._partial_workers = {}
    window._final_task_queues = {}
    window._final_workers = {}

    assert window._stop_workers() is None
    assert cancel_event.is_set()
    assert pending_cancelled.is_set()
    assert provider_cancelled.is_set()
    assert window._ui_priority_callback_queue.empty()


def test_reverse_latency_log_separates_provider_queue_reorder_ui_and_tts(caplog):
    now = time.monotonic()
    diagnostics = {
        "speech_ended_at": now - 1.0,
        "segment_emitted_at": now - 0.6,
        "vad_finalization_s": 0.4,
        "asr_event_loop_queue_s": 0.01,
        "asr_provider_queue_s": 0.02,
        "asr_provider_s": 0.20,
        "asr_connection_reused": True,
        "translation_context_lookup_s": 0.003,
        "translation_prompt_build_s": 0.004,
        "translation_provider_s": 0.15,
    }
    task = RealtimeTask(
        source=DESKTOP_SOURCE,
        session_id=9,
        sequence=3,
        provider_key="qwen",
        payload=None,
        submitted_at=now - 0.55,
        diagnostics=diagnostics,
    )
    completion = RealtimeCompletion(
        task=task,
        asr_queue_wait_s=0.03,
        asr_duration_s=0.25,
        translation_queue_wait_s=0.04,
        translation_duration_s=0.17,
        recognition_reorder_wait_s=0.01,
        rewrite_reorder_wait_s=0.02,
        ordered_delivery_wait_s=0.03,
        completed_at=now - 0.05,
    )
    window = MainWindow.__new__(MainWindow)

    with caplog.at_level("INFO", logger="src.ui_qt.main_window"):
        window._log_reverse_latency(
            completion,
            ui_enqueued_at=now - 0.02,
            ui_started_at=now - 0.01,
            ui_finished_at=now,
        )

    message = caplog.records[-1].getMessage()
    assert "asr_provider_queue_ms=" in message
    assert "translation_context_ms=" in message
    assert "translation_provider_ms=" in message
    assert "reorder_ms=" in message
    assert "ui_queue_ms=" in message
    assert "tts_wait_ms=0.0 tts_queued=false" in message
    assert "asr_connection_reused=True" in message


def test_stale_startup_cleanup_cannot_touch_newer_session():
    window = MainWindow.__new__(MainWindow)
    current_event = threading.Event()
    stale_event = threading.Event()
    window._listen_session = 9
    window._startup_cancel_event = current_event
    window._running = True

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("stale startup callback touched the current session")

    window._reset_streaming_state = fail_if_called
    window._stop_listen = fail_if_called
    window._stop_microphone_capture = fail_if_called
    window._stop_workers = fail_if_called
    window._close_osc_sender = fail_if_called
    window._close_asr_providers = fail_if_called
    window._refresh_start_button = fail_if_called

    window._cleanup_startup_failure(
        show_error=False,
        session_id=8,
        cancel_event=stale_event,
    )

    assert window._running is True
    assert window._listen_session == 9
    assert window._startup_cancel_event is current_event


def test_shutdown_sets_exact_startup_event_and_invalidates_session():
    window = MainWindow.__new__(MainWindow)
    startup_event = threading.Event()
    shutdown_barrier = threading.Event()
    close_waits: list[threading.Event | None] = []
    window._destroying = False
    window._running = False
    window._listen_session = 4
    window._startup_cancel_event = startup_event
    window._sender = None
    window._osc_service = None
    window._stop_hotkeys = lambda: None
    window._close_independent_tool_windows = lambda: None
    window._flush_config_save = lambda: None
    window._reset_streaming_state = lambda: None
    window._stop_listen = lambda: None
    window._stop_microphone_capture = lambda: None
    window._stop_workers = lambda: shutdown_barrier
    window._close_asr_providers = lambda *args, wait_for=None: close_waits.append(wait_for)
    window._close_osc_sender = lambda: None
    window._reset_tts_manager = lambda: None

    window._shutdown()

    assert startup_event.is_set()
    assert window._destroying is True
    assert window._running is False
    assert window._listen_session == 5
    assert close_waits == [shutdown_barrier]


def test_pipeline_start_waits_for_previous_runtime_cleanup():
    class ThreadState:
        def __init__(self, alive: bool) -> None:
            self.alive = alive

        def is_alive(self) -> bool:
            return self.alive

    window = MainWindow.__new__(MainWindow)
    window._asr_close_lock = threading.RLock()
    finished_startup = ThreadState(False)
    active_cleanup = ThreadState(True)
    window._startup_thread = finished_startup
    window._asr_cleanup_threads = [active_cleanup]
    window._closing_asr_providers = []

    assert window._pipeline_cleanup_in_progress() is True
    assert window._startup_thread is None
    assert window._asr_cleanup_threads == [active_cleanup]

    active_cleanup.alive = False
    assert window._pipeline_cleanup_in_progress() is False
    assert window._asr_cleanup_threads == []


def test_do_start_defers_before_constructing_replacement_runtime():
    class Button:
        def __init__(self) -> None:
            self.enabled = True
            self.text = ""

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

        def setText(self, text: str) -> None:
            self.text = text

    window = MainWindow.__new__(MainWindow)
    button = Button()
    retries: list[int] = []
    statuses: list[tuple[str, str, str]] = []
    window._start_btn = button
    window._destroying = False
    window._running = False
    window._startup_thread = None
    window._pipeline_cleanup_in_progress = lambda: True
    window._schedule_pipeline_start_retry = retries.append
    window._t = lambda key, **_kwargs: key
    window._set_status = (
        lambda message, color, *, key=None: statuses.append((message, color, key))
    )

    window._do_start()

    assert button.enabled is False
    assert button.text == "starting"
    assert retries == [100]
    assert statuses == [("starting", "accent", "starting")]
    assert window._startup_thread is None


def test_stop_workers_clears_every_translation_context_session():
    from queue import Queue

    store = TranslationContextStore()
    for session_id in (1, 2):
        store.remember(
            session_id=session_id,
            text=f"source-{session_id}",
            translated=f"target-{session_id}",
            src_lang="en",
            tgt_lang="ja",
            context_source="mic",
        )

    window = MainWindow.__new__(MainWindow)
    window._translation_context_store = store
    window._listen_session = 3
    window._realtime_delivery_cancel_event = threading.Event()
    window._ui_priority_callback_queue = Queue(maxsize=1)
    window._tts_manager = None
    window._osc_sender = None
    window._realtime_scheduler = None
    window._partial_task_queues = {}
    window._partial_workers = {}
    window._final_task_queues = {}
    window._final_workers = {}

    assert window._stop_workers() is None
    for session_id in (1, 2):
        assert store.snapshot(
            session_id=session_id,
            src_lang="en",
            tgt_lang="ja",
            context_source="mic",
        ) == ()


def test_translation_workers_keep_translators_confined_to_worker_state(monkeypatch):
    translator_inputs: list[object | None] = []
    created_translators: list[object] = []

    class FakePipeline:
        def __init__(self, _config, _dispatcher, translator_factory=None) -> None:
            del translator_factory

        def create_plan(self, *_args, **_kwargs):
            return SimpleNamespace(needs_api_translation=False)

        def translate_plan(self, _plan, translator):
            translator_inputs.append(translator)
            created = object()
            created_translators.append(created)
            return SimpleNamespace(api_translation_used=False), created

    monkeypatch.setattr(main_window, "MicPipeline", FakePipeline)

    window = MainWindow.__new__(MainWindow)
    main_window_translator = object()
    window._translator = main_window_translator
    window._running = True
    window._destroying = False
    window._listen_session = 17
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: None
    window._record_translation_failure = lambda _error: None

    payload = _RealtimeAudioPayload(
        audio=b"audio",
        asr_provider=object(),
        asr_language=None,
        source_language="en",
        target_language="ja",
        second_target_language="en",
        third_target_language="",
        listen_target_language="ja",
        listen_prefix="",
        send_to_chatbox=False,
        config_snapshot={},
    )
    task = RealtimeTask(
        source=MIC_SOURCE,
        session_id=17,
        sequence=0,
        provider_key="provider",
        payload=payload,
        submitted_at=time.monotonic(),
    )
    first_state = _RealtimeTranslationWorkerState()
    second_state = _RealtimeTranslationWorkerState()

    window._scheduler_translation_stage(task, "first", first_state, threading.Event())
    window._scheduler_translation_stage(task, "second", second_state, threading.Event())

    assert translator_inputs == [None, None]
    assert first_state.translator is created_translators[0]
    assert second_state.translator is created_translators[1]
    assert first_state.translator is not second_state.translator
    assert window._translator is main_window_translator


def test_translation_worker_keeps_reverse_client_separate_from_microphone():
    class Translator:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    mic = Translator()
    listen = Translator()
    state = _RealtimeTranslationWorkerState(
        translator=mic,
        listen_translator=listen,
    )

    state.close_translator(DESKTOP_SOURCE)

    assert state.translator is mic
    assert state.listen_translator is None
    assert mic.closed == 0
    assert listen.closed == 1

    state.close()
    assert mic.closed == 1


def test_realtime_translator_caps_only_realtime_timeout(monkeypatch):
    captured = {}

    def fake_create_translator(config, *, context_store=None):
        captured["config"] = config
        captured["context_store"] = context_store
        return object()

    monkeypatch.setattr(main_window, "create_translator", fake_create_translator)
    store = TranslationContextStore()
    window = MainWindow.__new__(MainWindow)
    window._translation_context_store = store
    original = {
        "translation": {
            "backend": "openai_compatible",
            "realtime_timeout_s": 6.0,
            "openai_compatible": {
                "timeout_s": 15.0,
                "max_retries": 2,
            },
            "qianwen": {
                "timeout_s": 20.0,
                "max_retries": 1,
            },
        }
    }

    window._create_realtime_translator(original)

    runtime_backend = captured["config"]["translation"]["openai_compatible"]
    runtime_fallback = captured["config"]["translation"]["qianwen"]
    assert runtime_backend["timeout_s"] == 6.0
    assert runtime_backend["max_retries"] == 0
    assert runtime_fallback["timeout_s"] == 6.0
    assert runtime_fallback["max_retries"] == 0
    assert captured["context_store"] is store
    assert original["translation"]["openai_compatible"]["timeout_s"] == 15.0


def test_reverse_translator_uses_separate_short_timeout(monkeypatch):
    captured = {}

    def fake_create_translator(config, *, context_store=None):
        captured["config"] = config
        captured["context_store"] = context_store
        return object()

    monkeypatch.setattr(main_window, "create_translator", fake_create_translator)
    window = MainWindow.__new__(MainWindow)
    window._translation_context_store = TranslationContextStore()
    original = {
        "translation": {
            "backend": "openai_compatible",
            "realtime_timeout_s": 8.0,
            "openai_compatible": {
                "timeout_s": 15.0,
                "max_retries": 2,
            },
        },
        "vrc_listen": {"translation_timeout_s": 3.5},
    }

    window._create_realtime_translator(original, source=DESKTOP_SOURCE)

    runtime = captured["config"]["translation"]["openai_compatible"]
    assert runtime["timeout_s"] == 3.5
    assert runtime["max_retries"] == 0
    assert original["translation"]["openai_compatible"]["timeout_s"] == 15.0
    assert original["translation"]["openai_compatible"]["max_retries"] == 2


def test_translation_worker_rebuilds_only_when_provider_runtime_changes(monkeypatch):
    translator_inputs: list[object | None] = []
    created_translators: list[object] = []

    class Translator:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class FakePipeline:
        def __init__(self, _config, _dispatcher, translator_factory=None) -> None:
            del translator_factory

        def create_plan(self, *_args, **_kwargs):
            return SimpleNamespace(needs_api_translation=False)

        def translate_plan(self, _plan, translator, **_kwargs):
            translator_inputs.append(translator)
            if translator is not None:
                return SimpleNamespace(api_translation_used=False), translator
            created = Translator()
            created_translators.append(created)
            return SimpleNamespace(api_translation_used=False), created

    monkeypatch.setattr(main_window, "MicPipeline", FakePipeline)

    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 19
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: None
    window._record_translation_failure = lambda _error: None

    first_snapshot = _freeze_snapshot_value(
        {"translation": {"backend": "qianwen", "qianwen": {"model": "first"}}}
    )
    second_snapshot = _freeze_snapshot_value(
        {"translation": {"backend": "qianwen", "qianwen": {"model": "second"}}}
    )
    style_only_snapshot = _freeze_snapshot_value(
        {
            "translation": {
                "backend": "qianwen",
                "qianwen": {"model": "second"},
                "asr_rewrite_style": "frieren",
                "rewrite_typed_text": True,
                "output_format": "translated_only",
            }
        }
    )

    def task_for(sequence: int, snapshot):
        payload = _RealtimeAudioPayload(
            audio=b"audio",
            asr_provider=object(),
            asr_language=None,
            source_language="en",
            target_language="ja",
            second_target_language="en",
            third_target_language="",
            listen_target_language="ja",
            listen_prefix="",
            send_to_chatbox=False,
            config_snapshot=snapshot,
        )
        return RealtimeTask(
            source=MIC_SOURCE,
            session_id=19,
            sequence=sequence,
            provider_key="provider",
            payload=payload,
            submitted_at=time.monotonic(),
        )

    state = _RealtimeTranslationWorkerState()
    window._scheduler_translation_stage(
        task_for(0, first_snapshot),
        "first",
        state,
        threading.Event(),
    )
    first_translator = state.translator
    window._scheduler_translation_stage(
        task_for(1, second_snapshot),
        "second",
        state,
        threading.Event(),
    )
    second_translator = state.translator
    window._scheduler_translation_stage(
        task_for(2, style_only_snapshot),
        "third",
        state,
        threading.Event(),
    )

    assert translator_inputs == [None, None, second_translator]
    assert first_translator is created_translators[0]
    assert first_translator.closed == 1
    assert len(created_translators) == 2
    assert second_translator is created_translators[1]
    assert second_translator.closed == 0
    assert state.translator is second_translator
    assert state.config_snapshot is style_only_snapshot


def test_rewrite_worker_keeps_warm_client_for_style_only_snapshot_changes():
    created = []

    class Translator:
        def __init__(self) -> None:
            self.closed = 0

        def rewrite_asr(self, text, style, **_kwargs):
            return f"{style}:{text}"

        def close(self) -> None:
            self.closed += 1

    window = MainWindow.__new__(MainWindow)
    window._realtime_task_active = lambda _task: True
    window._rewrite_coordinator = None
    window._create_realtime_translator = (
        lambda _config: created.append(Translator()) or created[-1]
    )

    def task_for(sequence: int, snapshot):
        return RealtimeTask(
            source=MIC_SOURCE,
            session_id=1,
            sequence=sequence,
            provider_key="provider",
            payload=_RealtimeAudioPayload(
                audio=b"audio",
                asr_provider=object(),
                asr_language=None,
                source_language="en",
                target_language="ja",
                second_target_language="en",
                third_target_language="",
                listen_target_language="ja",
                listen_prefix="",
                send_to_chatbox=False,
                config_snapshot=snapshot,
            ),
            submitted_at=time.monotonic(),
        )

    first_snapshot = _freeze_snapshot_value(
        {
            "translation": {
                "backend": "openai",
                "openai": {"model": "gpt-test", "api_key": "secret"},
                "asr_rewrite_style": "catgirl",
            }
        }
    )
    style_only_snapshot = _freeze_snapshot_value(
        {
            "translation": {
                "backend": "openai",
                "openai": {"model": "gpt-test", "api_key": "secret"},
                "asr_rewrite_style": "frieren",
                "rewrite_typed_text": True,
            }
        }
    )
    provider_change_snapshot = _freeze_snapshot_value(
        {
            "translation": {
                "backend": "openai",
                "openai": {"model": "gpt-other", "api_key": "secret"},
                "asr_rewrite_style": "frieren",
            }
        }
    )

    state = _RealtimeRewriteWorkerState()
    assert window._scheduler_rewrite_stage(
        task_for(0, first_snapshot),
        "hello",
        state,
        threading.Event(),
    ) == "catgirl:hello"
    first = state.translator
    assert window._scheduler_rewrite_stage(
        task_for(1, style_only_snapshot),
        "again",
        state,
        threading.Event(),
    ) == "frieren:again"

    assert len(created) == 1
    assert state.translator is first
    assert first.closed == 0

    window._scheduler_rewrite_stage(
        task_for(2, provider_change_snapshot),
        "changed",
        state,
        threading.Event(),
    )
    assert len(created) == 2
    assert first.closed == 1


def test_invalidating_reverse_source_cancels_only_reverse_generation_and_context():
    class Scheduler:
        def __init__(self) -> None:
            self.calls = []

        def cancel_source(self, source, *, session_id=None):
            self.calls.append((source, session_id))
            return 2

    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 23
    window._realtime_source_generations = {MIC_SOURCE: 4, DESKTOP_SOURCE: 8}
    window._realtime_scheduler = Scheduler()
    window._translation_context_store = TranslationContextStore()
    window._translation_context_store.stage_source(
        session_id=23,
        sequence=0,
        text="reverse stale",
        src_lang="en",
        tgt_lang="ja",
        context_source="listen",
    )
    window._translation_context_store.stage_source(
        session_id=23,
        sequence=0,
        text="mic stays",
        src_lang="en",
        tgt_lang="ja",
        context_source=MIC_SOURCE,
    )

    cancelled = window._invalidate_realtime_source(DESKTOP_SOURCE)

    assert cancelled == 2
    assert window._realtime_source_generations == {MIC_SOURCE: 4, DESKTOP_SOURCE: 9}
    assert window._realtime_scheduler.calls == [(DESKTOP_SOURCE, 23)]
    assert window._translation_context_store.snapshot(
        session_id=23,
        src_lang="en",
        tgt_lang="ja",
        context_source="listen",
        current_text="then?",
        before_sequence=1,
    ) == ()
    assert window._translation_context_store.snapshot(
        session_id=23,
        src_lang="en",
        tgt_lang="ja",
        context_source=MIC_SOURCE,
        current_text="then?",
        before_sequence=1,
    ) == (("mic stays", ""),)


def test_scheduler_reverse_translation_uses_isolated_listen_context(monkeypatch):
    captured = {}

    class FakeListenPipeline:
        def __init__(self, _config, _dispatcher, translator_factory=None) -> None:
            del translator_factory

        def create_plan(self, *_args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(needs_api_translation=False)

        @staticmethod
        def translate_plan(_plan, translator, **_kwargs):
            return SimpleNamespace(api_translation_used=False), translator

    monkeypatch.setattr(main_window, "ListenPipeline", FakeListenPipeline)

    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 29
    window._realtime_source_generations = {MIC_SOURCE: 0, DESKTOP_SOURCE: 0}
    window._translation_cooldown_active = lambda _source: False
    window._record_translation_success = lambda: None
    window._record_translation_failure = lambda _error: None
    payload = _RealtimeAudioPayload(
        audio=b"audio",
        asr_provider=object(),
        asr_language="en",
        source_language="en",
        target_language="ja",
        second_target_language="",
        third_target_language="",
        listen_target_language="zh",
        listen_prefix="[Listen]",
        send_to_chatbox=False,
        config_snapshot={},
    )
    task = RealtimeTask(
        source=DESKTOP_SOURCE,
        session_id=29,
        sequence=0,
        provider_key="desktop",
        payload=payload,
        submitted_at=time.monotonic(),
    )

    window._scheduler_translation_stage(
        task,
        "hello",
        _RealtimeTranslationWorkerState(),
        threading.Event(),
    )

    assert captured["context_source"] == "listen"


def test_translation_failure_discards_worker_client_for_clean_retry():
    class Translator:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class FailingPipeline:
        @staticmethod
        def create_plan(*_args, **_kwargs):
            return SimpleNamespace(needs_api_translation=True)

        @staticmethod
        def translate_plan(*_args, **_kwargs):
            raise RuntimeError("transport failed")

    snapshot = _freeze_snapshot_value({"translation": {}})
    payload = _RealtimeAudioPayload(
        audio=b"audio",
        asr_provider=object(),
        asr_language="en",
        source_language="en",
        target_language="ja",
        second_target_language="",
        third_target_language="",
        listen_target_language="zh",
        listen_prefix="",
        send_to_chatbox=False,
        config_snapshot=snapshot,
    )
    task = RealtimeTask(
        source=MIC_SOURCE,
        session_id=31,
        sequence=0,
        provider_key="mic",
        payload=payload,
        submitted_at=time.monotonic(),
    )
    translator = Translator()
    state = _RealtimeTranslationWorkerState(
        translator=translator,
        config_snapshot=snapshot,
        config={},
        dispatcher=object(),
        mic_pipeline=FailingPipeline(),
        listen_pipeline=object(),
    )
    window = MainWindow.__new__(MainWindow)
    window._running = True
    window._destroying = False
    window._listen_session = 31
    window._realtime_source_generations = {MIC_SOURCE: 0, DESKTOP_SOURCE: 0}
    window._translation_cooldown_active = lambda _source: False
    window._format_translation_error = lambda _error: SimpleNamespace(category="network")
    window._record_translation_failure = lambda _friendly: 0.0

    with pytest.raises(RuntimeError, match="transport failed"):
        window._scheduler_translation_stage(
            task,
            "hello",
            state,
            threading.Event(),
        )

    assert translator.closed == 1
    assert state.translator is None
