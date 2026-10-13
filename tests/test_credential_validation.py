import pytest

from src.utils.credential_validation import (
    first_missing_required_credential,
    missing_required_credentials,
)
from src.utils.i18n import tr
from src.utils.localization import SUPPORTED_UI_LANGUAGES


def test_translation_requirement_respects_active_original_only_mode():
    config = {
        "translation": {
            "backend": "qianwen",
            "output_format": "original_only",
            "qianwen": {"api_key": ""},
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()
    missing = first_missing_required_credential(
        config,
        scopes=("translation",),
        active_only=False,
    )

    assert missing is not None
    assert missing.scope == "translation"
    assert missing.provider_id == "qianwen"
    assert missing.credential_id == "translation.qianwen.api_key"
    assert missing.focus_target == "backend_api_key"


def test_original_only_read_translation_requires_translation_api_key():
    config = {
        "translation": {
            "backend": "qianwen",
            "output_format": "original_only_read_translation",
            "qianwen": {"api_key": ""},
        }
    }

    missing = first_missing_required_credential(
        config,
        scopes=("translation",),
    )

    assert missing is not None
    assert missing.provider_id == "qianwen"


def test_original_only_translation_still_requires_key_for_enabled_asr_rewrite():
    config = {
        "translation": {
            "backend": "openai",
            "output_format": "original_only",
            "asr_rewrite_style": "catgirl",
            "openai": {"api_key": ""},
        }
    }

    missing = first_missing_required_credential(
        config,
        scopes=("translation",),
    )

    assert missing is not None
    assert missing.provider_id == "openai"


def test_original_only_translation_still_requires_key_for_desktop_listen():
    config = {
        "translation": {
            "backend": "openai",
            "output_format": "original_only",
            "asr_rewrite_style": "off",
            "openai": {"api_key": ""},
        },
        "vrc_listen": {"enabled": True},
    }

    missing = first_missing_required_credential(
        config,
        scopes=("translation",),
    )

    assert missing is not None
    assert missing.provider_id == "openai"


def test_no_key_translation_provider_does_not_report_a_requirement():
    config = {
        "translation": {
            "backend": "google_web",
            "output_format": "translated_only",
            "google_web": {"api_key": ""},
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()


def test_keyless_openai_compatible_local_endpoint_does_not_require_api_key():
    config = {
        "translation": {
            "backend": "openai_compatible",
            "output_format": "translated_only",
            "openai_compatible": {
                "api_key": "",
                "base_url": "http://192.168.65.2:11434/v1",
            },
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8082", "http://192.168.1.20:8082/v1"],
)
def test_keyless_local_relay_on_the_anthropic_protocol_requires_an_api_key(base_url):
    """The Anthropic messages path always sends a key, even to a local relay."""

    config = {
        "translation": {
            "backend": "openai_compatible",
            "output_format": "translated_only",
            "openai_compatible": {
                "api_key": "",
                "base_url": base_url,
                "protocol": "anthropic",
            },
        }
    }

    missing = first_missing_required_credential(config, scopes=("translation",))

    assert missing is not None
    assert missing.provider_id == "openai_compatible"
    assert missing.credential_id == "translation.openai_compatible.api_key"
    assert missing.focus_target == "backend_api_key"


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8082", "http://192.168.1.20:8082/v1"],
)
def test_keyless_local_relay_on_the_openai_protocol_needs_no_api_key(base_url):
    config = {
        "translation": {
            "backend": "openai_compatible",
            "output_format": "translated_only",
            "openai_compatible": {
                "api_key": "",
                "base_url": base_url,
                "protocol": "openai",
            },
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()


def test_keyless_hosted_relay_requires_an_api_key_on_either_protocol():
    for protocol in ("openai", "anthropic"):
        config = {
            "translation": {
                "backend": "openai_compatible",
                "output_format": "translated_only",
                "openai_compatible": {
                    "api_key": "",
                    "base_url": "https://relay.example.com/v1",
                    "protocol": protocol,
                },
            }
        }

        missing = first_missing_required_credential(config, scopes=("translation",))

        assert missing is not None, protocol
        assert missing.provider_id == "openai_compatible"


@pytest.mark.parametrize("legacy", ["anthropic_compatible", "grok_compatible"])
def test_raw_legacy_relay_section_with_a_key_counts_as_configured(legacy):
    """A snapshot from an older release keeps the key in the legacy section."""

    config = {
        "translation": {
            "backend": legacy,
            "output_format": "translated_only",
            legacy: {"api_key": "relay-key"},
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()


def test_raw_legacy_relay_section_without_a_key_is_reported_as_the_relay():
    config = {
        "translation": {
            "backend": "anthropic_compatible",
            "output_format": "translated_only",
            "anthropic_compatible": {"api_key": "", "base_url": "http://127.0.0.1:8082"},
        }
    }

    missing = first_missing_required_credential(config, scopes=("translation",))

    assert missing is not None
    assert missing.provider_id == "openai_compatible"
    assert missing.credential_id == "translation.openai_compatible.api_key"


def test_main_and_enabled_listen_asr_requirements_are_checked_and_deduplicated():
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"api_key": ""},
        },
        "vrc_listen": {
            "enabled": True,
            "asr_engine": "same_as_main",
        },
    }

    missing = missing_required_credentials(config, scopes=("asr",))

    assert len(missing) == 1
    assert missing[0].provider_id == "qwen3-asr"
    assert missing[0].credential_id == "asr.qwen3_asr.api_key"
    assert missing[0].focus_target == "qwen_api_key"


def test_local_qwen_asr_endpoint_does_not_require_api_key():
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {
                "api_key": "",
                "region": "custom",
                "base_url": "http://192.168.1.20:8000/v1",
            },
        }
    }

    assert missing_required_credentials(config, scopes=("asr",)) == ()


def test_inactive_listen_asr_is_skipped_only_in_active_mode():
    config = {
        "asr": {
            "engine": "sensevoice-small",
            "qwen3_asr": {"api_key": ""},
        },
        "vrc_listen": {
            "enabled": False,
            "asr_engine": "qwen3-asr",
        },
    }

    assert missing_required_credentials(config, scopes=("asr",)) == ()
    missing = missing_required_credentials(
        config,
        scopes=("asr",),
        active_only=False,
    )

    assert len(missing) == 1
    assert missing[0].provider_id == "qwen3-asr"
    assert missing[0].focus_target == "qwen_api_key"


def test_enabled_independent_listen_asr_is_checked_with_local_main_asr():
    config = {
        "asr": {
            "engine": "sensevoice-small",
            "qwen3_asr": {"api_key": ""},
        },
        "vrc_listen": {
            "enabled": True,
            "asr_engine": "qwen3-asr",
        },
    }

    missing = missing_required_credentials(config, scopes="asr")

    assert len(missing) == 1
    assert missing[0].provider_id == "qwen3-asr"


def test_api_tts_requirement_respects_enabled_state():
    config = {
        "tts": {
            "enabled": False,
            "engine": "qwen_tts",
            "qwen_tts": {"api_key": ""},
        }
    }

    assert missing_required_credentials(config, scopes=("tts",)) == ()
    missing = missing_required_credentials(
        config,
        scopes=("tts",),
        active_only=False,
    )

    assert len(missing) == 1
    assert missing[0].credential_id == "tts.qwen_tts.api_key"
    assert missing[0].focus_target == "tts_api_key"


def test_local_api_tts_endpoint_does_not_require_api_key():
    config = {
        "tts": {
            "enabled": True,
            "engine": "qwen_tts",
            "qwen_tts": {
                "api_key": "",
                "region": "custom",
                "base_url": "http://10.0.0.20:9000/api/v1",
            },
        }
    }

    assert missing_required_credentials(config, scopes=("tts",)) == ()


def test_nonblank_protected_secret_blob_counts_as_configured():
    config = {
        "translation": {
            "backend": "openai",
            "output_format": "translated_only",
            "openai": {"api_key": "dpapi:still-protected"},
        }
    }

    assert missing_required_credentials(config, scopes=("translation",)) == ()


def test_requirement_result_and_repr_never_contain_configured_credential_values():
    secret_value = "secret-value-that-must-not-leak"
    config = {
        "translation": {
            "backend": "openai",
            "output_format": "translated_only",
            "openai": {"api_key": secret_value},
        },
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"api_key": ""},
        },
    }

    missing = first_missing_required_credential(config, scopes=("translation", "asr"))

    assert missing is not None
    assert missing.scope == "asr"
    assert secret_value not in repr(missing)
    assert secret_value not in str(missing)


def test_missing_credential_copy_is_localized_without_secret_values():
    config = {
        "tts": {
            "enabled": True,
            "engine": "mimo_tts",
            "mimo_tts": {"api_key": ""},
        }
    }
    for language in SUPPORTED_UI_LANGUAGES:
        missing = first_missing_required_credential(
            config,
            scopes=("tts",),
            ui_language=language,
        )
        assert missing is not None
        assert missing.credential_label != "api_credential_label"
        assert tr(language, "api_credential_missing_title") != "api_credential_missing_title"
        message = tr(
            language,
            "api_credential_missing_message",
            provider=missing.provider_label,
            credential=missing.credential_label,
        )
        assert missing.provider_label in message
        assert "secret-value" not in message
        assert tr(language, "api_credential_open_settings") != "api_credential_open_settings"
