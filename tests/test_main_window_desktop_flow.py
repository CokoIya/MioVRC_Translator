import threading
import time

from src.ui_qt.main_window import DESKTOP_SOURCE, MainWindow


class _Merger:
    @staticmethod
    def ingest_final(text: str) -> str:
        return text


class _Translator:
    @staticmethod
    def translate(text: str, src_lang: str, tgt_lang: str, *, context_source=None) -> str:
        assert text == "hello"
        assert src_lang == "en"
        assert tgt_lang == "ja"
        assert context_source == "listen"
        return "translated"


def _base_desktop_window(*, send_to_chatbox: bool):
    window = MainWindow.__new__(MainWindow)
    shown_source: list[str] = []
    shown_target: list[str] = []
    shown_overlay: list[tuple[str, str | None]] = []
    sent_chatbox: list[tuple[str, int]] = []
    auto_read_calls: list[dict[str, str]] = []

    window._running = True
    window._listen_session = 5
    window._config = {"translation": {}}
    window._output_dispatcher = None
    window._merge_lock = threading.Lock()
    window._translator = _Translator()
    window._get_output_format = lambda: "translated_with_original"
    window._final_segment_may_need_translation_api = lambda *args: True
    window._translation_cooldown_active = lambda _source: False
    window._set_translating_state = lambda _active: None
    window._transcribe_for_source = lambda *args, **kwargs: "hello"
    window._source_merger = lambda _source: _Merger()
    window._mic_audio_is_muted = lambda _source: False
    window._listen_suppress_reason = lambda _text: None
    window._mic_in_speech = False
    window._last_mic_activity_at = 0.0
    window._last_mic_result_at = 0.0
    window._translation_context_source = lambda _source: "listen"
    window._call_in_ui = lambda callback: callback()
    window._set_source_text = lambda text, *args, **kwargs: shown_source.append(text)
    window._show_tgt = lambda text: shown_target.append(text)
    window._recent_duplicate_listen_reason = lambda _text: None
    window._remember_recent_listen_text = lambda _text: None
    window._listen_translation_source_language = lambda selected: selected or "auto"
    window._listen_target_language = lambda: "ja"
    window._dispatch_output_message = (
        lambda message, *, sinks=None: shown_overlay.append((message.display_text, message.chatbox_text)) or {"overlay": True}
    )
    window._listen_send_to_chatbox_enabled = lambda: send_to_chatbox
    window._send_listen_chatbox = (
        lambda text, *, session_id=None: sent_chatbox.append((text, session_id))
    )
    window._auto_read_mic_translation = lambda **kwargs: auto_read_calls.append(kwargs) or True
    window._record_translation_success = lambda: None
    window._refresh_runtime_status = lambda: None

    return window, shown_source, shown_target, shown_overlay, sent_chatbox, auto_read_calls


def test_desktop_final_result_updates_ui_overlay_and_chatbox():
    window, shown_source, shown_target, shown_overlay, sent_chatbox, auto_read_calls = _base_desktop_window(
        send_to_chatbox=True
    )

    window._process_final_audio_segment("audio", "en", "en", 5, DESKTOP_SOURCE)

    assert shown_source == []
    assert shown_target == []
    assert shown_overlay and shown_overlay[0][0] == "hello（translated）"
    assert sent_chatbox == [(shown_overlay[0][1], 5)]
    assert auto_read_calls == []


def test_desktop_final_result_stays_local_when_chatbox_disabled():
    window, shown_source, shown_target, shown_overlay, sent_chatbox, auto_read_calls = _base_desktop_window(
        send_to_chatbox=False
    )

    window._process_final_audio_segment("audio", "en", "en", 5, DESKTOP_SOURCE)

    assert shown_source == []
    assert shown_target == []
    assert shown_overlay and shown_overlay[0][0] == "hello（translated）"
    assert sent_chatbox == []
    assert auto_read_calls == []


def test_desktop_final_result_does_not_cover_recent_mic_panel():
    window, shown_source, shown_target, shown_overlay, sent_chatbox, auto_read_calls = _base_desktop_window(
        send_to_chatbox=True
    )
    window._last_mic_result_at = time.monotonic()

    window._process_final_audio_segment("audio", "en", "en", 5, DESKTOP_SOURCE)

    assert shown_source == []
    assert shown_target == []
    assert shown_overlay and shown_overlay[0][0] == "hello（translated）"
    assert sent_chatbox == [(shown_overlay[0][1], 5)]
    assert auto_read_calls == []


def test_desktop_final_result_is_ignored_during_own_tts_playback():
    window, shown_source, shown_target, shown_overlay, sent_chatbox, auto_read_calls = _base_desktop_window(
        send_to_chatbox=True
    )
    window._listen_tts_echo_suppress_until = time.monotonic() + 5.0

    window._process_final_audio_segment("audio", "en", "en", 5, DESKTOP_SOURCE)

    assert shown_source == []
    assert shown_target == []
    assert shown_overlay == []
    assert sent_chatbox == []
    assert auto_read_calls == []
