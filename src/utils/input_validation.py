"""Input validation utilities for translation and TTS."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Maximum text length for translation
MAX_TRANSLATION_LENGTH = 5000

# Suspicious patterns that might indicate prompt injection
SUSPICIOUS_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+previous",
    r"disregard\s+previous",
    r"system\s*:",
    r"assistant\s*:",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|system\|>",
    r"<\|assistant\|>",
    r"<\|user\|>",
]


class ValidationError(ValueError):
    """Input validation error."""
    pass


def validate_translation_text(text: str, max_length: int = MAX_TRANSLATION_LENGTH) -> str:
    """Validate and sanitize translation input text.

    Args:
        text: Input text to validate.
        max_length: Maximum allowed text length.

    Returns:
        Sanitized text.

    Raises:
        ValidationError: If text is invalid.
    """
    if not text or not text.strip():
        raise ValidationError("Translation text cannot be empty")

    # Remove leading/trailing whitespace
    text = text.strip()

    # Check length
    if len(text) > max_length:
        logger.warning("Text truncated from %d to %d characters", len(text), max_length)
        text = text[:max_length]

    # Remove control characters (except newlines and tabs)
    text = "".join(
        char for char in text
        if char.isprintable() or char in "\n\t\r"
    )

    # Check for suspicious patterns (potential prompt injection)
    text_lower = text.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            logger.warning("Suspicious pattern detected in input: %s", pattern)
            # Don't reject, just log - could be legitimate text

    return text


def validate_tts_text(text: str, max_length: int = 1000) -> str:
    """Validate and sanitize TTS input text.

    Args:
        text: Input text to validate.
        max_length: Maximum allowed text length for TTS.

    Returns:
        Sanitized text.

    Raises:
        ValidationError: If text is invalid.
    """
    if not text or not text.strip():
        raise ValidationError("TTS text cannot be empty")

    text = text.strip()

    # TTS has stricter length limits
    if len(text) > max_length:
        logger.warning("TTS text truncated from %d to %d characters", len(text), max_length)
        text = text[:max_length]

    # Remove control characters
    text = "".join(
        char for char in text
        if char.isprintable() or char in "\n\t "
    )

    return text


def validate_api_key(api_key: str, min_length: int = 10) -> bool:
    """Validate API key format.

    Args:
        api_key: API key to validate.
        min_length: Minimum expected key length.

    Returns:
        True if valid, False otherwise.
    """
    if not api_key or not api_key.strip():
        return False

    # Check if it's encrypted (DPAPI format)
    if api_key.startswith("dpapi:v1:"):
        return True

    # Basic validation for plain text keys
    key = api_key.strip()

    # Must be at least min_length characters
    if len(key) < min_length:
        return False

    # Should contain alphanumeric characters
    if not any(c.isalnum() for c in key):
        return False

    return True


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize filename to remove invalid characters.

    Args:
        filename: Original filename.
        max_length: Maximum filename length.

    Returns:
        Sanitized filename.
    """
    # Remove invalid characters for Windows/Unix
    invalid_chars = r'<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Remove control characters
    filename = "".join(char for char in filename if ord(char) >= 32)

    # Truncate if too long
    if len(filename) > max_length:
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        max_name_len = max_length - len(ext) - 1 if ext else max_length
        filename = name[:max_name_len] + ("." + ext if ext else "")

    return filename.strip()


def validate_language_code(code: str) -> bool:
    """Validate language code format.

    Args:
        code: Language code (e.g., 'en', 'zh-CN', 'ja').

    Returns:
        True if valid format, False otherwise.
    """
    if not code or not isinstance(code, str):
        return False

    # Basic format: 2-3 letter code, optionally followed by -REGION
    pattern = r"^[a-z]{2,3}(-[A-Z]{2})?$"
    return bool(re.match(pattern, code))


def validate_sample_rate(rate: int) -> bool:
    """Validate audio sample rate.

    Args:
        rate: Sample rate in Hz.

    Returns:
        True if valid, False otherwise.
    """
    # Common valid sample rates
    valid_rates = [8000, 11025, 16000, 22050, 24000, 44100, 48000, 96000, 192000]
    return rate in valid_rates


def validate_port(port: int) -> bool:
    """Validate network port number.

    Args:
        port: Port number.

    Returns:
        True if valid, False otherwise.
    """
    return isinstance(port, int) and 1 <= port <= 65535
