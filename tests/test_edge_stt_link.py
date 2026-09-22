"""The Edge speech link on a bad route: keepalive, background reconnects,
link events, statistics, the system proxy and the settings-page link test.

A player's log showed the service dropping an idle socket after ~20 s
without a close frame; the reused socket then swallowed the next sentence
for 10-25 s. The socket is now pinged every 5 s, reopened in the background
when it dies or sits idle, and every turn's first answer is timed so a
session ends with one line that says how the route behaved.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from src.asr import edge_stt_asr
from src.asr import edge_stt_protocol as protocol
from src.asr.edge_stt_asr import (
    DEFAULT_PING_INTERVAL_SECONDS,
    DEFAULT_PING_TIMEOUT_SECONDS,
    EdgeSTTASRProvider,
    _socket_is_dead,
    diagnose_link,
)
from src.asr.errors import ASRNetworkError


class _Socket:
    """Answers each turn like the service; can be declared dead or refuse to answer."""

    def __init__(self, phrases=("ok",), *, answer=True):
        self.sent = []
        self.closed = False
        self.close_code = None
        self._phrases = list(phrases)
        self._inbox = []
        self._answer = answer

    def send(self, message):
        if self.closed:
            raise ConnectionError("socket closed")
        self.sent.append(message)
        path, _body = protocol.parse_message(message)
        if path == "speech.context" and self._answer:
            text = self._phrases.pop(0) if self._phrases else ""
            self._inbox.extend(
                [
                    protocol.text_message("turn.start", {"context": {"serviceTag": "tag"}}),
                    protocol.text_message(
                        "speech.phrase", {"RecognitionStatus": "Success", "DisplayText": text}
                    ),
                    protocol.text_message("turn.end", {}),
                ]
            )

    def recv(self, timeout=None):
        del timeout
        if not self._inbox:
            raise TimeoutError("no message")
        return self._inbox.pop(0)

    def close(self):
        self.closed = True


class _Connect:
    """A connect() that records its keyword arguments and hands out sockets in order."""

    def __init__(self, *sockets):
        self.sockets = list(sockets)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(kwargs)
        if not self.sockets:
            raise ConnectionError("no route")
        socket = self.sockets.pop(0)
        if isinstance(socket, Exception):
            raise socket
        return socket


def _provider(monkeypatch, connect, config=None):
    provider = EdgeSTTASRProvider(config or {"asr": {"edge_stt": {"language": "ja-JP"}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: connect)
    # Tests drive the keepalive by hand.
    monkeypatch.setattr(provider, "_ensure_keepalive", lambda: None)
    return provider


def _audio(seconds=1.0, rate=16000):
    return np.zeros(int(rate * seconds), dtype=np.float32)


class TestConnectOptions:
    def test_the_socket_is_pinged_often_and_connects_directly_by_default(self, monkeypatch):
        connect = _Connect(_Socket())
        provider = _provider(monkeypatch, connect)

        provider.transcribe(_audio(), 16000)

        options = connect.calls[0]
        assert options["ping_interval"] == DEFAULT_PING_INTERVAL_SECONDS
        assert options["ping_timeout"] == DEFAULT_PING_TIMEOUT_SECONDS
        assert options["proxy"] is True
        provider.close()

    def test_the_system_proxy_is_used_when_asked_for(self, monkeypatch):
        monkeypatch.setattr(edge_stt_asr, "system_proxy_url", lambda: "http://127.0.0.1:7890")
        connect = _Connect(_Socket())
        provider = _provider(
            monkeypatch, connect, {"asr": {"edge_stt": {"language": "ja-JP", "use_system_proxy": True}}}
        )

        provider.transcribe(_audio(), 16000)

        assert connect.calls[0]["proxy"] == "http://127.0.0.1:7890"
        provider.close()

    def test_without_a_system_proxy_the_switch_still_connects(self, monkeypatch):
        monkeypatch.setattr(edge_stt_asr, "system_proxy_url", lambda: "")
        connect = _Connect(_Socket())
        provider = _provider(
            monkeypatch, connect, {"asr": {"edge_stt": {"language": "ja-JP", "use_system_proxy": True}}}
        )

        assert provider.transcribe(_audio(), 16000) == "ok"
        assert connect.calls[0]["proxy"] is True
        provider.close()

    def test_system_proxy_url_reads_the_https_entry(self, monkeypatch):
        import urllib.request

        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "proxy.local:8080"})

        assert edge_stt_asr.system_proxy_url() == "http://proxy.local:8080"


class TestKeepalive:
    def test_a_dead_socket_is_reopened_between_utterances(self, monkeypatch):
        first, second = _Socket(["one"]), _Socket(["two"])
        connect = _Connect(first, second)
        provider = _provider(monkeypatch, connect)
        provider.transcribe(_audio(), 16000)

        first.close_code = 1006  # the library noticed the peer vanished
        assert provider._keepalive_tick(now=100.0) is True

        assert first.closed and provider._ws is second
        assert provider.link_stats["background_reconnects"] == 1
        # The next utterance rides the fresh socket with no handshake of its own.
        assert provider.transcribe(_audio(), 16000) == "two"
        assert len(connect.calls) == 2
        provider.close()

    def test_an_idle_socket_is_reopened_before_the_next_utterance(self, monkeypatch):
        first, second = _Socket(["one"]), _Socket(["two"])
        provider = _provider(monkeypatch, _Connect(first, second))
        provider.transcribe(_audio(), 16000)
        provider._last_activity_at = 10.0

        assert provider._keepalive_tick(now=10.0 + provider.reuse_idle_seconds + 1.0) is True
        assert provider._ws is second
        provider.close()

    def test_a_live_socket_is_left_alone(self, monkeypatch):
        first = _Socket(["one"])
        provider = _provider(monkeypatch, _Connect(first))
        provider.transcribe(_audio(), 16000)
        provider._last_activity_at = 10.0

        assert provider._keepalive_tick(now=11.0) is False
        assert provider._ws is first
        provider.close()

    def test_after_a_failure_the_reconnect_happens_in_the_background_with_backoff(self, monkeypatch):
        first = _Socket(["one"])
        provider = _provider(monkeypatch, _Connect(first, ConnectionError("down"), _Socket(["back"])))
        provider.transcribe(_audio(), 16000)
        provider._close_socket()  # what a mid-utterance failure leaves behind

        assert provider._keepalive_tick(now=50.0) is False  # the route refused
        assert provider._reconnect_not_before > 50.0
        assert provider._keepalive_tick(now=50.5) is False  # too soon to try again
        assert provider._keepalive_tick(now=60.0) is True
        assert provider.link_stats["background_reconnects"] == 1
        provider.close()

    def test_the_keepalive_never_runs_while_a_turn_holds_the_lock(self, monkeypatch):
        import threading

        first = _Socket(["one"])
        provider = _provider(monkeypatch, _Connect(first, _Socket()))
        provider.transcribe(_audio(), 16000)
        first.close_code = 1006

        # A turn in progress on a worker thread holds the provider lock; the
        # keepalive (another thread) must skip its tick rather than wait.
        held = threading.Event()
        release = threading.Event()

        def worker():
            with provider._lock:
                held.set()
                release.wait(5.0)

        turn = threading.Thread(target=worker)
        turn.start()
        assert held.wait(5.0)
        try:
            assert provider._keepalive_tick(now=100.0) is False
            assert provider._ws is first
        finally:
            release.set()
            turn.join(5.0)
        provider.close()

    def test_dead_socket_detection_reads_the_library_state(self):
        assert _socket_is_dead(None) is True
        assert _socket_is_dead(types.SimpleNamespace(close_code=1000)) is True
        assert _socket_is_dead(types.SimpleNamespace(close_code=None)) is False
        closed = types.SimpleNamespace(close_code=None, protocol=types.SimpleNamespace(state=types.SimpleNamespace(name="CLOSED")))
        assert _socket_is_dead(closed) is True


class TestEventsAndStats:
    def test_a_mid_utterance_failure_reports_reconnecting_then_recovered(self, monkeypatch):
        dying = _Socket()
        original_send = dying.send

        def send(message):
            # The handshake goes through; the utterance itself hits a dead peer.
            if protocol.parse_message(message)[0] == "speech.config":
                return original_send(message)
            raise ConnectionError("gone")

        dying.send = send
        provider = _provider(monkeypatch, _Connect(dying, _Socket(["again"])))
        events = []
        provider.on_event = lambda kind, details: events.append((kind, dict(details)))

        assert provider.transcribe(_audio(), 16000) == "again"

        assert [kind for kind, _ in events] == ["reconnecting", "recovered"]
        assert events[0][1]["reason"] == "ConnectionError"
        assert provider.link_stats["mid_utterance_failures"] == 1
        provider.close()

    def test_a_turn_that_never_answers_is_a_timeout_event(self, monkeypatch):
        silent = _Socket(answer=False)
        provider = _provider(
            monkeypatch,
            _Connect(silent, _Socket(answer=False)),
            {"asr": {"edge_stt": {"language": "ja-JP", "first_message_timeout_seconds": 0.1, "recognition_timeout_seconds": 0.5}}},
        )
        events = []
        provider.on_event = lambda kind, details: events.append(kind)

        with pytest.raises(ASRNetworkError):
            provider.transcribe(_audio(0.2), 16000)

        # Two stalls (the socket and its replacement), then the network error.
        assert provider.link_stats["stalls"] == 2
        assert "reconnecting" in events
        provider.close()

    def test_first_answers_are_timed_and_summarised(self, monkeypatch, caplog):
        provider = _provider(monkeypatch, _Connect(_Socket(["one", "two"])))

        provider.transcribe(_audio(), 16000)
        provider.transcribe(_audio(), 16000)

        assert len(provider._first_message_ms) == 2
        summary = provider.link_summary()
        assert "connections=1" in summary and "turns=2" in summary and "samples=2" in summary
        import logging

        with caplog.at_level(logging.INFO, logger="src.asr.edge_stt_asr"):
            provider.close()
        assert "Edge STT link summary" in caplog.text

    def test_a_failing_callback_never_breaks_recognition(self, monkeypatch):
        dying = _Socket()
        dying.send = lambda message: (_ for _ in ()).throw(ConnectionError("gone"))
        provider = _provider(monkeypatch, _Connect(dying, _Socket(["fine"])))
        provider.on_event = lambda kind, details: (_ for _ in ()).throw(RuntimeError("ui gone"))

        assert provider.transcribe(_audio(), 16000) == "fine"
        provider.close()


class TestLinkTest:
    def test_a_healthy_route_is_good(self):
        result = diagnose_link({"asr": {"edge_stt": {"language": "ja-JP"}}}, connect_module=_Connect(_Socket(["", ""])))

        assert result["verdict"] == "good"
        assert result["error"] == ""
        assert result["connect_ms"] is not None and result["first_message_ms"] is not None
        assert result["second_turn_ms"] is not None

    def test_an_unreachable_route_fails_with_the_reason(self):
        result = diagnose_link({"asr": {"edge_stt": {}}}, connect_module=_Connect(ConnectionError("refused")))

        assert result["verdict"] == "failed"
        assert "refused" in result["error"]

    def test_a_route_that_never_answers_fails(self):
        result = diagnose_link(
            {"asr": {"edge_stt": {"first_message_timeout_seconds": 0.1, "recognition_timeout_seconds": 0.3}}},
            connect_module=_Connect(_Socket(answer=False)),
        )

        assert result["verdict"] == "failed"
        assert "no answer" in result["error"]


class TestConfigAndFactory:
    def test_the_provider_section_defaults_to_fallback_on_and_direct_connection(self):
        from src.utils.config_manager import _ensure_asr_config

        config: dict = {"asr": {}}
        _ensure_asr_config(config)

        edge = config["asr"]["edge_stt"]
        assert edge["auto_fallback"] is True
        assert edge["use_system_proxy"] is False

    def test_edge_speech_gets_the_local_fallback_by_default(self, monkeypatch):
        from src.asr import factory
        from src.asr.fallback_asr import FallbackASR

        monkeypatch.setattr(factory, "_create_ready_sensevoice_fallback", lambda *a, **k: object())
        provider = factory.create_asr({"asr": {"engine": "edge-stt", "edge_stt": {}}})

        assert isinstance(provider, FallbackASR)
        assert isinstance(provider.primary, EdgeSTTASRProvider)
        provider.close()

    def test_the_switch_in_the_provider_section_turns_it_off(self, monkeypatch):
        from src.asr import factory

        provider = factory.create_asr({"asr": {"engine": "edge-stt", "edge_stt": {"auto_fallback": False}}})

        assert isinstance(provider, EdgeSTTASRProvider)
        provider.close()
