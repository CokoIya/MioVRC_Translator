# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""MyMemory Free Translation Provider - No API Key Required"""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class MyMemoryProvider:
    """
    MyMemory Translation API - Free translation service.

    Features:
    - No API key required
    - 500 words/day (anonymous)
    - 10,000 words/day (with email registration)
    - Supports 100+ language pairs

    API Documentation: https://mymemory.translated.net/doc/spec.php
    """

    BASE_URL = "https://api.mymemory.translated.net/get"
    MAX_CHARS_PER_REQUEST = 500

    def __init__(self, email: Optional[str] = None):
        """
        Initialize MyMemory provider.

        Args:
            email: Optional email for higher quota (10k words/day)
        """
        self.email = email
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MioTranslator/1.3.7"
        })

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        timeout: int = 10
    ) -> str:
        """
        Translate text using MyMemory API.

        Args:
            text: Text to translate (max 500 chars)
            source_lang: Source language code (e.g., 'en')
            target_lang: Target language code (e.g., 'zh-CN')
            timeout: Request timeout in seconds

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
        """
        # Truncate text if too long
        if len(text) > self.MAX_CHARS_PER_REQUEST:
            logger.warning(
                f"Text truncated from {len(text)} to {self.MAX_CHARS_PER_REQUEST} chars"
            )
            text = text[:self.MAX_CHARS_PER_REQUEST]

        # Normalize language codes
        source_lang = self._normalize_lang_code(source_lang)
        target_lang = self._normalize_lang_code(target_lang)

        # Build request parameters
        params = {
            "q": text,
            "langpair": f"{source_lang}|{target_lang}"
        }

        # Add email for higher quota
        if self.email:
            params["de"] = self.email

        try:
            response = self.session.get(
                self.BASE_URL,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()

            data = response.json()

            # Check response status
            if data.get("responseStatus") != 200:
                error_msg = data.get("responseDetails", "Unknown error")
                raise TranslationError(f"MyMemory API error: {error_msg}")

            # Extract translation
            translated_text = data["responseData"]["translatedText"]

            # Check match quality
            match_quality = data["responseData"].get("match", 0)
            if match_quality < 0.5:
                logger.debug(f"Low match quality: {match_quality}")

            return translated_text

        except requests.exceptions.Timeout:
            raise TranslationError("MyMemory API timeout")
        except requests.exceptions.RequestException as e:
            raise TranslationError(f"MyMemory API request failed: {e}")
        except (KeyError, ValueError) as e:
            raise TranslationError(f"MyMemory API response parse error: {e}")

    def _normalize_lang_code(self, lang_code: str) -> str:
        """
        Normalize language code for MyMemory API.

        MyMemory uses simple 2-letter codes (en, zh, ja)
        We need to convert zh-CN -> zh, zh-TW -> zh-TW
        """
        # Language code mapping
        mapping = {
            "zh-CN": "zh-CN",
            "zh-TW": "zh-TW",
            "zh": "zh-CN",
            "yue": "zh-TW",  # Cantonese -> Traditional Chinese
        }

        if lang_code in mapping:
            return mapping[lang_code]

        # Return first part (en-US -> en)
        return lang_code.split("-")[0].lower()

    def is_supported(self, source_lang: str, target_lang: str) -> bool:
        """
        Check if language pair is supported.

        MyMemory supports most common language pairs.
        """
        supported_langs = {
            "en", "zh-CN", "zh-TW", "ja", "ko",
            "es", "fr", "de", "it", "pt", "ru",
            "ar", "hi", "th", "vi", "id", "ms"
        }

        source = self._normalize_lang_code(source_lang)
        target = self._normalize_lang_code(target_lang)

        return source in supported_langs and target in supported_langs


class TranslationError(Exception):
    """Translation-specific error."""
    pass


# Example usage
if __name__ == "__main__":
    provider = MyMemoryProvider()

    # Test translations
    tests = [
        ("Hello, how are you?", "en", "zh-CN"),
        ("今天天气很好", "zh-CN", "en"),
        ("こんにちは", "ja", "en"),
    ]

    for text, src, tgt in tests:
        try:
            result = provider.translate(text, src, tgt)
            print(f"{text} -> {result}")
        except TranslationError as e:
            print(f"Error: {e}")
