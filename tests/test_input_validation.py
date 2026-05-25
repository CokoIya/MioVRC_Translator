"""Unit tests for input validation."""
import pytest
from src.utils.input_validation import (
    validate_translation_text,
    validate_tts_text,
    validate_api_key,
    sanitize_filename,
    validate_language_code,
    validate_sample_rate,
    validate_port,
    ValidationError,
)


class TestInputValidation:
    """Test input validation functions."""

    def test_validate_translation_text_valid(self):
        """Test validation of valid translation text."""
        text = "Hello, world!"
        result = validate_translation_text(text)
        assert result == text

    def test_validate_translation_text_empty(self):
        """Test validation rejects empty text."""
        with pytest.raises(ValidationError):
            validate_translation_text("")

        with pytest.raises(ValidationError):
            validate_translation_text("   ")

    def test_validate_translation_text_truncates_long(self):
        """Test validation truncates very long text."""
        long_text = "a" * 10000
        result = validate_translation_text(long_text, max_length=5000)
        assert len(result) == 5000

    def test_validate_translation_text_removes_control_chars(self):
        """Test validation removes control characters."""
        text = "Hello\x00\x01\x02World"
        result = validate_translation_text(text)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "Hello" in result
        assert "World" in result

    def test_validate_translation_text_keeps_newlines(self):
        """Test validation keeps newlines and tabs."""
        text = "Line 1\nLine 2\tTabbed"
        result = validate_translation_text(text)
        assert "\n" in result
        assert "\t" in result

    def test_validate_tts_text_valid(self):
        """Test TTS text validation."""
        text = "Hello, world!"
        result = validate_tts_text(text)
        assert result == text

    def test_validate_tts_text_truncates(self):
        """Test TTS text truncation."""
        long_text = "a" * 2000
        result = validate_tts_text(long_text, max_length=1000)
        assert len(result) == 1000

    def test_validate_api_key_valid(self):
        """Test API key validation."""
        # Valid plain text key
        assert validate_api_key("sk-1234567890abcdef")

        # Valid encrypted key
        assert validate_api_key("dpapi:v1:base64encodeddata")

        # Too short
        assert not validate_api_key("short")

        # Empty
        assert not validate_api_key("")
        assert not validate_api_key("   ")

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        # Remove invalid characters
        result = sanitize_filename("file<>:name.txt")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

        # Truncate long names
        long_name = "a" * 300 + ".txt"
        result = sanitize_filename(long_name, max_length=255)
        assert len(result) <= 255
        assert result.endswith(".txt")

    def test_validate_language_code(self):
        """Test language code validation."""
        # Valid codes
        assert validate_language_code("en")
        assert validate_language_code("zh-CN")
        assert validate_language_code("ja")

        # Invalid codes
        assert not validate_language_code("english")
        assert not validate_language_code("zh_CN")
        assert not validate_language_code("")
        assert not validate_language_code("1")

    def test_validate_sample_rate(self):
        """Test sample rate validation."""
        # Valid rates
        assert validate_sample_rate(16000)
        assert validate_sample_rate(24000)
        assert validate_sample_rate(44100)
        assert validate_sample_rate(48000)

        # Invalid rates
        assert not validate_sample_rate(12345)
        assert not validate_sample_rate(0)
        assert not validate_sample_rate(-1)

    def test_validate_port(self):
        """Test port number validation."""
        # Valid ports
        assert validate_port(80)
        assert validate_port(443)
        assert validate_port(9000)
        assert validate_port(65535)

        # Invalid ports
        assert not validate_port(0)
        assert not validate_port(-1)
        assert not validate_port(65536)
        assert not validate_port(100000)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
