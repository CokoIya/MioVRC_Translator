"""Shared local HTTP client for VOICEVOX-compatible TTS engines."""
from __future__ import annotations

import logging
from typing import Any

import requests

from .base import BaseTTS, TTSVoice

logger = logging.getLogger(__name__)


class VoicevoxCompatibleTTS(BaseTTS):
    """Base client for local VOICEVOX-compatible synthesis services."""

    ENGINE_LABEL = "VOICEVOX-compatible TTS"

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 50021,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = f"http://{str(host or '127.0.0.1').strip()}:{int(port)}"
        self._timeout = max(0.5, float(timeout))
        self._session = requests.Session()
        self._voices_cache: list[TTSVoice] | None = None

    def is_available(self) -> bool:
        """Check whether the local engine is reachable."""
        try:
            response = self._session.get(
                f"{self._base_url}/version",
                timeout=self._timeout,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.debug("%s unavailable at %s: %s", self.ENGINE_LABEL, self._base_url, exc)
            return False

    def get_available_voices(self) -> list[TTSVoice]:
        """Load speaker/style pairs exposed by the local engine."""
        if self._voices_cache is not None:
            return self._voices_cache

        try:
            response = self._session.get(
                f"{self._base_url}/speakers",
                timeout=self._timeout,
            )
            response.raise_for_status()
            speakers = response.json()
            if not isinstance(speakers, list):
                raise RuntimeError("Unexpected speakers response")

            voices: list[TTSVoice] = []
            for speaker in speakers:
                if not isinstance(speaker, dict):
                    continue
                speaker_name = str(speaker.get("name") or "").strip() or "Unknown Speaker"
                styles = speaker.get("styles")
                if not isinstance(styles, list):
                    continue

                for style in styles:
                    if not isinstance(style, dict):
                        continue
                    style_id = style.get("id")
                    if style_id is None:
                        continue
                    voice_id = str(style_id).strip()
                    if not voice_id:
                        continue
                    style_name = str(style.get("name") or "").strip()
                    display_name = (
                        f"{speaker_name} / {style_name}"
                        if style_name and style_name != speaker_name
                        else speaker_name
                    )
                    voices.append(
                        TTSVoice(
                            id=voice_id,
                            name=display_name,
                            language="ja",
                            gender="Neutral",
                            locale="ja-JP",
                        )
                    )

            self._voices_cache = voices
            logger.info("Loaded %d voices from %s", len(voices), self.ENGINE_LABEL)
            return voices
        except Exception as exc:
            logger.error("Failed to load %s voices: %s", self.ENGINE_LABEL, exc)
            return []

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> bytes:
        """Synthesize WAV audio through the compatible HTTP API."""
        clean_text = str(text or "").strip()
        clean_voice = str(voice or "").strip()
        if not clean_text:
            raise ValueError("Text cannot be empty")
        if not clean_voice:
            raise ValueError("Voice ID cannot be empty")

        rate = max(0.5, min(2.0, float(rate)))
        volume = max(0.0, min(1.0, float(volume)))

        try:
            audio_query_response = self._session.post(
                f"{self._base_url}/audio_query",
                params={"text": clean_text, "speaker": clean_voice},
                timeout=self._timeout,
            )
            audio_query_response.raise_for_status()
            audio_query = audio_query_response.json()
            if not isinstance(audio_query, dict):
                raise RuntimeError("Unexpected audio query response")

            self._apply_runtime_controls(audio_query, rate=rate, volume=volume)

            synthesis_response = self._session.post(
                f"{self._base_url}/synthesis",
                params={"speaker": clean_voice},
                json=audio_query,
                timeout=self._timeout,
            )
            synthesis_response.raise_for_status()
            audio_data = bytes(synthesis_response.content or b"")
            if not audio_data:
                raise RuntimeError("Engine returned empty audio")
            return audio_data
        except Exception as exc:
            logger.error("%s synthesis failed: %s", self.ENGINE_LABEL, exc)
            raise RuntimeError(f"{self.ENGINE_LABEL} synthesis failed: {exc}") from exc

    @staticmethod
    def _apply_runtime_controls(
        audio_query: dict[str, Any],
        *,
        rate: float,
        volume: float,
    ) -> None:
        audio_query["speedScale"] = rate
        audio_query["volumeScale"] = volume
