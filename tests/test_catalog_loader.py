import json
from pathlib import Path

import pytest

from src.utils import ui_config
from src.utils.catalog_loader import (
    BUILTIN_CATALOG,
    CATALOG_TRANSLATION_SECTION,
    load_catalog_from_data,
)
from src.utils.ui_config import (
    DEEPSEEK_PRO_MODEL_PATTERN,
    LEGACY_RELAY_BACKENDS,
    RELAY_BACKEND,
    TRANSLATION_BACKENDS,
    TRANSLATION_MODEL_PRESETS,
)

DOCS_CATALOG = Path(__file__).resolve().parents[1] / "docs" / "catalog.json"


def _v2(*, backends=None, presets=None, profiles=None) -> dict:
    """A remote catalog whose translation tables this release reads."""

    section: dict = {}
    if backends is not None:
        section["backends"] = backends
    if presets is not None:
        section["model_presets"] = presets
    if profiles is not None:
        section["model_profiles"] = profiles
    return {"version": 1, CATALOG_TRANSLATION_SECTION: section}


def _catalog_view():
    return (
        ui_config._catalog_backends(),
        ui_config._catalog_model_presets(),
        ui_config._catalog_model_profiles(),
        ui_config._catalog_region_base_urls(),
        ui_config._catalog_region_aliases(),
        ui_config._catalog_default_regions(),
    )


def test_unset_catalog_reads_the_same_tables_as_the_builtin_catalog(monkeypatch):
    # ui_config starts with no catalog and relies on its fallback tables
    # being exactly what BUILTIN_CATALOG carries.
    monkeypatch.setattr(ui_config, "_CATALOG", None)
    fallback = _catalog_view()
    monkeypatch.setattr(ui_config, "_CATALOG", BUILTIN_CATALOG)

    assert _catalog_view() == fallback


def test_catalog_loader_keeps_builtin_backends_when_remote_is_empty():
    catalog = load_catalog_from_data(_v2(backends={}))

    assert "qianwen" in catalog.translation_backends
    assert catalog.translation_backends["qianwen"]["model"] == TRANSLATION_BACKENDS["qianwen"]["model"]


def test_empty_v2_tables_yield_exactly_the_builtin_catalog():
    assert load_catalog_from_data(_v2(backends={}, presets={}, profiles={})) == BUILTIN_CATALOG
    assert load_catalog_from_data(_v2()) == BUILTIN_CATALOG
    assert load_catalog_from_data(None) == BUILTIN_CATALOG


def test_v1_only_catalog_data_yields_exactly_the_builtin_catalog():
    # The top-level sections are kept for older releases; their stale values
    # (separate relays, retired defaults) must not reach this release.
    stale = {
        "version": 1,
        "translation_backends": {
            "openai": {"model": "gpt-5.4-nano", "label": "GPT"},
            "openai_compatible": {"label": "GPT Compatible", "model": "gpt-5.6"},
            "grok_compatible": {"label": "Grok Compatible", "model": "grok-4-0709"},
            "qianwen": {"model": "qwen-mt-turbo", "timeout_s": 99},
        },
        "translation_model_presets": {
            "openai_compatible": ["gpt-5.6", "gpt-5.6-terra"],
            "anthropic_compatible": ["claude-3-5-sonnet-20241022"],
            "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
        },
        "translation_model_profiles": {"openai": {"gpt-5.6": {"speed": "fast"}}},
    }

    assert load_catalog_from_data(stale) == BUILTIN_CATALOG


def test_catalog_loader_merges_partial_backend_with_builtin_defaults():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                "qianwen": {
                    "model": "qwen3.7-max",
                }
            }
        )
    )

    qwen = catalog.translation_backends["qianwen"]
    assert qwen["model"] == TRANSLATION_BACKENDS["qianwen"]["model"]
    assert qwen["label"] == TRANSLATION_BACKENDS["qianwen"]["label"]
    assert qwen["base_url"] == TRANSLATION_BACKENDS["qianwen"]["base_url"]


def test_catalog_loader_applies_remote_mutable_fields():
    catalog = load_catalog_from_data(
        _v2(backends={"qianwen": {"model": "qwen-mt-plus", "timeout_s": 25, "model_hint": "Remote hint"}})
    )

    qwen = catalog.translation_backends["qianwen"]
    assert qwen["model"] == "qwen-mt-plus"
    assert qwen["timeout_s"] == 25.0
    assert qwen["model_hint"] == "Remote hint"


def test_catalog_loader_rejects_corrupted_openai_backend_data():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                "openai": {
                    "label": "claude",
                    "base_url": "https://api.openai.com/v1",
                    "model": "claude",
                }
            },
            presets={
                "openai": ["claude", "claude", "gpt-5.5", "gpt-5.5"],
            },
            profiles={
                "openai": {
                    "claude": {"speed": "slow", "quality": "high"},
                    "gpt-5.5": {"speed": "balanced", "quality": "high"},
                }
            },
        )
    )

    openai = catalog.translation_backends["openai"]
    assert openai["label"] == TRANSLATION_BACKENDS["openai"]["label"]
    assert openai["model"] == TRANSLATION_BACKENDS["openai"]["model"]
    assert catalog.translation_model_presets["openai"] == ("gpt-5.5",)
    assert "claude" not in catalog.translation_model_profiles["openai"]


def test_catalog_loader_falls_back_to_builtin_openai_presets_when_all_are_invalid():
    catalog = load_catalog_from_data(
        _v2(
            presets={
                "openai": ["claude", "claude"],
            },
        )
    )

    assert catalog.translation_model_presets["openai"] == TRANSLATION_MODEL_PRESETS["openai"]


def test_catalog_loader_cannot_restore_removed_gpt_or_claude_models():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                "openai": {"model": "gpt-5.4-nano"},
                "anthropic": {"model": "claude-sonnet-4-20250514"},
            },
            presets={
                "openai": ["gpt-5.4-nano", "gpt-5.6-sol"],
                "openai_compatible": ["gpt-5.6", "gpt-5.6-terra"],
                "anthropic": [
                    "claude-opus-4-8",
                    "claude-sonnet-4-20250514",
                    "claude-sonnet-5",
                ],
                "anthropic_compatible": [
                    "claude-3-5-sonnet-20241022",
                    "claude-sonnet-4-6",
                ],
            },
        )
    )

    # A retired id cannot come back through the catalog; served families can.
    assert catalog.translation_backends["openai"]["model"] == TRANSLATION_BACKENDS["openai"]["model"]
    assert catalog.translation_backends["openai"]["model"] != "gpt-5.4-nano"
    assert catalog.translation_backends["anthropic"]["model"] == "claude-opus-5"
    assert catalog.translation_model_presets["openai"] == ("gpt-5.6-sol",)
    # The relay's list is read from the relay itself, never from the catalog.
    assert catalog.translation_model_presets["openai_compatible"] == ()
    assert catalog.translation_model_presets["anthropic"] == (
        "claude-opus-4-8",
        "claude-sonnet-5",
    )
    # The old Claude-compatible backend no longer exists.
    assert "anthropic_compatible" not in catalog.translation_model_presets


def test_catalog_loader_rejects_remote_request_controls_and_bounds_cost_settings():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                "xiaomi": {
                    "base_url": "https://attacker.invalid/v1",
                    "extra_body": {"enable_thinking": True},
                    "prefer_max_completion_tokens": False,
                    "timeout_s": float("inf"),
                    "max_output_tokens": 1_000_000,
                    "max_retries": 999,
                    "unknown_request_option": "ignored",
                }
            }
        )
    )

    backend = catalog.translation_backends["xiaomi"]
    builtin = TRANSLATION_BACKENDS["xiaomi"]
    assert backend["base_url"] == builtin["base_url"]
    assert backend["extra_body"] == builtin["extra_body"]
    assert backend.get("prefer_max_completion_tokens") == builtin.get(
        "prefer_max_completion_tokens"
    )
    assert backend["timeout_s"] == builtin["timeout_s"]
    assert backend["max_output_tokens"] == 4096
    assert backend["max_retries"] == 5
    assert "unknown_request_option" not in backend


def test_catalog_loader_bounds_remote_presets_and_profile_metadata():
    presets = [f"remote-model-{index}" for index in range(200)]
    presets.extend(["bad\x00model", "x" * 300])
    profiles = {
        model: {
            "speed": "fast",
            "quality": "high",
            "untrusted": "ignored",
        }
        for model in presets
    }

    catalog = load_catalog_from_data(
        _v2(
            presets={"qianwen": presets},
            profiles={"qianwen": profiles},
        )
    )

    remote_presets = catalog.translation_model_presets["qianwen"]
    assert len(remote_presets) == 128
    assert all(len(model) <= 256 and model.isprintable() for model in remote_presets)
    remote_profile = catalog.translation_model_profiles["qianwen"]["remote-model-0"]
    assert remote_profile == {"speed": "fast", "quality": "high"}


@pytest.mark.parametrize(
    "pro_model",
    [
        "deepseek-pro",
        "deepseek-v4-pro",
        "deepseek-v4.1-pro",
        "DeepSeek-V5-Pro",
        "deepseek-v5-pro-261001",
    ],
)
def test_catalog_cannot_offer_a_deepseek_pro_model(pro_model):
    catalog = load_catalog_from_data(
        _v2(
            backends={"deepseek": {"model": pro_model}, "doubao": {"model": pro_model}},
            presets={
                "deepseek": [pro_model, "deepseek-flash"],
                "doubao": [pro_model, "doubao-seed-2-1-lite-260915"],
                "nvidia": [f"deepseek-ai/{pro_model}", "nvidia/nemotron-3.5-lightning-30b-a3b"],
            },
            profiles={"deepseek": {pro_model: {"speed": "fast"}}},
        )
    )

    assert catalog.translation_backends["deepseek"]["model"] == TRANSLATION_BACKENDS["deepseek"]["model"]
    assert catalog.translation_backends["doubao"]["model"] == TRANSLATION_BACKENDS["doubao"]["model"]
    assert catalog.translation_model_presets["deepseek"] == ("deepseek-flash",)
    assert catalog.translation_model_presets["doubao"] == ("doubao-seed-2-1-lite-260915",)
    assert catalog.translation_model_presets["nvidia"] == ("nvidia/nemotron-3.5-lightning-30b-a3b",)
    assert pro_model not in catalog.translation_model_profiles["deepseek"]


def test_catalog_keeps_deepseek_flash_and_prover_like_ids():
    catalog = load_catalog_from_data(
        _v2(presets={"nvidia": ["deepseek-ai/deepseek-v4.1-flash", "deepseek-ai/deepseek-prover-v2"]})
    )

    assert catalog.translation_model_presets["nvidia"] == (
        "deepseek-ai/deepseek-v4.1-flash",
        "deepseek-ai/deepseek-prover-v2",
    )


def test_catalog_drops_legacy_relay_backends_and_keeps_the_relay_app_defined():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                "grok_compatible": {"label": "Grok Compatible", "model": "grok-4.3"},
                "anthropic_compatible": {"label": "Claude Compatible", "model": "claude-sonnet-5"},
                RELAY_BACKEND: {
                    "label": "GPT Compatible",
                    "model": "gpt-5.6-terra",
                    "model_input": "select",
                    "model_hint": "Pick a GPT model",
                    "api_key_hint": "Use an OpenAI key",
                    "timeout_s": 30,
                },
            },
            presets={
                RELAY_BACKEND: ["gpt-5.6-terra", "claude-sonnet-5"],
                "grok_compatible": ["grok-4.3"],
                "anthropic_compatible": ["claude-sonnet-5"],
            },
            profiles={
                RELAY_BACKEND: {"gpt-5.6-terra": {"speed": "fast"}},
                "anthropic_compatible": {"claude-sonnet-5": {"speed": "fast"}},
            },
        )
    )

    for legacy in LEGACY_RELAY_BACKENDS:
        assert legacy not in catalog.translation_backends
        assert legacy not in catalog.translation_model_presets
        assert legacy not in catalog.translation_model_profiles
    relay = catalog.translation_backends[RELAY_BACKEND]
    builtin = TRANSLATION_BACKENDS[RELAY_BACKEND]
    for field_name in ("label", "model", "model_input", "model_hint", "api_key_hint"):
        assert relay[field_name] == builtin[field_name]
    # Cost and timing settings stay remote-tunable for the relay as well.
    assert relay["timeout_s"] == 30.0
    assert catalog.translation_model_presets[RELAY_BACKEND] == ()
    assert catalog.translation_model_profiles[RELAY_BACKEND] == {}


def test_catalog_cannot_change_model_list_filter_rules():
    catalog = load_catalog_from_data(
        _v2(
            backends={
                RELAY_BACKEND: {
                    "model_list": "builtin",
                    "model_list_exclude_patterns": [],
                    "model_list_priority": ["evil-model"],
                },
                "deepseek": {"model_list_require": [], "model_list_exclude_patterns": []},
            }
        )
    )

    relay = catalog.translation_backends[RELAY_BACKEND]
    assert relay["model_list"] == "remote"
    assert DEEPSEEK_PRO_MODEL_PATTERN in relay["model_list_exclude_patterns"]
    assert relay["model_list_priority"] == TRANSLATION_BACKENDS[RELAY_BACKEND]["model_list_priority"]
    deepseek = catalog.translation_backends["deepseek"]
    assert deepseek["model_list_require"] == ("flash",)
    assert DEEPSEEK_PRO_MODEL_PATTERN in deepseek["model_list_exclude_patterns"]


def test_shipped_catalog_keeps_v1_tables_and_carries_the_v2_section():
    data = json.loads(DOCS_CATALOG.read_text(encoding="utf-8"))

    # Already installed older releases still read the top-level tables.
    assert isinstance(data.get("translation_backends"), dict)
    assert data["translation_backends"]
    section = data.get(CATALOG_TRANSLATION_SECTION)
    assert isinstance(section, dict)
    assert set(section) == {"backends", "model_presets", "model_profiles"}
    assert set(section["backends"]) == set(TRANSLATION_BACKENDS)
    assert not set(section["backends"]) & set(LEGACY_RELAY_BACKENDS)
    assert section["model_presets"][RELAY_BACKEND] == []


def test_every_shipped_v2_value_survives_the_loader_unchanged():
    data = json.loads(DOCS_CATALOG.read_text(encoding="utf-8"))
    section = data[CATALOG_TRANSLATION_SECTION]

    catalog = load_catalog_from_data(data)

    for backend_id, spec in section["backends"].items():
        for key, value in spec.items():
            assert catalog.translation_backends[backend_id][key] == value, (backend_id, key)
    for backend_id, models in section["model_presets"].items():
        assert list(catalog.translation_model_presets[backend_id]) == models, backend_id
    for backend_id, profiles in section["model_profiles"].items():
        assert catalog.translation_model_profiles.get(backend_id, {}) == profiles, backend_id
    # Generated from the builtin tables, so loading it changes nothing.
    assert catalog == BUILTIN_CATALOG
