"""Google TTS (gTTS) implementation."""
from __future__ import annotations

import io
import logging
from typing import Optional

from .base import BaseTTS, TTSVoice
from src.utils.provider_warmup import warmup_dns_origin

logger = logging.getLogger(__name__)


# Language mapping for gTTS
GTTS_LANGUAGE_MAP = {
    "zh": "zh-CN",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "ru": "ru",
    "de": "de",
    "fr": "fr",
    "es": "es",
}


class GoogleTTS(BaseTTS):
    max_concurrent_synthesis = 2
    """Google TTS engine using gTTS."""

    def __init__(self):
        self._gtts = None
        try:
            from gtts import gTTS
            self._gtts = gTTS
        except ImportError:
            logger.warning("gTTS not installed, Google TTS unavailable")

    def is_available(self) -> bool:
        """Check if gTTS is available."""
        return self._gtts is not None

    def prewarm(self, voice: str = "") -> None:
        """Prime bounded Google DNS state without synthesizing speech."""

        del voice
        if not self.is_available():
            return
        result = warmup_dns_origin(
            "https://translate.google.com",
            timeout_s=2.0,
        )
        logger.log(
            logging.INFO if result.succeeded else logging.WARNING,
            "gTTS DNS prewarm %s (elapsed_ms=%.0f error_type=%s)",
            "finished" if result.succeeded else "failed",
            result.elapsed_s * 1000.0,
            result.error_type or "none",
        )

    def get_available_voices(self) -> list[TTSVoice]:
        """Get list of available voices from gTTS.

        Note: gTTS doesn't have multiple voices per language,
        so we return one voice per supported language.
        """
        if not self.is_available():
            return []

        voices = []
        for lang_code, gtts_code in GTTS_LANGUAGE_MAP.items():
            voices.append(TTSVoice(
                id=gtts_code,
                name=f"Google {lang_code.upper()}",
                language=lang_code,
                gender="Neutral",
                locale=gtts_code,
            ))

        return voices

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize text to speech using gTTS.

        Args:
            text: Text to synthesize.
            voice: Language code (e.g., "zh-CN", "en", "ja").
            rate: Speech rate (0.5 - 2.0, 1.0 = normal).
                  Note: gTTS supports slow=True/False only.
            volume: Volume (0.0 - 1.0).
                    Note: gTTS doesn't support volume control.

        Returns:
            Audio data in MP3 format.

        Raises:
            RuntimeError: If synthesis fails.
        """
        if not self.is_available():
            raise RuntimeError("gTTS is not available")

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        # gTTS only supports slow=True/False
        slow = rate < 0.8

        try:
            # Create gTTS instance
            tts = self._gtts(text=text, lang=voice, slow=slow)

            # Save to BytesIO
            audio_buffer = io.BytesIO()
            tts.write_to_fp(audio_buffer)
            audio_buffer.seek(0)

            return audio_buffer.read()

        except Exception as exc:
            logger.error("gTTS synthesis failed: %s", exc)
            raise RuntimeError(f"gTTS synthesis failed: {exc}") from exc

    def get_voice_by_language(self, language: str) -> Optional[TTSVoice]:
        """Get voice for a language.

        Args:
            language: Language code (e.g., "zh", "en", "ja").

        Returns:
            Voice for the language, or None if not found.
        """
        # Map language code to gTTS code
        gtts_code = GTTS_LANGUAGE_MAP.get(language.lower())
        if not gtts_code:
            return None

        voices = self.get_available_voices()
        for voice in voices:
            if voice.id == gtts_code:
                return voice

        return None
