"""TTS engine factory."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .base import BaseTTS

logger = logging.getLogger(__name__)

VoicevoxTTS: type[Any] | None = None
MimoTTS: type[Any] | None = None
QwenTTS: type[Any] | None = None
StyleBertVits2TTS: type[Any] | None = None
QwenVoiceCloneTTS: type[Any] | None = None


def _normalize_engine_name(name: str) -> str:
    """Normalize TTS engine name by stripping locale annotation in display labels."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"\s*（[^）]*）\s*$", "", name).strip()
    return name


def _config_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _optional_config_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _voicevox_tts_class() -> type[Any]:
    global VoicevoxTTS
    if VoicevoxTTS is None:
        from .voicevox_engine import VoicevoxTTS as loaded

        VoicevoxTTS = loaded
    return VoicevoxTTS


def _api_tts_classes() -> tuple[type[Any], type[Any]]:
    global MimoTTS, QwenTTS
    if MimoTTS is None or QwenTTS is None:
        from . import api_tts_engines

        if MimoTTS is None:
            MimoTTS = api_tts_engines.MimoTTS
        if QwenTTS is None:
            QwenTTS = api_tts_engines.QwenTTS
    return MimoTTS, QwenTTS


def _qwen_voice_clone_tts_class() -> type[Any]:
    global QwenVoiceCloneTTS
    if QwenVoiceCloneTTS is None:
        from . import api_tts_engines

        QwenVoiceCloneTTS = api_tts_engines.QwenVoiceCloneTTS
    return QwenVoiceCloneTTS


def _style_bert_vits2_tts_class() -> type[Any]:
    global StyleBertVits2TTS
    if StyleBertVits2TTS is None:
        from .style_bert_vits2_engine import StyleBertVits2TTS as loaded

        StyleBertVits2TTS = loaded
    return StyleBertVits2TTS


def create_tts_engine(
    engine_name: str,
    device: str = "cpu",
    bert_language: str = "jp",
    config: dict | None = None,
) -> Optional[BaseTTS]:
    """Create TTS engine by name.

    Args:
        engine_name: Engine name.

    Returns:
        TTS engine instance, or None if unavailable.
    """
    engine_name = _normalize_engine_name(engine_name.lower().strip())

    if engine_name == "voicevox":
        VoicevoxTTS = _voicevox_tts_class()
        engine = VoicevoxTTS()
        if engine.is_available():
            logger.info("Created VOICEVOX TTS engine")
            return engine
        logger.warning("VOICEVOX not available")
        return None

    if engine_name in {"mimo_tts", "mimo", "xiaomi_tts"}:
        MimoTTS, _QwenTTS = _api_tts_classes()
        engine = MimoTTS(config=config)
        if engine.is_available():
            logger.info("Created MiMo TTS engine")
            return engine
        logger.warning("MiMo TTS not available")
        return None

    if engine_name in {"qwen_tts", "qwen3_tts", "qwen-tts"}:
        _MimoTTS, QwenTTS = _api_tts_classes()
        engine = QwenTTS(config=config)
        if engine.is_available():
            logger.info("Created Qwen TTS engine")
            return engine
        logger.warning("Qwen TTS not available")
        return None

    if engine_name in {"qwen_vc", "qwen-vc", "voice_clone", "qwen_voice_clone", "xtts"}:
        # "xtts" is accepted so a config that still names the removed local
        # cloning engine resolves to the cloud one instead of failing outright.
        QwenVoiceCloneTTS = _qwen_voice_clone_tts_class()
        engine = QwenVoiceCloneTTS(config=config)
        if engine.is_available():
            logger.info("Created Qwen voice cloning TTS engine")
            return engine
        logger.warning(
            "Qwen voice cloning not available. Configure an API key and clone "
            "a voice in Settings first."
        )
        return None

    if engine_name in {"style_bert_vits2", "stylebertvits2", "sbv2"}:
        StyleBertVits2TTS = _style_bert_vits2_tts_class()
        engine = StyleBertVits2TTS(device=device, bert_language=bert_language)
        if engine.is_available():
            actual_device = getattr(engine, "device", device)
            logger.info(
                "Created Style-Bert-VITS2 TTS engine (device=%s, bert_language=%s)",
                actual_device,
                bert_language,
            )
            return engine
        logger.warning("Style-Bert-VITS2 not available")
        return None

    logger.error("Unknown TTS engine: %s", engine_name)
    return None


def create_tts_engine_with_fallback(
    preferred: str = "qwen_tts",
    device: str = "cpu",
    bert_language: str = "jp",
    config: dict | None = None,
) -> Optional[BaseTTS]:
    """Create the preferred TTS engine.

    Kept for call-site compatibility. Every engine now requires explicit
    configuration, so there is no usable substitute to fall back to.

    Args:
        preferred: Preferred engine name.
        device: Device for Style-Bert-VITS2 ("cpu" or "cuda").

    Returns:
        TTS engine instance, or None if the engine is unavailable.
    """
    engine = create_tts_engine(
        preferred,
        device=device,
        bert_language=bert_language,
        config=config,
    )
    if engine is not None:
        return engine

    # Every remaining engine needs the player to supply something first: an
    # API key, a locally installed server, or a downloaded model. There is no
    # zero-configuration engine left to silently fall back to, so report the
    # failure instead of substituting an engine that would also be unusable.
    logger.error(
        "TTS engine '%s' is unavailable and no configured fallback exists",
        preferred,
    )
    return None
