import socket
import sys
import types

import httpx
import pytest

from src.translators.factory import (
    FallbackTranslator,
    OPENAI_COMPATIBLE_BACKENDS,
    create_translator,
)
from src.translators.anthropic_translator import ANTHROPIC_HTTP_KEEPALIVE_EXPIRY_S
from src.translators.openai_translator import OPENAI_HTTP_KEEPALIVE_EXPIRY_S
from src.utils import provider_network
from src.utils.ui_config import (
    NVIDIA_TRANSLATION_BASE_URL,
    XIAOMI_TRANSLATION_BASE_URL_PAYG,
    backend_api_key_is_required,
    backend_base_url_is_editable,
    backend_model_is_selectable,
    get_backend_model_options,
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


class _FallbackFakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.chat = types.SimpleNamespace(completions=_FallbackFakeChatCompletions())


class _FallbackFakeChatCompletions:
    def create(self, **kwargs):
        if kwargs["model"] == "primary-fail":
            raise RuntimeError("primary unavailable")
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content=f"{kwargs['model']}:ok"),
                )
            ],
            usage=None,
        )


def test_local_ai_backend_allows_editable_base_url_and_empty_api_key(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
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
    assert translator._trust_env is False
    assert translator.model == "local-model"

    request = httpx.Request(
        "POST",
        "http://127.0.0.1:1234/v1/chat/completions",
        headers={"Authorization": "Bearer local-ai"},
    )
    for hook in translator._http_client._event_hooks["request"]:
        hook(request)
    assert "authorization" not in request.headers


def test_openai_compatible_backend_uses_custom_proxy_settings(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    config = {
        "translation": {
            "backend": "openai_compatible",
            "openai_compatible": {
                "api_key": "relay-key",
                "base_url": "https://relay.example.com/v1",
                "model": "gpt-proxy-router",
                "timeout_s": "9",
                "max_retries": "1",
            },
        }
    }

    translator = create_translator(config)

    assert "openai_compatible" in OPENAI_COMPATIBLE_BACKENDS
    assert backend_base_url_is_editable("openai_compatible") is True
    assert backend_api_key_is_required("openai_compatible") is True
    assert backend_model_is_selectable("openai_compatible") is True
    assert get_backend_model_options("openai_compatible")[0] == "gpt-5.6-sol"
    assert translator._client.kwargs["api_key"] == "relay-key"
    assert translator._client.kwargs["base_url"] == "https://relay.example.com/v1"
    assert translator._client.kwargs["timeout"] == 9.0
    assert translator._client.kwargs["max_retries"] == 1
    assert translator._client.kwargs["http_client"] is translator._http_client
    assert translator._trust_env is True
    assert OPENAI_HTTP_KEEPALIVE_EXPIRY_S == 60.0
    assert translator.model == "gpt-proxy-router"


def test_keyless_openai_compatible_local_endpoint_uses_direct_http(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("192.168.65.2", 0),
            )
        ],
    )
    config = {
        "translation": {
            "backend": "openai_compatible",
            "openai_compatible": {
                "api_key": "",
                "base_url": "http://host.docker.internal:11434/v1",
                "model": "local-compatible-model",
            },
        }
    }

    translator = create_translator(config)

    assert translator._client.kwargs["api_key"] == "local-ai"
    assert translator._trust_env is False
    request = httpx.Request(
        "POST",
        "http://host.docker.internal:11434/v1/chat/completions",
        headers={"Authorization": "Bearer local-ai"},
    )
    for hook in translator._http_client._event_hooks["request"]:
        hook(request)
    assert "authorization" not in request.headers
    assert request.url.host == "192.168.65.2"
    assert request.headers["host"] == "host.docker.internal:11434"


def test_docker_host_public_dns_result_is_rejected_before_client_creation(
    monkeypatch,
):
    class UnexpectedOpenAI:
        def __init__(self, **_kwargs):
            raise AssertionError("unsafe endpoint must not create an API client")

    monkeypatch.setitem(
        sys.modules,
        "openai",
        types.SimpleNamespace(OpenAI=UnexpectedOpenAI),
    )
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", 0),
            )
        ],
    )
    secret = "must-not-leak"

    with pytest.raises(ValueError, match="must use HTTPS") as exc_info:
        create_translator(
            {
                "translation": {
                    "backend": "local_ai",
                    "local_ai": {
                        "api_key": secret,
                        "base_url": "http://host.docker.internal:11434/v1",
                        "model": "local-model",
                    },
                }
            }
        )

    assert secret not in str(exc_info.value)


def test_credentialed_openai_proxy_rejects_plaintext_public_http(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    config = {
        "translation": {
            "backend": "openai_compatible",
            "openai_compatible": {
                "api_key": "relay-key",
                "base_url": "http://relay.example.com/v1",
                "model": "relay-model",
            },
        }
    }

    with pytest.raises(ValueError, match="must use HTTPS"):
        create_translator(config)


def test_keyless_local_ai_allows_literal_private_lan_http(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "local_ai",
                "local_ai": {
                    "api_key": "",
                    "base_url": "http://192.168.1.20:11434/v1",
                    "model": "local-model",
                },
            }
        }
    )

    assert translator._client.kwargs["base_url"] == "http://192.168.1.20:11434/v1"
    assert translator._trust_env is False


def test_credentialed_local_ai_allows_literal_private_lan_http(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "local_ai",
                "local_ai": {
                    "api_key": "private-key",
                    "base_url": "http://192.168.1.20:11434/v1",
                    "model": "local-model",
                },
            }
        }
    )

    assert translator._client.kwargs["api_key"] == "private-key"
    assert translator._client.kwargs["base_url"] == "http://192.168.1.20:11434/v1"
    assert translator._trust_env is False


def test_anthropic_compatible_backend_uses_custom_proxy_settings(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=_FakeAnthropic),
    )
    config = {
        "translation": {
            "backend": "anthropic_compatible",
            "anthropic_compatible": {
                "api_key": "relay-key",
                "base_url": "https://claude-relay.example.com",
                "model": "claude-router",
                "timeout_s": "11",
                "max_retries": "2",
            },
        }
    }

    translator = create_translator(config)

    assert backend_base_url_is_editable("anthropic_compatible") is True
    assert backend_api_key_is_required("anthropic_compatible") is True
    assert backend_model_is_selectable("anthropic_compatible") is True
    assert get_backend_model_options("anthropic_compatible")[0] == "claude-opus-5"
    assert translator._client.kwargs["api_key"] == "relay-key"
    assert translator._client.kwargs["base_url"] == "https://claude-relay.example.com"
    assert translator._client.kwargs["timeout"] == 11.0
    assert translator._client.kwargs["max_retries"] == 2
    assert translator._client.kwargs["http_client"] is translator._http_client
    assert ANTHROPIC_HTTP_KEEPALIVE_EXPIRY_S == 60.0
    assert translator.model == "claude-router"


@pytest.mark.parametrize(
    ("configured_url", "sdk_base_url"),
    (
        ("https://fast.fluapi.com/v1", "https://fast.fluapi.com"),
        (
            "https://claude-relay.example.com/gateway/v1/",
            "https://claude-relay.example.com/gateway",
        ),
        (
            "https://claude-relay.example.com/gateway/v1/messages",
            "https://claude-relay.example.com/gateway",
        ),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
    ),
)
def test_anthropic_compatible_normalizes_sdk_api_suffix(
    monkeypatch,
    configured_url,
    sdk_base_url,
):
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=_FakeAnthropic),
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "anthropic_compatible",
                "anthropic_compatible": {
                    "api_key": "relay-key",
                    "base_url": configured_url,
                    "model": "claude-router",
                },
            }
        }
    )

    assert translator._client.kwargs["base_url"] == sdk_base_url


def test_anthropic_compatible_rejects_plaintext_public_http(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=_FakeAnthropic),
    )

    with pytest.raises(ValueError, match="must use HTTPS"):
        create_translator(
            {
                "translation": {
                    "backend": "anthropic_compatible",
                    "anthropic_compatible": {
                        "api_key": "relay-key",
                        "base_url": "http://claude-relay.example.com/v1",
                        "model": "claude-router",
                    },
                }
            }
        )


def test_online_backends_expose_network_overrides(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
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
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    xiaomi = create_translator(
        {
            "translation": {
                "backend": "xiaomi",
                "xiaomi": {
                    "api_key": "test-xiaomi",
                    "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
                    "model": "mimo-v2.5-pro",
                },
            }
        }
    )

    assert xiaomi._client.kwargs["api_key"] == "test-xiaomi"
    assert xiaomi._client.kwargs["base_url"] == XIAOMI_TRANSLATION_BASE_URL_PAYG
    assert xiaomi.model == "mimo-v2.5-pro"
    assert xiaomi._extra_body["thinking"]["type"] == "disabled"

    nvidia = create_translator(
        {
            "translation": {
                "backend": "nvidia",
                "nvidia": {
                    "api_key": "test-nvidia",
                    "base_url": NVIDIA_TRANSLATION_BASE_URL,
                    "model": "nvidia/nemotron-3-nano-30b-a3b",
                },
            }
        }
    )

    assert nvidia._client.kwargs["api_key"] == "test-nvidia"
    assert nvidia._client.kwargs["base_url"] == NVIDIA_TRANSLATION_BASE_URL
    assert nvidia.model == "nvidia/nemotron-3-nano-30b-a3b"


def test_xiaomi_backend_uses_mimo_token_budget_parameter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "xiaomi",
                "xiaomi": {
                    "api_key": "test-xiaomi",
                    "base_url": XIAOMI_TRANSLATION_BASE_URL_PAYG,
                    "model": "mimo-v2.5-pro",
                },
            }
        }
    )

    assert translator.translate("hello", "en", "zh") == "你好"
    kwargs = translator._client.chat.completions.last_kwargs
    assert kwargs["model"] == "mimo-v2.5-pro"
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["extra_body"]["thinking"]["type"] == "disabled"


def test_translation_fallback_backend_is_used_after_primary_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FallbackFakeOpenAI)
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "openai",
                "fallback_backends": ["local_ai"],
                "openai": {
                    "api_key": "test-openai",
                    "base_url": "https://api.openai.com/v1",
                    "model": "primary-fail",
                },
                "local_ai": {
                    "api_key": "",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "fallback-ok",
                },
            }
        }
    )

    assert isinstance(translator, FallbackTranslator)
    assert translator.translate("hello", "en", "zh") == "fallback-ok:ok"


@pytest.mark.parametrize(
    "backend",
    sorted([*OPENAI_COMPATIBLE_BACKENDS, "anthropic", "anthropic_compatible"]),
)
def test_ai_backends_ignore_legacy_translation_persona_config(monkeypatch, backend):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_FakeAnthropic)
    )
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

    prompt = translator._build_prompt("hello", "en", "ja", context_source="manual")
    assert translator._prompt_profile == {}
    assert "Frieren Preset" not in prompt
    assert "calm, understated" not in prompt
    assert "social style instructions" not in prompt


def test_standard_social_config_with_saved_preset_does_not_affect_translation(
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
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


def test_qwen_tokyo_workspace_endpoint_uses_qwen_mt_request_handling(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )
    workspace_url = (
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    translator = create_translator(
        {
            "translation": {
                "backend": "qianwen",
                "qianwen": {
                    "api_key": "tokyo-key",
                    "region": "japan",
                    "base_url": workspace_url,
                    "model": "qwen-mt-plus",
                },
            }
        }
    )

    assert translator._client.kwargs["base_url"] == workspace_url
    assert translator._is_qwen_api_endpoint is True
    assert translator._uses_qwen_mt_translation_options is True


@pytest.mark.parametrize(
    "base_url",
    (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com.evil.test/compatible-mode/v1",
    ),
)
def test_qwen_tokyo_region_rejects_non_workspace_endpoints(monkeypatch, base_url):
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )

    with pytest.raises(ValueError, match="Tokyo workspace endpoint"):
        create_translator(
            {
                "translation": {
                    "backend": "qianwen",
                    "qianwen": {
                        "api_key": "tokyo-key",
                        "region": "japan",
                        "base_url": base_url,
                        "model": "qwen-mt-plus",
                    },
                }
            }
        )
