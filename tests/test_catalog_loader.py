from src.utils.catalog_loader import load_catalog_from_data
from src.utils.ui_config import TRANSLATION_BACKENDS


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
