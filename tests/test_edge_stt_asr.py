"""Edge speech-recognition protocol and provider behavior (no network)."""

from __future__ import annotations

import hashlib
import json
import struct
import threading

import numpy as np
import pytest

from src.asr import edge_stt_protocol as protocol
from src.asr.edge_stt_asr import EdgeSTTASRProvider, _language_code, _pcm16_bytes
from src.asr.errors import ASRNetworkError, ASRProviderError


# --------------------------------------------------------------------- protocol
def test_sec_ms_gec_signs_the_five_minute_window():
    """The service rounds to 5 minutes; a 30-second window produces a bad token."""
    token = protocol.generate_sec_ms_gec()
    assert len(token) == 64
    assert token == token.upper()

    import datetime

    ticks = datetime.datetime.now(datetime.timezone.utc).timestamp()
    ticks += protocol.WIN_EPOCH_SECONDS
    ticks -= ticks % 300
    ticks *= 1e9 / 100
    expected = hashlib.sha256(
        f"{ticks:.0f}{protocol.TRUSTED_CLIENT_TOKEN}".encode("ascii")
    ).hexdigest().upper()
    assert token == expected


def test_sec_ms_gec_is_stable_inside_one_window_and_shifts_with_skew():
    base = protocol.generate_sec_ms_gec()
    assert protocol.generate_sec_ms_gec() == base
    # A skew larger than the window must land on a different signature.
    assert protocol.generate_sec_ms_gec(clock_skew_seconds=3600) != base


def test_recognition_url_carries_the_language_and_signature():
    url = protocol.build_recognition_url("ja-JP")
    assert url.startswith("wss://speech.platform.bing.com/speech/recognition/edge/interactive/v1?")
    assert f"TrustedClientToken={protocol.TRUSTED_CLIENT_TOKEN}" in url
    assert "language=ja-JP" in url
    assert f"Sec-MS-GEC-Version={protocol.SEC_MS_GEC_VERSION}" in url
    assert "profanity=raw" in url


def test_text_message_uses_header_blank_line_body_framing():
    message = protocol.text_message(
        "speech.context", {"a": 1}, content_type="application/json", request_id="rid"
    )
    head, _, body = message.partition("\r\n\r\n")
    lines = head.split("\r\n")
    assert lines[1] == "Path:speech.context"
    assert "X-RequestId:rid" in lines
    assert "Content-Type:application/json" in lines
    assert json.loads(body) == {"a": 1}


def test_binary_message_prefixes_a_big_endian_header_length():
    payload = b"\x01\x02\x03"
    frame = protocol.binary_message("audio", "rid", payload, stream_id="7")
    head_len = struct.unpack(">H", frame[:2])[0]
    head = frame[2 : 2 + head_len].decode("utf-8")
    assert frame[2 + head_len :] == payload
    assert "Path:audio" in head
    assert "X-StreamId:7" in head


def test_parse_message_handles_both_frame_kinds():
    text = protocol.text_message("turn.end", {})
    assert protocol.parse_message(text)[0] == "turn.end"

    binary = protocol.binary_message("audio", "rid", b"body")
    assert protocol.parse_message(binary)[0] == "audio"
    assert protocol.parse_message(b"")[0] == ""


def test_speech_context_only_continues_when_a_service_tag_is_known():
    fresh = json.loads(protocol.speech_context_message("rid").split("\r\n\r\n", 1)[1])
    assert "continuation" not in fresh

    continued = json.loads(
        protocol.speech_context_message(
            "rid", previous_service_tag="tag-1", offset_ticks=123
        ).split("\r\n\r\n", 1)[1]
    )
    assert continued["continuation"]["previousServiceTag"] == "tag-1"
    assert continued["continuation"]["audio"]["streams"]["1"]["offset"] == "123"


def test_wav_header_declares_mono_pcm16_at_the_stream_rate():
    header = protocol.wav_header(16000)
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert struct.unpack("<H", header[22:24])[0] == 1  # channels
    assert struct.unpack("<I", header[24:28])[0] == 16000
    assert struct.unpack("<H", header[34:36])[0] == 16  # bits per sample


def test_offset_ticks_use_hundred_nanosecond_units():
    # One second of 16 kHz mono PCM16 is 32000 bytes -> 10,000,000 ticks.
    assert protocol.bytes_to_offset_ticks(32000, 16000) == 10_000_000


def test_speech_config_reports_the_actual_sample_rate():
    body = json.loads(protocol.speech_config_message(48000).split("\r\n\r\n", 1)[1])
    source = body["context"]["audio"]["source"]
    assert source["samplerate"] == "48000"
    assert source["channelcount"] == "1"
    assert source["bitspersample"] == "16"


# --------------------------------------------------------------------- provider
def test_language_codes_normalize_to_service_tags():
    assert _language_code("ja") == "ja-JP"
    assert _language_code("zh") == "zh-CN"
    assert _language_code("en") == "en-US"
    assert _language_code("pt-br") == "pt-BR"
    assert _language_code("") == "ja-JP"
    assert _language_code(None, "en-US") == "en-US"


def test_float_audio_is_converted_to_little_endian_pcm16():
    pcm = _pcm16_bytes(np.array([0.0, 1.0, -1.0], dtype=np.float32))
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [0, 32767, -32767]
    # Stereo input is mixed down; the service only accepts mono.
    stereo = _pcm16_bytes(np.array([[1.0, -1.0], [0.5, 0.5]], dtype=np.float32))
    assert np.frombuffer(stereo, dtype="<i2").tolist() == [0, 16383]
    # Already-integer audio passes through untouched.
    assert _pcm16_bytes(np.array([7, -7], dtype=np.int16)) == b"\x07\x00\xf9\xff"


class _FakeSocket:
    """Records what the provider sends and replays a scripted server turn."""

    def __init__(self, phrases, *, service_tag="tag-1"):
        self.sent = []
        self.closed = False
        self._phrases = list(phrases)
        self._service_tag = service_tag
        self._inbox = []

    def send(self, message):
        self.sent.append(message)
        path, _body = protocol.parse_message(message)
        if path == "speech.context":
            text = self._phrases.pop(0) if self._phrases else ""
            self._inbox.extend(
                [
                    protocol.text_message(
                        "turn.start", {"context": {"serviceTag": self._service_tag}}
                    ),
                    protocol.text_message(
                        "speech.phrase",
                        {"RecognitionStatus": "Success", "DisplayText": text},
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


def _provider(monkeypatch, socket, config=None):
    provider = EdgeSTTASRProvider(config or {"asr": {"edge_stt": {"language": "ja-JP"}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: lambda *a, **k: socket)
    return provider


def _audio(seconds=1.0, rate=16000):
    return np.zeros(int(rate * seconds), dtype=np.float32)


def test_transcribe_sends_config_context_header_then_audio(monkeypatch):
    socket = _FakeSocket(["こんにちは"])
    provider = _provider(monkeypatch, socket)

    assert provider.transcribe(_audio(), 16000) == "こんにちは"

    paths = [protocol.parse_message(m)[0] for m in socket.sent]
    assert paths[0] == "speech.config"
    assert paths[1] == "speech.context"
    assert paths[2] == "audio"  # WAV header
    assert paths[3] == "audio"  # samples
    provider.close()


def test_second_utterance_reuses_the_socket_and_continues_the_turn(monkeypatch):
    socket = _FakeSocket(["one", "two"])
    provider = _provider(monkeypatch, socket)

    assert provider.transcribe(_audio(), 16000) == "one"
    first_config_frames = sum(
        1 for m in socket.sent if protocol.parse_message(m)[0] == "speech.config"
    )
    assert provider.transcribe(_audio(), 16000) == "two"

    # No second handshake, and the second turn continues the first session.
    assert first_config_frames == 1
    assert sum(1 for m in socket.sent if protocol.parse_message(m)[0] == "speech.config") == 1
    contexts = [m for m in socket.sent if protocol.parse_message(m)[0] == "speech.context"]
    assert len(contexts) == 2
    second = json.loads(contexts[1].split("\r\n\r\n", 1)[1])
    assert second["continuation"]["previousServiceTag"] == "tag-1"
    assert int(second["continuation"]["audio"]["streams"]["2"]["offset"]) > 0
    provider.close()


def test_connection_is_rebuilt_once_the_turn_budget_is_spent(monkeypatch):
    sockets = []

    def connect(*_a, **_k):
        socket = _FakeSocket(["a", "b", "c"])
        sockets.append(socket)
        return socket

    provider = EdgeSTTASRProvider(
        {"asr": {"edge_stt": {"language": "ja-JP", "max_turns": 2}}}
    )
    monkeypatch.setattr(provider, "_connect_module", lambda: connect)

    for _ in range(3):
        provider.transcribe(_audio(), 16000)

    assert len(sockets) == 2
    assert sockets[0].closed is True
    provider.close()


def test_language_change_forces_a_new_connection(monkeypatch):
    sockets = []

    def connect(*_a, **_k):
        socket = _FakeSocket(["x", "y"])
        sockets.append(socket)
        return socket

    provider = EdgeSTTASRProvider({"asr": {"edge_stt": {"language": "ja-JP"}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: connect)

    provider.transcribe(_audio(), 16000, language="ja")
    provider.transcribe(_audio(), 16000, language="en")

    assert len(sockets) == 2
    provider.close()


def test_a_socket_that_dies_between_utterances_is_retried_once(monkeypatch):
    class _DeadSocket(_FakeSocket):
        def send(self, message):
            raise ConnectionResetError("closed by peer")

    sockets = [_DeadSocket([]), _FakeSocket(["recovered"])]

    def connect(*_a, **_k):
        return sockets.pop(0)

    provider = EdgeSTTASRProvider({"asr": {"edge_stt": {"language": "ja-JP"}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: connect)

    assert provider.transcribe(_audio(), 16000) == "recovered"
    provider.close()


def test_a_persistently_dead_connection_reports_a_network_error(monkeypatch):
    class _DeadSocket(_FakeSocket):
        def send(self, message):
            raise ConnectionResetError("closed by peer")

    provider = EdgeSTTASRProvider({"asr": {"edge_stt": {"language": "ja-JP"}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: lambda *a, **k: _DeadSocket([]))

    with pytest.raises(ASRNetworkError):
        provider.transcribe(_audio(), 16000)
    provider.close()


def test_no_match_status_yields_empty_text_rather_than_an_error(monkeypatch):
    class _NoMatchSocket(_FakeSocket):
        def send(self, message):
            self.sent.append(message)
            if protocol.parse_message(message)[0] == "speech.context":
                self._inbox.extend(
                    [
                        protocol.text_message(
                            "speech.phrase", {"RecognitionStatus": "NoMatch"}
                        ),
                        protocol.text_message("turn.end", {}),
                    ]
                )

    provider = _provider(monkeypatch, _NoMatchSocket([]))
    assert provider.transcribe(_audio(), 16000) == ""
    provider.close()


def test_partial_requests_do_no_work(monkeypatch):
    socket = _FakeSocket(["ignored"])
    provider = _provider(monkeypatch, socket)

    assert provider.transcribe(_audio(), 16000, is_final=False) == ""
    assert socket.sent == []
    assert EdgeSTTASRProvider.supports_partial is False
    provider.close()


def test_empty_audio_never_opens_a_connection(monkeypatch):
    socket = _FakeSocket(["unused"])
    provider = _provider(monkeypatch, socket)

    assert provider.transcribe(np.zeros(0, dtype=np.float32), 16000) == ""
    assert socket.sent == []
    provider.close()


def test_overlong_audio_is_truncated_before_upload(monkeypatch):
    socket = _FakeSocket(["long"])
    provider = _provider(monkeypatch, socket)

    provider.transcribe(_audio(seconds=120.0), 16000)

    audio_bytes = sum(
        len(protocol.parse_message(m)[1])
        for m in socket.sent
        if isinstance(m, (bytes, bytearray)) and protocol.parse_message(m)[0] == "audio"
    )
    # 60 s ceiling plus the WAV header and trailing silence, well under 120 s.
    assert audio_bytes < 120 * 16000 * 2
    provider.close()


def test_cancellation_stops_an_in_flight_utterance(monkeypatch):
    """Cancel interrupts the upload loop, not a request that has not started."""

    started = threading.Event()

    class _SlowSocket(_FakeSocket):
        def send(self, message):
            super().send(message)
            if protocol.parse_message(message)[0] == "audio":
                started.set()
                # Give the canceller a chance to run between chunks.
                threading.Event().wait(0.01)

    provider = _provider(monkeypatch, _SlowSocket(["never"]))
    error: list[BaseException] = []

    def run():
        try:
            provider.transcribe(_audio(seconds=30.0), 16000)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            error.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(timeout=5)
    provider.cancel_pending_requests()
    worker.join(timeout=10)

    assert error and isinstance(error[0], ASRProviderError)
    provider.close()


def test_a_stale_cancel_does_not_block_the_next_utterance(monkeypatch):
    """cancel_pending_requests targets in-flight work, not future requests."""

    provider = _provider(monkeypatch, _FakeSocket(["fresh"]))
    provider.cancel_pending_requests()

    assert provider.transcribe(_audio(), 16000) == "fresh"
    provider.close()


def test_close_is_idempotent_and_refuses_later_work(monkeypatch):
    socket = _FakeSocket(["x"])
    provider = _provider(monkeypatch, socket)
    provider.transcribe(_audio(), 16000)

    provider.close()
    provider.close()

    assert socket.closed is True
    with pytest.raises(ASRProviderError):
        provider.transcribe(_audio(), 16000)


def test_corrector_is_applied_to_the_final_text(monkeypatch):
    class _Corrector:
        def apply(self, text, language=None):
            self.language = language
            return text.upper()

    corrector = _Corrector()
    provider = EdgeSTTASRProvider(
        {"asr": {"edge_stt": {"language": "en-US"}}}, corrector=corrector
    )
    monkeypatch.setattr(
        provider, "_connect_module", lambda: lambda *a, **k: _FakeSocket(["hello"])
    )

    assert provider.transcribe(_audio(), 16000) == "HELLO"
    assert corrector.language == "en-US"
    provider.close()


def test_prewarm_opens_the_socket_without_sending_audio(monkeypatch):
    socket = _FakeSocket([])
    provider = _provider(monkeypatch, socket)

    assert provider.prewarm() is True
    paths = [protocol.parse_message(m)[0] for m in socket.sent]
    assert paths == ["speech.config"]
    provider.close()


def test_prewarm_failure_is_reported_without_raising(monkeypatch):
    def connect(*_a, **_k):
        raise OSError("no route to host")

    provider = EdgeSTTASRProvider({"asr": {"edge_stt": {}}})
    monkeypatch.setattr(provider, "_connect_module", lambda: connect)

    assert provider.prewarm() is False
    provider.close()


def test_concurrent_transcribe_calls_are_serialized(monkeypatch):
    socket = _FakeSocket(["a", "b"])
    provider = _provider(monkeypatch, socket)
    results = []

    def run():
        results.append(provider.transcribe(_audio(), 16000))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(results) == ["a", "b"]
    provider.close()


def test_end_of_stream_marker_is_sent_after_the_audio(monkeypatch):
    """The empty frame is what ends the turn.

    Without it the service falls back to its own segmentation timeout, which
    made short utterances take ~4.5 s instead of ~0.35 s.
    """

    socket = _FakeSocket(["done"])
    provider = _provider(monkeypatch, socket)
    provider.transcribe(_audio(), 16000)

    audio_frames = [
        m for m in socket.sent
        if isinstance(m, (bytes, bytearray)) and protocol.parse_message(m)[0] == "audio"
    ]
    assert audio_frames, "no audio frames were sent"
    # The final audio frame must carry an empty payload.
    assert protocol.parse_message(audio_frames[-1])[1] == ""
    # ...and it must come after real samples, not replace them.
    assert any(protocol.parse_message(m)[1] for m in audio_frames[:-1])
    provider.close()


def test_trailing_silence_stays_short():
    """A long silence tail only delays the result once the marker is in use."""
    from src.asr.edge_stt_asr import DEFAULT_TRAILING_SILENCE_SECONDS

    assert DEFAULT_TRAILING_SILENCE_SECONDS <= 0.2
