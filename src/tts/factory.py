"""TTS engine factory."""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseTTS
from .aivis_speech_engine import AivisSpeechTTS
from .edge_tts_engine import EdgeTTS
from .gtts_engine import GoogleTTS
from .pyttsx3_engine import Pyttsx3TTS
from .style_bert_vits2_engine import StyleBertVits2TTS
from .voicevox_engine import VoicevoxTTS

logger = logging.getLogger(__name__)


def create_tts_engine(
    engine_name: str,
    device: str = "cpu",
    bert_language: str = "jp",
) -> Optional[BaseTTS]:
    """Create TTS engine by name.

    Args:
        engine_name: Engine name.

    Returns:
        TTS engine instance, or None if unavailable.
    """
    engine_name = engine_name.lower().strip()

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

    if engine_name in {"style_bert_vits2", "stylebertvits2", "sbv2"}:
        engine = StyleBertVits2TTS(device=device, bert_language=bert_language)
        if engine.is_available():
            logger.info(
                "Created Style-Bert-VITS2 TTS engine (device=%s, bert_language=%s)",
                device,
                bert_language,
            )
            return engine
        logger.warning("Style-Bert-VITS2 not available")
        return None

    logger.error("Unknown TTS engine: %s", engine_name)
    return None


def create_tts_engine_with_fallback(
    preferred: str = "edge",
    device: str = "cpu",
    bert_language: str = "jp",
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
        )
        if engine is not None:
            logger.info("Using fallback TTS engine: %s", engine_name)
            return engine

    logger.error("No TTS engine available")
    return None
