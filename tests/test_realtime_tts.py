import threading

import pytest

from src.core.output_dispatcher import OutputMessage
from src.ui_qt.main_window import MIC_SOURCE, MainWindow


def _window_for_auto_read(
    *,
    tts_enabled: bool = True,
    auto_read: bool = True,
    output_format: str = "translated_only",
):
    window = MainWindow.__new__(MainWindow)
    window._tts_enabled = tts_enabled
    window._config = {
        "translation": {"output_format": output_format},
        "tts": {"auto_read": auto_read},
    }
    queued: list[str] = []
    window._queue_tts_playback = lambda text: queued.append(text) or True
    return window, queued


def test_realtime_mic_auto_read_uses_translated_text():
    window, queued = _window_for_auto_read()

    accepted = window._auto_read_mic_translation(
        original_text="hello",
        translated_text="こんにちは",
    )

    assert accepted is True
    assert queued == ["こんにちは"]


def test_realtime_mic_auto_read_uses_original_text_for_original_only_format():
    window, queued = _window_for_auto_read(output_format="original_only")

    accepted = window._auto_read_mic_translation(
        original_text="hello",
        translated_text="こんにちは",
    )

    assert accepted is True
    assert queued == ["hello"]


def test_original_only_read_translation_auto_reads_translated_text():
    window, queued = _window_for_auto_read(
        output_format="original_only_read_translation"
    )

    accepted = window._auto_read_mic_translation(
        original_text="hello",
        translated_text="こんにちは",
    )

    assert accepted is True
    assert queued == ["こんにちは"]


def test_manual_translation_finish_auto_reads_translated_text():
    window, queued = _window_for_auto_read()
    window._t = lambda key, **kwargs: key
    window._set_status = lambda *args, **kwargs: None
    window._src_text = "hello"
    window._last_tgt_text = "こんにちは"
    window._manual_send_after_translate = False
    window._manual_done_callback = None
    window._running = True

    window._finish_manual_translation(success=True)

    assert queued == ["こんにちは"]


def test_manual_translation_finish_reads_original_for_original_only_format():
    window, queued = _window_for_auto_read(output_format="original_only")
    window._t = lambda key, **kwargs: key
    window._set_status = lambda *args, **kwargs: None
    window._src_text = "hello"
    window._last_tgt_text = "hello"
    window._manual_send_after_translate = False
    window._manual_done_callback = None
    window._running = True

    window._finish_manual_translation(success=True)

    assert queued == ["hello"]


def test_manual_send_after_translate_waits_for_tts_before_original_osc():
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "translation": {
            "output_format": "original_only_read_translation",
            "original_only_read_translation_wait_for_tts": True,
        }
    }
    window._manual_send_after_translate = True
    window._manual_done_callback = None
    window._running = True
    window._src_text = "hello"
    window._last_tgt_text = "こんにちは"
    window._t = lambda key, **_kwargs: key
    window._set_status = lambda *_args, **_kwargs: None
    scheduled = []
    sent = []
    captured = {}
    window._send_to_vrc = lambda: pytest.fail("OSC must not send before TTS")
    window._call_in_ui = (
        lambda callback, delay_ms=0, **_kwargs: scheduled.append(
            (delay_ms, callback)
        )
        or True
    )
    window._send_chatbox_payload = lambda text, **kwargs: sent.append(
        (text, kwargs)
    ) or True

    def dispatch(message, *, sinks):
        assert sinks == ("tts",)
        captured["tts_callback"] = message.metadata[
            "tts_completion_callback"
        ]
        return {"tts": True}

    window._dispatch_output_message = dispatch
    message = OutputMessage(
        source="manual",
        original_text="hello",
        translated_text="こんにちは",
    )

    window._finish_manual_translation(
        success=True,
        output_message=message,
    )

    assert sent == []
    captured["tts_callback"](True, "")
    assert scheduled[0][0] == 200
    scheduled[0][1]()
    assert sent[0][0] == "hello"


def test_realtime_mic_auto_read_respects_tts_flags():
    window, queued = _window_for_auto_read(auto_read=False)

    assert window._auto_read_mic_translation(
        original_text="hello",
        translated_text="こんにちは",
    ) is False
    assert queued == []

    window, queued = _window_for_auto_read(tts_enabled=False)

    assert window._auto_read_mic_translation(
        original_text="hello",
        translated_text="こんにちは",
    ) is False
    assert queued == []


def test_qwen_tts_config_uses_translation_target_language_hint():
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {"output_format": "translated_only"},
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {"voice": "Cherry", "rate": 1.0, "volume": 0.8},
        },
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert engine_cfg["language_type"] == "Japanese"


def test_qwen_tts_config_does_not_derive_instructions_from_legacy_persona():
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {
            "output_format": "translated_only",
            "social": {
                "mode": "roleplay",
                "politeness": "casual",
                "tone": "playful",
                "persona_name": "Energetic Friend",
                "persona_prompt": "Sounds upbeat and reacts with friendly emotion.",
            },
        },
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {
                "model": "qwen3-tts-instruct-flash",
                "voice": "Cherry",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert "instructions" not in engine_cfg


def test_qwen_tts_config_preserves_explicit_instructions():
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {
            "output_format": "translated_only",
            "social": {
                "mode": "roleplay",
                "persona_name": "Energetic Friend",
            },
        },
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {
                "model": "qwen3-tts-instruct-flash",
                "voice": "Cherry",
                "instructions": "Use a custom calm announcer style.",
            },
        },
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert engine_cfg["instructions"] == "Use a custom calm announcer style."


def test_qwen_tts_config_does_not_force_hint_for_original_only_output():
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {"output_format": "original_only"},
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {"voice": "Cherry", "rate": 1.0, "volume": 0.8},
        },
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert "language_type" not in engine_cfg


def test_qwen_tts_uses_translation_language_for_original_only_read_translation():
    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {"output_format": "original_only_read_translation"},
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {"voice": "Cherry", "rate": 1.0, "volume": 0.8},
        },
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert engine_cfg["language_type"] == "Japanese"


class _FakeTtsManager:
    def __init__(self):
        self.clear_count = 0
        self.requests: list[str] = []

    def clear_queue(self):
        self.clear_count += 1

    def speak(self, text, voice, rate, volume, callback=None):
        self.requests.append(text)
        if callback is not None:
            callback(True, "")
        return True


class _FailingQwenTtsManager(_FakeTtsManager):
    def speak(self, text, voice, rate, volume, callback=None):
        self.requests.append(text)
        if callback is not None:
            callback(
                False,
                "Qwen TTS API request failed: Invalid API-key provided.",
            )
        return True


class _NetworkFailingQwenTtsManager(_FakeTtsManager):
    def speak(self, text, voice, rate, volume, callback=None):
        self.requests.append(text)
        if callback is not None:
            callback(
                False,
                "Qwen TTS synthesis failed: Qwen TTS network connection was interrupted.",
            )
        return True


class _ContextTtsManager(_FakeTtsManager):
    def __init__(self):
        super().__init__()
        self.contexts: list[dict[str, object]] = []

    def speak(
        self,
        text,
        voice,
        rate,
        volume,
        callback=None,
        *,
        request_context=None,
    ):
        self.contexts.append(dict(request_context or {}))
        return super().speak(text, voice, rate, volume, callback=callback)


def _window_for_tts_strategy(strategy: str):
    window = MainWindow.__new__(MainWindow)
    manager = _FakeTtsManager()
    window._tts_enabled = True
    window._config = {
        "simul_mode": {"tts_strategy": strategy},
        "tts": {
            "engine": "edge",
            "edge": {"voice": "test-voice", "rate": 1.0, "volume": 0.8},
        },
    }
    window._ensure_tts_manager = lambda: manager
    return window, manager


def test_tts_queue_strategy_preserves_pending_speech():
    window, manager = _window_for_tts_strategy("queue")

    assert window._queue_tts_playback("hello") is True

    assert manager.clear_count == 0
    assert manager.requests == ["hello"]


def test_tts_latest_strategy_discards_pending_speech():
    window, manager = _window_for_tts_strategy("latest")

    assert window._queue_tts_playback("hello") is True

    assert manager.clear_count == 1
    assert manager.requests == ["hello"]


def test_tts_request_context_is_forwarded_for_terminal_latency_correlation():
    window, _manager = _window_for_tts_strategy("queue")
    manager = _ContextTtsManager()
    window._ensure_tts_manager = lambda: manager
    context = {
        "source": "mic",
        "session_id": 4,
        "sequence": 9,
        "upstream_started_at": 10.0,
        "ui_delivered_at": 10.5,
        "ui_delivery_s": 0.02,
    }

    assert window._queue_tts_playback("hello", request_context=context) is True

    assert manager.contexts == [context]


def test_tts_completion_callback_runs_only_after_accepted_playback_finishes():
    window, _manager = _window_for_tts_strategy("queue")
    callbacks = []

    class DeferredManager(_FakeTtsManager):
        def __init__(self):
            super().__init__()
            self.terminal_callback = None

        def speak(self, text, voice, rate, volume, callback=None):
            self.requests.append(text)
            self.terminal_callback = callback
            return True

    manager = DeferredManager()
    window._ensure_tts_manager = lambda: manager

    assert window._queue_tts_playback(
        "hello",
        completion_callback=lambda success, message: callbacks.append(
            (success, message)
        ),
    ) is True
    assert callbacks == []

    manager.terminal_callback(True, "")

    assert callbacks == [(True, "")]


def test_delayed_original_chatbox_callback_waits_200ms_after_successful_tts():
    window = MainWindow.__new__(MainWindow)
    scheduled = []
    sent = []
    window._call_in_ui = (
        lambda callback, delay_ms=0, **_kwargs: scheduled.append(
            (delay_ms, callback)
        )
        or True
    )
    window._send_chatbox_payload = lambda text, **kwargs: sent.append(
        (text, kwargs)
    ) or True

    callback = window._delayed_original_chatbox_after_tts(
        "原文",
        session_id=7,
        request_context={"source": "mic"},
    )
    callback(False, "tts_error:playback")
    assert scheduled == []

    callback(True, "")
    assert len(scheduled) == 1
    assert scheduled[0][0] == 200
    scheduled[0][1]()

    assert sent == [
        (
            "原文",
            {
                "session_id": 7,
                "request_context": {"source": "mic"},
                "completion_callback": None,
            },
        )
    ]


@pytest.mark.parametrize(
    ("token", "expected_key"),
    [
        ("tts_error:timeout", "qwen_tts_timeout_error"),
        ("tts_error:rate_limit", "qwen_tts_rate_limit_error"),
        ("tts_error:unsupported_model", "qwen_tts_model_error"),
        ("tts_error:invalid_endpoint", "qwen_tts_endpoint_error"),
        ("tts_error:queue_full", "qwen_tts_busy_error"),
        ("tts_error:playback", "qwen_tts_playback_error"),
        ("tts_error:provider", "qwen_tts_provider_error"),
        ("tts_error:invalid_input", "qwen_tts_input_error"),
        ("tts_error:configuration", "qwen_tts_configuration_error"),
        ("tts_error:safety", "qwen_tts_safety_error"),
        ("tts_error:unavailable", "qwen_tts_unavailable_error"),
    ],
)
def test_qwen_tts_structured_failures_use_localized_runtime_copy(
    token,
    expected_key,
):
    window = MainWindow.__new__(MainWindow)
    reports = []
    window._ui_lang = "en"
    window._config = {"tts": {"engine": "qwen_tts"}}
    window._call_in_ui = lambda callback, **_kwargs: callback() or True
    window._set_bottom = lambda text, color="default", key=None: reports.append(
        (text, color, key)
    )

    window._handle_tts_failure(token)

    assert reports == [
        (
            window._copy(expected_key),
            "warning",
            expected_key,
        )
    ]


def test_qwen_tts_auth_failure_is_reported_to_runtime_ui():
    window = MainWindow.__new__(MainWindow)
    manager = _FailingQwenTtsManager()
    reports: list[tuple[str, str, str | None]] = []
    window._ui_lang = "en"
    window._tts_enabled = True
    window._config = {
        "translation": {"output_format": "translated_only", "target_language": "en"},
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {
                "voice": "Cherry",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
    }
    window._ensure_tts_manager = lambda: manager
    window._call_in_ui = lambda callback, **_kwargs: callback() or True
    window._set_bottom = (
        lambda text, color="default", key=None: reports.append((text, color, key))
    )

    assert window._queue_tts_playback("hello") is True

    assert manager.requests == ["hello"]
    assert reports == [
        (
            "Qwen TTS rejected the credential; check the API key and service region",
            "warning",
            "qwen_tts_auth_error",
        )
    ]


def test_qwen_tts_network_failure_is_reported_to_runtime_ui():
    window = MainWindow.__new__(MainWindow)
    manager = _NetworkFailingQwenTtsManager()
    reports: list[tuple[str, str, str | None]] = []
    window._ui_lang = "en"
    window._tts_enabled = True
    window._config = {
        "translation": {"output_format": "translated_only", "target_language": "en"},
        "tts": {
            "engine": "qwen_tts",
            "qwen_tts": {
                "voice": "Cherry",
                "rate": 1.0,
                "volume": 0.8,
            },
        },
    }
    window._ensure_tts_manager = lambda: manager
    window._call_in_ui = lambda callback, **_kwargs: callback() or True
    window._set_bottom = (
        lambda text, color="default", key=None: reports.append((text, color, key))
    )

    assert window._queue_tts_playback("hello") is True

    assert manager.requests == ["hello"]
    assert reports == [
        (
            "Qwen TTS network connection was interrupted; check the network or proxy settings and try again",
            "warning",
            "qwen_tts_network_error",
        )
    ]


def test_tts_to_vrchat_suppresses_desktop_listen_echo():
    window, manager = _window_for_tts_strategy("queue")
    window._desktop_capture_enabled = True
    window._desktop_devices = {"Speakers (MIXLINE)": 0}
    window._desktop_capture_config = lambda: {"loopback_device": "Speakers (MIXLINE)"}
    window._config["tts"]["enabled"] = True
    window._config["tts"]["output_to_vrchat"] = True
    window._config["tts"]["output_device_name"] = "Speakers (MIXLINE)"

    assert window._queue_tts_playback("hello") is True

    assert manager.requests == ["hello"]
    assert window._listen_tts_echo_suppress_active() is True
    assert window._listen_suppress_reason("hello") == "own_tts_playback"


def test_tts_to_vrchat_does_not_suppress_separate_real_desktop_output():
    window, manager = _window_for_tts_strategy("queue")
    window._desktop_capture_enabled = True
    window._desktop_devices = {
        "Speakers (MIXLINE)": 0,
        "Headphones (Realtek(R) Audio)": 1,
    }
    window._desktop_capture_config = lambda: {
        "loopback_device": "Headphones (Realtek(R) Audio)"
    }
    window._config["tts"]["enabled"] = True
    window._config["tts"]["output_to_vrchat"] = True
    window._config["tts"]["output_device_name"] = "Speakers (MIXLINE)"

    assert window._queue_tts_playback("hello") is True

    assert manager.requests == ["hello"]
    assert window._listen_tts_echo_suppress_active() is False


def test_tts_without_vrchat_output_does_not_suppress_desktop_listen():
    window, manager = _window_for_tts_strategy("queue")
    window._desktop_capture_enabled = True
    window._config["tts"]["enabled"] = True
    window._config["tts"]["output_to_vrchat"] = False

    assert window._queue_tts_playback("hello") is True

    assert manager.requests == ["hello"]
    assert window._listen_tts_echo_suppress_active() is False


def test_reset_tts_manager_stops_manager_worker():
    class FakeManager:
        def __init__(self):
            self.stopped = False
            self.playback_stopped = False

        def stop(self):
            self.stopped = True

        def stop_playback(self):
            self.playback_stopped = True

    window = MainWindow.__new__(MainWindow)
    manager = FakeManager()
    window._tts_manager = manager
    window._tts_manager_signature = ("qwen_vc",)

    window._reset_tts_manager()

    assert manager.stopped is True
    assert manager.playback_stopped is False
    assert window._tts_manager is None
    assert window._tts_manager_signature is None


class _FakeAsr:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, _audio, *, language=None, is_final=False):
        return self.text


class _FakeMerger:
    @staticmethod
    def ingest_final(text: str) -> str:
        return text


class _FakeTranslator:
    @staticmethod
    def translate(text: str, src_lang: str, tgt_lang: str, *, context_source=None) -> str:
        assert text == "hello"
        assert src_lang == "en"
        assert tgt_lang == "ja"
        assert context_source == MIC_SOURCE
        return "こんにちは"


class _FakeSender:
    def __init__(self):
        self.messages: list[str] = []

    def send_chatbox(self, message: str):
        self.messages.append(message)
        return message


def test_final_mic_segment_queues_tts_after_translation():
    window = MainWindow.__new__(MainWindow)
    sender = _FakeSender()
    auto_read_calls: list[dict[str, str]] = []

    window._running = True
    window._runtime_generation = 1
    window._merge_lock = threading.Lock()
    window._translator = _FakeTranslator()
    window._current_tgt_lang = "ja"
    window._config = {"translation": {"output_format": "translated_only", "send_to_chatbox": True}}
    window._output_dispatcher = None
    window._desktop_capture_enabled = False
    window._own_msgs = set()
    window._get_output_format = lambda: "translated_only"
    # _get_output_format_2 always returns "disabled" (merged into output_format)
    window._final_segment_may_need_translation_api = lambda *args: True
    window._translation_cooldown_active = lambda _source: False
    window._set_translating_state = lambda _active: None
    window._asr_for_source = lambda _source: _FakeAsr("hello")
    window._source_merger = lambda _source: _FakeMerger()
    window._mic_audio_is_muted = lambda _source: False
    window._translation_context_source = lambda source: source
    window._call_in_ui = lambda callback: callback()
    window._set_source_text = lambda *args, **kwargs: None
    window._show_tgt = lambda _text: None
    window._record_translation_success = lambda: None
    window._remember_recent_mic_texts = lambda *args: None
    window._mic_send_to_chatbox_enabled = lambda: True
    window._ensure_sender = lambda: sender
    window._auto_read_mic_translation = lambda **kwargs: auto_read_calls.append(kwargs) or True
    window._refresh_runtime_status = lambda: None

    window._process_final_audio_segment(
        "audio",
        "en",
        "en",
        window._runtime_generation,
        MIC_SOURCE,
    )

    assert sender.messages == ["こんにちは"]
    assert auto_read_calls == [
        {
            "original_text": "hello",
            "translated_text": "こんにちは",
        }
    ]


def test_final_mic_segment_reports_translating_status():
    window = MainWindow.__new__(MainWindow)
    status_events: list[tuple[str | None, str, str]] = []

    window._running = True
    window._runtime_generation = 1
    window._translator = _FakeTranslator()
    window._current_tgt_lang = "ja"
    window._config = {"translation": {"output_format": "translated_only", "send_to_chatbox": False}}
    window._output_dispatcher = None
    window._mic_muted = False
    window._status_label = object()
    window._status_key = "status_running"
    window._translating = False
    window._t = lambda key, **_kwargs: {
        "status_running": "监听中...",
        "status_recognizing": "识别中...",
        "status_translating": "翻译中...",
    }.get(key, key)

    def record_status(text, color="default", key=None):
        window._status_key = key
        status_events.append((key, text, color))

    window._set_status = record_status
    window._transcribe_for_source = lambda *args, **kwargs: "hello"
    window._get_output_format = lambda: "translated_only"
    window._format_chatbox_output = lambda _original, translated, *_args: translated
    window._translation_cooldown_active = lambda _source: False
    window._call_in_ui = lambda callback: callback()
    window._set_source_text = lambda *args, **kwargs: None
    window._show_tgt = lambda *args, **kwargs: None
    window._mic_send_to_chatbox_enabled = lambda: False
    window._auto_read_mic_translation = lambda **kwargs: False
    window._record_translation_success = lambda: None

    window._process_final_audio_segment(
        "audio",
        "en",
        "en",
        window._runtime_generation,
        MIC_SOURCE,
    )

    # Recognition and translation are told apart now: a recognizer stall
    # used to read as "translating forever".
    assert status_events[0] == ("status_recognizing", "识别中...", "accent")
    assert status_events[-1] == ("status_running", "监听中...", "accent")


def test_final_mic_segment_original_only_does_not_create_translator(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    sender = _FakeSender()
    shown_source: list[str] = []
    shown_target: list[str] = []
    auto_read_calls: list[dict[str, str]] = []

    monkeypatch.setattr(
        "src.ui_qt.main_window.create_translator",
        lambda _config: (_ for _ in ()).throw(AssertionError("translator should not be created")),
    )

    window._running = True
    window._runtime_generation = 1
    window._translator = None
    window._config = {
        "translation": {
            "output_format": "original_only",
            "send_to_chatbox": True,
        }
    }
    window._current_tgt_lang = "ja"
    window._mic_muted = False
    window._transcribe_for_source = lambda *args, **kwargs: "hello"
    window._translation_cooldown_active = lambda _source: False
    window._call_in_ui = lambda callback: callback()
    window._set_source_text = lambda text: shown_source.append(text)
    window._show_tgt = lambda text: shown_target.append(text)
    window._ensure_sender = lambda: sender
    window._auto_read_mic_translation = lambda **kwargs: auto_read_calls.append(kwargs) or True

    window._process_final_audio_segment(
        "audio",
        "en",
        "en",
        window._runtime_generation,
        MIC_SOURCE,
    )

    assert sender.messages == ["hello"]
    assert shown_source == ["hello"]
    assert shown_target == ["hello"]
    assert auto_read_calls == [{"original_text": "hello", "translated_text": "hello"}]
