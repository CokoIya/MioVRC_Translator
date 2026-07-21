from __future__ import annotations

import copy

import pytest

from src.tts.api_tts_config import (
    QWEN_TTS_BASE_URL_INTERNATIONAL,
    QWEN_TTS_BASE_URL_MAINLAND,
    resolve_tts_api_config,
)
from src.utils import config_manager


CUSTOM_QWEN_TTS_BASE_URL = "https://qwen-relay.example.com/api/v1"


@pytest.mark.parametrize(
    ("raw_config", "expected_region", "expected_base_url"),
    (
        (
            {"base_url": QWEN_TTS_BASE_URL_MAINLAND},
            "china_mainland",
            QWEN_TTS_BASE_URL_MAINLAND,
        ),
        (
            {"base_url": f"{QWEN_TTS_BASE_URL_INTERNATIONAL}/"},
            "singapore",
            QWEN_TTS_BASE_URL_INTERNATIONAL,
        ),
        (
            {"base_url": CUSTOM_QWEN_TTS_BASE_URL},
            "custom",
            CUSTOM_QWEN_TTS_BASE_URL,
        ),
        (
            {
                "region": "singapore",
                "base_url": QWEN_TTS_BASE_URL_MAINLAND,
            },
            "singapore",
            QWEN_TTS_BASE_URL_INTERNATIONAL,
        ),
    ),
)
def test_resolve_qwen_tts_config_uses_raw_region_or_legacy_base_url(
    raw_config,
    expected_region,
    expected_base_url,
):
    resolved = resolve_tts_api_config("qwen_tts", raw_config)

    assert resolved["region"] == expected_region
    assert resolved["base_url"] == expected_base_url


@pytest.mark.parametrize(
    ("raw_config", "expected_region", "expected_base_url"),
    (
        (
            {"base_url": QWEN_TTS_BASE_URL_MAINLAND},
            "china_mainland",
            QWEN_TTS_BASE_URL_MAINLAND,
        ),
        (
            {"base_url": QWEN_TTS_BASE_URL_INTERNATIONAL},
            "singapore",
            QWEN_TTS_BASE_URL_INTERNATIONAL,
        ),
        (
            {"base_url": CUSTOM_QWEN_TTS_BASE_URL},
            "custom",
            CUSTOM_QWEN_TTS_BASE_URL,
        ),
        (
            {
                "region": "china_mainland",
                "base_url": QWEN_TTS_BASE_URL_INTERNATIONAL,
            },
            "china_mainland",
            QWEN_TTS_BASE_URL_MAINLAND,
        ),
    ),
)
def test_ensure_tts_config_migrates_qwen_region_from_raw_loaded_config(
    raw_config,
    expected_region,
    expected_base_url,
):
    loaded = {"tts": {"qwen_tts": copy.deepcopy(raw_config)}}
    merged_qwen_config = {
        "region": "singapore",
        "base_url": QWEN_TTS_BASE_URL_INTERNATIONAL,
        **copy.deepcopy(raw_config),
    }
    config = {"tts": {"qwen_tts": merged_qwen_config}}

    assert config_manager._ensure_tts_config(config, loaded=loaded) is True
    assert config["tts"]["qwen_tts"]["region"] == expected_region
    assert config["tts"]["qwen_tts"]["base_url"] == expected_base_url


def test_qwen_custom_model_id_and_timeout_controls_are_preserved_exactly():
    resolved = resolve_tts_api_config(
        "qwen_tts",
        {
            "region": "custom",
            "base_url": CUSTOM_QWEN_TTS_BASE_URL,
            "model": "relay/qwen3-tts-private-2026-07",
            "connect_timeout_seconds": 2.5,
            "read_timeout_seconds": 11.0,
            "wall_timeout_seconds": 19.0,
        },
    )

    assert resolved["model"] == "relay/qwen3-tts-private-2026-07"
    assert resolved["base_url"] == CUSTOM_QWEN_TTS_BASE_URL
    assert resolved["connect_timeout_seconds"] == 2.5
    assert resolved["read_timeout_seconds"] == 11.0
    assert resolved["wall_timeout_seconds"] == 19.0
