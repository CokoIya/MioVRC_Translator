# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""DashScope voice enrollment (cloud voice cloning) for Qwen-Audio-TTS.

Alibaba retired ``qwen-voice-enrollment`` and the ``qwen3-tts-vc`` models on
2026-10-10. Their successor is the ``voice-enrollment`` model, whose target is
a Qwen-Audio-TTS model (``qwen-audio-3.0-tts-flash``). Enrollment no longer
accepts audio bytes: it takes a URL. A local recording is therefore first
placed in Model Studio's temporary storage (``oss://``, kept 48 hours, China
(Beijing) only) and that URL is enrolled; the resulting voice stays valid
after the temporary file expires. New voices are reviewed (``DEPLOYING``)
before they become usable (``OK``).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import requests
from urllib3.util import parse_url

from src.tts.api_tts_config import target_model_from_voice_id
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

ENROLLMENT_MODEL = "voice-enrollment"
ENROLLMENT_PATH = "/services/audio/tts/customization"
UPLOAD_POLICY_PATH = "/uploads"
OSS_RESOURCE_RESOLVE_HEADER = "X-DashScope-OssResourceResolve"
# Voice states reported by the service after review.
VOICE_STATUS_DEPLOYING = "DEPLOYING"
VOICE_STATUS_OK = "OK"
VOICE_STATUS_UNDEPLOYED = "UNDEPLOYED"
_REVIEW_POLL_SECONDS = 2.0
_REVIEW_WAIT_SECONDS = 24.0
# The temporary-storage host: a plain DNS name under aliyuncs.com made of
# lower-case letters, digits, inner hyphens and dots, nothing else.
_UPLOAD_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+aliyuncs\.com")

# DashScope rejects oversized payloads outright.  Guard locally so a rejected
# upload surfaces as an actionable validation error instead of an opaque 400.
MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024
# voice-enrollment needs at least 5 seconds of continuous clear speech.
MIN_REFERENCE_SECONDS = 5.0
MAX_REFERENCE_SECONDS = 60.0
RECOMMENDED_MIN_SECONDS = 10.0
RECOMMENDED_MAX_SECONDS = 20.0
# voice-enrollment accepts 16 kHz and above (24 kHz recommended).
MIN_REFERENCE_SAMPLE_RATE = 16000

_MAX_RESPONSE_BYTES = 256 * 1024
_PROTECTED_SECRET_PREFIX = "dpapi:v1:"
_PREFERRED_NAME_RE = re.compile(r"[^A-Za-z0-9]+")
_MAX_PREFERRED_NAME_LENGTH = 10
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
    # Service region the voice was enrolled in; a voice is not visible to the
    # same key in another region.
    region: str = ""
    # Review state reported by the service: DEPLOYING, OK or UNDEPLOYED.
    status: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "voice_id": self.voice_id,
            "display_name": self.display_name,
            "target_model": self.target_model,
            "created_at": self.created_at,
            "language": self.language,
            "region": self.region,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> "ClonedVoice | None":
        if not isinstance(data, Mapping):
            return None
        voice_id = str(data.get("voice_id") or data.get("voice") or "").strip()
        if not voice_id:
            return None
        target_model = str(data.get("target_model") or "").strip()
        if not target_model:
            target_model = target_model_from_voice_id(voice_id)
        return cls(
            voice_id=voice_id,
            display_name=str(data.get("display_name") or "").strip(),
            target_model=target_model,
            created_at=str(
                data.get("created_at") or data.get("gmt_create") or ""
            ).strip(),
            language=str(data.get("language") or "").strip(),
            region=str(data.get("region") or "").strip(),
            status=str(data.get("status") or "").strip().upper(),
        )




def safe_preferred_name(name: object) -> str:
    """Reduce a player-supplied label to the charset DashScope accepts.

    ``prefix`` allows at most 10 ASCII letters and digits.
    Non-Latin display names (the common case here) collapse to an empty
    string, so fall back to a generated prefix rather than sending a name the
    API would reject.  The readable label stays in local config.
    """

    text = _PREFERRED_NAME_RE.sub("", str(name or "")).strip("_")
    text = text[:_MAX_PREFERRED_NAME_LENGTH]
    if text and not text[0].isalpha():
        text = f"v{text}"[:_MAX_PREFERRED_NAME_LENGTH]
    if not text:
        text = f"mio{int(time.time()) % 10000000}"
    return text[:_MAX_PREFERRED_NAME_LENGTH]


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


_AUDIO_EXTENSIONS = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}


def _validated_reference_audio(data: bytes) -> tuple[bytes, str]:
    """Check the recording and return it with the file extension to upload."""

    if not data:
        raise QwenVoiceEnrollmentError("Reference audio is empty")
    if len(data) > MAX_REFERENCE_AUDIO_BYTES:
        raise QwenVoiceEnrollmentError(
            "Reference audio exceeds the 10 MB limit accepted by the service"
        )
    return data, _AUDIO_EXTENSIONS[_mime_for_audio(data)]


def _trusted_upload_url(url: object) -> str:
    """Return the temporary-storage upload URL rebuilt from checked parts.

    The host must be an HTTPS Alibaba Cloud OSS host (``*.aliyuncs.com``).
    urlsplit and urllib3, which requests connects with, disagree on some
    spellings: urlsplit keeps a backslash inside the host while urllib3 ends
    the host there, so ``https://evil.com\\.aliyuncs.com`` passed a suffix
    check on urlsplit's host but sent the recording and the signed OSS fields
    to evil.com. Both parsers must therefore agree on the host, and the upload
    goes to ``https://{host}[:{port}]{path}`` rebuilt from the checked parts,
    never to the string the service returned. Returns "" when untrusted.
    """

    raw = str(url or "").strip()
    if (
        not raw
        or "\\" in raw
        or not raw.isascii()
        or any(ord(char) <= 32 or ord(char) == 127 for char in raw)
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
        connect_host = (parse_url(raw).host or "").lower()
    except ValueError:
        # urllib3's LocationParseError is a ValueError as well.
        return ""
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or connect_host != host
    ):
        return ""
    host = host.removesuffix(".")
    if not _UPLOAD_HOST_RE.fullmatch(host):
        return ""
    netloc = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit(("https", netloc, parsed.path, "", ""))


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

    def _post(
        self,
        payload: Mapping[str, object],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        self._require_api_key()
        session = self._get_session()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The bounded reader streams the raw body and refuses any
            # compressed encoding, so the response must not be gzipped.
            "Accept-Encoding": "identity",
        }
        headers.update(dict(extra_headers or {}))
        try:
            response = session.post(
                self.endpoint,
                headers=headers,
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

        return self._read_json(response, label="Qwen voice cloning API")

    def _read_json(self, response: object, *, label: str) -> Mapping[str, Any]:
        status_code = 0
        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in _REDIRECT_STATUS_CODES:
                raise QwenVoiceEnrollmentError(f"{label} redirects are not allowed")
            content = read_bounded_requests_response(
                response,
                limit=_MAX_RESPONSE_BYTES,
                label=label,
            )
        except QwenVoiceEnrollmentError:
            raise
        except requests.RequestException as exc:
            # The connection dropped while the body was streaming in. Callers
            # only handle QwenVoiceEnrollmentError; a raw transport error here
            # failed an enrollment whose voice had already been created.
            logger.error(
                "%s reply was interrupted (%s)",
                label,
                safe_exception_summary(exc),
            )
            raise QwenVoiceEnrollmentError(
                "Could not reach the Qwen voice cloning service: the reply was interrupted",
                status_code=status_code if status_code >= 400 else None,
            ) from exc
        except RuntimeError as exc:
            # The bounded reader refuses oversized, compressed or truncated
            # bodies with a RuntimeError of its own.
            logger.error(
                "%s reply could not be read (%s)",
                label,
                safe_exception_summary(exc),
            )
            raise QwenVoiceEnrollmentError(
                f"{label} returned an unreadable response",
                status_code=status_code if status_code >= 400 else None,
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        try:
            data = json.loads(content.decode("utf-8-sig") or "{}")
        except (UnicodeDecodeError, ValueError) as exc:
            # A gateway error page (HTML) still carries a meaningful status.
            raise QwenVoiceEnrollmentError(
                f"{label} returned an unreadable response",
                status_code=status_code if status_code >= 400 else None,
            ) from exc
        if not isinstance(data, Mapping):
            raise QwenVoiceEnrollmentError(f"{label} returned invalid data")
        if status_code >= 400:
            provider_code = _provider_code(data)
            logger.error(
                "%s rejected the request (status=%s code=%s)",
                label,
                status_code,
                provider_code or "unknown",
            )
            raise QwenVoiceEnrollmentError(
                _failure_message(status_code, data),
                status_code=status_code,
                provider_code=provider_code,
            )
        return data

    def upload_reference_audio(self, audio_data: bytes) -> str:
        """Put a recording into Model Studio's temporary storage (oss:// URL).

        Voice enrollment only takes a URL. The temporary file is bound to the
        enrollment model and this account and is deleted after 48 hours; the
        enrolled voice itself does not depend on it afterwards.
        """

        self._require_api_key()
        data, extension = _validated_reference_audio(audio_data)
        session = self._get_session()
        try:
            policy_response = session.get(
                f"{self._base_url}{UPLOAD_POLICY_PATH}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                params={"action": "getPolicy", "model": ENROLLMENT_MODEL},
                timeout=self._timeout_seconds,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(
                "Voice cloning upload credential request failed (%s)",
                safe_exception_summary(exc),
            )
            raise QwenVoiceEnrollmentError(
                "Could not reach the Qwen voice cloning service"
            ) from exc
        policy = self._read_json(policy_response, label="Voice cloning upload credential")
        fields = policy.get("data") if isinstance(policy.get("data"), Mapping) else {}
        upload_url = _trusted_upload_url(fields.get("upload_host"))
        upload_dir = str(fields.get("upload_dir") or "").strip("/")
        required = ("oss_access_key_id", "signature", "policy")
        if (
            not upload_url
            or not upload_dir
            or any(not str(fields.get(name) or "") for name in required)
        ):
            raise QwenVoiceEnrollmentError(
                "Voice cloning upload credential is incomplete. Voice cloning "
                "is only available in the China (Beijing) region"
            )
        key = f"{upload_dir}/mio-reference-{int(time.time() * 1000)}.{extension}"
        form = {
            "OSSAccessKeyId": (None, str(fields["oss_access_key_id"])),
            "Signature": (None, str(fields["signature"])),
            "policy": (None, str(fields["policy"])),
            "x-oss-object-acl": (None, str(fields.get("x_oss_object_acl") or "private")),
            "x-oss-forbid-overwrite": (None, str(fields.get("x_oss_forbid_overwrite") or "true")),
            "key": (None, key),
            "success_action_status": (None, "200"),
            "file": (f"reference.{extension}", data),
        }
        try:
            upload_response = session.post(
                upload_url,
                files=form,
                timeout=self._timeout_seconds,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(
                "Voice cloning reference upload failed (%s)",
                safe_exception_summary(exc),
            )
            raise QwenVoiceEnrollmentError(
                "Could not upload the reference recording"
            ) from exc
        try:
            status_code = int(getattr(upload_response, "status_code", 0) or 0)
        finally:
            close = getattr(upload_response, "close", None)
            if callable(close):
                close()
        if status_code != 200:
            raise QwenVoiceEnrollmentError(
                f"Uploading the reference recording failed (HTTP {status_code})",
                status_code=status_code,
            )
        return f"oss://{key}"

    def create_voice(
        self,
        audio_data: bytes,
        *,
        target_model: str,
        display_name: str = "",
        language: str = "",
        wait_for_review: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> ClonedVoice:
        """Upload a recording, enroll it, and wait briefly for the review."""

        model = str(target_model or "").strip()
        if not model:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning requires a target synthesis model"
            )
        oss_url = self.upload_reference_audio(audio_data)
        input_payload: dict[str, object] = {
            "action": "create_voice",
            "target_model": model,
            "prefix": safe_preferred_name(display_name),
            "url": oss_url,
        }
        language_hint = str(language or "").strip().lower()
        if language_hint:
            input_payload["language_hints"] = [language_hint]
        data = self._post(
            {"model": ENROLLMENT_MODEL, "input": input_payload},
            extra_headers={OSS_RESOURCE_RESOLVE_HEADER: "enable"},
        )
        output = data.get("output")
        voice_id = ""
        if isinstance(output, Mapping):
            voice_id = str(output.get("voice_id") or output.get("voice") or "").strip()
        if not voice_id:
            raise QwenVoiceEnrollmentError(
                "Qwen voice cloning API did not return a voice id"
            )
        logger.info("Registered a cloned voice for target model %s", model)
        voice = ClonedVoice(
            voice_id=voice_id,
            display_name=str(display_name or "").strip(),
            target_model=model,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            language=language_hint,
            status=VOICE_STATUS_DEPLOYING,
        )
        if wait_for_review:
            voice = self.wait_for_review(voice, sleep=sleep)
        if voice.status == VOICE_STATUS_UNDEPLOYED:
            # A rejected voice can never speak; release its quota slot now
            # instead of leaving an unusable entry on the account.
            try:
                self.delete_voice(voice.voice_id)
            except QwenVoiceEnrollmentError:
                logger.warning("Could not delete a voice rejected during review")
            raise QwenVoiceEnrollmentError(
                "The service rejected this recording during review",
                provider_code=VOICE_STATUS_UNDEPLOYED,
            )
        return voice

    def wait_for_review(
        self,
        voice: ClonedVoice,
        *,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = _REVIEW_WAIT_SECONDS,
    ) -> ClonedVoice:
        """Poll ``query_voice`` until the review finishes or the wait ends."""

        deadline = time.monotonic() + max(0.0, float(timeout_s))
        current = voice
        while True:
            try:
                status = self.query_voice_status(current.voice_id)
            except QwenVoiceEnrollmentError:
                logger.debug("Voice review status query failed", exc_info=True)
                return current
            if status:
                current = replace(current, status=status)
            if current.status != VOICE_STATUS_DEPLOYING or time.monotonic() >= deadline:
                return current
            sleep(_REVIEW_POLL_SECONDS)

    def query_voice_status(self, voice_id: str) -> str:
        target = str(voice_id or "").strip()
        if not target:
            return ""
        data = self._post(
            {
                "model": ENROLLMENT_MODEL,
                "input": {"action": "query_voice", "voice_id": target},
            }
        )
        output = data.get("output")
        if isinstance(output, Mapping):
            return str(output.get("status") or "").strip().upper()
        return ""

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
                "action": "list_voice",
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

    def list_all_voices(self, *, page_size: int = 100, max_pages: int = 20) -> list[ClonedVoice]:
        """Return every registered voice, following the service's pagination.

        The account may hold up to 1000 voices; reading only the first page
        silently dropped the rest from the local list on every refresh.
        """

        size = max(1, min(int(page_size or 100), 100))
        voices: list[ClonedVoice] = []
        seen: set[str] = set()
        for page_index in range(max(1, int(max_pages))):
            page = self.list_voices(page_size=size, page_index=page_index)
            new_entries = [voice for voice in page if voice.voice_id not in seen]
            for voice in new_entries:
                seen.add(voice.voice_id)
                voices.append(voice)
            if len(page) < size or not new_entries:
                break
        return voices

    def delete_voice(self, voice_id: str) -> None:
        """Remove a registered voice so the account quota is released."""

        target = str(voice_id or "").strip()
        if not target:
            raise QwenVoiceEnrollmentError("No voice was selected for deletion")
        self._post(
            {
                "model": ENROLLMENT_MODEL,
                "input": {"action": "delete_voice", "voice_id": target},
            }
        )
        logger.info("Deleted a cloned voice")


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
    if status_code == 401:
        base = (
            "Qwen voice cloning rejected the API key. Check that the key is "
            "valid and belongs to the selected service region"
        )
    elif status_code == 403:
        base = (
            "Qwen voice cloning access denied (HTTP 403). The key may lack "
            "permission for the model, Model Studio may not be activated, or "
            "only the free tier is allowed"
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
