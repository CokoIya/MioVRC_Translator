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

from src.tts.api_tts_config import (
    normalize_cloned_voices,
    resolve_tts_api_config,
)
from src.tts.qwen_voice_enrollment import (
    ClonedVoice,
    QwenVoiceEnrollmentClient,
    QwenVoiceEnrollmentError,
)

logger = logging.getLogger(__name__)

VOICE_CLONE_ENGINE_ID = "qwen_vc"


class VoiceCloneNotConfiguredError(QwenVoiceEnrollmentError):
    """Raised when cloning is attempted before an API key is supplied."""


class VoiceCloneConsentRequiredError(QwenVoiceEnrollmentError):
    """Raised when a recording would be uploaded without explicit consent."""


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
        engine_cfg["voice"] = voices[0]["voice_id"] if voices else ""


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
    with _client(config) as client:
        voice = client.create_voice(
            audio_data,
            target_model=target_model,
            display_name=display_name,
        )

    voices = [dict(item) for item in stored_voices(config)]
    voices = [item for item in voices if item["voice_id"] != voice.voice_id]
    voices.append(voice.as_dict())
    _write_voices(config, voices)
    engine_cfg = _mutable_engine_config(config)
    engine_cfg["voice"] = voice.voice_id
    return voice


def delete_voice(config: dict, voice_id: str) -> None:
    """Remove a voice from the service and from local config.

    Local removal happens even when the service reports the voice as already
    gone, so a stale entry cannot become permanently undeletable in the UI.
    """

    target = str(voice_id or "").strip()
    if not target:
        raise QwenVoiceEnrollmentError("No voice was selected for deletion")
    try:
        with _client(config) as client:
            client.delete_voice(target)
    except VoiceCloneNotConfiguredError:
        raise
    except QwenVoiceEnrollmentError:
        logger.warning(
            "Voice cloning service rejected a delete request; "
            "removing the local entry anyway"
        )
    voices = [
        dict(item) for item in stored_voices(config) if item["voice_id"] != target
    ]
    _write_voices(config, voices)


def refresh_voices(config: dict) -> tuple[dict[str, str], ...]:
    """Re-read the account's voices from the service and adopt that list.

    The service expires voices unused for a year, so the local list can drift.
    Locally stored display names are preserved because the service only keeps
    the sanitized ASCII prefix.
    """

    with _client(config) as client:
        remote = client.list_voices()

    known_names = {
        item["voice_id"]: item["display_name"] for item in stored_voices(config)
    }
    target_model = str(voice_clone_config(config).get("model", "") or "").strip()
    voices: list[dict[str, str]] = []
    for voice in remote:
        # Voices registered for another target model cannot be used by this
        # engine, so keep them out of the selectable list.
        if target_model and voice.target_model and voice.target_model != target_model:
            continue
        entry = voice.as_dict()
        entry["display_name"] = (
            known_names.get(voice.voice_id) or entry["display_name"] or voice.voice_id
        )
        voices.append(entry)

    _write_voices(config, voices)
    return tuple(voices)
