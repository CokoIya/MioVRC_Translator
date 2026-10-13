from __future__ import annotations

import copy

import pytest

from src.tts.api_tts_config import (
    QWEN_TTS_BASE_URL_INTERNATIONAL,
    QWEN_TTS_BASE_URL_MAINLAND,
    QWEN_TTS_DEFAULT_MODEL,
    QWEN_VC_MODEL_OPTIONS,
    get_tts_api_model_options,
    qwen_tts_model_is_retired,
    qwen_vc_model_is_supported,
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


def test_qwen_japan_region_requires_and_preserves_workspace_endpoint():
    workspace_url = (
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com/api/v1"
    )
    resolved = resolve_tts_api_config(
        "qwen_tts",
        {
            "region": "japan",
            "api_key": "jp-region-key",
            "base_url": workspace_url,
        },
    )

    assert resolved["region"] == "japan"
    assert resolved["base_url"] == workspace_url


def test_qwen_japan_region_does_not_inherit_international_endpoint():
    resolved = resolve_tts_api_config(
        "qwen_tts",
        {"region": "japan", "api_key": "jp-region-key"},
    )

    assert resolved["region"] == "japan"
    assert resolved["base_url"] == ""


def test_qwen_tts_offers_only_the_stable_alias():
    # qwen3-tts-instruct-flash and the dated snapshots were retired on 2026-10-10.
    assert QWEN_TTS_DEFAULT_MODEL == "qwen3-tts-flash"
    assert get_tts_api_model_options("qwen_tts") == ("qwen3-tts-flash",)
    # A relay's own model id typed by the player is still offered.
    assert get_tts_api_model_options("qwen_tts", "relay/qwen3-tts-private-2026-07") == (
        "qwen3-tts-flash",
        "relay/qwen3-tts-private-2026-07",
    )


@pytest.mark.parametrize(
    ("model", "retired"),
    (
        ("qwen3-tts-instruct-flash", True),
        ("qwen3-tts-instruct-flash-2026-01-26", True),
        ("qwen3-tts-flash-2025-11-27", True),
        ("qwen3-tts-flash-2025-09-18", True),
        ("Qwen-TTS", True),
        ("qwen-tts-latest", True),
        ("qwen3-tts-flash", False),
        ("relay/qwen3-tts-private-2026-07", False),
        ("", False),
        (None, False),
    ),
)
def test_retired_qwen_tts_models_are_recognised(model, retired):
    assert qwen_tts_model_is_retired(model) is retired


def test_qwen_vc_offers_only_models_that_accept_cloned_voices():
    assert get_tts_api_model_options("qwen_vc") == QWEN_VC_MODEL_OPTIONS
    assert all(qwen_vc_model_is_supported(model) for model in QWEN_VC_MODEL_OPTIONS)
