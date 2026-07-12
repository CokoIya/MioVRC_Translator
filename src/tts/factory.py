"""TTS engine factory."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from .base import BaseTTS

logger = logging.getLogger(__name__)

EdgeTTS: type[Any] | None = None
GoogleTTS: type[Any] | None = None
Pyttsx3TTS: type[Any] | None = None
VoicevoxTTS: type[Any] | None = None
AivisSpeechTTS: type[Any] | None = None
MimoTTS: type[Any] | None = None
QwenTTS: type[Any] | None = None
StyleBertVits2TTS: type[Any] | None = None
XTTSTS: type[Any] | None = None
XTTS_DEFAULT_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

_XTTS_SUPPORTED_LANGUAGES = {
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "pl",
    "tr",
    "ru",
    "nl",
    "cs",
    "ar",
    "zh-cn",
    "ja",
    "hu",
    "ko",
    "hi",
}
_XTTS_LANGUAGE_ALIASES = {
    "auto": "auto",
    "automatic": "auto",
    "detect": "auto",
    "english": "en",
    "eng": "en",
    "en-us": "en",
    "en-gb": "en",
    "chinese": "zh-cn",
    "zh": "zh-cn",
    "zh-hans": "zh-cn",
    "zh-sg": "zh-cn",
    "zh-tw": "zh-cn",
    "zh-hant": "zh-cn",
    "cn": "zh-cn",
    "japanese": "ja",
    "jp": "ja",
    "kor": "ko",
    "kr": "ko",
    "korean": "ko",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "pt-br": "pt",
    "pt-pt": "pt",
    "polish": "pl",
    "turkish": "tr",
    "russian": "ru",
    "dutch": "nl",
    "czech": "cs",
    "arabic": "ar",
    "hungarian": "hu",
    "hindi": "hi",
}


def normalize_xtts_language_code(language: object) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text:
        return "auto"
    normalized = _XTTS_LANGUAGE_ALIASES.get(text, text)
    if normalized == "auto" or normalized in _XTTS_SUPPORTED_LANGUAGES:
        return normalized
    return "auto"


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


def _edge_tts_class() -> type[Any]:
    global EdgeTTS
    if EdgeTTS is None:
        from .edge_tts_engine import EdgeTTS as loaded

        EdgeTTS = loaded
    return EdgeTTS


def _google_tts_class() -> type[Any]:
    global GoogleTTS
    if GoogleTTS is None:
        from .gtts_engine import GoogleTTS as loaded

        GoogleTTS = loaded
    return GoogleTTS


def _pyttsx3_tts_class() -> type[Any]:
    global Pyttsx3TTS
    if Pyttsx3TTS is None:
        from .pyttsx3_engine import Pyttsx3TTS as loaded

        Pyttsx3TTS = loaded
    return Pyttsx3TTS


def _voicevox_tts_class() -> type[Any]:
    global VoicevoxTTS
    if VoicevoxTTS is None:
        from .voicevox_engine import VoicevoxTTS as loaded

        VoicevoxTTS = loaded
    return VoicevoxTTS


def _aivis_speech_tts_class() -> type[Any]:
    global AivisSpeechTTS
    if AivisSpeechTTS is None:
        from .aivis_speech_engine import AivisSpeechTTS as loaded

        AivisSpeechTTS = loaded
    return AivisSpeechTTS


def _api_tts_classes() -> tuple[type[Any], type[Any]]:
    global MimoTTS, QwenTTS
    if MimoTTS is None or QwenTTS is None:
        from . import api_tts_engines

        if MimoTTS is None:
            MimoTTS = api_tts_engines.MimoTTS
        if QwenTTS is None:
            QwenTTS = api_tts_engines.QwenTTS
    return MimoTTS, QwenTTS


def _style_bert_vits2_tts_class() -> type[Any]:
    global StyleBertVits2TTS
    if StyleBertVits2TTS is None:
        from .style_bert_vits2_engine import StyleBertVits2TTS as loaded

        StyleBertVits2TTS = loaded
    return StyleBertVits2TTS


def _xtts_symbols() -> tuple[str, type[Any], Callable[[object], str]]:
    global XTTS_DEFAULT_MODEL_NAME, XTTSTS, normalize_xtts_language_code
    if XTTSTS is None:
        from .xtts_engine import (
            XTTS_DEFAULT_MODEL_NAME as loaded_model_name,
            XTTSTS as loaded_xtts,
            normalize_xtts_language_code as loaded_normalize_language,
        )

        XTTS_DEFAULT_MODEL_NAME = loaded_model_name
        XTTSTS = loaded_xtts
        normalize_xtts_language_code = loaded_normalize_language
    return XTTS_DEFAULT_MODEL_NAME, XTTSTS, normalize_xtts_language_code


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
        EdgeTTS = _edge_tts_class()
        engine = EdgeTTS()
        if engine.is_available():
            logger.info("Created Edge TTS engine")
            return engine
        logger.warning("Edge TTS not available")
        return None

    if engine_name == "gtts" or engine_name == "google":
        GoogleTTS = _google_tts_class()
        engine = GoogleTTS()
        if engine.is_available():
            logger.info("Created Google TTS engine")
            return engine
        logger.warning("Google TTS not available")
        return None

    if engine_name == "pyttsx3":
        Pyttsx3TTS = _pyttsx3_tts_class()
        engine = Pyttsx3TTS()
        if engine.is_available():
            logger.info("Created pyttsx3 TTS engine")
            return engine
        logger.warning("pyttsx3 not available")
        return None

    if engine_name == "voicevox":
        VoicevoxTTS = _voicevox_tts_class()
        engine = VoicevoxTTS()
        if engine.is_available():
            logger.info("Created VOICEVOX TTS engine")
            return engine
        logger.warning("VOICEVOX not available")
        return None

    if engine_name in {"aivis_speech", "aivis"}:
        AivisSpeechTTS = _aivis_speech_tts_class()
        engine = AivisSpeechTTS()
        if engine.is_available():
            logger.info("Created AivisSpeech TTS engine")
            return engine
        logger.warning("AivisSpeech not available")
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

    if engine_name in {"xtts", "xtts_v2", "xtts-v2", "xttsts"}:
        xtts_default_model_name, XTTSTS, normalize_xtts_language_code = _xtts_symbols()
        xtts_cfg = config if isinstance(config, dict) else {}
        xtts_device = str(xtts_cfg.get("device") or device or "cpu").strip().lower()
        xtts_language = normalize_xtts_language_code(xtts_cfg.get("language"))
        xtts_model = str(
            xtts_cfg.get("model_name")
            or xtts_cfg.get("model")
            or xtts_default_model_name
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
            precision=str(xtts_cfg.get("precision") or "auto"),
            cuda_device_index=xtts_cfg.get("cuda_device_index", 0),
            allow_cpu_fallback=_config_bool(
                xtts_cfg.get("allow_cpu_fallback"),
                True,
            ),
            cuda_tf32=_config_bool(xtts_cfg.get("cuda_tf32"), True),
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
