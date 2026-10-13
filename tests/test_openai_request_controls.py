"""Request controls of the OpenAI-compatible translator, per model family.

Reasoning effort, thinking switches, token limits and the fallback ladders for
every provider that speaks the OpenAI chat completions protocol, the relay
included. Translators are built through the factory with a recording fake SDK:
no request leaves the process.
"""

from __future__ import annotations

import copy
import sys
import types

import pytest

from src.translators import factory
from src.translators.anthropic_translator import ALWAYS_THINKING_MIN_MAX_TOKENS
from src.translators.openai_translator import OpenAITranslator

RELAY = "https://relay.example.com/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
SOURCE = "hello there, how are you doing today"
REPLY = "你好呀，朋友"
STRUCTURED_REPLY = '{"result": "你好呀，朋友"}'
# Token headroom for models that think a little even at their lowest effort.
LOW_FLOOR = 1024
MINIMAL_FLOOR = 512
THINKING_DISABLED = {"type": "disabled"}
THINKING_ENABLED = {"type": "enabled"}


class ProviderError(Exception):
    """An SDK-style HTTP error: ``status_code`` plus the "Error code" prefix."""

    def __init__(self, status_code: int, body: object) -> None:
        super().__init__(f"Error code: {status_code} - {body}")
        self.status_code = status_code


def openai_error(status: int, message: str, *, param=None, code=None) -> ProviderError:
    # Null fields print as ``None``, exactly like the SDK's error message.
    return ProviderError(
        status,
        {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": param,
                "code": code,
            }
        },
    )


def effort_value_rejected() -> ProviderError:
    return openai_error(
        400,
        "Unsupported value: 'reasoning_effort' does not support 'none' with this "
        "model. Supported values are: 'low', 'medium', and 'high'.",
        param="reasoning_effort",
        code="unsupported_value",
    )


def effort_parameter_unsupported() -> ProviderError:
    return openai_error(
        400,
        "Unsupported parameter: 'reasoning_effort' is not supported with this model.",
        param="reasoning_effort",
        code="unsupported_parameter",
    )


def effort_parameter_unrecognized() -> ProviderError:
    return openai_error(400, "Unrecognized request argument supplied: reasoning_effort")


def effort_argument_not_supported_camel_case() -> ProviderError:
    # xAI's wording for a reasoning-only model given an effort it cannot take.
    return ProviderError(
        400,
        {"code": "Client specified an invalid argument", "error": "Argument not supported on this model: reasoningEffort"},
    )


def no_available_channel() -> ProviderError:
    return ProviderError(
        503,
        {
            "error": {
                "message": "No available channel for model claude-sonnet-5-5-thinking "
                "under group default (not supported)",
                "type": "new_api_error",
                "param": "",
                "code": None,
            }
        },
    )


def zhipu_always_thinks(message: str = "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。") -> ProviderError:
    return ProviderError(400, {"error": {"code": "1210", "message": message}})


def openrouter_error(status: int, message: str) -> ProviderError:
    # OpenRouter's error body: {"error": {"message", "code"}}.
    return ProviderError(status, {"error": {"message": message, "code": status}})


OPENROUTER_MANDATORY_REASONING = "Reasoning is mandatory for this endpoint and cannot be disabled."


def temperature_unsupported() -> ProviderError:
    return openai_error(
        400,
        "Unsupported parameter: 'temperature' is not supported with this model.",
        param="temperature",
        code="unsupported_parameter",
    )


def max_tokens_unsupported() -> ProviderError:
    return openai_error(
        400,
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead.",
        param="max_tokens",
        code="unsupported_parameter",
    )


def _chat_response(text: str = REPLY):
    message = types.SimpleNamespace(content=text, reasoning_content=None, refusal=None)
    choice = types.SimpleNamespace(message=message, finish_reason="stop", text=None)
    return types.SimpleNamespace(choices=[choice], usage=None, status=None)


class _Endpoint:
    """Records every request; plays the script, then answers normally."""

    def __init__(self, default) -> None:
        self.calls: list[dict] = []
        self.script: list[object] = []
        self._default = default

    def create(self, **kwargs):
        self.calls.append(
            copy.deepcopy(
                {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"messages", "input", "timeout"}
                }
            )
        )
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return self._default()


class _FakeOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.default_headers = dict(kwargs.get("default_headers") or {})
        self.chat = types.SimpleNamespace(completions=_Endpoint(_chat_response))
        self.responses = _Endpoint(lambda: types.SimpleNamespace(output_text=REPLY))

    def close(self) -> None:
        return None


@pytest.fixture
def build(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    created: list[OpenAITranslator] = []

    def make(backend: str, model: str, base_url: str | None = None, **settings):
        section = {"api_key": "sk-test-not-a-real-key-0000", "model": model, **settings}
        if base_url:
            section["base_url"] = base_url
        translator = factory._create_translator_for_backend(
            {"backend": backend, backend: section}, backend
        )
        assert isinstance(translator, OpenAITranslator)
        created.append(translator)
        return translator

    yield make
    for translator in created:
        translator.close()


def _chat(translator: OpenAITranslator) -> _Endpoint:
    return translator._client.chat.completions


def _translate(translator: OpenAITranslator, text: str = SOURCE) -> str:
    return translator.translate(text, "en", "zh")


def _rewrite(translator: OpenAITranslator, text: str = SOURCE) -> str:
    return translator.rewrite_asr(text, "language_exchange", language_hint="en")


def _chat_retry(translator: OpenAITranslator) -> str:
    _chat(translator).script.append(_chat_response(STRUCTURED_REPLY))
    return translator._retry_chat_transformation(
        messages=[
            {"role": "system", "content": "Translate."},
            {"role": "user", "content": SOURCE},
        ],
        source_text=SOURCE,
        operation="translation",
        reason="echo",
        output_tokens=translator._estimate_max_tokens(SOURCE),
        target_language="zh",
    )


def _token_limit(call: dict) -> int:
    return call.get("max_completion_tokens", call.get("max_tokens"))


def _assert_request(call: dict, expected: dict, absent: tuple[str, ...]) -> None:
    for key, value in expected.items():
        if key == "extra_body":
            for body_key, body_value in value.items():
                assert call["extra_body"][body_key] == body_value
        else:
            assert call[key] == value, key
    for key in absent:
        assert key not in call, key


# --- First request per model family -------------------------------------------------

FAMILY_CASES = [
    ("openai", "gpt-6-luna", None, {"reasoning_effort": "none", "max_completion_tokens": 32}, ("temperature", "max_tokens", "extra_body")),
    ("openai", "gpt-6.1-sol", None, {"reasoning_effort": "low", "max_completion_tokens": LOW_FLOOR}, ("temperature",)),
    ("openai", "gpt-5", None, {"reasoning_effort": "minimal", "max_completion_tokens": MINIMAL_FLOOR}, ("temperature",)),
    ("openai", "gpt-5.4-mini", None, {"reasoning_effort": "none", "max_completion_tokens": 32}, ("temperature",)),
    ("gemini", "gemini-3.6-flash", None, {"reasoning_effort": "minimal", "max_tokens": MINIMAL_FLOOR}, ("temperature",)),
    ("gemini", "gemini-3.8-flash", None, {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("temperature",)),
    ("xai", "grok-4.3", None, {"reasoning_effort": "none", "temperature": 0.0}, ()),
    ("xai", "grok-4.7", None, {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("temperature",)),
    ("xai", "grok-4.20-0309-non-reasoning", None, {"temperature": 0.0}, ("reasoning_effort",)),
    ("mistral", "mistral-medium-3-5", None, {"reasoning_effort": "none"}, ()),
    ("mistral", "mistral-medium-2508", None, {}, ("reasoning_effort",)),
    ("kimi", "kimi-k2.6", None, {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort", "temperature")),
    ("kimi", "kimi-k3", None, {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("extra_body", "temperature")),
    ("zhipu", "glm-4.7-flashx", None, {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort",)),
    ("zhipu", "glm-5.3-flash", None, {"extra_body": {"thinking": THINKING_ENABLED}, "reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ()),
    ("hunyuan", "hy3", None, {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort",)),
    ("hunyuan", "hy-mt2-plus", None, {}, ("extra_body", "reasoning_effort")),
    ("deepseek", "deepseek-flash", None, {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort",)),
    ("xiaomi", "mimo-v2.6-flash", None, {"extra_body": {"thinking": THINKING_DISABLED}, "max_completion_tokens": 32}, ("max_tokens", "reasoning_effort")),
    ("doubao", "doubao-seed-2-1-lite-260915", None, {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort",)),
    ("qianwen", "qwen-plus", None, {"extra_body": {"enable_thinking": False}}, ("reasoning_effort",)),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", None, {"extra_body": {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}}}, ("reasoning_effort",)),
    ("nvidia", "nvidia/nemotron-3-super-120b-a12b", None, {"reasoning_effort": "none"}, ("extra_body",)),
    ("nvidia", "openai/gpt-oss-20b", None, {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("extra_body",)),
    ("openai_compatible", "gpt-6-luna", RELAY, {"reasoning_effort": "none", "max_completion_tokens": 32}, ("temperature", "max_tokens")),
    ("openai_compatible", "claude-sonnet-5-5", RELAY, {"reasoning_effort": "none", "max_tokens": 32}, ("temperature", "extra_body")),
    ("openai_compatible", "gemini-3.6-flash", RELAY, {"reasoning_effort": "minimal", "max_tokens": MINIMAL_FLOOR}, ("temperature",)),
    ("openai_compatible", "grok-4.7", RELAY, {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("temperature",)),
    ("openai_compatible", "deepseek-flash", RELAY, {"extra_body": {"thinking": THINKING_DISABLED}, "temperature": 0.0}, ("reasoning_effort",)),
    ("openai_compatible", "some-unknown-relay-model", RELAY, {"temperature": 0.0, "max_tokens": 32}, ("reasoning_effort", "extra_body")),
]


@pytest.mark.parametrize(
    ("backend", "model", "base_url", "expected", "absent"),
    FAMILY_CASES,
    ids=[f"{case[0]}-{case[1]}" for case in FAMILY_CASES],
)
def test_first_request_carries_the_controls_of_the_model_family(
    build, backend, model, base_url, expected, absent
):
    translator = build(backend, model, base_url)

    assert _translate(translator) == REPLY

    call = _chat(translator).calls[-1]
    assert call["model"] == model
    _assert_request(call, expected, absent)


# --- T1: relay ids are matched by family ----------------------------------------------

RELAY_FAMILY_CASES = [
    ("openai/gpt-6.1-sol", {"reasoning_effort": "low", "max_completion_tokens": LOW_FLOOR}, ("temperature", "max_tokens")),
    ("~openai/gpt-6-luna:free", {"reasoning_effort": "none", "max_completion_tokens": 32}, ("temperature",)),
    ("google/gemini-3.6-flash", {"reasoning_effort": "minimal", "max_tokens": MINIMAL_FLOOR}, ("temperature",)),
    ("x-ai/grok-4.7", {"reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ("temperature",)),
    ("anthropic/claude-haiku-5.5", {"reasoning_effort": "none", "max_tokens": 32}, ("temperature",)),
    ("moonshotai/kimi-k2.6", {"extra_body": {"thinking": THINKING_DISABLED}}, ("temperature", "reasoning_effort")),
    ("deepseek/deepseek-v4.1-flash", {"extra_body": {"thinking": THINKING_DISABLED}}, ("reasoning_effort",)),
    ("xiaomi/mimo-v2.6-flash", {"extra_body": {"thinking": THINKING_DISABLED}, "max_completion_tokens": 32}, ("max_tokens",)),
]


@pytest.mark.parametrize(("model", "expected", "absent"), RELAY_FAMILY_CASES, ids=[case[0] for case in RELAY_FAMILY_CASES])
def test_relay_matches_decorated_ids_by_family_and_sends_them_verbatim(
    build, model, expected, absent
):
    translator = build("openai_compatible", model, RELAY)

    _translate(translator)

    call = _chat(translator).calls[-1]
    # The fetched id is sent back exactly as the relay listed it.
    assert call["model"] == model
    _assert_request(call, expected, absent)


def test_nvidia_and_local_servers_keep_namespaced_ids_for_their_rules(build):
    nvidia = build("nvidia", "openai/gpt-oss-20b")
    local = build(
        "local_ai", "anthropic/claude-haiku-5.5", "http://127.0.0.1:11434/v1"
    )

    _translate(nvidia)
    _translate(local)

    # NIM's rule is written for the namespaced id; local servers get no
    # vendor rules at all.
    assert nvidia._family_model_id(nvidia.model) == "openai/gpt-oss-20b"
    assert _chat(nvidia).calls[-1]["reasoning_effort"] == "low"
    local_call = _chat(local).calls[-1]
    assert "reasoning_effort" not in local_call
    assert local_call["temperature"] == 0.0


# --- T2: always-thinking Claude over the OpenAI protocol --------------------------------


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5", "anthropic/claude-opus-5.5"],
)
def test_relay_always_thinking_claude_keeps_effort_none_with_an_output_floor(build, model):
    translator = build("openai_compatible", model, RELAY)

    _translate(translator)
    _rewrite(translator)
    _chat_retry(translator)

    calls = _chat(translator).calls
    assert len(calls) == 3
    for call in calls:
        # new-api maps "none" to the lowest setting these models accept.
        assert call["reasoning_effort"] == "none"
        assert call["max_tokens"] == ALWAYS_THINKING_MIN_MAX_TOKENS


def test_relay_claude_that_can_stop_thinking_gets_no_output_floor(build):
    translator = build("openai_compatible", "claude-sonnet-5-5", RELAY)

    _translate(translator)

    assert _chat(translator).calls[-1]["max_tokens"] < MINIMAL_FLOOR


# --- T3: GPT-5 and later send max_completion_tokens from the start ------------------------


@pytest.mark.parametrize(
    ("backend", "model", "base_url"),
    [
        ("openai", "gpt-6-luna", None),
        ("openai", "gpt-7-mini", None),
        ("openai", "gpt-5.4-mini", None),
        ("openai_compatible", "gpt-6-luna", RELAY),
        ("openai_compatible", "openai/gpt-6-luna", RELAY),
    ],
)
def test_gpt5_and_later_send_max_completion_tokens_on_the_first_request(
    build, backend, model, base_url
):
    translator = build(backend, model, base_url)

    _translate(translator)

    calls = _chat(translator).calls
    assert len(calls) == 1
    assert "max_completion_tokens" in calls[0]
    assert "max_tokens" not in calls[0]


# --- T4: the reasoning floor survives the max_output_tokens clamp -------------------------


@pytest.mark.parametrize(("model", "floor"), [("gpt-6.1-sol", LOW_FLOOR), ("gpt-5", MINIMAL_FLOOR)])
def test_reasoning_floor_applies_to_translate_rewrite_and_retry(build, model, floor):
    translator = build("openai", model)

    _translate(translator)
    _rewrite(translator)
    _chat_retry(translator)

    calls = _chat(translator).calls
    assert len(calls) == 3
    assert [call["max_completion_tokens"] for call in calls] == [floor, floor, floor]


def test_reasoning_floor_applies_to_responses_api_requests(build, monkeypatch):
    monkeypatch.setenv("MIO_TRANSLATOR_USE_RESPONSES_API", "1")
    translator = build("openai", "gpt-6.1-sol")
    assert translator._use_responses_api is True
    responses = translator._client.responses

    _translate(translator)
    _rewrite(translator)
    responses.script.append(types.SimpleNamespace(output_text=STRUCTURED_REPLY))
    translator._retry_responses_transformation(
        messages=[
            {"role": "system", "content": "Translate."},
            {"role": "user", "content": SOURCE},
        ],
        source_text=SOURCE,
        operation="translation",
        reason="echo",
        output_tokens=translator._estimate_max_tokens(SOURCE),
        target_language="zh",
    )

    assert len(responses.calls) == 3
    for call in responses.calls:
        assert call["max_output_tokens"] == LOW_FLOOR
        assert call["reasoning"] == {"effort": "low"}


def test_reasoning_floor_does_not_lower_a_larger_configured_cap(build):
    translator = build("openai", "gpt-6.1-sol", max_output_tokens=2048)

    assert translator._with_output_floor(1500) == 1500
    assert translator._with_output_floor(64) == LOW_FLOOR


# --- T5: relay thinking switches by family ----------------------------------------------

RELAY_SWITCH_CASES = [
    ("kimi-k2.6", THINKING_DISABLED, None),
    ("kimi-k3", None, "low"),
    ("kimi-k2.7-code", None, None),
    ("glm-4.7", THINKING_DISABLED, None),
    ("glm-5.3-flash", THINKING_ENABLED, "low"),
    ("z-ai/glm-5.3-flash", THINKING_ENABLED, "low"),
    ("mimo-v2.6-flash", THINKING_DISABLED, None),
    ("deepseek-flash", THINKING_DISABLED, None),
]


@pytest.mark.parametrize(("model", "thinking", "effort"), RELAY_SWITCH_CASES, ids=[case[0] for case in RELAY_SWITCH_CASES])
def test_relay_thinking_switch_follows_the_model_family(build, model, thinking, effort):
    translator = build("openai_compatible", model, RELAY)

    _translate(translator)

    call = _chat(translator).calls[-1]
    assert call.get("extra_body", {}).get("thinking") == thinking
    assert call.get("reasoning_effort") == effort
    if effort == "low":
        assert _token_limit(call) == LOW_FLOOR
    if thinking is not None:
        assert "thinking" in translator._managed_no_thinking_extra_keys


def test_relay_thinking_switch_is_dropped_when_the_relay_rejects_it(build):
    translator = build("openai_compatible", "kimi-k2.6", RELAY)
    chat = _chat(translator)
    chat.script.append(openai_error(400, "Unsupported parameter: thinking"))

    assert _translate(translator) == REPLY
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert first["extra_body"]["thinking"] == THINKING_DISABLED
    assert "extra_body" not in second
    assert "extra_body" not in third
    assert translator._no_thinking_request_supported is False


# --- T6: effort escalation -------------------------------------------------------------


def test_refused_effort_value_is_raised_to_low_and_kept_after_success(build):
    translator = build("openai_compatible", "gpt-6.2-sol", RELAY)
    chat = _chat(translator)
    chat.script.append(effort_value_rejected())

    assert _translate(translator) == REPLY

    first, second = chat.calls
    assert first["reasoning_effort"] == "none"
    assert second["reasoning_effort"] == "low"
    assert _token_limit(second) == LOW_FLOOR
    assert translator._reasoning_effort_override == "low"

    _translate(translator, "see you tomorrow")

    assert len(chat.calls) == 3
    assert chat.calls[-1]["reasoning_effort"] == "low"


def test_raised_effort_is_not_kept_when_the_raised_request_fails(build):
    translator = build("openai_compatible", "gpt-6.2-sol", RELAY)
    chat = _chat(translator)
    chat.script.extend([effort_value_rejected(), no_available_channel()])

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 2
    assert translator._reasoning_effort_override is None

    _translate(translator, "see you tomorrow")

    assert chat.calls[-1]["reasoning_effort"] == "none"
    assert _token_limit(chat.calls[-1]) < MINIMAL_FLOOR


@pytest.mark.parametrize(
    "error_factory",
    [
        effort_parameter_unsupported,
        effort_parameter_unrecognized,
        effort_argument_not_supported_camel_case,
    ],
    ids=[
        "unsupported-parameter",
        "unrecognized-argument-with-null-fields",
        "xai-camel-case-argument",
    ],
)
def test_refused_effort_parameter_drops_the_control_instead_of_raising_it(
    build, error_factory
):
    translator = build("openai_compatible", "gpt-5.2-chat-latest", RELAY)
    chat = _chat(translator)
    chat.script.append(error_factory())

    assert _translate(translator) == REPLY

    first, second = chat.calls
    assert first["reasoning_effort"] == "none"
    assert "reasoning_effort" not in second
    assert _token_limit(second) == _token_limit(first)
    assert translator._reasoning_effort_override is None
    assert translator._no_thinking_request_supported is False

    _translate(translator, "see you tomorrow")

    later = chat.calls[-1]
    assert "reasoning_effort" not in later
    assert _token_limit(later) == _token_limit(first)


def test_a_server_error_naming_a_thinking_model_is_not_an_effort_rejection(build):
    translator = build("openai_compatible", "claude-sonnet-5-5-thinking", RELAY)
    chat = _chat(translator)
    chat.script.append(no_available_channel())

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 1
    assert translator._reasoning_effort_override is None
    assert translator._no_thinking_request_supported is True


def test_an_unquoted_refused_effort_value_is_recognised(build):
    translator = build("gemini", "gemini-3.7-flash")
    chat = _chat(translator)
    chat.script.append(
        ProviderError(
            400,
            [
                {
                    "error": {
                        "code": 400,
                        "message": "Thinking level MINIMAL is not supported for this "
                        "model. Please retry with other thinking level.",
                        "status": "INVALID_ARGUMENT",
                    }
                }
            ],
        )
    )

    _translate(translator)

    assert [call["reasoning_effort"] for call in chat.calls] == ["minimal", "low"]


def test_rejected_effort_value_needs_the_value_or_a_list_of_values():
    sent = {"reasoning_effort": "none"}
    reject = OpenAITranslator._rejected_effort_value

    assert reject(openai_error(400, "reasoning_effort 'none' is not available"), sent) == "none"
    assert reject(openai_error(400, "reasoning_effort must be one of: low, high"), sent) == "none"
    assert reject(openai_error(400, "bad reasoning_effort", code="unsupported_value"), sent) == "none"
    # ``'param': None`` in the SDK message is a null field, not the value.
    assert reject(openai_error(400, "reasoning_effort: invalid request"), sent) == ""
    # "not supported" alone describes the parameter, not the value.
    assert reject(openai_error(400, "reasoning_effort is not supported"), sent) == ""
    assert reject(openai_error(400, "temperature must be one of: 0, 1"), sent) == ""
    assert reject(openai_error(400, "reasoning 'low' must be one of: high"), {"reasoning_effort": "low"}) == ""


def test_dropping_the_controls_also_drops_a_raised_effort_and_its_headroom(build):
    translator = build("openai_compatible", "gpt-6.2-sol", RELAY)
    chat = _chat(translator)
    chat.script.extend([effort_value_rejected(), effort_parameter_unsupported()])

    assert _translate(translator) == REPLY

    first, second, third = chat.calls
    assert second["reasoning_effort"] == "low"
    assert _token_limit(second) == LOW_FLOOR
    assert "reasoning_effort" not in third
    assert _token_limit(third) == _token_limit(first)
    assert translator._reasoning_effort_override is None


def test_a_kept_raised_effort_is_cleared_when_the_controls_are_dropped_later(build):
    translator = build("openai_compatible", "gpt-6.2-sol", RELAY)
    chat = _chat(translator)
    chat.script.append(effort_value_rejected())
    _translate(translator)
    assert translator._reasoning_effort_override == "low"

    chat.script.append(effort_parameter_unsupported())
    _translate(translator, "see you tomorrow")

    retried = chat.calls[-1]
    assert "reasoning_effort" not in retried
    assert _token_limit(retried) <= translator._max_output_tokens
    assert translator._reasoning_effort_override is None

    _translate(translator, "good night everyone")

    last = chat.calls[-1]
    assert "reasoning_effort" not in last
    assert _token_limit(last) < MINIMAL_FLOOR


def test_dropped_controls_are_kept_only_after_a_request_without_them_succeeds(build):
    translator = build("openai_compatible", "kimi-k2.6", RELAY)
    chat = _chat(translator)
    chat.script.extend(
        [openai_error(400, "Unsupported parameter: thinking"), no_available_channel()]
    )

    with pytest.raises(ProviderError):
        _translate(translator)

    assert translator._no_thinking_request_supported is True

    _translate(translator, "see you tomorrow")

    assert chat.calls[-1]["extra_body"]["thinking"] == THINKING_DISABLED


# --- T8: no-thinking fallbacks ------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        openai_error(400, "Unsupported parameter: chat_template_kwargs"),
        ProviderError(
            422,
            {
                "object": "error",
                "message": "[{'type': 'extra_forbidden', 'loc': ('body', "
                "'chat_template_kwargs'), 'msg': 'Extra inputs are not permitted'}]",
                "type": "BadRequestError",
                "code": 422,
            },
        ),
    ],
    ids=["unsupported-parameter", "pydantic-extra-forbidden"],
)
def test_rejected_chat_template_kwargs_are_dropped(build, error):
    translator = build("nvidia", "meta/llama-3.3-70b-instruct")
    chat = _chat(translator)
    chat.script.append(error)

    assert _translate(translator) == REPLY
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert "chat_template_kwargs" in first["extra_body"]
    assert "extra_body" not in second
    assert "extra_body" not in third


@pytest.mark.parametrize(
    "message",
    [
        "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。",
        "This model always uses thinking; disabling it is not supported.",
    ],
)
def test_zhipu_model_that_always_thinks_runs_enabled_at_low_effort(build, message):
    translator = build("zhipu", "glm-4.7")
    chat = _chat(translator)
    chat.script.append(zhipu_always_thinks(message))

    assert _translate(translator) == REPLY

    first, second = chat.calls
    assert first["extra_body"]["thinking"] == THINKING_DISABLED
    assert second["extra_body"]["thinking"] == THINKING_ENABLED
    assert second["reasoning_effort"] == "low"
    assert _token_limit(second) == LOW_FLOOR
    assert translator._thinking_required is True

    # Remembered for the instance: later requests start there directly.
    _translate(translator, "see you tomorrow")
    _rewrite(translator)

    assert len(chat.calls) == 4
    for call in chat.calls[2:]:
        assert call["extra_body"]["thinking"] == THINKING_ENABLED
        assert call["reasoning_effort"] == "low"
        assert _token_limit(call) == LOW_FLOOR


@pytest.mark.parametrize(
    "error",
    [
        # Code 1210 also covers max_tokens range errors.
        ProviderError(400, {"error": {"code": "1210", "message": "max_tokens 参数超出范围"}}),
        ProviderError(429, {"error": {"code": "1302", "message": "思考请求过多，请稍后再试"}}),
    ],
    ids=["1210-max-tokens", "429-mentions-thinking"],
)
def test_other_zhipu_errors_do_not_turn_thinking_on(build, error):
    translator = build("zhipu", "glm-4.7")
    chat = _chat(translator)
    chat.script.append(error)

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 1
    assert translator._thinking_required is False


def test_only_zhipu_reads_a_thinking_error_as_always_thinking(build):
    translator = build("deepseek", "deepseek-flash")
    chat = _chat(translator)
    chat.script.append(zhipu_always_thinks())

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 1
    assert translator._thinking_required is False


def test_zhipu_always_thinking_is_remembered_only_after_success(build):
    translator = build("zhipu", "glm-4.7")
    chat = _chat(translator)
    chat.script.extend([zhipu_always_thinks(), ProviderError(500, "upstream error")])

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 2
    assert translator._thinking_required is False


# --- T9: OpenRouter -------------------------------------------------------------------

OPENROUTER_CASES = [
    ("openai/gpt-6-luna", {"effort": "none"}, 0),
    ("openai/gpt-6.1-sol", {"effort": "low"}, LOW_FLOOR),
    ("google/gemini-3.6-flash", {"effort": "minimal"}, MINIMAL_FLOOR),
    ("x-ai/grok-4.3", {"effort": "none"}, 0),
    ("x-ai/grok-4.7", {"effort": "low"}, LOW_FLOOR),
    ("moonshotai/kimi-k3", {"effort": "low"}, LOW_FLOOR),
    ("z-ai/glm-5.3-flash", {"effort": "low"}, LOW_FLOOR),
    ("anthropic/claude-sonnet-5.5", {"effort": "low"}, LOW_FLOOR),
    ("anthropic/claude-opus-5.5", {"effort": "low"}, ALWAYS_THINKING_MIN_MAX_TOKENS),
    # No "none" effort on OpenRouter: reasoning is switched off instead.
    ("deepseek/deepseek-v4.1-flash", {"enabled": False}, 0),
    ("anthropic/claude-haiku-5.5", {"enabled": False}, 0),
    ("anthropic/claude-opus-5", {"enabled": False}, 0),
    ("moonshotai/kimi-k2.6", {"enabled": False}, 0),
    ("z-ai/glm-4.7", {"enabled": False}, 0),
    ("xiaomi/mimo-v2.6-flash", {"enabled": False}, 0),
]


@pytest.mark.parametrize(("model", "reasoning", "floor"), OPENROUTER_CASES, ids=[case[0] for case in OPENROUTER_CASES])
def test_openrouter_takes_one_reasoning_object(build, model, reasoning, floor):
    translator = build("openai_compatible", model, OPENROUTER)

    _translate(translator)
    _rewrite(translator)
    _chat_retry(translator)

    calls = _chat(translator).calls
    assert len(calls) == 3
    for call in calls:
        assert call["model"] == model
        assert call["extra_body"]["reasoning"] == reasoning
        assert "reasoning_effort" not in call
        assert "thinking" not in call["extra_body"]
        if floor:
            assert _token_limit(call) == floor
        else:
            assert _token_limit(call) < MINIMAL_FLOOR
    assert "reasoning" in translator._managed_no_thinking_extra_keys


def test_openrouter_sends_no_reasoning_object_for_unknown_families(build):
    translator = build("openai_compatible", "meta-llama/llama-4-maverick", OPENROUTER)

    _translate(translator)

    call = _chat(translator).calls[-1]
    assert "reasoning_effort" not in call
    assert "reasoning" not in call.get("extra_body", {})


def test_openrouter_raises_a_refused_effort_inside_the_reasoning_object(build):
    translator = build("openai_compatible", "google/gemini-3.7-flash", OPENROUTER)
    chat = _chat(translator)
    chat.script.append(
        ProviderError(
            400,
            {
                "error": {
                    "message": "Reasoning effort 'minimal' is not supported for this "
                    "model. Supported values: low, medium, high",
                    "code": 400,
                }
            },
        )
    )

    _translate(translator)
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert first["extra_body"]["reasoning"] == {"effort": "minimal"}
    assert second["extra_body"]["reasoning"] == {"effort": "low"}
    assert _token_limit(second) == LOW_FLOOR
    assert translator._reasoning_effort_override == "low"
    assert third["extra_body"]["reasoning"] == {"effort": "low"}


def test_openrouter_reasoning_object_is_removed_with_the_other_controls(build):
    translator = build("openai_compatible", "openai/gpt-6-luna", OPENROUTER)
    request = {
        "model": translator.model,
        "max_completion_tokens": 32,
        "extra_body": {"reasoning": {"effort": "none"}, "provider": {"sort": "latency"}},
    }

    stripped = translator._without_no_thinking_controls(request)

    assert stripped["extra_body"] == {"provider": {"sort": "latency"}}


def test_a_relay_host_that_only_looks_like_openrouter_is_a_plain_relay(build):
    translator = build(
        "openai_compatible", "openai/gpt-6-luna", "https://openrouter.ai.example.com/v1"
    )

    _translate(translator)

    call = _chat(translator).calls[-1]
    assert call["reasoning_effort"] == "none"
    assert "reasoning" not in call.get("extra_body", {})


# --- TP-1: OpenRouter "-latest" aliases, mandatory reasoning, a refused object -----------

# The ``reasoning`` object of every ``~vendor/<family>-latest`` alias the relay
# list offers, from OpenRouter's /models list (saved 2026-10-12):
# (mandatory, supported_efforts).
OPENROUTER_ALIAS_REASONING = {
    "~anthropic/claude-fable-latest": (True, ("max", "xhigh", "high", "medium", "low")),
    "~anthropic/claude-haiku-latest": (False, ("max", "xhigh", "high", "medium", "low")),
    "~anthropic/claude-opus-latest": (True, ("max", "xhigh", "high", "medium", "low")),
    "~anthropic/claude-sonnet-latest": (True, ("max", "xhigh", "high", "medium", "low")),
    "~deepseek/deepseek-flash-latest": (False, ("max", "high", "low")),
    "~deepseek/deepseek-v4-flash-latest": (False, ("max", "high", "low")),
    "~google/gemini-flash-latest": (True, ("high", "medium", "low")),
    "~google/gemini-pro-latest": (True, ("high", "medium", "low")),
    "~moonshotai/kimi-latest": (False, ("max", "high", "low")),
    "~openai/gpt-astra-latest": (True, ("max", "xhigh", "high", "medium", "low")),
    "~openai/gpt-luna-latest": (False, ("max", "xhigh", "high", "medium", "low", "none")),
    "~openai/gpt-mini-latest": (False, ("xhigh", "high", "medium", "low", "none")),
    "~openai/gpt-sol-latest": (True, ("max", "xhigh", "high", "medium", "low")),
    "~openai/gpt-terra-latest": (False, ("max", "xhigh", "high", "medium", "low", "none")),
    "~x-ai/grok-latest": (True, ("xhigh", "high", "medium", "low")),
    "~z-ai/glm-flash-latest": (True, ("max", "high", "low")),
    "~z-ai/glm-latest": (True, ("max", "high", "low")),
}


@pytest.mark.parametrize(
    ("model", "metadata"),
    sorted(OPENROUTER_ALIAS_REASONING.items()),
    ids=sorted(OPENROUTER_ALIAS_REASONING),
)
def test_openrouter_latest_aliases_follow_their_reasoning_metadata(build, model, metadata):
    mandatory, efforts = metadata
    translator = build("openai_compatible", model, OPENROUTER)

    _translate(translator)

    call = _chat(translator).calls[-1]
    assert call["model"] == model
    reasoning = call["extra_body"]["reasoning"]
    if mandatory:
        # Reasoning cannot be switched off there: the lowest effort, with room.
        assert reasoning == {"effort": "low"}
        assert "low" in efforts
        assert _token_limit(call) >= LOW_FLOOR
    elif "none" in efforts:
        assert reasoning == {"effort": "none"}
        assert _token_limit(call) < MINIMAL_FLOOR
    else:
        assert reasoning == {"enabled": False}
        assert _token_limit(call) < MINIMAL_FLOOR


OPENROUTER_ALIAS_SHAPE_CASES = [
    # GPT aliases take the GPT-5+ request shape: max_completion_tokens, no temperature.
    ("~openai/gpt-luna-latest", {"effort": "none"}, "max_completion_tokens", 0, False),
    ("~openai/gpt-terra-latest", {"effort": "none"}, "max_completion_tokens", 0, False),
    ("~openai/gpt-mini-latest", {"effort": "none"}, "max_completion_tokens", 0, False),
    ("~openai/gpt-sol-latest", {"effort": "low"}, "max_completion_tokens", LOW_FLOOR, False),
    ("~openai/gpt-astra-latest", {"effort": "low"}, "max_completion_tokens", LOW_FLOOR, False),
    ("~x-ai/grok-latest", {"effort": "low"}, "max_tokens", LOW_FLOOR, False),
    ("~anthropic/claude-sonnet-latest", {"effort": "low"}, "max_tokens", LOW_FLOOR, False),
    # The Opus alias points at Opus 5.5, which always thinks.
    ("~anthropic/claude-opus-latest", {"effort": "low"}, "max_tokens", ALWAYS_THINKING_MIN_MAX_TOKENS, False),
    ("~z-ai/glm-latest", {"effort": "low"}, "max_tokens", LOW_FLOOR, True),
    ("~z-ai/glm-flash-latest", {"effort": "low"}, "max_tokens", LOW_FLOOR, True),
]


@pytest.mark.parametrize(
    ("model", "reasoning", "token_field", "floor", "temperature"),
    OPENROUTER_ALIAS_SHAPE_CASES,
    ids=[case[0] for case in OPENROUTER_ALIAS_SHAPE_CASES],
)
def test_openrouter_latest_aliases_take_the_request_shape_of_their_family(
    build, model, reasoning, token_field, floor, temperature
):
    translator = build("openai_compatible", model, OPENROUTER)

    _translate(translator)
    _rewrite(translator)
    _chat_retry(translator)

    calls = _chat(translator).calls
    assert len(calls) == 3
    other_field = "max_tokens" if token_field == "max_completion_tokens" else "max_completion_tokens"
    for call in calls:
        assert call["model"] == model
        # One reasoning object and no vendor switch (GLM's "enabled" included).
        assert call["extra_body"] == {"reasoning": reasoning}
        assert "reasoning_effort" not in call
        assert other_field not in call
        assert ("temperature" in call) is temperature
        if floor:
            assert call[token_field] == floor
        else:
            assert call[token_field] < MINIMAL_FLOOR


@pytest.mark.parametrize(
    ("model", "expected", "absent"),
    [
        ("~openai/gpt-sol-latest", {"reasoning_effort": "low", "max_completion_tokens": LOW_FLOOR}, ("temperature", "max_tokens")),
        ("~openai/gpt-luna-latest", {"reasoning_effort": "none", "max_completion_tokens": 32}, ("temperature", "max_tokens")),
        ("~z-ai/glm-latest", {"extra_body": {"thinking": THINKING_ENABLED}, "reasoning_effort": "low", "max_tokens": LOW_FLOOR}, ()),
        ("~anthropic/claude-opus-latest", {"reasoning_effort": "none", "max_tokens": ALWAYS_THINKING_MIN_MAX_TOKENS}, ("temperature",)),
    ],
    ids=["gpt-sol", "gpt-luna", "glm", "claude-opus"],
)
def test_latest_aliases_keep_their_family_rules_on_other_relays(build, model, expected, absent):
    translator = build("openai_compatible", model, RELAY)

    _translate(translator)

    call = _chat(translator).calls[-1]
    assert call["model"] == model
    _assert_request(call, expected, absent)


@pytest.mark.parametrize(
    ("model", "first_reasoning"),
    [
        ("moonshotai/kimi-k2-thinking", {"enabled": False}),
        ("~anthropic/claude-haiku-latest", {"enabled": False}),
        ("openai/gpt-6-luna", {"effort": "none"}),
    ],
)
def test_openrouter_mandatory_reasoning_runs_at_low_effort_and_is_kept_after_success(
    build, model, first_reasoning
):
    translator = build("openai_compatible", model, OPENROUTER)
    chat = _chat(translator)
    chat.script.append(openrouter_error(400, OPENROUTER_MANDATORY_REASONING))

    assert _translate(translator) == REPLY

    first, second = chat.calls
    assert first["extra_body"]["reasoning"] == first_reasoning
    assert second["extra_body"]["reasoning"] == {"effort": "low"}
    assert "reasoning_effort" not in second
    assert _token_limit(second) == LOW_FLOOR
    assert translator._thinking_required is True

    # Remembered for the instance: later requests start at low effort.
    _translate(translator, "see you tomorrow")
    _rewrite(translator)
    _chat_retry(translator)

    assert len(chat.calls) == 5
    for call in chat.calls[2:]:
        assert call["extra_body"]["reasoning"] == {"effort": "low"}
        assert "reasoning_effort" not in call
        assert _token_limit(call) == LOW_FLOOR


def test_openrouter_mandatory_reasoning_is_kept_only_after_a_request_succeeds(build):
    translator = build("openai_compatible", "deepseek/deepseek-v4.1-flash", OPENROUTER)
    chat = _chat(translator)
    chat.script.extend(
        [
            openrouter_error(400, OPENROUTER_MANDATORY_REASONING),
            openrouter_error(503, "upstream overloaded"),
        ]
    )

    with pytest.raises(ProviderError):
        _translate(translator)

    assert [call["extra_body"]["reasoning"] for call in chat.calls] == [
        {"enabled": False},
        {"effort": "low"},
    ]
    assert translator._thinking_required is False

    _translate(translator, "see you tomorrow")

    assert chat.calls[-1]["extra_body"]["reasoning"] == {"enabled": False}
    assert _token_limit(chat.calls[-1]) < MINIMAL_FLOOR


@pytest.mark.parametrize(
    ("base_url", "model", "error"),
    [
        # Only OpenRouter's answer counts; a plain relay never sent the object.
        (RELAY, "deepseek-flash", openrouter_error(400, OPENROUTER_MANDATORY_REASONING)),
        # Rate limits and server errors repeating the phrase are not a refusal.
        (OPENROUTER, "deepseek/deepseek-v4.1-flash", openrouter_error(429, OPENROUTER_MANDATORY_REASONING)),
        (OPENROUTER, "deepseek/deepseek-v4.1-flash", openrouter_error(503, OPENROUTER_MANDATORY_REASONING)),
        # The request already ran at low effort: nothing was switched off.
        (OPENROUTER, "x-ai/grok-4.7", openrouter_error(400, OPENROUTER_MANDATORY_REASONING)),
        # A 400 about something else.
        (OPENROUTER, "deepseek/deepseek-v4.1-flash", openrouter_error(400, "Reasoning tokens exceed max_tokens")),
    ],
    ids=["plain-relay", "429", "503", "already-low", "other-400"],
)
def test_other_errors_do_not_turn_openrouter_reasoning_on(build, base_url, model, error):
    translator = build("openai_compatible", model, base_url)
    chat = _chat(translator)
    chat.script.append(error)

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 1
    assert translator._thinking_required is False


@pytest.mark.parametrize(
    "message",
    [
        "Unsupported parameter: reasoning",
        "Invalid parameter: 'reasoning.enabled' is not supported for this model",
    ],
    ids=["unsupported-parameter", "invalid-parameter"],
)
@pytest.mark.parametrize(
    ("model", "sent"),
    [
        ("deepseek/deepseek-v4.1-flash", {"enabled": False}),
        ("openai/gpt-6-luna", {"effort": "none"}),
    ],
    ids=["switched-off", "effort-none"],
)
def test_openrouter_refused_reasoning_object_is_dropped_and_kept_off(build, model, sent, message):
    translator = build("openai_compatible", model, OPENROUTER)
    chat = _chat(translator)
    chat.script.append(openrouter_error(400, message))

    assert _translate(translator) == REPLY
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert first["extra_body"]["reasoning"] == sent
    assert "reasoning" not in second.get("extra_body", {})
    assert "reasoning" not in third.get("extra_body", {})
    assert translator._no_thinking_request_supported is False


@pytest.mark.parametrize(
    ("base_url", "model", "message"),
    [
        # A model id containing the word is not the parameter.
        (OPENROUTER, "deepseek/deepseek-v4.1-flash", "Unknown model: deepseek/deepseek-v4-non-reasoning"),
        # Plain relays are never sent a reasoning object.
        (RELAY, "kimi-k2.6", "Unsupported parameter: reasoning"),
    ],
    ids=["model-id", "plain-relay"],
)
def test_a_reasoning_word_elsewhere_does_not_drop_the_controls(build, base_url, model, message):
    translator = build("openai_compatible", model, base_url)
    chat = _chat(translator)
    chat.script.append(openrouter_error(400, message))

    with pytest.raises(ProviderError):
        _translate(translator)

    assert len(chat.calls) == 1
    assert translator._no_thinking_request_supported is True


# --- Other steps of the compatibility ladder -------------------------------------------


def test_rejected_temperature_is_dropped_and_remembered(build):
    translator = build("openai_compatible", "o3", RELAY)
    chat = _chat(translator)
    chat.script.append(
        openai_error(
            400,
            "Unsupported parameter: 'temperature' is not supported with this model.",
            param="temperature",
            code="unsupported_parameter",
        )
    )

    _translate(translator)
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert "temperature" in first
    assert "temperature" not in second
    assert "temperature" not in third


def test_rejected_token_parameter_is_swapped_and_remembered(build):
    translator = build("openai_compatible", "some-relay-model", RELAY)
    chat = _chat(translator)
    chat.script.append(
        openai_error(
            400,
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
            param="max_tokens",
            code="unsupported_parameter",
        )
    )

    _translate(translator)
    _translate(translator, "see you tomorrow")

    first, second, third = chat.calls
    assert "max_tokens" in first
    assert second["max_completion_tokens"] == first["max_tokens"]
    assert "max_tokens" not in second
    assert "max_completion_tokens" in third


# --- TP-3: what a retry learned is kept only after a request with it succeeds ------------


def test_rejected_temperature_is_kept_only_after_a_request_without_it_succeeds(build):
    translator = build("openai_compatible", "o3", RELAY)
    chat = _chat(translator)
    chat.script.extend([temperature_unsupported(), no_available_channel()])

    with pytest.raises(ProviderError):
        _translate(translator)

    assert ["temperature" in call for call in chat.calls] == [True, False]
    assert translator._omits_temperature is False

    _translate(translator, "see you tomorrow")

    assert "temperature" in chat.calls[-1]


def test_swapped_token_parameter_is_kept_only_after_a_request_with_it_succeeds(build):
    translator = build("openai_compatible", "some-relay-model", RELAY)
    chat = _chat(translator)
    chat.script.extend([max_tokens_unsupported(), no_available_channel()])

    with pytest.raises(ProviderError):
        _translate(translator)

    first, second = chat.calls
    assert "max_tokens" in first
    assert "max_completion_tokens" in second
    assert translator._uses_max_completion_tokens is False

    _translate(translator, "see you tomorrow")

    assert "max_tokens" in chat.calls[-1]
    assert "max_completion_tokens" not in chat.calls[-1]
