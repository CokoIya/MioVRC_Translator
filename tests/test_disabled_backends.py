from __future__ import annotations

from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.factory import create_translator
from src.translators.openai_translator import OpenAITranslator
from src.utils.ui_config import (
    DISABLED_TRANSLATION_BACKENDS,
    LEGACY_RELAY_BACKENDS,
    RELAY_BACKEND,
    get_backend_order,
    get_backend_value,
    normalize_backend,
)


def test_no_backend_is_disabled_and_legacy_relays_fold_into_the_relay() -> None:
    assert DISABLED_TRANSLATION_BACKENDS == frozenset()
    order = set(get_backend_order())
    assert "anthropic" in order
    assert normalize_backend("anthropic") == "anthropic"
    assert RELAY_BACKEND in order
    # The GPT/Grok/Claude-compatible backends were merged into one relay
    # backend; their ids only survive as aliases of it.
    assert set(LEGACY_RELAY_BACKENDS) == {"grok_compatible", "anthropic_compatible"}
    for legacy in LEGACY_RELAY_BACKENDS:
        assert legacy not in order
        assert normalize_backend(legacy) == RELAY_BACKEND


def test_factory_builds_the_claude_translator() -> None:
    translator = create_translator(
        {
            "translation": {
                "backend": "anthropic",
                "anthropic": {"api_key": "sk-ant-test-key-0000000000"},
            }
        }
    )
    try:
        assert isinstance(translator, AnthropicTranslator)
        assert translator.model == "claude-opus-5"
        assert translator._base_url == "https://api.anthropic.com"
        assert translator._provider_id == "anthropic"
    finally:
        translator.close()


def test_raw_legacy_claude_relay_config_builds_the_claude_translator() -> None:
    translator = create_translator(
        {
            "translation": {
                "backend": "anthropic_compatible",
                "anthropic_compatible": {"api_key": "sk-ant-test-key-0000000000"},
            }
        }
    )
    try:
        assert isinstance(translator, AnthropicTranslator)
        # A legacy section without endpoint/model used Claude's own defaults.
        assert translator.model == "claude-opus-5"
        assert translator._base_url == "https://api.anthropic.com"
        assert translator._provider_id == "anthropic_compatible"
    finally:
        translator.close()


def test_raw_legacy_grok_relay_config_builds_the_openai_translator_on_xai() -> None:
    translator = create_translator(
        {
            "translation": {
                "backend": "grok_compatible",
                "grok_compatible": {"api_key": "xai-test-key-0000000000"},
            }
        }
    )
    try:
        assert isinstance(translator, OpenAITranslator)
        # A legacy section without endpoint/model used x.ai's own defaults.
        assert translator._base_url == "https://api.x.ai/v1"
        assert translator._base_url == get_backend_value("xai", "base_url")
        assert translator.model == get_backend_value("xai", "model")
        assert translator._provider_id == RELAY_BACKEND
    finally:
        translator.close()
