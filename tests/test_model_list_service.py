"""fetch_backend_models: build the translator, read and filter its list."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from src.translators import model_list_service
from src.translators.model_listing import ListedModel, builtin_model_list
from src.utils.ui_config import RELAY_BACKEND, TRANSLATION_BACKENDS, model_list_source_key

RELAY_URL = "https://relay.example.com/v1"


class _FakeTranslator:
    def __init__(self, models=None, *, error=None, close_error=None):
        self._models = list(models or [])
        self._error = error
        self._close_error = close_error
        self.list_calls: list[float] = []
        self.closed = 0

    def list_models(self, *, timeout_s):
        self.list_calls.append(timeout_s)
        if self._error is not None:
            raise self._error
        return list(self._models)

    def close(self):
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error


class _Factory:
    """Records what fetch_backend_models asks the translator factory for."""

    def __init__(self, translator=None, *, error=None):
        self.translator = translator
        self.error = error
        self.calls: list[tuple[dict, str]] = []

    def __call__(self, trans_cfg, backend):
        assert isinstance(trans_cfg, Mapping)
        self.calls.append((dict(trans_cfg), backend))
        if self.error is not None:
            raise self.error
        return self.translator


@pytest.fixture
def factory(monkeypatch):
    def install(translator=None, *, error=None):
        stub = _Factory(translator, error=error)
        monkeypatch.setattr(model_list_service, "_create_translator_for_backend", stub)
        return stub

    return install


def _relay_config(**relay):
    section = {"api_key": "sk-test", "base_url": RELAY_URL, "model": "gpt-6-luna", "protocol": "openai"}
    section.update(relay)
    return {"translation": {"backend": RELAY_BACKEND, RELAY_BACKEND: section}}


def test_success_returns_the_filtered_remote_list(factory):
    translator = _FakeTranslator(
        [
            ListedModel("openai/gpt-6-luna"),
            ListedModel("text-embedding-3-small"),
            ListedModel("gpt-image-1", {"supported_endpoint_types": ["image-generation", "openai"]}),
            ListedModel("claude-sonnet-5", {"supported_endpoint_types": ["anthropic", "openai"]}),
            ListedModel("deepseek-v4-pro"),
        ]
    )
    stub = factory(translator)

    result = model_list_service.fetch_backend_models(_relay_config(), timeout_s=7.5)

    assert result.source == "remote"
    assert result.remote is True
    assert result.error is None
    assert result.backend == RELAY_BACKEND
    assert result.models == ("openai/gpt-6-luna", "claude-sonnet-5")
    assert result.hidden_count == 3
    assert result.source_key == model_list_source_key(RELAY_BACKEND, RELAY_URL, "openai")
    assert stub.calls[0][1] == RELAY_BACKEND
    assert translator.list_calls == [7.5]
    assert translator.closed == 1


def test_list_errors_fall_back_to_the_builtin_list_with_the_error(factory):
    error = RuntimeError("connection reset")
    translator = _FakeTranslator(error=error)
    factory(translator)

    result = model_list_service.fetch_backend_models(_relay_config())

    assert result.source == "builtin"
    assert result.remote is False
    assert result.error is error
    assert result.models == builtin_model_list(RELAY_BACKEND)
    assert result.source_key == model_list_source_key(RELAY_BACKEND, RELAY_URL, "openai")
    assert translator.closed == 1


def test_translator_construction_errors_are_reported(factory):
    error = ValueError("Relay Compatible API Key is not configured")
    factory(error=error)

    result = model_list_service.fetch_backend_models(_relay_config(api_key=""))

    assert result.source == "builtin"
    assert result.error is error


def test_an_empty_filtered_list_is_an_error(factory):
    translator = _FakeTranslator([ListedModel("tts-1"), ListedModel("whisper-1")])
    factory(translator)

    result = model_list_service.fetch_backend_models(_relay_config())

    assert result.source == "builtin"
    assert isinstance(result.error, RuntimeError)
    # The settings window recognises this wording as "no usable models".
    assert "no usable chat models" in str(result.error)
    assert translator.closed == 1


def test_a_translator_without_list_models_is_reported_and_closed(factory):
    class _NoListing:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    translator = _NoListing()
    factory(translator)

    result = model_list_service.fetch_backend_models(_relay_config())

    assert result.source == "builtin"
    assert isinstance(result.error, RuntimeError)
    assert translator.closed == 1


def test_close_failures_never_escape(factory):
    translator = _FakeTranslator([ListedModel("gpt-6-luna")], close_error=OSError("socket already closed"))
    factory(translator)

    result = model_list_service.fetch_backend_models(_relay_config())

    assert result.source == "remote"
    assert result.models == ("gpt-6-luna",)
    assert translator.closed == 1


@pytest.mark.parametrize("backend", ["doubao", "google_web", "microsoft_edge_web", "deepl"])
def test_backends_without_a_remote_list_use_the_builtin_list(factory, backend):
    stub = factory(error=AssertionError("no translator may be built"))

    result = model_list_service.fetch_backend_models(
        {"translation": {"backend": backend, backend: {"api_key": "key"}}}
    )

    assert stub.calls == []
    assert result.backend == backend
    assert result.source == "builtin"
    assert result.error is None
    assert result.models == builtin_model_list(backend)


def test_a_raw_legacy_claude_relay_config_is_merged_first(factory):
    translator = _FakeTranslator(
        [
            ListedModel("deepseek-flash", {"supported_endpoint_types": ["openai"]}),
            ListedModel("claude-sonnet-5", {"supported_endpoint_types": ["anthropic"]}),
            ListedModel("gpt-5.4-mini"),
        ]
    )
    stub = factory(translator)
    config = {
        "translation": {
            "backend": "anthropic_compatible",
            "anthropic_compatible": {
                "api_key": "sk-ant",
                "base_url": "https://claude-relay.example.com",
                "model": "claude-sonnet-5",
            },
        }
    }

    result = model_list_service.fetch_backend_models(config)

    trans_cfg, backend = stub.calls[0]
    assert backend == RELAY_BACKEND
    assert trans_cfg["backend"] == RELAY_BACKEND
    assert "anthropic_compatible" not in trans_cfg
    relay = trans_cfg[RELAY_BACKEND]
    assert relay["protocol"] == "anthropic"
    assert relay["base_url"] == "https://claude-relay.example.com"
    assert result.backend == RELAY_BACKEND
    assert result.source_key == model_list_source_key(
        RELAY_BACKEND, "https://claude-relay.example.com", "anthropic"
    )
    # Filtered for the Anthropic protocol: Claude first, an OpenAI-only
    # channel model hidden.
    assert result.models == ("claude-sonnet-5", "gpt-5.4-mini")
    # The caller's snapshot is not modified.
    assert "anthropic_compatible" in config["translation"]


def test_a_raw_legacy_grok_relay_config_uses_the_openai_protocol(factory):
    stub = factory(_FakeTranslator([ListedModel("grok-4.3")]))
    config = {
        "translation": {
            "backend": "grok_compatible",
            "grok_compatible": {"api_key": "xai-key", "base_url": "https://grok-relay.example.com/v1"},
        }
    }

    result = model_list_service.fetch_backend_models(config)

    trans_cfg, backend = stub.calls[0]
    assert backend == RELAY_BACKEND
    assert trans_cfg[RELAY_BACKEND]["protocol"] == "openai"
    assert result.models == ("grok-4.3",)
    assert result.source_key.endswith("|openai")


def test_anthropic_relay_without_endpoint_uses_the_anthropic_defaults(factory):
    factory(error=RuntimeError("offline"))
    config = {"translation": {"backend": RELAY_BACKEND, RELAY_BACKEND: {"api_key": "k", "protocol": "anthropic"}}}

    result = model_list_service.fetch_backend_models(config)

    anthropic = TRANSLATION_BACKENDS["anthropic"]
    assert result.source_key == model_list_source_key(RELAY_BACKEND, anthropic["base_url"], "anthropic")
    assert result.models == (anthropic["model"],)


def test_provider_backend_list_uses_its_own_filters(factory):
    translator = _FakeTranslator(
        [ListedModel("claude-haiku-4-5-20251001"), ListedModel("claude-opus-5"), ListedModel("claude-3-haiku-20240307")]
    )
    stub = factory(translator)

    result = model_list_service.fetch_backend_models(
        {"translation": {"backend": "anthropic", "anthropic": {"api_key": "sk-ant"}}}
    )

    assert stub.calls[0][1] == "anthropic"
    assert result.models == ("claude-opus-5", "claude-haiku-4-5")
    assert result.hidden_count == 1
    assert result.source_key == model_list_source_key(
        "anthropic", TRANSLATION_BACKENDS["anthropic"]["base_url"]
    )


@pytest.mark.parametrize("config", [{}, {"translation": None}, {"translation": []}, None])
def test_malformed_snapshots_do_not_raise(factory, config):
    factory(error=RuntimeError("no key"))

    result = model_list_service.fetch_backend_models(config)

    assert result.source == "builtin"
    assert result.models
