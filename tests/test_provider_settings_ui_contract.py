from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.utils.i18n import UI_TEXTS
from src.utils.localization import SUPPORTED_UI_LANGUAGES, placeholder_fields
from src.utils.provider_settings import (
    AI_PROVIDER_BACKENDS,
    PROVIDER_CHOICES,
    parse_optional_timeout,
    preserve_provider_id,
    resolve_tabbed_provider_authority,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_ROOT = PROJECT_ROOT / "src" / "ui_qt" / "settings"


def test_provider_choices_keep_official_and_compatible_paths_distinct():
    provider_ids = tuple(backend for _label_key, backend in PROVIDER_CHOICES)

    assert AI_PROVIDER_BACKENDS == (
        "openai",
        "openai_compatible",
        "anthropic",
        "anthropic_compatible",
        "xai",
        "grok_compatible",
    )
    assert len(provider_ids) == len(set(provider_ids))
    assert {
        "openai",
        "openai_compatible",
        "anthropic",
        "anthropic_compatible",
        "xai",
        "grok_compatible",
    }.issubset(provider_ids)


def test_provider_id_preservation_does_not_migrate_unknown_backends():
    assert preserve_provider_id("future_vendor_relay") == "future_vendor_relay"
    assert preserve_provider_id("OpenAI/CaseSensitive-ID") == "OpenAI/CaseSensitive-ID"
    assert preserve_provider_id("qwen") == "qianwen"
    assert preserve_provider_id("") == "openai"


@pytest.mark.parametrize(
    (
        "original",
        "api_models",
        "quick_provider",
        "quick_key",
        "expected",
    ),
    [
        (
            {
                "backend": "openai",
                "openai": {"api_key": "old-openai"},
                "openai_compatible": {"api_key": "old-relay"},
            },
            {
                "backend": "openai_compatible",
                "openai": {"api_key": "old-openai"},
                "openai_compatible": {"api_key": "new-relay"},
            },
            "openai",
            "old-openai",
            ("openai_compatible", "new-relay"),
        ),
        (
            {
                "backend": "openai",
                "openai": {"api_key": "old-openai"},
                "qianwen": {"api_key": "old-qwen"},
            },
            {
                "backend": "openai",
                "openai": {"api_key": "old-openai"},
                "qianwen": {"api_key": "old-qwen"},
            },
            "qianwen",
            "new-qwen",
            ("qianwen", "new-qwen"),
        ),
        (
            {"backend": "openai", "openai": {"api_key": "old-openai"}},
            {"backend": "openai", "openai": {"api_key": ""}},
            "openai",
            "old-openai",
            ("openai", ""),
        ),
        (
            {"backend": "openai", "openai": {"api_key": "old-openai"}},
            {"backend": "openai", "openai": {"api_key": "old-openai"}},
            "openai",
            "new-openai",
            ("openai", "new-openai"),
        ),
    ],
)
def test_tabbed_provider_authority_preserves_intentional_edits(
    original,
    api_models,
    quick_provider,
    quick_key,
    expected,
):
    assert resolve_tabbed_provider_authority(
        original,
        api_models,
        quick_provider,
        quick_key,
    ) == expected


@pytest.mark.parametrize(
    ("value", "minimum", "maximum", "optional", "expected"),
    [
        ("", 0.1, 120.0, True, None),
        ("0.1", 0.1, 120.0, True, 0.1),
        ("120", 0.1, 120.0, True, 120.0),
        ("15", 3.0, 120.0, False, 15.0),
    ],
)
def test_provider_timeout_parser_preserves_optional_inheritance(
    value,
    minimum,
    maximum,
    optional,
    expected,
):
    assert parse_optional_timeout(
        value,
        minimum=minimum,
        maximum=maximum,
        optional=optional,
    ) == expected


@pytest.mark.parametrize("value", ["", "nan", "inf", "0.09", "121", "not-a-number"])
def test_provider_timeout_parser_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_optional_timeout(
            value,
            minimum=0.1,
            maximum=120.0,
            optional=False,
        )


def test_provider_settings_copy_is_complete_and_localized():
    keys = {
        label_key for label_key, _backend in PROVIDER_CHOICES
    } | {
        "openai_official_config",
        "openai_compatible_config",
        "anthropic_official_config",
        "anthropic_compatible_config",
        "xai_official_config",
        "grok_compatible_config",
        "provider_hint_openai_official",
        "provider_hint_openai_compatible",
        "provider_hint_anthropic_official",
        "provider_hint_anthropic_compatible",
        "provider_hint_xai_official",
        "provider_hint_grok_compatible",
        "model_id_preserved_hint",
        "official_base_url_read_only_hint",
        "relay_base_url_hint",
        "timeout_settings",
        "connect_timeout",
        "pool_timeout",
        "read_timeout",
        "write_timeout",
        "wall_timeout",
        "optional_timeout_placeholder",
        "timeout_settings_hint",
        "current_provider_unavailable",
        "timeout_value_invalid",
        "invalid_base_url",
        "connection_test_invalid_settings",
    }

    for key in keys:
        english = UI_TEXTS["en"][key]
        fields = placeholder_fields(english)
        for language in SUPPORTED_UI_LANGUAGES:
            value = UI_TEXTS[language].get(key)
            assert value and value != key, (language, key)
            assert placeholder_fields(value) == fields, (language, key)
            if language != "en" and key not in {
                "provider_deepseek",
                "provider_gemini",
                "provider_qwen",
            }:
                assert value != english, (language, key)


def test_connection_buttons_dispatch_to_real_connection_test():
    source_path = SETTINGS_ROOT / "api_models_tab.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    test_provider = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_test_provider"
    )
    called_names = {
        node.func.id
        for node in ast.walk(test_provider)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "test_translation_connection" in called_names


def test_tabbed_provider_sources_do_not_normalize_unknown_ids_to_openai():
    for filename in (
        "api_models_tab.py",
        "quick_setup_tab.py",
        "settings_window_tabbed.py",
    ):
        source = (SETTINGS_ROOT / filename).read_text(encoding="utf-8")
        assert "normalize_backend(" not in source, filename


def test_provider_settings_validation_never_displays_raw_exception_text():
    api_models = (SETTINGS_ROOT / "api_models_tab.py").read_text(encoding="utf-8")
    tabbed_window = (SETTINGS_ROOT / "settings_window_tabbed.py").read_text(
        encoding="utf-8"
    )

    assert "error=str(exc)" not in api_models
    assert 'error=self._t("unknown_error")' in api_models
    assert "message = str(exc)" not in tabbed_window
    assert 'error=tr(self._ui_language, "unknown_error")' in tabbed_window
