"""Base TTS (Text-to-Speech) interface."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Optional


@dataclass
class TTSVoice:
    """TTS voice information."""
    id: str
    name: str
    language: str
    gender: str  # "Male", "Female", "Neutral"
    locale: str  # e.g., "zh-CN", "en-US"


class BaseTTS(ABC):
    """Base class for TTS engines."""

    max_concurrent_synthesis = 1

    def prewarm(self, voice: str = "") -> None:
        """Optionally prepare heavy runtime state before the first utterance."""

        del voice

    def close_thread_context(self) -> None:
        """Release resources owned only by the current synthesis worker."""

    def close(self) -> None:
        """Release process-level resources owned by this engine."""

    def request_close(self) -> None:
        """Ask an in-flight engine operation to stop as soon as practical.

        The default implementation is intentionally a no-op. Network-backed
        engines override it so manager shutdown can wake deadline-aware body
        readers before their ordinary read timeout expires.
        """

    def consume_last_synthesis_diagnostics(self) -> Mapping[str, object]:
        """Return and clear diagnostics for the current synthesis thread."""

        return {}

    @abstractmethod
    def get_available_voices(self) -> list[TTSVoice]:
        """Get list of available voices.

        Returns:
            List of available voices.
        """
        pass

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize text to speech.

        Args:
            text: Text to synthesize.
            voice: Voice ID to use.
            rate: Speech rate (0.5 - 2.0, 1.0 = normal).
            volume: Volume (0.0 - 1.0).

        Returns:
            Audio data in WAV format.

        Raises:
            RuntimeError: If synthesis fails.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if TTS engine is available.

        Returns:
            True if engine is available, False otherwise.
        """
        pass

    def get_voice_by_language(self, language: str) -> Optional[TTSVoice]:
        """Get default voice for a language.

        Args:
            language: Language code (e.g., "zh", "en", "ja").

        Returns:
            Default voice for the language, or None if not found.
        """
        voices = self.get_available_voices()

        # Try exact match first
        for voice in voices:
            if voice.language.lower() == language.lower():
                return voice

        # Try prefix match (e.g., "zh" matches "zh-CN")
        for voice in voices:
            if voice.language.lower().startswith(language.lower()):
                return voice

        return None
