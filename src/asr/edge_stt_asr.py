# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Edge speech recognition over a direct WebSocket connection.

Unlike the Web Speech provider, which drives a hidden browser page, this talks
to Microsoft's recognition endpoint itself. That removes the browser from the
recognition path and, more importantly, lets one socket serve many utterances:
each turn is continued with the previous service tag instead of reconnecting,
so only the first utterance of a session pays for the handshake.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.base import ASRProvider, ProgressCallback
from src.asr.edge_stt_protocol import (
    binary_message,
    build_recognition_url,
    wss_headers,
    bytes_to_offset_ticks,
    parse_json_body,
    parse_message,
    speech_config_message,
    speech_context_message,
    wav_header,
)
from src.asr.errors import ASRNetworkError, ASRProviderError
from src.asr.text_corrections import LayeredASRCorrector

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "ja-JP"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 6.0
DEFAULT_RECOGNITION_TIMEOUT_SECONDS = 12.0
# A live socket answers a turn within a few hundred milliseconds: turn.start
# comes back before any audio is processed. Nothing at all for this long means
# the socket is dead. On some routes the service drops an idle connection
# without a close frame, and a player's log showed each such drop costing a
# full recognition timeout plus the retry - 10 to 25 s of "translating" per
# sentence - before this window existed.
DEFAULT_FIRST_MESSAGE_TIMEOUT_SECONDS = 3.0
# A socket idle longer than this is reopened before the next utterance. The
# same log showed connections dying after about 20 s of silence; a reconnect
# costs a few hundred milliseconds, a dead socket costs the window above.
DEFAULT_REUSE_IDLE_SECONDS = 15.0
# A turn can only take so long for the audio it was given: the service's own
# segmentation timeout is about 4.5 s, and it processes faster than real time.
_TURN_TIMEOUT_BASE_SECONDS = 5.0
# recv waits are sliced so a cancel is noticed promptly instead of after the
# whole recognition timeout, which is what kept pipeline restarts waiting.
_RECV_SLICE_SECONDS = 0.5
# The service ends a session after enough turns; reconnecting a little earlier
# keeps a mid-conversation utterance from paying for the forced restart.
DEFAULT_MAX_TURNS = 18
# A short silence tail lets the recognizer settle the final word; the empty
# audio frame sent after it is the end-of-stream marker that actually ends the
# turn. Without that marker the service waits out its own ~4.5 s segmentation
# timeout, which dominates short utterances: a 1 s clip measured 4498 ms
# without the marker versus 358 ms with it.
DEFAULT_TRAILING_SILENCE_SECONDS = 0.15
_AUDIO_CHUNK_SECONDS = 0.1
_MAX_UTTERANCE_SECONDS = 60.0
# WebSocket pings keep the connection busy, so the middleboxes on a bad route
# that dropped an idle socket after ~20 s see traffic instead, and a socket
# that did die is noticed within ping_interval + ping_timeout rather than
# on the next utterance. The library default of 20 s sat right on the
# route's idle limit.
DEFAULT_PING_INTERVAL_SECONDS = 5.0
DEFAULT_PING_TIMEOUT_SECONDS = 5.0
# The keepalive thread looks at the socket this often and reopens one that
# died, sat idle or ran out of turns, so the next utterance never pays for a
# reconnect. A route that refuses reconnects is retried with backoff.
_KEEPALIVE_TICK_SECONDS = 1.0
_BACKGROUND_RECONNECT_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 30.0)
_FIRST_MESSAGE_SAMPLES = 200
# Link test thresholds (milliseconds): connect and first answer.
_LINK_GOOD_MS = 1500.0
_LINK_SLOW_MS = 4000.0
_LINK_TEST_SILENCE_SECONDS = 0.6

_LANGUAGE_ALIASES = {
    "ja": "ja-JP",
    "jp": "ja-JP",
    "zh": "zh-CN",
    "cn": "zh-CN",
    "zh-cn": "zh-CN",
    "yue": "zh-HK",
    "en": "en-US",
    "ko": "ko-KR",
    "ru": "ru-RU",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-BR",
}


def _language_code(value: object, default: str = DEFAULT_LANGUAGE) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lowered]
    if "-" in text:
        head, _, tail = text.partition("-")
        return f"{head.lower()}-{tail.upper()}"
    return _LANGUAGE_ALIASES.get(lowered, default)


def _float_value(value: object, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return max(minimum, min(parsed, maximum))


def _int_value(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _provider_config(config: Mapping[str, object] | None) -> Mapping[str, object]:
    asr_cfg = config.get("asr", {}) if isinstance(config, Mapping) else {}
    if not isinstance(asr_cfg, Mapping):
        return {}
    provider_cfg = asr_cfg.get("edge_stt", {})
    return provider_cfg if isinstance(provider_cfg, Mapping) else {}


class _SocketStalled(Exception):
    """A turn was sent and nothing came back: the socket is dead, not slow."""


def _socket_is_dead(ws: Any) -> bool:
    """True when the library already knows the connection is gone."""

    if ws is None:
        return True
    if getattr(ws, "close_code", None) is not None:
        return True
    protocol = getattr(ws, "protocol", None)
    state = getattr(protocol, "state", None)
    if state is None:
        return False
    return str(getattr(state, "name", state)).upper() in {"CLOSING", "CLOSED"}


def system_proxy_url() -> str:
    """The machine's HTTPS proxy, from the environment or the Windows registry."""

    try:
        from urllib.request import getproxies

        proxies = getproxies()
    except Exception:
        return ""
    for key in ("wss", "https", "http"):
        value = str(proxies.get(key, "") or "").strip()
        if value:
            if "://" not in value:
                value = "http://" + value
            return value
    return ""


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _pcm16_bytes(audio: np.ndarray) -> bytes:
    samples = np.asarray(audio)
    if samples.ndim > 1 and samples.size:
        samples = samples.mean(axis=1)
    samples = samples.reshape(-1)
    if samples.dtype == np.int16:
        return samples.tobytes()
    samples = np.nan_to_num(
        samples.astype(np.float32, copy=False), nan=0.0, posinf=1.0, neginf=-1.0
    )
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class EdgeSTTASRProvider(ASRProvider):
    """Recognize speech through Microsoft Edge's recognition WebSocket."""

    provider_id = "edge-stt"
    display_name = "Edge Speech"
    requires_api_key = False
    # Recognition happens in one batch call, so there is no interim text to
    # report before the final phrase; claiming otherwise would surface the
    # previous utterance's hypothesis.
    supports_partial = False
    max_concurrent_transcriptions = 1

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        corrector: LayeredASRCorrector | None = None,
    ) -> None:
        provider_cfg = _provider_config(config)
        asr_cfg = config.get("asr", {}) if isinstance(config, Mapping) else {}
        fallback_language = (
            asr_cfg.get("language") if isinstance(asr_cfg, Mapping) else None
        )
        self.language = _language_code(
            provider_cfg.get("language", fallback_language or DEFAULT_LANGUAGE)
        )
        self.connect_timeout_seconds = _float_value(
            provider_cfg.get("connect_timeout_seconds"),
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=30.0,
        )
        self.recognition_timeout_seconds = _float_value(
            provider_cfg.get("recognition_timeout_seconds"),
            DEFAULT_RECOGNITION_TIMEOUT_SECONDS,
            minimum=0.5,
            maximum=60.0,
        )
        self.first_message_timeout_seconds = _float_value(
            provider_cfg.get("first_message_timeout_seconds"),
            DEFAULT_FIRST_MESSAGE_TIMEOUT_SECONDS,
            minimum=0.1,
            maximum=30.0,
        )
        self.reuse_idle_seconds = _float_value(
            provider_cfg.get("reuse_idle_seconds"),
            DEFAULT_REUSE_IDLE_SECONDS,
            minimum=0.1,
            maximum=600.0,
        )
        self.trailing_silence_seconds = _float_value(
            provider_cfg.get("trailing_silence_seconds"),
            DEFAULT_TRAILING_SILENCE_SECONDS,
            minimum=0.1,
            maximum=3.0,
        )
        self.max_turns = _int_value(
            provider_cfg.get("max_turns"), DEFAULT_MAX_TURNS, minimum=1, maximum=50
        )
        self.reuse_connection = bool(provider_cfg.get("reuse_connection", True))
        self.use_system_proxy = bool(provider_cfg.get("use_system_proxy", False))
        self.ping_interval_seconds = _float_value(
            provider_cfg.get("ping_interval_seconds"),
            DEFAULT_PING_INTERVAL_SECONDS,
            minimum=1.0,
            maximum=60.0,
        )
        self.ping_timeout_seconds = _float_value(
            provider_cfg.get("ping_timeout_seconds"),
            DEFAULT_PING_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=60.0,
        )
        # Told about link trouble as it happens: ("reconnecting", details),
        # ("recovered", details), ("timeout", details). Called on the worker
        # thread that hit it; the UI marshals for itself.
        self.on_event: Any = None

        self._corrector = corrector
        self._lock = threading.RLock()
        self._closed = False
        self._cancel = threading.Event()

        self._ws: Any = None
        self._ws_language = ""
        self._ws_sample_rate = 0
        self._service_tag: str | None = None
        self._turns = 0
        self._stream_id = 0
        self._bytes_sent = 0
        self._clock_skew_seconds = 0.0
        self._last_activity_at = 0.0
        self._last_first_message_ms: float | None = None
        self._proxy_logged = False

        self.link_stats: dict[str, int] = {
            "connections": 0,
            "connect_failures": 0,
            "turns": 0,
            "stalls": 0,
            "mid_utterance_failures": 0,
            "timeouts": 0,
            "background_reconnects": 0,
        }
        self._first_message_ms: list[float] = []
        self._keepalive_thread: threading.Thread | None = None
        self._keepalive_stop = threading.Event()
        self._reconnect_not_before = 0.0
        self._reconnect_failures = 0

    # ------------------------------------------------------------- events
    def _emit(self, kind: str, **details: object) -> None:
        callback = self.on_event
        if callback is None:
            return
        try:
            callback(kind, details)
        except Exception:
            logger.debug("Edge STT event callback failed", exc_info=True)

    def _proxy_setting(self) -> Any:
        """What to hand websockets as ``proxy``: the system proxy, or its env default."""

        if not self.use_system_proxy:
            return True
        url = system_proxy_url()
        if not url:
            if not self._proxy_logged:
                self._proxy_logged = True
                logger.info("Edge STT: system proxy requested but none is configured; connecting directly")
            return True
        if not self._proxy_logged:
            self._proxy_logged = True
            logger.info("Edge STT: connecting through the system proxy %s", url)
        return url

    def _record_first_message(self, milliseconds: float) -> None:
        self._last_first_message_ms = milliseconds
        self._first_message_ms.append(milliseconds)
        if len(self._first_message_ms) > _FIRST_MESSAGE_SAMPLES:
            del self._first_message_ms[: len(self._first_message_ms) - _FIRST_MESSAGE_SAMPLES]

    def link_summary(self) -> str:
        """One line for the log: how the route behaved this session."""

        stats = self.link_stats
        samples = list(self._first_message_ms)
        return (
            "connections={connections} connect_failures={connect_failures} turns={turns} "
            "stalls={stalls} mid_utterance_failures={mid_utterance_failures} timeouts={timeouts} "
            "background_reconnects={background_reconnects} first_message_ms median={median:.0f} "
            "p90={p90:.0f} samples={count}"
        ).format(
            median=_percentile(samples, 0.5),
            p90=_percentile(samples, 0.9),
            count=len(samples),
            **stats,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _connect_module():
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise ASRProviderError(
                "The websockets package is required for Edge speech recognition"
            ) from exc
        return connect

    def _close_socket(self) -> None:
        ws, self._ws = self._ws, None
        self._service_tag = None
        self._turns = 0
        self._stream_id = 0
        self._bytes_sent = 0
        if ws is None:
            return
        try:
            ws.close()
        except Exception:
            logger.debug("Edge STT socket close failed", exc_info=True)

    def _needs_reconnect(self, language: str, sample_rate: int) -> bool:
        if self._ws is None:
            return True
        if not self.reuse_connection:
            return True
        # Language lives in the URL and the sample rate in speech.config, so
        # neither can change without a new connection.
        if language != self._ws_language or sample_rate != self._ws_sample_rate:
            return True
        if self._turns >= self.max_turns:
            return True
        # A socket that sat idle may already be dead on the far side; the
        # failure would only show as silence on the next turn.
        idle = time.monotonic() - self._last_activity_at
        return self._last_activity_at > 0 and idle > self.reuse_idle_seconds

    def _open_socket(self, language: str, sample_rate: int) -> None:
        connect = self._connect_module()
        url = build_recognition_url(language, self._clock_skew_seconds)
        started_at = time.monotonic()
        try:
            ws = connect(
                url,
                additional_headers=wss_headers(),
                open_timeout=self.connect_timeout_seconds,
                close_timeout=1.0,
                max_size=None,
                ping_interval=self.ping_interval_seconds,
                ping_timeout=self.ping_timeout_seconds,
                proxy=self._proxy_setting(),
            )
        except Exception as exc:
            self.link_stats["connect_failures"] += 1
            raise ASRNetworkError(
                f"Could not reach the Edge speech recognition service: {exc}"
            ) from exc
        self.link_stats["connections"] += 1

        try:
            ws.send(speech_config_message(sample_rate))
        except Exception as exc:
            try:
                ws.close()
            except Exception:
                pass
            raise ASRNetworkError(
                f"Edge speech recognition rejected the session setup: {exc}"
            ) from exc

        self._ws = ws
        self._ws_language = language
        self._ws_sample_rate = sample_rate
        self._service_tag = None
        self._turns = 0
        self._stream_id = 0
        self._bytes_sent = 0
        self._last_activity_at = time.monotonic()
        logger.info(
            "Edge STT connected (language=%s rate=%dHz) in %.0fms",
            language,
            sample_rate,
            (time.monotonic() - started_at) * 1000.0,
        )
        if self.reuse_connection:
            self._ensure_keepalive()

    # ---------------------------------------------------------- keepalive
    def _ensure_keepalive(self) -> None:
        thread = self._keepalive_thread
        if thread is not None and thread.is_alive():
            return
        self._keepalive_stop.clear()
        thread = threading.Thread(
            target=self._keepalive_loop, name="edge-stt-keepalive", daemon=True
        )
        self._keepalive_thread = thread
        thread.start()

    def _keepalive_loop(self) -> None:
        while not self._keepalive_stop.wait(_KEEPALIVE_TICK_SECONDS):
            try:
                self._keepalive_tick()
            except Exception:
                logger.debug("Edge STT keepalive tick failed", exc_info=True)

    def _keepalive_tick(self, now: float | None = None) -> bool:
        """Reopen a socket that died, sat idle or ran out of turns.

        Runs between utterances only (a turn in progress holds the lock), so
        the next utterance finds a live socket instead of paying for the
        reconnect - or worse, for a dead socket's silence. Returns True when
        it reconnected.
        """

        if self._closed or not self.reuse_connection:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            moment = time.monotonic() if now is None else float(now)
            if not self._ws_language:
                # Never connected in this session: nothing to keep alive.
                return False
            ws = self._ws
            if ws is None:
                reason = "after a failure"
            elif _socket_is_dead(ws):
                reason = "the socket died"
            elif self._turns >= self.max_turns:
                reason = "turn limit"
            elif self._last_activity_at > 0 and moment - self._last_activity_at > self.reuse_idle_seconds:
                reason = "idle"
            else:
                return False
            if moment < self._reconnect_not_before:
                return False
            language, rate = self._ws_language, self._ws_sample_rate or 16000
            self._close_socket()
            try:
                self._open_socket(language, rate)
            except ASRNetworkError as exc:
                self._reconnect_failures += 1
                backoff = _BACKGROUND_RECONNECT_BACKOFF_SECONDS[
                    min(self._reconnect_failures, len(_BACKGROUND_RECONNECT_BACKOFF_SECONDS)) - 1
                ]
                self._reconnect_not_before = moment + backoff
                logger.info(
                    "Edge STT background reconnect (%s) failed, next try in %.0fs: %s",
                    reason,
                    backoff,
                    exc,
                )
                return False
            self._reconnect_failures = 0
            self._reconnect_not_before = 0.0
            self.link_stats["background_reconnects"] += 1
            logger.info("Edge STT reconnected in the background (%s)", reason)
            return True
        finally:
            self._lock.release()

    def _send_audio_turn(self, pcm: bytes, sample_rate: int) -> str:
        ws = self._ws
        if ws is None:
            raise ASRProviderError("Edge speech recognition is not connected")

        request_id = uuid.uuid4().hex
        self._stream_id += 1
        self._turns += 1
        stream_id = str(self._stream_id)

        continuation_tag = self._service_tag if self._turns > 1 else None
        ws.send(
            speech_context_message(
                request_id,
                stream_id=stream_id,
                previous_service_tag=continuation_tag,
                offset_ticks=(
                    bytes_to_offset_ticks(self._bytes_sent, sample_rate)
                    if continuation_tag
                    else None
                ),
            )
        )
        ws.send(
            binary_message(
                "audio",
                request_id,
                wav_header(sample_rate),
                stream_id=stream_id,
                content_type="audio/x-wav",
            )
        )

        chunk_bytes = max(2, int(sample_rate * _AUDIO_CHUNK_SECONDS) * 2)
        for offset in range(0, len(pcm), chunk_bytes):
            if self._cancel.is_set():
                raise ASRProviderError("Edge speech recognition was cancelled")
            block = pcm[offset : offset + chunk_bytes]
            ws.send(binary_message("audio", request_id, block, stream_id=stream_id))
            self._bytes_sent += len(block)

        silence = b"\x00\x00" * int(sample_rate * self.trailing_silence_seconds)
        if silence:
            ws.send(binary_message("audio", request_id, silence, stream_id=stream_id))
            self._bytes_sent += len(silence)
        # Empty payload = end of stream. This is what makes the service finalize
        # immediately instead of waiting out its own segmentation timeout.
        ws.send(binary_message("audio", request_id, b"", stream_id=stream_id))

        return self._await_phrase(len(pcm) / float(2 * max(1, sample_rate)))

    def _turn_timeout(self, audio_seconds: float) -> float:
        """How long one turn may take for a clip of ``audio_seconds``."""

        budget = _TURN_TIMEOUT_BASE_SECONDS + max(0.0, float(audio_seconds))
        return max(0.1, min(self.recognition_timeout_seconds, budget))

    def _await_phrase(self, audio_seconds: float = 0.0) -> str:
        ws = self._ws
        started = time.monotonic()
        deadline = started + self._turn_timeout(audio_seconds)
        first_deadline = started + min(self.first_message_timeout_seconds, deadline - started)
        received_any = False
        text = ""
        self.link_stats["turns"] += 1
        while True:
            if self._cancel.is_set():
                raise ASRProviderError("Edge speech recognition was cancelled")
            now = time.monotonic()
            if now >= deadline:
                self.link_stats["timeouts"] += 1
                self._emit("timeout", seconds=now - started)
                raise ASRNetworkError(
                    "Edge speech recognition timed out before returning a result"
                )
            if not received_any and now >= first_deadline:
                self.link_stats["stalls"] += 1
                raise _SocketStalled(
                    f"no answer within {first_deadline - started:.1f}s of sending the turn"
                )
            limit = deadline if received_any else first_deadline
            wait = max(0.01, min(_RECV_SLICE_SECONDS, limit - now))
            try:
                raw = ws.recv(timeout=wait)
            except TimeoutError:
                # A socket that gives up sooner than asked (a stub, a closed
                # pipe) must not turn this loop into a spin.
                elapsed = time.monotonic() - now
                if elapsed < wait:
                    time.sleep(min(0.05, wait - elapsed))
                continue
            if not received_any:
                self._record_first_message((time.monotonic() - started) * 1000.0)
            received_any = True

            path, body = parse_message(raw)
            if path == "turn.start":
                context = parse_json_body(body).get("context")
                if isinstance(context, Mapping):
                    tag = context.get("serviceTag")
                    if isinstance(tag, str) and tag:
                        self._service_tag = tag
            elif path == "speech.phrase":
                payload = parse_json_body(body)
                status = str(payload.get("RecognitionStatus") or "")
                if status and status != "Success":
                    # NoMatch/InitialSilenceTimeout are ordinary outcomes for a
                    # segment that held no speech.
                    logger.debug("Edge STT returned status %s", status)
                    text = ""
                else:
                    text = str(payload.get("DisplayText") or "")
            elif path == "turn.end":
                self._last_activity_at = time.monotonic()
                return text
        return text

    # --------------------------------------------------------------- provider
    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        del progress_callback

    def prewarm(self) -> bool:
        """Open the socket ahead of the first utterance."""

        if self._closed:
            return False
        with self._lock:
            try:
                if self._needs_reconnect(self.language, 16000):
                    self._close_socket()
                    self._open_socket(self.language, 16000)
                return True
            except Exception as exc:
                logger.info("Edge STT prewarm failed: %s", exc)
                self._close_socket()
                return False

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        if not is_final:
            return ""
        if self._closed:
            raise ASRProviderError("Edge speech recognition is closed")

        pcm = _pcm16_bytes(audio)
        rate = int(sample_rate) or 16000
        max_bytes = int(_MAX_UTTERANCE_SECONDS * rate) * 2
        if len(pcm) > max_bytes:
            pcm = pcm[:max_bytes]
        if not pcm:
            return ""

        target_language = _language_code(language or self.language, self.language)
        self._cancel.clear()

        with self._lock:
            text = self._recognize_with_retry(pcm, rate, target_language)

        text = clean_asr_text(text)
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=target_language)
        return text

    def _recognize_with_retry(self, pcm: bytes, rate: int, language: str) -> str:
        """Recognize once, retrying on a socket that died between utterances.

        A reused connection can be closed by the service at any time, and the
        failure only surfaces on the next send. One clean reconnect keeps that
        invisible instead of losing the utterance.
        """

        retried = False
        for attempt in (1, 2):
            try:
                if self._needs_reconnect(language, rate):
                    self._close_socket()
                    self._open_socket(language, rate)
            except ASRNetworkError as exc:
                if attempt == 2 or self._cancel.is_set():
                    raise
                logger.info("Edge STT could not connect; retrying once")
                retried = True
                self._emit("reconnecting", reason="connect", error=str(exc))
                continue
            try:
                text = self._send_audio_turn(pcm, rate)
            except ASRProviderError:
                raise
            except ASRNetworkError:
                # The socket answered and then went quiet for the whole
                # budget: the service is slow, not the socket dead. A second
                # full wait would only double the player's silence.
                self._close_socket()
                raise
            except Exception as exc:
                self._close_socket()
                self.link_stats["mid_utterance_failures"] += 1
                if attempt == 2 or self._cancel.is_set():
                    raise ASRNetworkError(
                        f"Edge speech recognition failed: {exc}"
                    ) from exc
                logger.info(
                    "Edge STT connection failed mid-utterance; retrying once (%s)",
                    type(exc).__name__,
                )
                retried = True
                self._emit("reconnecting", reason=type(exc).__name__, error=str(exc))
                continue
            if retried:
                self._emit("recovered", attempt=attempt)
            return text
        return ""

    def cancel_pending_requests(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self._closed = True
        self._cancel.set()
        self._keepalive_stop.set()
        with self._lock:
            self._close_socket()
        if self.link_stats["connections"] or self.link_stats["connect_failures"]:
            # One line per session so a player's log says how the route did.
            logger.info("Edge STT link summary: %s", self.link_summary())

    @property
    def is_loaded(self) -> bool:
        return True


def diagnose_link(
    config: Mapping[str, object] | None,
    *,
    sample_rate: int = 16000,
    connect_module: Any = None,
) -> dict[str, object]:
    """Measure the route to the service: connect, first answer, two turns.

    Sends a short silent turn twice on one socket, the way a session does,
    and reports milliseconds plus a verdict ("good", "slow", "unstable",
    "failed") for the settings page. Never raises.
    """

    provider = EdgeSTTASRProvider(config)
    if connect_module is not None:
        # The provider asks for the connect function each time; hand it this one.
        provider._connect_module = lambda: connect_module  # type: ignore[method-assign]
    # A one-off probe must not leave a keepalive thread behind.
    provider.reuse_connection = False
    result: dict[str, object] = {
        "connect_ms": None,
        "first_message_ms": None,
        "turn_ms": None,
        "second_turn_ms": None,
        "verdict": "failed",
        "error": "",
        "proxy": system_proxy_url() if provider.use_system_proxy else "",
    }
    pcm = b"\x00\x00" * int(sample_rate * _LINK_TEST_SILENCE_SECONDS)
    try:
        started = time.monotonic()
        provider._open_socket(provider.language, sample_rate)
        result["connect_ms"] = (time.monotonic() - started) * 1000.0
        started = time.monotonic()
        provider._send_audio_turn(pcm, sample_rate)
        result["turn_ms"] = (time.monotonic() - started) * 1000.0
        result["first_message_ms"] = provider._last_first_message_ms
        started = time.monotonic()
        provider._send_audio_turn(pcm, sample_rate)
        result["second_turn_ms"] = (time.monotonic() - started) * 1000.0
    except _SocketStalled as exc:
        result["error"] = f"no answer from the service: {exc}"
    except Exception as exc:
        result["error"] = str(exc) or type(exc).__name__
    finally:
        provider.close()
    if result["error"]:
        return result
    slowest = max(
        float(result["connect_ms"] or 0.0),
        float(result["first_message_ms"] or 0.0),
        float(result["second_turn_ms"] or 0.0),
    )
    if slowest <= _LINK_GOOD_MS:
        result["verdict"] = "good"
    elif slowest <= _LINK_SLOW_MS:
        result["verdict"] = "slow"
    else:
        result["verdict"] = "unstable"
    return result
