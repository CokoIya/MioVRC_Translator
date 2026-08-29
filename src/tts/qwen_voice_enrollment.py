# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""DashScope Qwen-TTS voice enrollment (cloud voice cloning).

Enrollment is a separate endpoint from synthesis: a reference recording is
registered once and returns a voice id that an ordinary Qwen TTS synthesis
request then accepts in ``input.voice``.  Only the reference audio is sent;
the service does not take a transcript.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import requests

from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_network import (
    SystemTrustHTTPAdapter,
    configure_requests_session_for_url,
    should_bypass_environment_proxies,
)
from src.utils.secure_http import (
    read_bounded_requests_response,
    validate_api_base_url,
)

logger = logging.getLogger(__name__)

ENROLLMENT_MODEL = "qwen-voice-enrollment"
ENROLLMENT_PATH = "/services/audio/tts/customization"

# DashScope rejects oversized payloads outright.  Guard locally so a rejected
# upload surfaces as an actionable validation error instead of an opaque 400.
MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024
MIN_REFERENCE_SECONDS = 3.0
MAX_REFERENCE_SECONDS = 60.0
RECOMMENDED_MIN_SECONDS = 10.0
RECOMMENDED_MAX_SECONDS = 20.0
MIN_REFERENCE_SAMPLE_RATE = 24000

_MAX_RESPONSE_BYTES = 256 * 1024
_PROTECTED_SECRET_PREFIX = "dpapi:v1:"
_PREFERRED_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")
_MAX_PREFERRED_NAME_LENGTH = 16
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class QwenVoiceEnrollmentError(RuntimeError):
    """Raised when a voice enrollment request cannot be completed.

    Carries the HTTP status and the provider's error code as attributes so
    diagnostics can record what actually failed without logging the provider's
    prose, which may echo request content back.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        # safe_exception_summary reads `code` for the provider error identifier.
        self.code = provider_code or None


@dataclass(frozen=True)
class ClonedVoice:
    """A voice registered with DashScope."""

    voice_id: str
    display_name: str = ""
    target_model: str = ""
    created_at: str = ""
    language: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "voice_id": self.voice_id,
            "display_name": self.display_name,
            "target_model": self.target_model,
            "created_at": self.created_at,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> "ClonedVoice | None":
        if not isinstance(data, Mapping):
            return None
        voice_id = str(data.get("voice_id") or data.get("voice") or "").strip()
        if not voice_id:
            return None
        return cls(
            voice_id=voice_id,
            display_name=str(data.get("display_name") or "").strip(),
            target_model=str(data.get("target_model") or "").strip(),
            created_at=str(
                data.get("created_at") or data.get("gmt_create") or ""
            ).strip(),
            language=str(data.get("language") or "").strip(),
        )


def safe_preferred_name(name: object) -> str:
    """Reduce a player-supplied label to the charset DashScope accepts.

    ``preferred_name`` allows at most 16 alphanumeric/underscore characters.
    Non-Latin display names (the common case here) collapse to an empty
    string, so fall back to a generated prefix rather than sending a name the
    API would reject.  The readable label stays in local config.
    """

    text = _PREFERRED_NAME_RE.sub("", str(name or "")).strip("_")
    text = text[:_MAX_PREFERRED_NAME_LENGTH]
    if text and not text[0].isalpha():
        text = f"v{text}"[:_MAX_PREFERRED_NAME_LENGTH]
    if not text:
        text = f"mio{int(time.time()) % 100000000}"
    return text


def reference_audio_requirements() -> dict[str, object]:
    """Expose the documented reference-audio limits for UI validation."""

    return {
        "min_seconds": MIN_REFERENCE_SECONDS,
        "max_seconds": MAX_REFERENCE_SECONDS,
        "recommended_min_seconds": RECOMMENDED_MIN_SECONDS,
        "recommended_max_seconds": RECOMMENDED_MAX_SECONDS,
        "min_sample_rate": MIN_REFERENCE_SAMPLE_RATE,
        "max_bytes": MAX_REFERENCE_AUDIO_BYTES,
        "channels": 1,
    }


def _mime_for_audio(data: bytes) -> str:
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    raise QwenVoiceEnrollmentError(
        "Reference audio must be a WAV, MP3, or M4A recording"
    )


def _audio_data_url(data: bytes) -> str:
    if not data:
        raise QwenVoiceEnrollmentError("Reference audio is empty")
    if len(data) > MAX_REFERENCE_AUDIO_BYTES:
        raise QwenVoiceEnrollmentError(
            "Reference audio exceeds the 10 MB limit accepted by the service"
        )
    mime = _mime_for_audio(data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class QwenVoiceEnrollmentClient:
    """Create, list, and delete DashScope custom voices."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        raw_base_url = str(base_url or "").strip().rstrip("/")
        if not raw_base_url:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning service endpoint is not configured"
            )
        self._base_url = validate_api_base_url(
            raw_base_url,
            label="Qwen voice cloning API",
            allow_private_http=should_bypass_environment_proxies(raw_base_url),
        )
        # Enrollment uploads an entire recording, so it needs a longer ceiling
        # than the per-utterance synthesis timeout.
        self._timeout_seconds = max(5.0, min(float(timeout_seconds or 60.0), 300.0))
        self._session: requests.Session | None = None

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}{ENROLLMENT_PATH}"

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                logger.debug("Failed to close voice enrollment session", exc_info=True)

    def __enter__(self) -> "QwenVoiceEnrollmentClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise QwenVoiceEnrollmentError("Qwen voice cloning requires an API key")
        if self._api_key.startswith(_PROTECTED_SECRET_PREFIX):
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API key is still encrypted and cannot be used"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in self._api_key):
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API key contains invalid characters"
            )

    def _get_session(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            configure_requests_session_for_url(session, self._base_url)
            adapter = SystemTrustHTTPAdapter(
                max_retries=0,
                pool_connections=1,
                pool_maxsize=1,
                pool_block=False,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session
        return self._session

    def _post(self, payload: Mapping[str, object]) -> Mapping[str, Any]:
        self._require_api_key()
        session = self._get_session()
        try:
            response = session.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # The bounded reader streams the raw body and refuses any
                    # compressed encoding, so the response must not be gzipped.
                    "Accept-Encoding": "identity",
                },
                json=dict(payload),
                timeout=self._timeout_seconds,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(
                "Qwen voice enrollment request failed (%s)",
                safe_exception_summary(exc),
            )
            raise QwenVoiceEnrollmentError(
                "Could not reach the Qwen voice cloning service"
            ) from exc

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in _REDIRECT_STATUS_CODES:
                raise QwenVoiceEnrollmentError(
                    "Qwen voice cloning API redirects are not allowed"
                )
            content = read_bounded_requests_response(
                response,
                limit=_MAX_RESPONSE_BYTES,
                label="Qwen voice cloning API response",
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        try:
            data = json.loads(content.decode("utf-8-sig") or "{}")
        except (UnicodeDecodeError, ValueError) as exc:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API returned an unreadable response"
            ) from exc
        if not isinstance(data, Mapping):
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API returned invalid data"
            )
        if status_code >= 400:
            provider_code = _provider_code(data)
            logger.error(
                "Qwen voice enrollment rejected the request (status=%s code=%s)",
                status_code,
                provider_code or "unknown",
            )
            raise QwenVoiceEnrollmentError(
                _failure_message(status_code, data),
                status_code=status_code,
                provider_code=provider_code,
            )
        return data

    def create_voice(
        self,
        audio_data: bytes,
        *,
        target_model: str,
        display_name: str = "",
    ) -> ClonedVoice:
        """Register a reference recording and return the new voice."""

        model = str(target_model or "").strip()
        if not model:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning requires a target synthesis model"
            )
        payload = {
            "model": ENROLLMENT_MODEL,
            "input": {
                "action": "create",
                "target_model": model,
                "preferred_name": safe_preferred_name(display_name),
                "audio": {"data": _audio_data_url(audio_data)},
            },
        }
        data = self._post(payload)
        output = data.get("output")
        voice_id = ""
        if isinstance(output, Mapping):
            voice_id = str(output.get("voice") or output.get("voice_id") or "").strip()
        if not voice_id:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API did not return a voice id"
            )
        logger.info("Registered a Qwen cloned voice for target model %s", model)
        return ClonedVoice(
            voice_id=voice_id,
            display_name=str(display_name or "").strip(),
            target_model=model,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def list_voices(
        self,
        *,
        page_size: int = 50,
        page_index: int = 0,
    ) -> list[ClonedVoice]:
        """Return the voices registered under the configured API key."""

        payload = {
            "model": ENROLLMENT_MODEL,
            "input": {
                "action": "list",
                "page_size": max(1, min(int(page_size or 50), 100)),
                "page_index": max(0, int(page_index or 0)),
            },
        }
        data = self._post(payload)
        output = data.get("output")
        entries = output.get("voice_list") if isinstance(output, Mapping) else None
        voices: list[ClonedVoice] = []
        if isinstance(entries, list):
            for entry in entries:
                voice = ClonedVoice.from_dict(
                    entry if isinstance(entry, Mapping) else None
                )
                if voice is not None:
                    voices.append(voice)
        return voices

    def delete_voice(self, voice_id: str) -> None:
        """Remove a registered voice so the account quota is released."""

        target = str(voice_id or "").strip()
        if not target:
            raise QwenVoiceEnrollmentError("No voice was selected for deletion")
        self._post(
            {
                "model": ENROLLMENT_MODEL,
                "input": {"action": "delete", "voice": target},
            }
        )
        logger.info("Deleted a Qwen cloned voice")


def _provider_code(data: Mapping[str, Any]) -> str:
    for key in ("code", "error_code", "Code"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:64]
    return ""


def _failure_message(status_code: int, data: Mapping[str, Any]) -> str:
    detail = ""
    for key in ("message", "error_message", "msg"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            detail = value.strip()
            break
    code = _provider_code(data)
    if status_code in (401, 403):
        base = (
            "Qwen voice cloning rejected the API key. Check that the key is "
            "valid and belongs to the selected service region"
        )
    elif status_code == 429:
        base = "Qwen voice cloning rate limit exceeded (HTTP 429)"
    elif status_code == 404:
        base = "Qwen voice cloning endpoint not found (HTTP 404)"
    elif status_code >= 500:
        base = f"Qwen voice cloning provider failure (HTTP {status_code})"
    else:
        base = f"Qwen voice cloning request failed (HTTP {status_code})"
    parts = [base]
    if code:
        parts.append(code)
    if detail:
        parts.append(detail)
    return ": ".join(parts)
