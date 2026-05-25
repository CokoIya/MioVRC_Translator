from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from collections.abc import Mapping
from typing import Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
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

DEFAULT_MODEL = "gemini-2.0-flash"
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
    arr = _normalized_audio(audio)
    if arr.size == 0:
        return b""
    pcm16 = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(max(int(sample_rate or 16000), 1))
        wav_file.writeframes(pcm16.tobytes())
    return buffer.getvalue()


def _encode_pcm16_bytes(audio: np.ndarray) -> bytes:
    arr = _normalized_audio(audio)
    if arr.size == 0:
        return b""
    pcm16 = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
    return pcm16.tobytes()


def _normalized_audio(audio: np.ndarray) -> np.ndarray:
    arr = np.asarray(audio)
    if arr.size == 0:
        return np.asarray([], dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    arr = np.nan_to_num(arr.astype(np.float32, copy=False).flatten())
    if arr.size == 0:
        return arr
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1.5:
        arr = arr / 32768.0
    return arr


def _prompt(language: str, system_instruction: str) -> str:
    language_name = _LANGUAGE_NAMES.get(language, "the spoken language")
    instruction = system_instruction.strip() or (
        "You are a speech-to-text engine. Output only the transcription. "
        "Do not translate, summarize, explain, or answer."
    )
    return f"{instruction}\nLanguage hint: {language_name}. Return only the transcript."


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raise in caller thread
            result["error"] = exc

    thread = threading.Thread(target=_target, daemon=True, name="gemini-live-asr")
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result.get("value")


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
        self._corrector = corrector
        self._client = None
        self._genai = None
        self._lock = threading.RLock()

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
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
                self._client = genai.Client(api_key=self.api_key)
                self._genai = genai
            except ASRError:
                raise
            except Exception as exc:
                _raise_provider_error(exc)
            if progress_callback is not None:
                progress_callback({"stage": "ready", "message": "Gemini Live ASR ready"})

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
        with self._lock:
            if self._client is None:
                self.load()
            genai = self._genai
            started_at = time.monotonic()
            try:
                if self.use_live_api:
                    pcm_bytes = _encode_pcm16_bytes(audio)
                    if not pcm_bytes:
                        return ""
                    raw_text = str(
                        _run_async(
                            self._transcribe_live_once(
                                pcm_bytes=pcm_bytes,
                                sample_rate=sample_rate,
                                language=lang,
                            )
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
                    response = self._client.models.generate_content(
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

        mime_type = f"audio/pcm;rate={max(int(sample_rate or 16000), 1)}"
        session_cm = connect(model=self.live_model, config=config)
        async with session_cm as session:
            blob = genai.types.Blob(data=pcm_bytes, mime_type=mime_type)
            await session.send_realtime_input(audio=blob)
            try:
                await session.send_realtime_input(audio_stream_end=True)
            except TypeError:
                await session.send_realtime_input(activity_end=True)
            return await self._collect_live_transcript(session)

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

    @property
    def is_loaded(self) -> bool:
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
