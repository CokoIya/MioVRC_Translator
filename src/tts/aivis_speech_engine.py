"""AivisSpeech local TTS engine."""
from __future__ import annotations

from .voicevox_compatible_engine import VoicevoxCompatibleTTS


class AivisSpeechTTS(VoicevoxCompatibleTTS):
    """Client for the default local AivisSpeech engine endpoint."""

    ENGINE_LABEL = "AivisSpeech"

    def __init__(self) -> None:
        super().__init__(host="127.0.0.1", port=10101)
