"""Per-model request settings of the Claude translator (restored after 1.4.1)."""

from __future__ import annotations

import sys
import types

import pytest

from src.translators.anthropic_translator import (
    REFUSAL_FALLBACK_BETA,
    AnthropicTranslator,
    _strip_internal_tags,
)
from src.utils import config_manager


class _RecordingMessages:
    def __init__(self, response):
        self.calls: list[dict] = []
        self._response = response

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropic:
    response = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = _RecordingMessages(type(self).response)


def _text_response(text: str, stop_reason: str = "end_turn", stop_details=None):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=None,
    )


@pytest.fixture
def make_translator(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_FakeAnthropic)
    )
    created: list[AnthropicTranslator] = []

    def build(model: str, *, base_url: str = "https://api.anthropic.com", **extra):
        translator = AnthropicTranslator(
            api_key="sk-ant-test-key-0000000000",
            model=model,
            base_url=base_url,
            **extra,
        )
        created.append(translator)
        return translator

    yield build
    for translator in created:
        translator.close()


def _request(translator: AnthropicTranslator) -> dict:
    kwargs = {
        "model": translator.model,
        "system": "Translate.",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
    }
    translator._apply_model_request_defaults(kwargs)
    return kwargs


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-5"])
def test_models_that_think_by_default_get_thinking_switched_off(make_translator, model):
    kwargs = _request(make_translator(model))

    assert kwargs["thinking"] == {"type": "disabled"}
    assert "output_config" not in kwargs
    assert kwargs["max_tokens"] == 64
    assert kwargs["system"].startswith("Translate.")
    assert "internal or system XML tags" in kwargs["system"]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1"])
def test_models_that_always_think_run_at_low_effort_with_headroom(make_translator, model):
    kwargs = _request(make_translator(model))

    # Disabling thinking would be rejected with a 400 on these models.
    assert "thinking" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] >= 2048


@pytest.mark.parametrize(
    "model", ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
)
def test_models_that_do_not_think_unless_asked_are_left_alone(make_translator, model):
    kwargs = _request(make_translator(model))

    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert "extra_body" not in kwargs
    assert kwargs["system"] == "Translate."


def test_refusal_fallback_is_requested_only_on_the_official_endpoint(make_translator):
    official = _request(make_translator("claude-opus-5"))
    relay = _request(
        make_translator("claude-opus-5", base_url="https://claude-relay.example.com")
    )

    assert official["extra_body"] == {"fallbacks": "default"}
    assert official["extra_headers"]["anthropic-beta"] == REFUSAL_FALLBACK_BETA
    assert "extra_body" not in relay
    assert "extra_headers" not in relay


def test_refusal_fallback_keeps_configured_beta_headers(make_translator):
    translator = make_translator(
        "claude-opus-5", custom_headers={"anthropic-beta": "some-beta-2026-01-01"}
    )

    betas = _request(translator)["extra_headers"]["anthropic-beta"].split(",")

    assert betas == ["some-beta-2026-01-01", REFUSAL_FALLBACK_BETA]


def test_refusal_fallback_can_be_switched_off(make_translator):
    kwargs = _request(make_translator("claude-opus-5", refusal_fallbacks=False))

    assert "extra_body" not in kwargs


def test_a_refused_request_is_a_safety_failure_not_a_subtitle(make_translator):
    _FakeAnthropic.response = _text_response(
        "partial",
        stop_reason="refusal",
        stop_details=types.SimpleNamespace(category="cyber"),
    )
    translator = make_translator("claude-opus-5")

    with pytest.raises(RuntimeError, match="safety policy.*category=cyber"):
        translator._message_request_unbounded(_request(translator))


def test_leaked_internal_tags_are_removed_from_the_output(make_translator):
    _FakeAnthropic.response = _text_response("<thinking>plan</thinking>こんにちは")
    translator = make_translator("claude-opus-5")

    output, _response = translator._message_request_unbounded(_request(translator))

    assert output == "こんにちは"


def test_strip_internal_tags_leaves_ordinary_text_alone():
    assert _strip_internal_tags("a < b and 3 > 2") == "a < b and 3 > 2"
    assert _strip_internal_tags("</thinking>ok") == "ok"
    assert _strip_internal_tags("") == ""


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        ("claude-opus-4-8", "claude-opus-4-8"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-opus-5-5", "claude-opus-5-5"),
        ("claude-3-5-sonnet-20241022", "claude-opus-5"),
        ("claude-opus-4-1-20250805", "claude-opus-5"),
    ],
)
def test_saved_official_claude_models_keep_served_families(saved, expected):
    config = {
        "translation": {
            "backend": "anthropic",
            "backend_source": "manual",
            "anthropic": {"model": saved},
        }
    }

    config_manager._ensure_translation_config(
        config, loaded={"translation": dict(config["translation"])}
    )

    assert config["translation"]["anthropic"]["model"] == expected
