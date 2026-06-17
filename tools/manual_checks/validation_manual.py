"""Test script for input validation."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.utils.input_validation import (
    ValidationError,
    validate_api_key,
    validate_translation_text,
    validate_tts_text,
)

print("=" * 60)
print("Testing Input Validation Module")
print("=" * 60)

# Test 1: Valid text
try:
    result = validate_translation_text("Hello world")
    print("[PASS] Test 1: Valid text accepted")
except Exception as e:
    print(f"[FAIL] Test 1: {e}")

# Test 2: Empty text rejection
try:
    validate_translation_text("")
    print("[FAIL] Test 2: Empty text should be rejected")
except ValidationError:
    print("[PASS] Test 2: Empty text rejected")

# Test 3: Long text truncation
try:
    long_text = "a" * 10000
    result = validate_translation_text(long_text, max_length=5000)
    if len(result) == 5000:
        print(f"[PASS] Test 3: Long text truncated to {len(result)} chars")
    else:
        print(f"[FAIL] Test 3: Expected 5000 chars, got {len(result)}")
except Exception as e:
    print(f"[FAIL] Test 3: {e}")

# Test 4: Control character removal
try:
    text_with_control = "Hello\x00\x01World"
    result = validate_translation_text(text_with_control)
    if "\x00" not in result and "Hello" in result and "World" in result:
        print("[PASS] Test 4: Control characters removed")
    else:
        print("[FAIL] Test 4: Control characters not removed properly")
except Exception as e:
    print(f"[FAIL] Test 4: {e}")

# Test 5: TTS text validation
try:
    result = validate_tts_text("Test TTS")
    print("[PASS] Test 5: TTS text validated")
except Exception as e:
    print(f"[FAIL] Test 5: {e}")

# Test 6: API key validation
try:
    valid_key = validate_api_key("sk-1234567890abcdef")
    dpapi_key = validate_api_key("dpapi:v1:base64data")
    invalid_key = validate_api_key("short")

    if valid_key and dpapi_key and not invalid_key:
        print("[PASS] Test 6: API key validation works")
    else:
        print(f"[FAIL] Test 6: valid={valid_key}, dpapi={dpapi_key}, invalid={invalid_key}")
except Exception as e:
    print(f"[FAIL] Test 6: {e}")

print("=" * 60)
print("Input Validation Tests Complete")
print("=" * 60)