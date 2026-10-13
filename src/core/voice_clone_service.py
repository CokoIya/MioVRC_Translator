# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Config-facing operations for cloud voice cloning.

Wraps :mod:`src.tts.qwen_voice_enrollment` so the settings UIs deal in config
dictionaries instead of HTTP details, and so the local record of registered
voices stays consistent with the service.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace

from src.tts.api_tts_config import (
    cloned_voice_is_usable,
    normalize_cloned_voices,
    qwen_vc_model_is_supported,
    resolve_tts_api_config,
)
from src.tts.qwen_voice_enrollment import (
    VOICE_STATUS_UNDEPLOYED,
    ClonedVoice,
    QwenVoiceEnrollmentClient,
    QwenVoiceEnrollmentError,
)

logger = logging.getLogger(__name__)

VOICE_CLONE_ENGINE_ID = "qwen_vc"

# Languages voice-enrollment accepts as a hint for the recording. A hint that
# does not match the audio is ignored by the service, so a wrong guess is
# harmless; no hint at all defaults to Chinese.
_ENROLLMENT_LANGUAGE_HINTS = frozenset(
    {
        "zh", "en", "fr", "de", "ja", "ko", "ru", "pt", "th", "id", "vi",
        "it", "es", "ms", "fil", "ar",
    }
)
_ENROLLMENT_LANGUAGE_ALIASES = {"yue": "zh", "tl": "fil", "in": "id"}


class VoiceCloneNotConfiguredError(QwenVoiceEnrollmentError):
    """Raised when cloning is attempted before an API key is supplied."""


class VoiceCloneConsentRequiredError(QwenVoiceEnrollmentError):
    """Raised when a recording would be uploaded without explicit consent."""


class VoiceCloneModelUnsupportedError(QwenVoiceEnrollmentError):
    """Raised when the selected synthesis model cannot use cloned voices."""


def recording_language_hint(config: Mapping[str, object] | None) -> str:
    """The language the player speaks, as a voice-enrollment hint, or ""."""

    trans_cfg = config.get("translation") if isinstance(config, Mapping) else None
    source = (
        str(trans_cfg.get("source_language") or "")
        if isinstance(trans_cfg, Mapping)
        else ""
    )
    base = source.strip().lower().replace("_", "-").split("-", 1)[0]
    base = _ENROLLMENT_LANGUAGE_ALIASES.get(base, base)
    return base if base in _ENROLLMENT_LANGUAGE_HINTS else ""


def voice_clone_config(config: Mapping[str, object] | None) -> dict[str, object]:
    """Return the resolved cloning engine config from a full app config."""

    tts_cfg = config.get("tts") if isinstance(config, Mapping) else None
    engine_cfg = (
        tts_cfg.get(VOICE_CLONE_ENGINE_ID) if isinstance(tts_cfg, Mapping) else None
    )
    return resolve_tts_api_config(
        VOICE_CLONE_ENGINE_ID,
        engine_cfg if isinstance(engine_cfg, Mapping) else {},
    )


def stored_voices(config: Mapping[str, object] | None) -> tuple[dict[str, str], ...]:
    """Return the cloned voices remembered locally."""

    return normalize_cloned_voices(voice_clone_config(config).get("custom_voices"))


def has_upload_consent(config: Mapping[str, object] | None) -> bool:
    return bool(voice_clone_config(config).get("upload_consent"))


def _client(config: Mapping[str, object] | None) -> QwenVoiceEnrollmentClient:
    resolved = voice_clone_config(config)
    api_key = str(resolved.get("api_key", "") or "").strip()
    if not api_key:
        raise VoiceCloneNotConfiguredError(
            "Voice cloning needs a DashScope API key. Enter one in Settings first."
        )
    return QwenVoiceEnrollmentClient(
        api_key=api_key,
        base_url=str(resolved.get("base_url", "") or ""),
    )


def _mutable_engine_config(config: dict) -> dict:
    tts_cfg = config.setdefault("tts", {})
    if not isinstance(tts_cfg, dict):
        tts_cfg = {}
        config["tts"] = tts_cfg
    engine_cfg = tts_cfg.setdefault(VOICE_CLONE_ENGINE_ID, {})
    if not isinstance(engine_cfg, dict):
        engine_cfg = {}
        tts_cfg[VOICE_CLONE_ENGINE_ID] = engine_cfg
    return engine_cfg


def _write_voices(config: dict, voices: list[dict[str, str]]) -> None:
    engine_cfg = _mutable_engine_config(config)
    engine_cfg["custom_voices"] = voices
    selected = str(engine_cfg.get("voice", "") or "").strip()
    known = {voice["voice_id"] for voice in voices}
    if selected not in known:
        # The list also keeps voices of other Qwen-Audio models and regions;
        # selecting one of those would only fail at the first utterance.
        resolved = voice_clone_config(config)
        engine_cfg["voice"] = next(
            (
                voice["voice_id"]
                for voice in voices
                if cloned_voice_is_usable(
                    voice,
                    model=resolved.get("model"),
                    region=resolved.get("region"),
                )
            ),
            "",
        )


def create_voice(
    config: dict,
    audio_data: bytes,
    display_name: str,
) -> ClonedVoice:
    """Upload a reference recording and remember the resulting voice.

    The recording leaves the machine here, so consent is checked immediately
    before the upload rather than trusted from an earlier UI state.
    """

    resolved = voice_clone_config(config)
    if not bool(resolved.get("upload_consent")):
        raise VoiceCloneConsentRequiredError(
            "Uploading a voice recording requires confirming the cloning "
            "privacy notice first."
        )
    target_model = str(resolved.get("model", "") or "").strip()
    if not qwen_vc_model_is_supported(target_model):
        raise VoiceCloneModelUnsupportedError(
            f"Model '{target_model}' cannot use cloned voices; choose a "
            "Qwen-Audio-TTS model"
        )
    region = str(resolved.get("region", "") or "").strip()
    with _client(config) as client:
        voice = client.create_voice(
            audio_data,
            target_model=target_model,
            display_name=display_name,
            language=recording_language_hint(config),
        )
    voice = replace(voice, region=region)

    voices = [dict(item) for item in stored_voices(config)]
    voices = [item for item in voices if item["voice_id"] != voice.voice_id]
    voices.append(voice.as_dict())
    _write_voices(config, voices)
    engine_cfg = _mutable_engine_config(config)
    engine_cfg["voice"] = voice.voice_id
    return voice


_VOICE_GONE_TOKENS = (
    "not found",
    "not exist",
    "does not exist",
    "no such voice",
    "invalid voice",
    "voice_not_found",
)


def is_voice_gone_error(error: BaseException) -> bool:
    """Return whether the service says the voice no longer exists."""

    status_code = getattr(error, "status_code", None)
    if status_code == 404:
        return True
    text = f"{getattr(error, 'provider_code', '') or ''} {error}".casefold()
    return any(token in text for token in _VOICE_GONE_TOKENS)


def delete_voice(config: dict, voice_id: str) -> None:
    """Remove a voice from the service and from local config.

    The local entry is dropped when the service confirms the deletion or
    reports the voice as already gone, so a stale entry cannot become
    permanently undeletable.  Any other failure (network, credentials, quota)
    is raised so the voice stays listed and the player sees why; silently
    dropping it left voices behind on the account.
    """

    target = str(voice_id or "").strip()
    if not target:
        raise QwenVoiceEnrollmentError("No voice was selected for deletion")
    try:
        with _client(config) as client:
            client.delete_voice(target)
    except VoiceCloneNotConfiguredError:
        raise
    except QwenVoiceEnrollmentError as exc:
        if not is_voice_gone_error(exc):
            raise
        logger.warning(
            "Voice cloning service reports the voice as already gone; "
            "removing the local entry"
        )
    voices = [
        dict(item) for item in stored_voices(config) if item["voice_id"] != target
    ]
    _write_voices(config, voices)


def refresh_voices(config: dict) -> tuple[dict[str, str], ...]:
    """Re-read the account's voices from the service and adopt that list.

    The service expires voices unused for a year, so the local list can drift.
    Locally stored display names and languages are preserved because the
    service only keeps the sanitized ASCII prefix.  Voices recorded in another
    service region are kept: the account there still holds them and they
    become usable again when the player switches back.  Voices of another
    Qwen-Audio-TTS model are kept for the same reason; dropping them lost
    their names on every sync after the model box changed.  The account-wide
    list also carries CosyVoice and realtime voices, which this engine cannot
    speak with, and each voice's review status, which tells the player when a
    new voice becomes usable.
    """

    resolved = voice_clone_config(config)
    region = str(resolved.get("region", "") or "").strip()
    with _client(config) as client:
        remote = client.list_all_voices()

    known = {item["voice_id"]: item for item in stored_voices(config)}
    voices: list[dict[str, str]] = []
    for voice in remote:
        # A recording rejected during review can never speak.
        if voice.status == VOICE_STATUS_UNDEPLOYED:
            continue
        previous = known.get(voice.voice_id, {})
        # The model is read from the voice id; an id that does not name it
        # keeps the model recorded when the voice was created.
        target_model = voice.target_model or str(
            previous.get("target_model") or ""
        ).strip()
        # CosyVoice, realtime and unrecognized voices cannot be spoken here.
        if not qwen_vc_model_is_supported(target_model):
            continue
        entry = voice.as_dict()
        entry["target_model"] = target_model
        entry["display_name"] = (
            previous.get("display_name") or entry["display_name"] or voice.voice_id
        )
        entry["language"] = previous.get("language") or entry["language"]
        entry["created_at"] = entry["created_at"] or previous.get("created_at") or ""
        entry["region"] = region
        voices.append(entry)
    # Every id the service listed here belongs to this region, including the
    # ones skipped above, so only unlisted records can be another region's.
    listed = {voice.voice_id for voice in remote}
    for voice_id, previous in known.items():
        previous_region = str(previous.get("region") or "")
        if voice_id not in listed and previous_region and previous_region != region:
            voices.append(dict(previous))

    _write_voices(config, voices)
    return tuple(voices)
