"""Per-model request settings of the Claude translator (restored after 1.4.1)."""

from __future__ import annotations

import copy
import sys
import types

import anthropic
import httpx
import openai
import pytest

from src.translators import factory
from src.translators.anthropic_translator import (
    ALWAYS_THINKING_MIN_MAX_TOKENS,
    REFUSAL_FALLBACK_BETA,
    AnthropicTranslator,
    _strip_internal_tags,
    provider_error_status,
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


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-5-5"])
def test_models_that_think_by_default_get_thinking_switched_off(make_translator, model):
    kwargs = _request(make_translator(model))

    assert kwargs["thinking"] == {"type": "disabled"}
    assert "output_config" not in kwargs
    assert kwargs["max_tokens"] == 64
    assert kwargs["system"].startswith("Translate.")
    assert "internal or system XML tags" in kwargs["system"]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5"])
def test_models_that_always_think_run_at_low_effort_with_headroom(make_translator, model):
    kwargs = _request(make_translator(model))

    # Disabling thinking would be rejected with a 400 on these models.
    assert "thinking" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] >= 2048


def test_sonnet_5_5_uses_its_lowest_switch_without_extra_headroom(make_translator):
    kwargs = _request(make_translator("claude-sonnet-5-5"))

    # Sonnet 5.5 rejects ``disabled``; ``between_tools`` means no up-front
    # thinking for a request without tools.
    assert kwargs["thinking"] == {"type": "between_tools"}
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] == 64


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
        # Retired models move to the current model of the same family.
        ("claude-3-5-sonnet-20241022", "claude-sonnet-5-5"),
        ("claude-opus-4-1-20250805", "claude-opus-4-8"),
        ("claude-3-5-haiku-20241022", "claude-haiku-5-5"),
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


# --- Thinking ladder (a model that rejects the switch it was sent) -----------

_DISABLED_REJECTED = (
    "thinking.type: disabled is not supported for this model. "
    "Use adaptive thinking instead."
)
_BETWEEN_TOOLS_REJECTED = "thinking.type: between_tools is not supported for this model."


def _anthropic_error(status: int, message: str) -> anthropic.APIStatusError:
    """The error the Anthropic SDK raises for an HTTP error answer."""

    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json=body)
    return anthropic.APIStatusError(
        f"Error code: {status} - {body}", response=response, body=body
    )


class _ScriptedMessages:
    """Raises the scripted errors in order, then answers normally."""

    def __init__(self, *outcomes: BaseException) -> None:
        self.calls: list[dict] = []
        self._outcomes = list(outcomes)

    def create(self, **kwargs):
        self.calls.append(
            copy.deepcopy(
                {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"messages", "system", "timeout"}
                }
            )
        )
        if self._outcomes:
            raise self._outcomes.pop(0)
        return _text_response("こんにちは")


def _scripted(translator: AnthropicTranslator, *outcomes: BaseException) -> _ScriptedMessages:
    messages = _ScriptedMessages(*outcomes)
    translator._client.messages = messages
    return messages


def test_thinking_ladder_steps_down_and_keeps_the_accepted_switch(make_translator):
    # A Claude id newer than this code: it thinks by default like Opus 5.
    translator = make_translator("claude-opus-5-7")
    messages = _scripted(translator, _anthropic_error(400, _DISABLED_REJECTED))

    assert translator.translate("hello", "en", "ja") == "こんにちは"
    assert [call.get("thinking") for call in messages.calls] == [
        {"type": "disabled"},
        {"type": "between_tools"},
    ]
    assert messages.calls[1]["output_config"] == {"effort": "low"}
    assert translator._thinking_mode_override == "between_tools"

    translator.translate("good morning", "en", "ja")

    assert len(messages.calls) == 3
    assert messages.calls[2]["thinking"] == {"type": "between_tools"}


def test_thinking_ladder_ends_at_low_effort_with_room_for_the_reply(make_translator):
    translator = make_translator("claude-opus-5-7")
    messages = _scripted(
        translator,
        _anthropic_error(400, _DISABLED_REJECTED),
        _anthropic_error(400, _BETWEEN_TOOLS_REJECTED),
    )

    assert translator.translate("hello", "en", "ja") == "こんにちは"

    last = messages.calls[-1]
    assert len(messages.calls) == 3
    assert "thinking" not in last
    assert last["output_config"] == {"effort": "low"}
    assert last["max_tokens"] >= ALWAYS_THINKING_MIN_MAX_TOKENS
    assert translator._thinking_mode_override == "adaptive_low"


def test_thinking_ladder_accepts_a_422_that_names_the_setting(make_translator):
    translator = make_translator("claude-sonnet-5")
    messages = _scripted(
        translator,
        _anthropic_error(422, "thinking cannot be disabled for this model"),
    )

    translator.translate("hello", "en", "ja")

    assert messages.calls[-1]["thinking"] == {"type": "between_tools"}
    assert translator._thinking_mode_override == "between_tools"


@pytest.mark.parametrize("status", [401, 403, 404, 408, 409, 429, 500, 503, 529])
def test_thinking_ladder_ignores_errors_that_are_not_a_rejected_switch(
    make_translator, status
):
    translator = make_translator("claude-opus-5")
    # Relays echo the model id (here one containing "thinking") and even the
    # setting name in errors that have nothing to do with the switch.
    messages = _scripted(
        translator,
        _anthropic_error(
            status, "claude-opus-5-thinking: thinking.type disabled; upstream busy"
        ),
    )

    with pytest.raises(anthropic.APIStatusError):
        translator.translate("hello", "en", "ja")

    assert len(messages.calls) == 1
    assert translator._thinking_mode_override == ""


@pytest.mark.parametrize(
    "error",
    [
        _anthropic_error(400, "max_tokens: 0 is below the minimum of 1"),
        # Only the model id mentions thinking; the switch itself is fine.
        _anthropic_error(400, "model: claude-opus-5-thinking is not available"),
        RuntimeError(_DISABLED_REJECTED),
    ],
    ids=["400-about-something-else", "400-model-id-only", "no-http-status"],
)
def test_thinking_ladder_needs_an_http_rejection_of_the_switch(make_translator, error):
    translator = make_translator("claude-opus-5")
    messages = _scripted(translator, error)

    with pytest.raises(type(error)):
        translator.translate("hello", "en", "ja")

    assert len(messages.calls) == 1
    assert translator._thinking_mode_override == ""


def test_stepped_down_switch_is_kept_only_after_a_request_succeeds(make_translator):
    translator = make_translator("claude-opus-5")
    messages = _scripted(
        translator,
        _anthropic_error(400, _DISABLED_REJECTED),
        _anthropic_error(429, "rate limited, retry later"),
    )

    with pytest.raises(anthropic.APIStatusError):
        translator.translate("hello", "en", "ja")

    assert [call["thinking"] for call in messages.calls] == [
        {"type": "disabled"},
        {"type": "between_tools"},
    ]
    assert translator._thinking_mode_override == ""

    translator.translate("good morning", "en", "ja")

    assert messages.calls[-1]["thinking"] == {"type": "disabled"}


# --- Relay ids (Anthropic protocol) ---------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected_thinking"),
    [
        ("anthropic/claude-sonnet-5.5", {"type": "between_tools"}),
        ("claude-sonnet-5.5", {"type": "between_tools"}),
        ("anthropic/claude-haiku-5.5", {"type": "disabled"}),
    ],
)
def test_relay_claude_ids_are_matched_by_family_and_sent_verbatim(
    make_translator, model, expected_thinking
):
    translator = make_translator(
        model,
        base_url="https://claude-relay.example.com",
        provider_id="anthropic_compatible",
    )

    kwargs = _request(translator)

    assert kwargs["thinking"] == expected_thinking
    assert kwargs["model"] == model


def test_relay_always_thinking_claude_ids_get_low_effort_and_headroom(make_translator):
    translator = make_translator(
        "anthropic/claude-opus-5.5",
        base_url="https://claude-relay.example.com",
        provider_id="anthropic_compatible",
    )

    kwargs = _request(translator)

    assert "thinking" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] >= ALWAYS_THINKING_MIN_MAX_TOKENS


def test_official_endpoint_ids_are_not_rewritten_for_rule_matching(make_translator):
    translator = make_translator("claude-opus-5.5")

    # Anthropic's own ids are hyphenated; a dotted id there is not Opus 5.5.
    assert translator._family_model_id() == "claude-opus-5.5"


# --- Non-Claude models on the relay's Anthropic protocol (TP-2) -------------------


def _relay_translator(make_translator, model: str) -> AnthropicTranslator:
    return make_translator(
        model,
        base_url="https://relay.example.com",
        provider_id="anthropic_compatible",
    )


@pytest.mark.parametrize(
    "model",
    [
        "deepseek-flash",
        "deepseek/deepseek-v4.1-flash",
        "glm-4.7",
        "z-ai/glm-4.7-flash",
        "kimi-k2.6",
        "moonshotai/kimi-k2.6",
    ],
)
def test_relay_vendor_models_get_thinking_switched_off(make_translator, model):
    kwargs = _request(_relay_translator(make_translator, model))

    # Their Anthropic-format endpoints take the same switch as Claude.
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["model"] == model
    assert kwargs["max_tokens"] == 64
    assert "output_config" not in kwargs
    # The Claude-specific tag instruction is not added to other vendors.
    assert kwargs["system"] == "Translate."


@pytest.mark.parametrize("model", ["glm-5.3", "glm-5.3-flash", "z-ai/glm-5.3-flash"])
def test_relay_glm_5_3_runs_at_low_effort_with_headroom(make_translator, model):
    kwargs = _request(_relay_translator(make_translator, model))

    # GLM-5.3 cannot stop thinking: no switch, low effort, room for the reply.
    assert "thinking" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] >= ALWAYS_THINKING_MIN_MAX_TOKENS


@pytest.mark.parametrize(
    "model", ["kimi-k3", "moonshotai/kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"]
)
def test_relay_always_thinking_kimi_gets_headroom_but_no_switch(make_translator, model):
    kwargs = _request(_relay_translator(make_translator, model))

    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert kwargs["max_tokens"] >= ALWAYS_THINKING_MIN_MAX_TOKENS


@pytest.mark.parametrize("model", ["deepseek-flash", "glm-5.3-flash", "kimi-k3"])
def test_vendor_thinking_rules_apply_only_to_the_relay(make_translator, model):
    kwargs = _request(make_translator(model))

    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert kwargs["max_tokens"] == 64


def test_relay_unknown_families_are_left_alone(make_translator):
    kwargs = _request(_relay_translator(make_translator, "some-relay-model"))

    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert kwargs["max_tokens"] == 64


def test_relay_vendor_switch_rejection_steps_down_like_claude(make_translator):
    translator = _relay_translator(make_translator, "deepseek-flash")
    messages = _scripted(
        translator,
        _anthropic_error(400, "thinking.type: disabled is not supported for this model."),
        _anthropic_error(400, _BETWEEN_TOOLS_REJECTED),
    )

    assert translator.translate("hello", "en", "ja") == "こんにちは"

    assert [call.get("thinking") for call in messages.calls] == [
        {"type": "disabled"},
        {"type": "between_tools"},
        None,
    ]
    assert messages.calls[-1]["output_config"] == {"effort": "low"}
    assert messages.calls[-1]["max_tokens"] >= ALWAYS_THINKING_MIN_MAX_TOKENS
    assert translator._thinking_mode_override == "adaptive_low"


def test_relay_built_by_the_factory_switches_deepseek_thinking_off(make_translator):
    # make_translator installed the fake SDK; build the relay like the app does.
    translator = factory._create_translator_for_backend(
        {
            "backend": "openai_compatible",
            "openai_compatible": {
                "api_key": "sk-test-not-a-real-key-0000",
                "protocol": "anthropic",
                "model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/anthropic",
            },
        },
        "openai_compatible",
    )
    try:
        assert isinstance(translator, AnthropicTranslator)
        messages = _scripted(translator)

        assert translator.translate("hello", "en", "ja") == "こんにちは"
        translator.translate("good morning", "en", "ja")
    finally:
        translator.close()

    assert len(messages.calls) == 2
    for call in messages.calls:
        assert call["model"] == "deepseek-flash"
        assert call["thinking"] == {"type": "disabled"}
        assert "output_config" not in call
        assert call["max_tokens"] < ALWAYS_THINKING_MIN_MAX_TOKENS


# --- HTTP status of provider errors -------------------------------------------------


def test_provider_error_status_reads_sdk_errors_and_message_prefixes():
    request = httpx.Request("POST", "https://relay.example.com/v1/chat/completions")
    openai_error = openai.APIStatusError(
        "Error code: 400 - {}",
        response=httpx.Response(400, request=request),
        body=None,
    )

    class WithResponse(Exception):
        response = types.SimpleNamespace(status_code=404)

    flagged = RuntimeError("flag")
    flagged.status_code = True

    assert provider_error_status(_anthropic_error(422, "bad")) == 422
    assert provider_error_status(openai_error) == 400
    assert provider_error_status(WithResponse("missing")) == 404
    assert provider_error_status(RuntimeError("Error code: 503 - upstream busy")) == 503
    assert provider_error_status(RuntimeError("Connection reset by peer")) is None
    assert provider_error_status(flagged) is None
