"""pyttsx3 offline TTS implementation."""
from __future__ import annotations

import io
import logging
import tempfile
import wave
from pathlib import Path
from typing import Optional

from .base import BaseTTS, TTSVoice

logger = logging.getLogger(__name__)


class Pyttsx3TTS(BaseTTS):
    """Offline TTS engine using pyttsx3."""

    def __init__(self):
        self._engine = None
        self._voices_cache: Optional[list[TTSVoice]] = None
        try:
            import pyttsx3
            self._pyttsx3 = pyttsx3
            self._engine = pyttsx3.init()
        except Exception as exc:
            logger.warning("pyttsx3 not available: %s", exc)

    def is_available(self) -> bool:
        """Check if pyttsx3 is available."""
        return self._engine is not None

    def get_available_voices(self) -> list[TTSVoice]:
        """Get list of available system voices."""
        if not self.is_available():
            return []

        if self._voices_cache is not None:
            return self._voices_cache

        try:
            system_voices = self._engine.getProperty("voices")
            voices = []

            for sys_voice in system_voices:
                # Try to extract language from voice ID or name
                voice_id = sys_voice.id
                voice_name = sys_voice.name

                # Guess language from voice name/ID
                language = self._guess_language(voice_id, voice_name)

                # Guess gender from voice name
                gender = self._guess_gender(voice_name)

                voices.append(TTSVoice(
                    id=voice_id,
                    name=voice_name,
                    language=language,
                    gender=gender,
                    locale=language,
                ))

            self._voices_cache = voices
            logger.info("Loaded %d voices from pyttsx3", len(voices))
            return voices

        except Exception as exc:
            logger.error("Failed to get pyttsx3 voices: %s", exc)
            return []

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize text to speech using pyttsx3.

        Args:
            text: Text to synthesize.
            voice: Voice ID.
            rate: Speech rate (0.5 - 2.0, 1.0 = normal).
            volume: Volume (0.0 - 1.0).

        Returns:
            Audio data in WAV format.

        Raises:
            RuntimeError: If synthesis fails.
        """
        if not self.is_available():
            raise RuntimeError("pyttsx3 is not available")

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        # Clamp rate and volume
        rate = max(0.5, min(2.0, rate))
        volume = max(0.0, min(1.0, volume))

        try:
            # Set voice
            self._engine.setProperty("voice", voice)

            # Set rate (pyttsx3 uses words per minute, default ~200)
            default_rate = 150
            self._engine.setProperty("rate", int(default_rate * rate))

            # Set volume
            self._engine.setProperty("volume", volume)

            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)

            try:
                self._engine.save_to_file(text, str(tmp_path))
                self._engine.runAndWait()

                # Read audio data
                with open(tmp_path, "rb") as f:
                    audio_data = f.read()

                return audio_data

            finally:
                # Clean up temp file
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        except Exception as exc:
            logger.error("pyttsx3 synthesis failed: %s", exc)
            raise RuntimeError(f"pyttsx3 synthesis failed: {exc}") from exc

    @staticmethod
    def _guess_language(voice_id: str, voice_name: str) -> str:
        """Guess language from voice ID or name."""
        text = (voice_id + " " + voice_name).lower()

        # Common language indicators
        if any(x in text for x in ["zh", "chinese", "mandarin", "cantonese"]):
            return "zh"
        if any(x in text for x in ["en", "english", "us", "uk", "au"]):
            return "en"
        if any(x in text for x in ["ja", "japanese", "japan"]):
            return "ja"
        if any(x in text for x in ["ko", "korean", "korea"]):
            return "ko"
        if any(x in text for x in ["ru", "russian", "russia"]):
            return "ru"
        if any(x in text for x in ["de", "german", "germany"]):
            return "de"
        if any(x in text for x in ["fr", "french", "france"]):
            return "fr"
        if any(x in text for x in ["es", "spanish", "spain"]):
            return "es"

        return "en"  # Default to English

    @staticmethod
    def _guess_gender(voice_name: str) -> str:
        """Guess gender from voice name."""
        name_lower = voice_name.lower()

        # Common male indicators
        if any(x in name_lower for x in ["male", "man", "david", "mark", "james"]):
            return "Male"

        # Common female indicators
        if any(x in name_lower for x in ["female", "woman", "zira", "hazel", "susan"]):
            return "Female"

        return "Neutral"
