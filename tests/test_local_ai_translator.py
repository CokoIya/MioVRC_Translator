import sys
import types

from src.translators.factory import create_translator
from src.utils.ui_config import backend_base_url_is_editable, backend_api_key_is_required


class _FakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


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
