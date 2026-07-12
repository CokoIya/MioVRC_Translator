from __future__ import annotations

import json
import time
import webbrowser
from urllib.request import urlopen

from src.asr.webspeech_asr import _BridgeState, _page, WebSpeechASRProvider


def test_webspeech_bridge_page_applies_runtime_options():
    page = _page(
        "zh-CN",
        continuous=False,
        interim_results=False,
        max_alternatives=3,
        restart_on_end=False,
        silence_timeout_ms=1200,
    ).decode("utf-8")

    assert '"continuous":false' in page
    assert '"interimResults":false' in page
    assert '"maxAlternatives":3' in page
    assert '"restartOnEnd":false' in page
    assert '"silenceTimeoutMs":1200' in page
    assert "rec.continuous = options.continuous" in page
    assert "postResult(lastPartial, true)" in page
    assert "fetch('/capture-state'" in page
    assert "rec.abort()" in page

    paused_page = _page("zh-CN", capture_enabled=False).decode("utf-8")
    assert '"captureEnabled":false' in paused_page


def test_webspeech_state_deduplicates_final_results():
    state = _BridgeState()

    state.set_result("hello", False)
    assert state.latest_partial(0.01) == "hello"

    state.set_result("hello", True)
    state.set_result("hello", True)

    assert state.pop_final(0.01) == "hello"
    assert state.pop_final(0.01) == ""


def test_webspeech_capture_pause_discards_and_blocks_microphone_results():
    state = _BridgeState()
    state.set_result("before mute", True)

    state.set_capture_enabled(False)
    state.set_result("while muted", True)

    assert state.capture_status() == {"capture_enabled": False}
    assert state.pop_final(0.01) == ""

    state.set_capture_enabled(True)
    state.set_result("after unmute", True)

    assert state.pop_final(0.01) == "after unmute"


def test_webspeech_provider_opens_browser_once(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened.append(url))

    provider = WebSpeechASRProvider(
        {
            "asr": {
                "webspeech": {
                    "bridge_port": 0,
                    "auto_open_browser": True,
                }
            }
        }
    )
    try:
        provider.load()
        provider.load()
    finally:
        provider.close()

    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:")


def test_webspeech_capture_control_is_exposed_to_browser_page():
    provider = WebSpeechASRProvider(
        {
            "asr": {
                "webspeech": {
                    "bridge_port": 0,
                    "auto_open_browser": False,
                }
            }
        }
    )
    try:
        provider.set_capture_enabled(False)
        provider.load()
        with urlopen(f"{provider._url}capture-state", timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        with urlopen(provider._url, timeout=2.0) as response:
            page = response.read().decode("utf-8")
    finally:
        provider.close()

    assert payload == {"capture_enabled": False}
    assert '"captureEnabled":false' in page


def test_webspeech_provider_uses_embedded_opener_and_closes_handle(monkeypatch):
    opened: list[str] = []
    closed: list[bool] = []

    class Handle:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        webbrowser,
        "open_new_tab",
        lambda _url: (_ for _ in ()).throw(AssertionError("system browser opened")),
    )

    provider = WebSpeechASRProvider(
        {
            "asr": {
                "webspeech": {
                    "bridge_port": 0,
                    "auto_open_browser": True,
                    "embedded_browser": True,
                }
            }
        }
    )
    provider.set_browser_opener(lambda url: opened.append(url) or Handle())
    try:
        provider.load()
        provider.load()
    finally:
        provider.close()

    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:")
    assert closed == [True]


def test_webspeech_provider_switches_bridge_language_without_restart():
    provider = WebSpeechASRProvider(
        {
            "ui": {"language": "en"},
            "asr": {
                "webspeech": {
                    "bridge_port": 0,
                    "auto_open_browser": False,
                }
            },
        }
    )
    try:
        provider.load()
        provider.update_language("ru-RU")
        with urlopen(provider._url, timeout=2.0) as response:
            page = response.read().decode("utf-8")
        with urlopen(f"{provider._url}status", timeout=2.0) as response:
            status = json.loads(response.read().decode("utf-8"))
    finally:
        provider.close()

    assert '<html lang="ru">' in page
    assert status["ui_language"] == "ru"


def test_webspeech_provider_reloads_embedded_handle_on_language_change():
    received: list[str] = []

    class Handle:
        def update_language(self, language: str) -> None:
            received.append(language)

        def close(self) -> None:
            return None

    provider = WebSpeechASRProvider(
        {
            "ui": {"language": "en"},
            "asr": {
                "webspeech": {
                    "bridge_port": 0,
                    "auto_open_browser": True,
                    "embedded_browser": True,
                }
            },
        }
    )
    provider.set_browser_opener(lambda _url: Handle())
    try:
        provider.load()
        provider.update_language("ko-KR")
    finally:
        provider.close()

    assert received == ["ko"]


def test_webspeech_state_marks_stale_heartbeat_disconnected():
    state = _BridgeState()
    state.set_connected()

    with state.condition:
        state.last_heartbeat_at = time.monotonic() - 30.0

    assert state.mark_stale_if_needed(8.0) is True
    assert state.connected is False
    assert state.partial_text == ""


def test_webspeech_final_timeout_seconds_is_not_treated_as_milliseconds():
    provider = WebSpeechASRProvider(
        {
            "asr": {
                "webspeech": {
                    "final_timeout_seconds": 4.0,
                    "silence_timeout_ms": 800,
                }
            }
        }
    )

    assert provider.final_timeout_seconds == 4.0


def test_webspeech_legacy_silence_timeout_is_converted_to_seconds():
    provider = WebSpeechASRProvider(
        {
            "asr": {
                "webspeech": {
                    "silence_timeout_ms": 800,
                }
            }
        }
    )

    assert provider.final_timeout_seconds == 0.8
