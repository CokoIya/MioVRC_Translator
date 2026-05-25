"""Edge TTS (Microsoft Edge Read Aloud) implementation."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

from .base import BaseTTS, TTSVoice

logger = logging.getLogger(__name__)


# Recommended voices for each language
RECOMMENDED_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",  # Chinese female
    "en": "en-US-JennyNeural",      # English US female
    "ja": "ja-JP-NanamiNeural",     # Japanese female
    "ko": "ko-KR-SunHiNeural",      # Korean female
    "ru": "ru-RU-SvetlanaNeural",   # Russian female
}


def _edge_no_audio_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no audio" in text or "no audio was received" in text


def _edge_retry_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return clean
    if clean[-1] in ".。!！?？,，;；":
        return clean
    if any("\u3400" <= char <= "\u9fff" for char in clean):
        return f"{clean}。"
    return f"{clean}."


class EdgeTTS(BaseTTS):
    """Edge TTS engine using Microsoft Edge Read Aloud."""

    def __init__(self):
        self._voices_cache: Optional[list[TTSVoice]] = None
        self._edge_tts = None
        try:
            import edge_tts
            self._edge_tts = edge_tts
        except ImportError:
            logger.warning("edge-tts not installed, Edge TTS unavailable")

    def is_available(self) -> bool:
        """Check if Edge TTS is available."""
        return self._edge_tts is not None

    def get_available_voices(self) -> list[TTSVoice]:
        """Get list of available voices from Edge TTS."""
        if not self.is_available():
            return []

        if self._voices_cache is not None:
            return self._voices_cache

        try:
            # Run async function in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                voices_data = loop.run_until_complete(self._edge_tts.list_voices())
            finally:
                loop.close()

            voices = []
            for voice_data in voices_data:
                # Extract language code (e.g., "zh-CN" from "zh-CN-XiaoxiaoNeural")
                voice_id = voice_data["ShortName"]
                locale = voice_data["Locale"]
                language = locale.split("-")[0] if "-" in locale else locale

                voices.append(TTSVoice(
                    id=voice_id,
                    name=voice_data["FriendlyName"],
                    language=language,
                    gender=voice_data["Gender"],
                    locale=locale,
                ))

            self._voices_cache = voices
            logger.info("Loaded %d voices from Edge TTS", len(voices))
            return voices

        except Exception as exc:
            logger.error("Failed to get Edge TTS voices: %s", exc)
            return []

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize text to speech using Edge TTS.

        Args:
            text: Text to synthesize.
            voice: Voice ID (e.g., "zh-CN-XiaoxiaoNeural").
            rate: Speech rate (0.5 - 2.0, 1.0 = normal).
            volume: Volume (0.0 - 1.0).

        Returns:
            Audio data in MP3 format.

        Raises:
            RuntimeError: If synthesis fails.
        """
        if not self.is_available():
            raise RuntimeError("Edge TTS is not available")

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        # Clamp rate and volume
        rate = max(0.5, min(2.0, rate))
        volume = max(0.0, min(1.0, volume))

        # Convert rate to percentage string (e.g., "+0%", "+50%", "-50%")
        rate_percent = int(round((rate - 1.0) * 100))
        rate_str = f"{rate_percent:+d}%"

        # Convert volume to percentage string
        volume_percent = int(round((volume - 1.0) * 100))
        volume_str = f"{volume_percent:+d}%"

        try:
            # Run async synthesis in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                audio_data = loop.run_until_complete(
                    self._synthesize_with_retry_async(
                        text.strip(),
                        voice,
                        rate_str,
                        volume_str,
                    )
                )
            finally:
                loop.close()

            return audio_data

        except Exception as exc:
            logger.error("Edge TTS synthesis failed: %s", exc)
            raise RuntimeError(f"Edge TTS synthesis failed: {exc}") from exc

    async def _synthesize_with_retry_async(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str,
    ) -> bytes:
        attempts = [(text, volume)]
        retry_text = _edge_retry_text(text)
        if retry_text != text or volume != "+0%":
            attempts.append((retry_text, "+0%"))

        last_error: Exception | None = None
        for index, (attempt_text, attempt_volume) in enumerate(attempts, start=1):
            try:
                audio_data = await self._synthesize_async(
                    attempt_text,
                    voice,
                    rate,
                    attempt_volume,
                )
                if audio_data:
                    if index > 1:
                        logger.info("Edge TTS synthesis succeeded after safe retry")
                    return audio_data
                last_error = RuntimeError("No audio was received from Edge TTS")
            except Exception as exc:
                last_error = exc
                if not _edge_no_audio_error(exc) or index >= len(attempts):
                    raise
                logger.warning("Edge TTS returned no audio; retrying with safe parameters")

        if last_error is not None:
            raise last_error
        raise RuntimeError("No audio was received from Edge TTS")

    async def _synthesize_async(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str,
    ) -> bytes:
        """Async synthesis implementation."""
        communicate = self._edge_tts.Communicate(text, voice, rate=rate, volume=volume)

        audio_buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

        return audio_buffer.getvalue()

    def get_voice_by_language(self, language: str) -> Optional[TTSVoice]:
        """Get recommended voice for a language.

        Args:
            language: Language code (e.g., "zh", "en", "ja").

        Returns:
            Recommended voice for the language, or None if not found.
        """
        # Try recommended voice first
        recommended_id = RECOMMENDED_VOICES.get(language.lower())
        if recommended_id:
            voices = self.get_available_voices()
            for voice in voices:
                if voice.id == recommended_id:
                    return voice

        # Fallback to base implementation
        return super().get_voice_by_language(language)
