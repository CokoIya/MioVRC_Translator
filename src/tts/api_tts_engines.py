from __future__ import annotations

import base64
import logging
from collections.abc import Mapping
from typing import Any

import requests

from .api_tts_config import (
    get_tts_api_voice_options,
    resolve_tts_api_config,
)
from .base import BaseTTS, TTSVoice
from .persona_instructions import qwen_tts_model_supports_instructions
from src.translators.factory import _float_setting, _int_setting

logger = logging.getLogger(__name__)

_PROTECTED_SECRET_PREFIX = "dpapi:v1:"

from src.utils.app_paths import writable_app_dir

_HIDDEN_VOICES: set[str] | None = None


def _load_hidden_voices() -> set[str]:
    global _HIDDEN_VOICES
    if _HIDDEN_VOICES is not None:
        return _HIDDEN_VOICES

    import json

    _HIDDEN_VOICES = set()
    try:
        path = writable_app_dir() / "seren.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            voices = data.get("hidden_voices", [])
            if isinstance(voices, list):
                for v in voices:
                    if isinstance(v, str):
                        _HIDDEN_VOICES.add(v)
    except Exception:
        pass

    if not _HIDDEN_VOICES:
        _HIDDEN_VOICES = set()

    if _HIDDEN_VOICES:
        logger.debug("Hidden voices loaded: %s", _HIDDEN_VOICES)
    return _HIDDEN_VOICES


class _APITTSBase(BaseTTS):
    ENGINE_ID = ""
    ENGINE_LABEL = "API TTS"
    AUTH_HEADER_NAME = "Authorization"
    AUTH_HEADER_PREFIX = "Bearer "

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        resolved = resolve_tts_api_config(self.ENGINE_ID, config)
        self.api_key = str(resolved.get("api_key", "") or "").strip()
        self.region = str(resolved.get("region", "") or "").strip()
        self.base_url = str(resolved.get("base_url", "") or "").strip().rstrip("/")
        self.model = str(resolved.get("model", "") or "").strip()
        self.default_voice = str(resolved.get("voice", "") or "").strip()
        self.language_type_hint = str(
            resolved.get("language_type") or resolved.get("language_hint") or ""
        ).strip()
        self.instructions = str(resolved.get("instructions", "") or "").strip()
        self.optimize_instructions = bool(resolved.get("optimize_instructions", True))
        self.timeout_seconds = _float_setting(
            resolved.get("timeout_seconds"), 30.0, minimum=3.0, maximum=120.0
        )
        self.max_retries = _int_setting(resolved.get("max_retries"), 0, minimum=0, maximum=3)
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=self.max_retries)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def is_available(self) -> bool:
        return bool(self.base_url and self.model)

    def get_available_voices(self) -> list[TTSVoice]:
        voices: list[TTSVoice] = []
        hidden = _load_hidden_voices()
        for voice_id, name, language, gender, locale in get_tts_api_voice_options(self.ENGINE_ID):
            if voice_id in hidden:
                continue
            voices.append(
                TTSVoice(
                    id=voice_id,
                    name=name,
                    language=language,
                    gender=gender,
                    locale=locale,
                )
            )
        return voices

    def _auth_headers(self) -> dict[str, str]:
        self._require_api_key()
        auth_value = (
            f"{self.AUTH_HEADER_PREFIX}{self.api_key}"
            if self.AUTH_HEADER_PREFIX
            else self.api_key
        )
        return {
            self.AUTH_HEADER_NAME: auth_value,
            "Content-Type": "application/json",
        }

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(f"{self.ENGINE_LABEL} API Key is not configured")
        if self.api_key.startswith(_PROTECTED_SECRET_PREFIX):
            raise RuntimeError(
                f"{self.ENGINE_LABEL} API Key is still encrypted and cannot be used"
            )

    def _request_json_audio(self, url: str, payload: Mapping[str, object]) -> bytes:
        response = self._session.post(
            url,
            headers=self._auth_headers(),
            json=dict(payload),
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            detail = _response_error_detail(response)
            message = f"{self.ENGINE_LABEL} API request failed"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc

        content = bytes(response.content or b"")
        content_type = str(response.headers.get("content-type", "") or "").lower()
        if _looks_like_audio(content, content_type):
            return content

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned non-audio data") from exc
        audio = self._extract_audio_from_payload(data)
        if not audio:
            raise RuntimeError(f"{self.ENGINE_LABEL} API returned no audio data")
        return audio

    def _extract_audio_from_payload(self, payload: Mapping[str, object]) -> bytes:
        data_value = _first_path_value(
            payload,
            (
                ("choices", 0, "message", "audio", "data"),
                ("choices", 0, "message", "audio"),
                ("output", "audio", "data"),
                ("audio", "data"),
                ("data",),
            ),
        )
        audio = _decode_audio_data(data_value)
        if audio:
            return audio

        url_value = _first_path_value(
            payload,
            (
                ("choices", 0, "message", "audio", "url"),
                ("output", "audio", "url"),
                ("output", "url"),
                ("audio", "url"),
                ("url",),
            ),
        )
        audio_url = str(url_value or "").strip()
        if audio_url.startswith(("http://", "https://")):
            return self._download_audio(audio_url)
        return b""

    def _download_audio(self, url: str) -> bytes:
        response = self._session.get(url, timeout=self.timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            detail = _response_error_detail(response)
            message = f"{self.ENGINE_LABEL} audio download failed"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc
        audio = bytes(response.content or b"")
        if not audio:
            raise RuntimeError(f"{self.ENGINE_LABEL} audio download returned empty audio")
        return audio


class MimoTTS(_APITTSBase):
    ENGINE_ID = "mimo_tts"
    ENGINE_LABEL = "MiMo TTS"
    AUTH_HEADER_NAME = "api-key"
    AUTH_HEADER_PREFIX = ""

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")
        clean_voice = str(voice or self.default_voice or "").strip()
        if not clean_voice:
            raise ValueError("Voice ID cannot be empty")

        payload = {
            "model": self.model,
            "modalities": ["text", "audio"],
            "audio": {"voice": clean_voice, "format": "wav"},
            "messages": [
                {
                    "role": "user",
                    "content": "Read the following assistant message aloud exactly.",
                },
                {"role": "assistant", "content": clean_text},
            ],
        }
        try:
            return self._request_json_audio(f"{self.base_url}/chat/completions", payload)
        except Exception as exc:
            logger.error("MiMo TTS synthesis failed: %s", exc)
            raise RuntimeError(f"MiMo TTS synthesis failed: {exc}") from exc


class QwenTTS(_APITTSBase):
    ENGINE_ID = "qwen_tts"
    ENGINE_LABEL = "Qwen TTS"

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")
        clean_voice = str(voice or self.default_voice or "").strip()
        if not clean_voice:
            raise ValueError("Voice ID cannot be empty")

        payload = {
            "model": self.model,
            "input": {
                "text": clean_text,
                "voice": clean_voice,
                "language_type": _qwen_language_type(clean_text, self.language_type_hint),
            },
        }
        if self.instructions and qwen_tts_model_supports_instructions(self.model):
            payload["input"]["instructions"] = self.instructions
            payload["input"]["optimize_instructions"] = self.optimize_instructions
        try:
            return self._request_json_audio(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                payload,
            )
        except Exception as exc:
            logger.error("Qwen TTS synthesis failed: %s", exc)
            raise RuntimeError(f"Qwen TTS synthesis failed: {exc}") from exc


def _looks_like_audio(content: bytes, content_type: str) -> bool:
    if not content:
        return False
    if content_type.startswith("audio/"):
        return True
    return content.startswith(b"RIFF") or content.startswith(b"ID3") or (
        len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    )


def _first_path_value(payload: Any, paths: tuple[tuple[object, ...], ...]) -> Any:
    for path in paths:
        current = payload
        found = True
        for part in path:
            if isinstance(part, int):
                if isinstance(current, list) and 0 <= part < len(current):
                    current = current[part]
                else:
                    found = False
                    break
            elif isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _decode_audio_data(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Mapping):
        return _decode_audio_data(value.get("data") or value.get("audio"))
    text = str(value or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return b""
    if text.startswith("data:"):
        _meta, _sep, text = text.partition(",")
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return b""


def _response_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.text or "").strip()[:500]
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("code") or error).strip()
        if error:
            return str(error).strip()
        for key in ("message", "msg", "code", "request_id"):
            value = payload.get(key)
            if value:
                return str(value).strip()
    return str(payload).strip()[:500]


def _normalize_qwen_language_type_hint(value: object) -> str:
    hint = str(value or "").strip().lower().replace("_", "-")
    if hint in {"japanese", "ja", "jp", "日本語", "日文", "日语"}:
        return "Japanese"
    if hint in {"chinese", "zh", "zh-cn", "cn", "中文", "简体中文", "中国語"}:
        return "Chinese"
    if hint in {"korean", "ko", "kr", "한국어", "韩语", "韓国語"}:
        return "Korean"
    if hint in {"english", "en", "en-us", "en-gb", "英文", "英语"}:
        return "English"
    return ""


def _qwen_language_type(text: str, language_hint: object = "") -> str:
    value = str(text or "")
    hint = _normalize_qwen_language_type_hint(language_hint)
    if any("\u3040" <= char <= "\u30ff" for char in value):
        return "Japanese"
    if any("\uac00" <= char <= "\ud7af" for char in value):
        return "Korean"
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        if hint in {"Chinese", "Japanese", "Korean"}:
            return hint
        return "Chinese"
    stripped = value.strip()
    if stripped and all(ord(char) < 128 for char in stripped):
        return "English"
    return "Auto"
