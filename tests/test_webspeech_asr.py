from __future__ import annotations

import webbrowser
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


def test_webspeech_state_deduplicates_final_results():
    state = _BridgeState()

    state.set_result("hello", False)
    assert state.latest_partial(0.01) == "hello"

    state.set_result("hello", True)
    state.set_result("hello", True)

    assert state.pop_final(0.01) == "hello"
    assert state.pop_final(0.01) == ""


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
