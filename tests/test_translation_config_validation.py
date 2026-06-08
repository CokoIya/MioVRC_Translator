from src.utils.translation_config_validation import missing_required_translation_api_key


def test_missing_translation_api_key_blocks_online_backend_when_not_original_only():
    missing, backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "qianwen",
                "output_format": "translated_with_original",
                "qianwen": {"api_key": ""},
            }
        }
    )

    assert missing is True
    assert backend == "Qwen"


def test_missing_translation_api_key_allows_original_only():
    missing, _backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "qianwen",
                "output_format": "original_only",
                "qianwen": {"api_key": ""},
            }
        }
    )

    assert missing is False


def test_missing_translation_api_key_allows_local_backend():
    missing, _backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "local_ai",
                "output_format": "translated_only",
                "local_ai": {"api_key": ""},
            }
        }
    )

    assert missing is False
