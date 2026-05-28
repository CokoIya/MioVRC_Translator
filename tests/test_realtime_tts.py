import threading

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
    window._listen_session = 1
    window._merge_lock = threading.Lock()
    window._translator = _FakeTranslator()
    window._current_tgt_lang = "ja"
    window._desktop_capture_enabled = False
    window._own_msgs = set()
    window._get_output_format = lambda: "translated_only"
    window._get_output_format_2 = lambda: "disabled"
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
        window._listen_session,
        MIC_SOURCE,
    )

    assert sender.messages == ["こんにちは"]
    assert auto_read_calls == [
        {
            "original_text": "hello",
            "translated_text": "こんにちは",
        }
    ]
