"""TTS engine factory."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import BaseTTS
from .aivis_speech_engine import AivisSpeechTTS
from .api_tts_engines import MimoTTS, QwenTTS
from .edge_tts_engine import EdgeTTS
from .gtts_engine import GoogleTTS
from .pyttsx3_engine import Pyttsx3TTS
from .style_bert_vits2_engine import StyleBertVits2TTS
from .voicevox_engine import VoicevoxTTS
from .xtts_engine import XTTS_DEFAULT_MODEL_NAME, XTTSTS

logger = logging.getLogger(__name__)


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

    if engine_name == "edge":
        engine = EdgeTTS()
        if engine.is_available():
            logger.info("Created Edge TTS engine")
            return engine
        logger.warning("Edge TTS not available")
        return None

    if engine_name == "gtts" or engine_name == "google":
        engine = GoogleTTS()
        if engine.is_available():
            logger.info("Created Google TTS engine")
            return engine
        logger.warning("Google TTS not available")
        return None

    if engine_name == "pyttsx3":
        engine = Pyttsx3TTS()
        if engine.is_available():
            logger.info("Created pyttsx3 TTS engine")
            return engine
        logger.warning("pyttsx3 not available")
        return None

    if engine_name == "voicevox":
        engine = VoicevoxTTS()
        if engine.is_available():
            logger.info("Created VOICEVOX TTS engine")
            return engine
        logger.warning("VOICEVOX not available")
        return None

    if engine_name in {"aivis_speech", "aivis"}:
        engine = AivisSpeechTTS()
        if engine.is_available():
            logger.info("Created AivisSpeech TTS engine")
            return engine
        logger.warning("AivisSpeech not available")
        return None

    if engine_name in {"mimo_tts", "mimo", "xiaomi_tts"}:
        engine = MimoTTS(config=config)
        if engine.is_available():
            logger.info("Created MiMo TTS engine")
            return engine
        logger.warning("MiMo TTS not available")
        return None

    if engine_name in {"qwen_tts", "qwen3_tts", "qwen-tts"}:
        engine = QwenTTS(config=config)
        if engine.is_available():
            logger.info("Created Qwen TTS engine")
            return engine
        logger.warning("Qwen TTS not available")
        return None

    if engine_name in {"style_bert_vits2", "stylebertvits2", "sbv2"}:
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

    if engine_name in {"xtts", "xtts_v2", "xtts-v2", "xttsts"}:
        xtts_cfg = config if isinstance(config, dict) else {}
        xtts_device = str(xtts_cfg.get("device") or device or "cpu").strip().lower()
        xtts_language = str(xtts_cfg.get("language") or "auto").strip().lower() or "auto"
        xtts_model = str(
            xtts_cfg.get("model_name")
            or xtts_cfg.get("model")
            or XTTS_DEFAULT_MODEL_NAME
        )
        try:
            xtts_cache_size = int(xtts_cfg.get("conditioning_cache_size", 4) or 4)
        except (TypeError, ValueError):
            xtts_cache_size = 4
        engine = XTTSTS(
            device=xtts_device,
            model_name=xtts_model,
            language=xtts_language,
            lazy_load=_config_bool(xtts_cfg.get("lazy_load"), True),
            optimized_inference=_config_bool(xtts_cfg.get("optimized_inference"), True),
            conditioning_cache_size=xtts_cache_size,
            enable_text_splitting=_config_bool(xtts_cfg.get("enable_text_splitting"), True),
            temperature=xtts_cfg.get("temperature"),
            length_penalty=xtts_cfg.get("length_penalty"),
            repetition_penalty=xtts_cfg.get("repetition_penalty"),
            top_k=xtts_cfg.get("top_k"),
            top_p=xtts_cfg.get("top_p"),
            do_sample=_optional_config_bool(xtts_cfg.get("do_sample")),
            num_beams=xtts_cfg.get("num_beams"),
        )
        if engine.is_available():
            actual_device = getattr(engine, "_device", device)
            logger.info(
                "Created XTTS-v2 TTS engine (device=%s)",
                actual_device,
            )
            return engine
        logger.warning("XTTS-v2 not available. Install the coqui-tts runtime and download the XTTS-v2 model.")
        return None

    logger.error("Unknown TTS engine: %s", engine_name)
    return None


def create_tts_engine_with_fallback(
    preferred: str = "edge",
    device: str = "cpu",
    bert_language: str = "jp",
    config: dict | None = None,
) -> Optional[BaseTTS]:
    """Create TTS engine with automatic fallback.

    Args:
        preferred: Preferred engine name.
        device: Device for Style-Bert-VITS2 ("cpu" or "cuda").

    Returns:
        TTS engine instance, or None if no engine available.
    """
    # Try preferred engine first
    engine = create_tts_engine(
        preferred,
        device=device,
        bert_language=bert_language,
        config=config,
    )
    if engine is not None:
        return engine

    # Try fallback engines
    fallback_order = ["edge", "gtts", "pyttsx3"]
    if preferred in fallback_order:
        fallback_order.remove(preferred)

    for engine_name in fallback_order:
        engine = create_tts_engine(
            engine_name,
            device=device,
            bert_language=bert_language,
            config=config,
        )
        if engine is not None:
            logger.info("Using fallback TTS engine: %s", engine_name)
            return engine

    logger.error("No TTS engine available")
    return None
