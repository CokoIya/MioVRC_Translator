from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import logging
import threading
import time
from collections.abc import Mapping
from typing import Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.audio_encoding import (
    normalized_mono_audio,
    pcm16_bytes,
    pcm16_wav_bytes,
)
from src.asr.base import ASRProvider, ProgressCallback
from src.asr.errors import (
    ASRConfigurationError,
    ASRError,
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRProviderError,
    ASRRateLimitError,
)
from src.asr.text_corrections import LayeredASRCorrector
from src.utils.config_manager import is_protected_secret_blob

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_LIVE_SILENCE_DURATION_MS = 600
_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "yue": "Cantonese",
    "ja": "Japanese",
    "en": "English",
    "ko": "Korean",
    "ru": "Russian",
}
_LANGUAGE_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "cn": "zh",
    "ja-jp": "ja",
    "jp": "ja",
    "jpn": "ja",
    "en-us": "en",
    "en-gb": "en",
    "ko-kr": "ko",
    "kr": "ko",
}


def _cfg(config: Mapping[str, object] | None) -> Mapping[str, object]:
    asr_cfg = (config or {}).get("asr", {}) if isinstance(config, Mapping) else {}
    if not isinstance(asr_cfg, Mapping):
        return {}
    provider_cfg = asr_cfg.get("gemini_live", {})
    return provider_cfg if isinstance(provider_cfg, Mapping) else {}


def _language_code(language: object) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text or text == "auto":
        return ""
    text = _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])
    return text if text in _LANGUAGE_NAMES else ""


def _encode_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    return pcm16_wav_bytes(audio, sample_rate)


def _encode_pcm16_bytes(audio: np.ndarray) -> bytes:
    return pcm16_bytes(audio)


def _normalized_audio(audio: np.ndarray) -> np.ndarray:
    return normalized_mono_audio(audio)


def _prompt(language: str, system_instruction: str) -> str:
    language_name = _LANGUAGE_NAMES.get(language, "the spoken language")
    instruction = system_instruction.strip() or (
        "You are a speech-to-text engine. Output only the transcription. "
        "Do not translate, summarize, explain, or answer."
    )
    return f"{instruction}\nLanguage hint: {language_name}. Return only the transcript."


class _AsyncLoopRunner:
    """One reusable event loop for all Live API sessions owned by a provider."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="gemini-live-event-loop",
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Gemini Live event loop did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._closed.set()

    def run(self, coroutine, *, timeout: float):
        loop = self._loop
        if loop is None or self._closed.is_set():
            if hasattr(coroutine, "close"):
                coroutine.close()
            raise RuntimeError("Gemini Live event loop is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=max(float(timeout), 0.1))
        except FutureTimeoutError:
            future.cancel()
            raise ASRNetworkError("Gemini Live request timed out")

    def close(self) -> None:
        loop = self._loop
        if loop is None or self._closed.is_set():
            return
        loop.call_soon_threadsafe(loop.stop)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)


@dataclass(slots=True)
class _LiveSessionSlot:
    key: tuple[str, str, int]
    context_manager: object
    session: object
    created_at: float
    uses: int = 0
    closed: bool = False


class GeminiLiveASRProvider(ASRProvider):
    provider_id = "gemini-live"
    display_name = "Gemini Live API"
    requires_api_key = True
    supports_partial = False

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        corrector: LayeredASRCorrector | None = None,
    ) -> None:
        provider_cfg = _cfg(config)
        self.api_key = str(provider_cfg.get("api_key", "") or "").strip()
        self.model = str(provider_cfg.get("model", DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        live_model = str(provider_cfg.get("live_model", "") or "").strip()
        self.live_model = live_model or (
            self.model if "live" in self.model.lower() else DEFAULT_LIVE_MODEL
        )
        self.language = _language_code(provider_cfg.get("language", "ja")) or "ja"
        self.system_instruction = str(provider_cfg.get("system_instruction", "") or "").strip()
        self.timeout_seconds = _float_value(
            provider_cfg.get("timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.use_live_api = _bool_value(provider_cfg.get("use_live_api"), True)
        self.live_silence_duration_ms = _int_range(
            provider_cfg.get("live_silence_duration_ms"),
            DEFAULT_LIVE_SILENCE_DURATION_MS,
            200,
            3000,
        )
        self.max_concurrent_transcriptions = _int_range(
            provider_cfg.get("max_concurrent_transcriptions"),
            2,
            1,
            4,
        )
        self._corrector = corrector
        self._client = None
        self._genai = None
        self._lock = threading.RLock()
        self._async_runner: _AsyncLoopRunner | None = None
        self._live_condition: asyncio.Condition | None = None
        self._live_idle: dict[tuple[str, str, int], list[_LiveSessionSlot]] = {}
        self._live_active: dict[int, _LiveSessionSlot] = {}
        self._live_session_count = 0
        self._closing = False
        self._prewarm_thread: threading.Thread | None = None

    def load(
        self,
        progress_callback: Optional[ProgressCallback] = None,
        *,
        prewarm: bool = True,
    ) -> None:
        with self._lock:
            if self._client is not None:
                return
            if not self.api_key:
                raise ASRMissingAPIKeyError("Gemini Live API Key is not configured")
            if is_protected_secret_blob(self.api_key):
                raise ASRMissingAPIKeyError(
                    "Gemini Live API Key is encrypted but could not be decrypted on this host"
                )
            try:
                from google import genai
            except ImportError as exc:
                raise ASRConfigurationError("google-genai package is required for Gemini Live ASR") from exc
            try:
                client = genai.Client(api_key=self.api_key)
                runner = self._async_runner
                if self.use_live_api and self._async_runner is None:
                    runner = _AsyncLoopRunner()
                self._client = client
                self._genai = genai
                self._async_runner = runner
                self._closing = False
            except ASRError:
                raise
            except Exception as exc:
                _raise_provider_error(exc)
            if progress_callback is not None:
                progress_callback({"stage": "ready", "message": "Gemini Live ASR ready"})
            if prewarm and self.use_live_api and self._async_runner is not None:
                self._start_live_prewarm(self._async_runner)

    def _start_live_prewarm(self, runner: _AsyncLoopRunner) -> None:
        if self._prewarm_thread is not None and self._prewarm_thread.is_alive():
            return

        def _prewarm() -> None:
            try:
                runner.run(
                    self._prewarm_live_session(),
                    timeout=min(self.timeout_seconds + 5.0, 15.0),
                )
            except Exception:
                logger.debug("Gemini Live session prewarm failed", exc_info=True)

        self._prewarm_thread = threading.Thread(
            target=_prewarm,
            daemon=True,
            name="gemini-live-prewarm",
        )
        self._prewarm_thread.start()

    async def _prewarm_live_session(self) -> None:
        slot = await self._acquire_live_session(self.language)
        await self._release_live_session(slot, reusable=True)

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        if not is_final:
            return ""
        if np.asarray(audio).size == 0:
            return ""
        lang = _language_code(language) or self.language
        if self._client is None:
            self.load(prewarm=False)
        client = self._client
        genai = self._genai
        if client is None or genai is None:
            raise ASRConfigurationError("Gemini Live client is not loaded")
        started_at = time.monotonic()
        try:
            if self.use_live_api:
                pcm_bytes = _encode_pcm16_bytes(audio)
                if not pcm_bytes:
                    return ""
                runner = self._async_runner
                if runner is None:
                    raise ASRConfigurationError("Gemini Live event loop is unavailable")
                raw_text = str(
                    runner.run(
                        self._transcribe_live_once(
                            pcm_bytes=pcm_bytes,
                            sample_rate=sample_rate,
                            language=lang,
                        ),
                        timeout=self.timeout_seconds + 10.0,
                    )
                    or ""
                )
            else:
                wav_bytes = _encode_wav_bytes(audio, sample_rate)
                if not wav_bytes:
                    return ""
                parts = [
                    _prompt(lang, self.system_instruction),
                    genai.types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                ]
                response = client.models.generate_content(
                    model=self.model,
                    contents=parts,
                )
                raw_text = _response_text(response)
        except Exception as exc:
            _raise_provider_error(exc)
        logger.info(
            "Gemini Live ASR request finished (mode=%s audio_ms=%.0f duration_ms=%.0f)",
            "live" if self.use_live_api else "generate_content",
            len(np.asarray(audio).flatten()) / max(float(sample_rate or 16000), 1.0) * 1000.0,
            (time.monotonic() - started_at) * 1000.0,
        )
        text = clean_asr_text(raw_text)
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=lang or None)
        return text

    async def _transcribe_live_once(
        self,
        *,
        pcm_bytes: bytes,
        sample_rate: int,
        language: str,
    ) -> str:
        genai = self._genai
        client = self._client
        if genai is None or client is None:
            raise ASRConfigurationError("Gemini Live client is not loaded")
        live = getattr(getattr(client, "aio", None), "live", None)
        connect = getattr(live, "connect", None)
        if connect is None:
            raise ASRConfigurationError("google-genai Live API client is not available")
        del connect
        mime_type = f"audio/pcm;rate={max(int(sample_rate or 16000), 1)}"
        for attempt in range(2):
            slot = await self._acquire_live_session(language)
            was_reused = slot.uses > 0
            reusable = False
            try:
                session = slot.session
                blob = genai.types.Blob(data=pcm_bytes, mime_type=mime_type)
                await session.send_realtime_input(audio=blob)
                try:
                    await session.send_realtime_input(audio_stream_end=True)
                except TypeError:
                    await session.send_realtime_input(activity_end=True)
                transcript = await self._collect_live_transcript(session)
                reusable = True
                return transcript
            except Exception:
                if not was_reused or attempt > 0:
                    raise
                logger.debug(
                    "Reconnecting a stale Gemini Live session",
                    exc_info=True,
                )
            finally:
                await self._release_live_session(slot, reusable=reusable)
        raise ASRNetworkError("Gemini Live session could not be refreshed")

    def _live_session_key(self, language: str) -> tuple[str, str, int]:
        instruction_language = language if self.system_instruction else ""
        return (
            instruction_language,
            self.live_model,
            self.live_silence_duration_ms,
        )

    def _live_config(self, language: str) -> dict[str, object]:
        language_name = _LANGUAGE_NAMES.get(language, "the spoken language")
        config: dict[str, object] = {
            "response_modalities": ["AUDIO"],
            "input_audio_transcription": {},
            "realtime_input_config": {
                "automatic_activity_detection": {
                    "disabled": False,
                    "silence_duration_ms": self.live_silence_duration_ms,
                }
            },
        }
        if self.system_instruction:
            config["system_instruction"] = (
                f"{self.system_instruction}\nLanguage hint: {language_name}."
            )
        return config

    async def _open_live_session(
        self,
        key: tuple[str, str, int],
        language: str,
    ) -> _LiveSessionSlot:
        client = self._client
        live = getattr(getattr(client, "aio", None), "live", None)
        connect = getattr(live, "connect", None)
        if connect is None:
            raise ASRConfigurationError("google-genai Live API client is not available")
        context_manager = connect(
            model=self.live_model,
            config=self._live_config(language),
        )
        session = await context_manager.__aenter__()
        return _LiveSessionSlot(
            key=key,
            context_manager=context_manager,
            session=session,
            created_at=time.monotonic(),
        )

    async def _close_live_slot(self, slot: _LiveSessionSlot) -> None:
        if slot.closed:
            return
        slot.closed = True
        exit_method = getattr(slot.context_manager, "__aexit__", None)
        if exit_method is None:
            return
        try:
            await exit_method(None, None, None)
        except Exception:
            logger.debug("Gemini Live session close failed", exc_info=True)

    def _condition(self) -> asyncio.Condition:
        condition = self._live_condition
        if condition is None:
            condition = asyncio.Condition()
            self._live_condition = condition
        return condition

    async def _acquire_live_session(self, language: str) -> _LiveSessionSlot:
        key = self._live_session_key(language)
        condition = self._condition()
        while True:
            expired: list[_LiveSessionSlot] = []
            should_create = False
            async with condition:
                if self._closing:
                    raise ASRConfigurationError("Gemini Live provider is closing")
                idle = self._live_idle.get(key, [])
                while idle:
                    slot = idle.pop()
                    if (
                        not slot.closed
                        and slot.uses < 50
                        and time.monotonic() - slot.created_at < 600.0
                    ):
                        self._live_active[id(slot)] = slot
                        return slot
                    self._live_session_count = max(0, self._live_session_count - 1)
                    expired.append(slot)
                if self._live_session_count < self.max_concurrent_transcriptions:
                    self._live_session_count += 1
                    should_create = True
                else:
                    # Reclaim an idle session configured for another language.
                    for other_key, other_idle in self._live_idle.items():
                        if other_key == key or not other_idle:
                            continue
                        expired.append(other_idle.pop())
                        self._live_session_count = max(
                            0,
                            self._live_session_count - 1,
                        )
                        self._live_session_count += 1
                        should_create = True
                        break
                    if not should_create:
                        await condition.wait()
                        continue

            for slot in expired:
                await self._close_live_slot(slot)
            if not should_create:
                continue
            try:
                slot = await self._open_live_session(key, language)
            except BaseException:
                async with condition:
                    self._live_session_count = max(0, self._live_session_count - 1)
                    condition.notify_all()
                raise
            async with condition:
                if self._closing:
                    self._live_session_count = max(0, self._live_session_count - 1)
                    condition.notify_all()
                    close_after = True
                else:
                    self._live_active[id(slot)] = slot
                    close_after = False
            if close_after:
                await self._close_live_slot(slot)
                raise ASRConfigurationError("Gemini Live provider is closing")
            return slot

    async def _release_live_session(
        self,
        slot: _LiveSessionSlot,
        *,
        reusable: bool,
    ) -> None:
        condition = self._condition()
        should_close = False
        async with condition:
            self._live_active.pop(id(slot), None)
            slot.uses += 1
            if reusable and not self._closing and not slot.closed:
                self._live_idle.setdefault(slot.key, []).append(slot)
            else:
                self._live_session_count = max(0, self._live_session_count - 1)
                should_close = True
            condition.notify_all()
        if should_close:
            await self._close_live_slot(slot)

    async def _close_live_pool(self) -> None:
        condition = self._condition()
        async with condition:
            self._closing = True
            slots = [
                *self._live_active.values(),
                *(
                    slot
                    for idle in self._live_idle.values()
                    for slot in idle
                ),
            ]
            self._live_active.clear()
            self._live_idle.clear()
            self._live_session_count = 0
            condition.notify_all()
        for slot in slots:
            await self._close_live_slot(slot)

    async def _collect_live_transcript(self, session: object) -> str:
        receive = getattr(session, "receive", None)
        if receive is None:
            raise ASRConfigurationError("Gemini Live session does not expose receive()")
        iterator = receive().__aiter__()
        deadline = time.monotonic() + max(self.timeout_seconds, 0.1)
        pieces: list[str] = []

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(iterator.__anext__(), remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as exc:
                raise ASRNetworkError("Gemini Live request timed out") from exc

            text = _live_message_transcript(message)
            if text:
                pieces.append(text)
            if _live_message_turn_complete(message):
                break

        return clean_asr_text(" ".join(piece for piece in pieces if piece))

    def close(self) -> None:
        with self._lock:
            runner = self._async_runner
            client = self._client
            prewarm_thread = self._prewarm_thread
            self._closing = True
            self._client = None
            self._genai = None
            self._async_runner = None
            self._prewarm_thread = None
        if runner is not None:
            try:
                runner.run(self._close_live_pool(), timeout=5.0)
            except Exception:
                logger.debug("Gemini Live pool shutdown failed", exc_info=True)
            finally:
                runner.close()
        if (
            prewarm_thread is not None
            and prewarm_thread is not threading.current_thread()
        ):
            prewarm_thread.join(timeout=1.0)
        close_client = getattr(client, "close", None)
        if callable(close_client):
            try:
                close_client()
            except Exception:
                logger.debug("Gemini client close failed", exc_info=True)
        with self._lock:
            self._live_condition = None
            self._live_idle.clear()
            self._live_active.clear()
            self._live_session_count = 0

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._client is not None


def _float_value(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _int_range(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _bool_value(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _response_text(response: object) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        for part in parts or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text)
    return ""


def _live_message_transcript(message: object) -> str:
    server_content = getattr(message, "server_content", None) or getattr(message, "serverContent", None)
    transcription = None
    if server_content is not None:
        transcription = (
            getattr(server_content, "input_transcription", None)
            or getattr(server_content, "inputTranscription", None)
        )
    if transcription is not None:
        text = getattr(transcription, "text", None)
        if text:
            return str(text)

    text = getattr(message, "text", None)
    if text:
        return str(text)
    candidates = getattr(message, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        for part in parts or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text)
    return ""


def _live_message_turn_complete(message: object) -> bool:
    server_content = getattr(message, "server_content", None) or getattr(message, "serverContent", None)
    if server_content is None:
        return False
    return bool(
        getattr(server_content, "turn_complete", False)
        or getattr(server_content, "turnComplete", False)
    )


def _raise_provider_error(exc: Exception) -> None:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in {401, 403} or "api key" in lowered or "permission" in lowered:
        raise ASRMissingAPIKeyError("Gemini Live authentication failed") from exc
    if status_code == 429 or "rate limit" in lowered or "quota" in lowered:
        raise ASRRateLimitError("Gemini Live rate limit") from exc
    if status_code and int(status_code) >= 500:
        raise ASRNetworkError("Gemini Live service unavailable") from exc
    if "timeout" in lowered or "connection" in lowered or "network" in lowered:
        raise ASRNetworkError(message) from exc
    raise ASRProviderError(message) from exc
