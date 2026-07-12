from src.utils.catalog_loader import load_catalog_from_data
from src.utils.ui_config import TRANSLATION_BACKENDS, TRANSLATION_MODEL_PRESETS


def test_catalog_loader_keeps_builtin_backends_when_remote_is_empty():
    catalog = load_catalog_from_data({"translation_backends": {}})

    assert "qianwen" in catalog.translation_backends
    assert catalog.translation_backends["qianwen"]["model"] == TRANSLATION_BACKENDS["qianwen"]["model"]


def test_catalog_loader_merges_partial_backend_with_builtin_defaults():
    catalog = load_catalog_from_data(
        {
            "translation_backends": {
                "qianwen": {
                    "model": "qwen3.7-max",
                }
            }
        }
    )

    qwen = catalog.translation_backends["qianwen"]
    assert qwen["model"] == TRANSLATION_BACKENDS["qianwen"]["model"]
    assert qwen["label"] == TRANSLATION_BACKENDS["qianwen"]["label"]
    assert qwen["base_url"] == TRANSLATION_BACKENDS["qianwen"]["base_url"]


def test_catalog_loader_rejects_corrupted_openai_backend_data():
    catalog = load_catalog_from_data(
        {
            "translation_backends": {
                "openai": {
                    "label": "claude",
                    "base_url": "https://api.openai.com/v1",
                    "model": "claude",
                }
            },
            "translation_model_presets": {
                "openai": ["claude", "claude", "gpt-5.5", "gpt-5.5"],
            },
            "translation_model_profiles": {
                "openai": {
                    "claude": {"speed": "slow", "quality": "high"},
                    "gpt-5.5": {"speed": "balanced", "quality": "high"},
                }
            },
        }
    )

    openai = catalog.translation_backends["openai"]
    assert openai["label"] == TRANSLATION_BACKENDS["openai"]["label"]
    assert openai["model"] == TRANSLATION_BACKENDS["openai"]["model"]
    assert catalog.translation_model_presets["openai"] == ("gpt-5.5",)
    assert "claude" not in catalog.translation_model_profiles["openai"]


def test_catalog_loader_falls_back_to_builtin_openai_presets_when_all_are_invalid():
    catalog = load_catalog_from_data(
        {
            "translation_model_presets": {
                "openai": ["claude", "claude"],
            },
        }
    )

    assert catalog.translation_model_presets["openai"] == TRANSLATION_MODEL_PRESETS["openai"]


def test_catalog_loader_cannot_restore_removed_gpt_or_claude_models():
    catalog = load_catalog_from_data(
        {
            "translation_backends": {
                "openai": {"model": "gpt-5.4-mini"},
                "anthropic": {"model": "claude-opus-4-8"},
            },
            "translation_model_presets": {
                "openai": ["gpt-5.4-mini", "gpt-5.6-sol"],
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
        }
    )

    assert catalog.translation_backends["openai"]["model"] == "gpt-5.6-sol"
    assert catalog.translation_backends["anthropic"]["model"] == "claude-sonnet-4-6"
    assert catalog.translation_model_presets["openai"] == ("gpt-5.6-sol",)
    assert catalog.translation_model_presets["openai_compatible"] == (
        "gpt-5.6-terra",
    )
    assert catalog.translation_model_presets["anthropic"] == (
        "claude-sonnet-5",
    )
    assert catalog.translation_model_presets["anthropic_compatible"] == (
        "claude-sonnet-4-6",
    )


def test_catalog_loader_rejects_remote_request_controls_and_bounds_cost_settings():
    catalog = load_catalog_from_data(
        {
            "translation_backends": {
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
        }
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
        {
            "translation_model_presets": {"qianwen": presets},
            "translation_model_profiles": {"qianwen": profiles},
        }
    )

    remote_presets = catalog.translation_model_presets["qianwen"]
    assert len(remote_presets) == 128
    assert all(len(model) <= 256 and model.isprintable() for model in remote_presets)
    remote_profile = catalog.translation_model_profiles["qianwen"]["remote-model-0"]
    assert remote_profile == {"speed": "fast", "quality": "high"}
