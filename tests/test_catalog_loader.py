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
    assert qwen["model"] == "qwen3.7-max"
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
