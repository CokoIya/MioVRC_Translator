import sys
import types

import pytest

from src.translators.factory import OPENAI_COMPATIBLE_BACKENDS, create_translator
from src.utils.ui_config import (
    NVIDIA_TRANSLATION_BASE_URL,
    XIAOMI_TRANSLATION_BASE_URL_PAYG,
    backend_api_key_is_required,
    backend_base_url_is_editable,
    get_backend_value,
)


class _FakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.chat = types.SimpleNamespace(completions=_FakeChatCompletions())


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content="你好"),
                )
            ],
            usage=None,
        )


class _FakeAnthropic:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.messages = types.SimpleNamespace(create=lambda **_kwargs: None)


def test_local_ai_backend_allows_editable_base_url_and_empty_api_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    config = {
        "translation": {
            "backend": "local_ai",
            "local_ai": {
                "api_key": "",
                "base_url": "http://127.0.0.1:1234/v1",
                "model": "local-model",
            },
        }
    }

    translator = create_translator(config)

    assert backend_base_url_is_editable("local_ai") is True
    assert backend_api_key_is_required("local_ai") is False
    assert translator._client.kwargs["api_key"] == "local-ai"
    assert translator._client.kwargs["base_url"] == "http://127.0.0.1:1234/v1"
    assert translator.model == "local-model"


def test_online_backends_expose_network_overrides(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    config = {
        "translation": {
            "backend": "deepseek",
            "deepseek": {
                "api_key": "test-key",
                "base_url": "https://proxy.example.com/v1",
                "model": "deepseek-v4-flash",
                "timeout_s": "7",
                "max_retries": "2",
            },
        }
    }

    translator = create_translator(config)

    assert backend_base_url_is_editable("qianwen") is True
    assert backend_base_url_is_editable("deepseek") is True
    assert translator._client.kwargs["base_url"] == "https://proxy.example.com/v1"
    assert translator._client.kwargs["timeout"] == 7.0
    assert translator._client.kwargs["max_retries"] == 2


def test_new_ai_backends_use_openai_compatible_translator(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    xiaomi = create_translator(
        {
            "translation": {
                "backend": "xiaomi",
                "xiaomi": {
                    "api_key": "test-xiaomi",
                    "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
                    "model": "mimo-v2-flash",
                },
            }
        }
    )

    assert xiaomi._client.kwargs["api_key"] == "test-xiaomi"
    assert xiaomi._client.kwargs["base_url"] == XIAOMI_TRANSLATION_BASE_URL_PAYG
    assert xiaomi.model == "mimo-v2-flash"
    assert xiaomi._extra_body["thinking"]["type"] == "disabled"

    nvidia = create_translator(
        {
            "translation": {
                "backend": "nvidia",
                "nvidia": {
                    "api_key": "test-nvidia",
                    "base_url": NVIDIA_TRANSLATION_BASE_URL,
                    "model": "nvidia/llama-3.1-nemotron-nano-8b-v1",
                },
            }
        }
    )

    assert nvidia._client.kwargs["api_key"] == "test-nvidia"
    assert nvidia._client.kwargs["base_url"] == NVIDIA_TRANSLATION_BASE_URL
    assert nvidia.model == "nvidia/llama-3.1-nemotron-nano-8b-v1"


def test_xiaomi_backend_uses_mimo_token_budget_parameter(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    translator = create_translator(
        {
            "translation": {
                "backend": "xiaomi",
                "xiaomi": {
                    "api_key": "test-xiaomi",
                    "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
                    "model": "mimo-v2-flash",
                },
            }
        }
    )

    assert translator.translate("hello", "en", "zh") == "你好"
    kwargs = translator._client.chat.completions.last_kwargs
    assert kwargs["model"] == "mimo-v2-flash"
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["extra_body"]["thinking"]["type"] == "disabled"


@pytest.mark.parametrize("backend", sorted([*OPENAI_COMPATIBLE_BACKENDS, "anthropic"]))
def test_ai_backends_receive_roleplay_prompt_profile(monkeypatch, backend):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_FakeAnthropic))
    backend_cfg = {
        "api_key": "" if backend == "local_ai" else "test-key",
        "base_url": get_backend_value(backend, "base_url"),
        "model": get_backend_value(backend, "model"),
    }
    config = {
        "translation": {
            "backend": backend,
            "social": {
                "mode": "roleplay",
                "persona_preset": "frieren",
                "politeness": "polite",
                "tone": "cool",
                "persona_name": "Frieren Preset",
                "persona_prompt": "Use calm, understated wording.",
                "persona_glossary": "Calm and restrained\nShort, plain wording",
            },
            backend: backend_cfg,
        }
    }

    translator = create_translator(config)

    assert translator._prompt_profile == {
        "mode": "roleplay",
        "politeness": "polite",
        "tone": "cool",
        "persona_name": "Frieren Preset",
        "persona_prompt": "Use calm, understated wording.",
        "glossary": ["Calm and restrained", "Short, plain wording"],
    }


def test_standard_social_config_with_saved_preset_does_not_affect_translation(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    config = {
        "translation": {
            "backend": "qianwen",
            "social": {
                "mode": "standard",
                "persona_preset": "frieren",
                "politeness": "polite",
                "tone": "cool",
                "persona_name": "Saved Disabled Preset",
                "persona_prompt": "This should not affect normal translation.",
                "persona_glossary": "Should not appear",
            },
            "qianwen": {
                "api_key": "test-key",
                "base_url": get_backend_value("qianwen", "base_url"),
                "model": "qwen-mt-flash",
            },
        }
    }

    translator = create_translator(config)
    prompt = translator._build_prompt("hello", "en", "ja", context_source="mic")

    assert translator._prompt_profile == {}
    assert "Saved Disabled Preset" not in prompt
    assert "social style instructions" not in prompt
    assert translator._should_use_qwen_mt_translation_options("en", "ja")
