"""VOICEVOX local TTS engine."""
from __future__ import annotations

from .voicevox_compatible_engine import VoicevoxCompatibleTTS


class VoicevoxTTS(VoicevoxCompatibleTTS):
    """Client for the default local VOICEVOX engine endpoint."""

    ENGINE_LABEL = "VOICEVOX"

    def __init__(self) -> None:
        super().__init__(host="127.0.0.1", port=50021)
