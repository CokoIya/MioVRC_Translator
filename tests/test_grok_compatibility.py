from __future__ import annotations

import copy
import sys
import types

import pytest

from src.translators.factory import OPENAI_COMPATIBLE_BACKENDS, create_translator
from src.utils import config_manager
from src.utils.credential_validation import first_missing_required_credential
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import (
    backend_base_url_is_editable,
    backend_model_is_editable,
    get_backend_label,
    get_backend_model_options,
    get_backend_value,
)


class _FakeStream:
    def __init__(self, fragments: tuple[str, ...]) -> None:
        self._fragments = fragments
        self.closed = False

    def __iter__(self):
        for fragment in self._fragments:
            yield types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        delta=types.SimpleNamespace(content=fragment)
                    )
                ]
            )

    def close(self) -> None:
        self.closed = True


class _StreamingCompletions:
    def __init__(self, *, reject_streaming: bool = False) -> None:
        self.reject_streaming = reject_streaming
        self.calls: list[dict] = []
        self.streams: list[_FakeStream] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs.get("stream"):
            if self.reject_streaming:
                raise ValueError("Unsupported parameter: stream")
            stream = _FakeStream(("翻", "訳"))
            self.streams.append(stream)
            return stream
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content="翻訳"),
                )
            ],
            usage=None,
        )


def _install_fake_openai(monkeypatch, *, reject_streaming: bool = False):
    instances = []

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.completions = _StreamingCompletions(
                reject_streaming=reject_streaming
            )
            self.chat = types.SimpleNamespace(completions=self.completions)
            instances.append(self)

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    return instances


def _grok_config(**overrides) -> dict:
    provider = {
        "api_key": "relay-key",
        "base_url": "https://relay.example.com/openai/v1",
        "model": "relay/Grok-Custom-Preview:Alpha",
        "timeout_s": 9,
        "max_retries": 0,
        "streaming": True,
        "custom_headers": {
            "X-Relay-Token": "header-secret",
            "HTTP-Referer": "https://mio.example",
        },
    }
    provider.update(overrides)
    return {
        "translation": {
            "backend": "grok_compatible",
            "grok_compatible": provider,
        }
    }


def test_grok_profile_is_distinct_editable_and_localized():
    assert "grok_compatible" in OPENAI_COMPATIBLE_BACKENDS
    assert get_backend_value("grok_compatible", "model") == "grok-4.6"
    assert get_backend_model_options("grok_compatible") == (
        "grok-4.6",
        "grok-4.5",
    )
    assert backend_base_url_is_editable("grok_compatible") is True
    assert backend_model_is_editable("grok_compatible") is True
    for language in ("zh-CN", "en", "ja", "ru", "ko"):
        assert get_backend_label("grok_compatible", language)


def test_grok_factory_preserves_relay_model_headers_and_timeout(monkeypatch):
    instances = _install_fake_openai(monkeypatch)
    translator = create_translator(_grok_config())

    client = instances[0]
    assert client.kwargs["api_key"] == "relay-key"
    assert client.kwargs["base_url"] == "https://relay.example.com/openai/v1"
    assert client.kwargs["timeout"] == 9.0
    assert client.kwargs["default_headers"] == {
        "X-Relay-Token": "header-secret",
        "HTTP-Referer": "https://mio.example",
    }
    assert translator.model == "relay/Grok-Custom-Preview:Alpha"
    assert translator._streaming_enabled is True
    assert client.kwargs["http_client"] is translator._http_client
    translator.close()


def test_grok_streaming_is_used_for_translation_and_rewrite(monkeypatch):
    instances = _install_fake_openai(monkeypatch)
    translator = create_translator(_grok_config())

    assert translator.translate("Hello", "en", "ja") == "翻訳"
    assert translator.rewrite_asr(
        "Hello there",
        "catgirl",
        language_hint="en",
    ) == "翻訳"

    calls = instances[0].completions.calls
    assert len(calls) == 2
    assert all(call["stream"] is True for call in calls)
    assert all(call["model"] == "relay/Grok-Custom-Preview:Alpha" for call in calls)
    assert all(stream.closed for stream in instances[0].completions.streams)
    translator.close()


def test_grok_falls_back_only_when_relay_explicitly_rejects_streaming(monkeypatch):
    instances = _install_fake_openai(monkeypatch, reject_streaming=True)
    translator = create_translator(_grok_config())

    assert translator.translate("First", "en", "ja") == "翻訳"
    assert translator.translate("Second", "en", "ja") == "翻訳"

    calls = instances[0].completions.calls
    assert calls[0]["stream"] is True
    assert "stream" not in calls[1]
    assert "stream" not in calls[2]
    assert translator._streaming_supported is False
    translator.close()


def test_grok_custom_model_and_endpoint_survive_config_normalization():
    config = {
        "ui": {"language": "en"},
        "translation": {
            "backend": "grok_compatible",
            "backend_source": "manual",
            "source_language": "en",
            "target_language": "ja",
            "language_pair_source": "manual",
            "grok_compatible": {
                "api_key": "relay-key",
                "base_url": "https://custom.grok-relay.example/api/openai/v1",
                "model": "Custom-GROK/router:model-001",
                "timeout_s": 17,
                "streaming": False,
                "custom_headers": {"X-Tenant": "player-one"},
            },
        },
    }
    loaded = copy.deepcopy(config)

    config_manager._ensure_translation_config(config, loaded=loaded)

    grok = config["translation"]["grok_compatible"]
    assert grok["base_url"] == "https://custom.grok-relay.example/api/openai/v1"
    assert grok["model"] == "Custom-GROK/router:model-001"
    assert grok["timeout_s"] == 17
    assert grok["streaming"] is False
    assert grok["custom_headers"] == {"X-Tenant": "player-one"}


def test_grok_custom_header_values_are_protected_at_rest(monkeypatch):
    config = _grok_config()
    monkeypatch.setattr(
        config_manager,
        "_protect_secret",
        lambda value: f"dpapi:v1:sealed:{value}" if value else "",
    )
    stored = config_manager._protect_config_for_storage(config)

    headers = stored["translation"]["grok_compatible"]["custom_headers"]
    assert headers["X-Relay-Token"] == "dpapi:v1:sealed:header-secret"
    assert headers["HTTP-Referer"] == "dpapi:v1:sealed:https://mio.example"
    assert config["translation"]["grok_compatible"]["custom_headers"] == {
        "X-Relay-Token": "header-secret",
        "HTTP-Referer": "https://mio.example",
    }

    monkeypatch.setattr(
        config_manager,
        "_unprotect_secret",
        lambda value: str(value).removeprefix("dpapi:v1:sealed:"),
    )
    runtime = config_manager._unprotect_config_for_runtime(stored)
    assert runtime["translation"]["grok_compatible"]["custom_headers"] == {
        "X-Relay-Token": "header-secret",
        "HTTP-Referer": "https://mio.example",
    }


def test_grok_missing_key_and_errors_are_actionable():
    missing = first_missing_required_credential(
        {
            "translation": {
                "backend": "grok_compatible",
                "grok_compatible": {"api_key": ""},
            }
        },
        scopes=("translation",),
        ui_language="en",
        active_only=False,
    )
    assert missing is not None
    assert missing.provider_id == "grok_compatible"
    assert missing.credential_id == "translation.grok_compatible.api_key"
    assert missing.focus_target == "backend_api_key"

    endpoint = format_translation_error(
        ValueError("Translation API base URL is malformed"),
        backend="grok_compatible",
        ui_language="en",
    )
    auth = format_translation_error(
        RuntimeError("401 Unauthorized: invalid api key"),
        backend="grok_compatible",
        ui_language="en",
    )
    rate_limit = format_translation_error(
        RuntimeError("429 rate limit exceeded"),
        backend="grok_compatible",
        ui_language="en",
    )
    assert endpoint.category == "endpoint"
    assert "Base URL" in endpoint.short_message
    assert auth.category == "auth"
    assert "Grok Compatible" in auth.short_message
    assert rate_limit.category == "quota"


@pytest.mark.parametrize(
    "headers",
    (
        {"Host": "relay.example.com"},
        {"X-Bad": "line\nbreak"},
        {"Bad Header": "value"},
        ["not", "an", "object"],
    ),
)
def test_invalid_custom_headers_are_rejected(headers):
    with pytest.raises(ValueError):
        normalize_openai_custom_headers(headers)
