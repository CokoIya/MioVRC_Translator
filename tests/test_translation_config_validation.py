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


def test_missing_translation_api_key_blocks_original_only_read_translation():
    missing, backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "qianwen",
                "output_format": "original_only_read_translation",
                "qianwen": {"api_key": ""},
            }
        }
    )

    assert missing is True
    assert backend == "Qwen"


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


def test_missing_translation_api_key_localizes_backend_qualifier():
    missing, backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "openai_compatible",
                "output_format": "translated_only",
                "openai_compatible": {"api_key": ""},
            }
        },
        "ru-RU",
    )

    assert missing is True
    assert backend == "Совместимость с ретранслятором"


def test_missing_translation_api_key_names_the_relay_for_a_legacy_relay_section():
    # A raw config that still selects the old Claude-compatible backend is
    # read as the relay (Anthropic protocol), which needs a key.
    missing, backend = missing_required_translation_api_key(
        {
            "translation": {
                "backend": "anthropic_compatible",
                "output_format": "translated_only",
                "anthropic_compatible": {"api_key": ""},
            }
        },
        "ja",
    )

    assert missing is True
    assert backend == "中継サービス互換"
