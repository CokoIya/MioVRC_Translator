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
    WSS_HEADERS,
    binary_message,
    build_recognition_url,
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
            minimum=2.0,
            maximum=60.0,
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
        return self._turns >= self.max_turns

    def _open_socket(self, language: str, sample_rate: int) -> None:
        connect = self._connect_module()
        url = build_recognition_url(language, self._clock_skew_seconds)
        started_at = time.monotonic()
        try:
            ws = connect(
                url,
                additional_headers=WSS_HEADERS,
                open_timeout=self.connect_timeout_seconds,
                close_timeout=1.0,
                max_size=None,
            )
        except Exception as exc:
            raise ASRNetworkError(
                f"Could not reach the Edge speech recognition service: {exc}"
            ) from exc

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
        logger.info(
            "Edge STT connected (language=%s rate=%dHz) in %.0fms",
            language,
            sample_rate,
            (time.monotonic() - started_at) * 1000.0,
        )

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

        return self._await_phrase()

    def _await_phrase(self) -> str:
        ws = self._ws
        deadline = time.monotonic() + self.recognition_timeout_seconds
        text = ""
        while True:
            if self._cancel.is_set():
                raise ASRProviderError("Edge speech recognition was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ASRNetworkError(
                    "Edge speech recognition timed out before returning a result"
                )
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError as exc:
                raise ASRNetworkError(
                    "Edge speech recognition timed out before returning a result"
                ) from exc

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

        for attempt in (1, 2):
            try:
                if self._needs_reconnect(language, rate):
                    self._close_socket()
                    self._open_socket(language, rate)
                return self._send_audio_turn(pcm, rate)
            except ASRProviderError:
                raise
            except Exception as exc:
                self._close_socket()
                if attempt == 2 or self._cancel.is_set():
                    if isinstance(exc, ASRNetworkError):
                        raise
                    raise ASRNetworkError(
                        f"Edge speech recognition failed: {exc}"
                    ) from exc
                logger.info(
                    "Edge STT connection failed mid-utterance; retrying once (%s)",
                    type(exc).__name__,
                )
        return ""

    def cancel_pending_requests(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self._closed = True
        self._cancel.set()
        with self._lock:
            self._close_socket()

    @property
    def is_loaded(self) -> bool:
        return True
