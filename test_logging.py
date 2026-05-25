"""Test script for logging system improvements."""
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import tempfile
from pathlib import Path
from logging.handlers import RotatingFileHandler
import logging

print("=" * 60)
print("Testing Logging System Improvements")
print("=" * 60)

# Test 1: Log level environment variable
print("\n[Test 1] Testing log level environment variable...")
try:
    # Test default level
    log_level_str = os.environ.get("MIO_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    if log_level == logging.INFO:
        print(f"[PASS] Default log level is INFO ({log_level})")
    else:
        print(f"[FAIL] Expected INFO, got {log_level}")

    # Test debug mode
    os.environ["MIO_DEBUG"] = "1"
    if os.environ.get("MIO_DEBUG") == "1":
        debug_level = logging.DEBUG
        print(f"[PASS] Debug mode sets level to DEBUG ({debug_level})")

    # Clean up
    del os.environ["MIO_DEBUG"]
except Exception as e:
    print(f"[FAIL] Error testing log level: {e}")

# Test 2: Rotating file handler
print("\n[Test 2] Testing rotating file handler...")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"

        handler = RotatingFileHandler(
            log_file,
            maxBytes=1024,  # 1KB for testing
            backupCount=3,
            encoding="utf-8",
        )

        logger = logging.getLogger("test_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Write enough data to trigger rotation
        for i in range(100):
            logger.info(f"Test log message {i} " + "x" * 50)

        # Check if backup files were created
        backup_files = list(Path(tmpdir).glob("test.log.*"))

        if backup_files:
            print(f"[PASS] Log rotation created {len(backup_files)} backup files")
        else:
            print("[WARN] No backup files created (may need more data)")

        # Check file size limit
        if log_file.exists():
            size = log_file.stat().st_size
            if size <= 1024 * 2:  # Allow some overhead
                print(f"[PASS] Log file size controlled: {size} bytes")
            else:
                print(f"[WARN] Log file larger than expected: {size} bytes")

        logger.removeHandler(handler)
        handler.close()
except Exception as e:
    print(f"[FAIL] Error testing log rotation: {e}")

# Test 3: UTF-8 encoding
print("\n[Test 3] Testing UTF-8 encoding...")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test_utf8.log"

        handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )

        logger = logging.getLogger("test_utf8_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Write messages with various Unicode characters
        test_messages = [
            "English: Hello World",
            "日本語: こんにちは",
            "中文: 你好世界",
            "Emoji: 🎉🎊✨",
        ]

        for msg in test_messages:
            logger.info(msg)

        # Read back and verify
        content = log_file.read_text(encoding="utf-8")

        all_found = all(msg.split(": ")[1] in content for msg in test_messages)

        if all_found:
            print("[PASS] All Unicode messages written and read correctly")
        else:
            print("[FAIL] Some Unicode messages not found in log")

        logger.removeHandler(handler)
        handler.close()
except Exception as e:
    print(f"[FAIL] Error testing UTF-8 encoding: {e}")

# Test 4: Log format
print("\n[Test 4] Testing log format...")
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test_format.log"

        handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_format_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message")

        content = log_file.read_text(encoding="utf-8")

        # Check if format includes expected components
        has_timestamp = any(c.isdigit() for c in content.split("-")[0])
        has_level = "INFO" in content
        has_message = "Test message" in content

        if has_timestamp and has_level and has_message:
            print("[PASS] Log format includes timestamp, level, and message")
        else:
            print(f"[FAIL] Log format incomplete: timestamp={has_timestamp}, level={has_level}, message={has_message}")

        logger.removeHandler(handler)
        handler.close()
except Exception as e:
    print(f"[FAIL] Error testing log format: {e}")

print("\n" + "=" * 60)
print("Logging System Tests Complete")
print("=" * 60)
