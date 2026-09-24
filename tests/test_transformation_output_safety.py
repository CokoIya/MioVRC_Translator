from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import BaseTranslator, TransformationOutputRejected
from src.translators.openai_translator import OpenAITranslator


_CONVERSATIONAL_REPLY = "Sure, I can help you with that."

_OPENAI_PROVIDER_CONFIGS = {
    "openai": {
        "model": "gpt-5.6-sol",
        "base_url": "https://api.openai.com/v1",
        "provider_id": "openai",
        "responses_api": True,
    },
    "openai_compatible": {
        "model": "relay/custom-model",
        "base_url": "https://relay.example.com/v1",
        "provider_id": "openai_compatible",
    },
    "grok": {
        "model": "grok-4.5",
        "base_url": "https://api.x.ai/v1",
        "provider_id": "grok_compatible",
    },
    "deepseek": {
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "provider_id": "deepseek",
    },
    "qwen": {
        "model": "qwen-mt-turbo",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "provider_id": "qianwen",
    },
}

_OPERATIONS = {
    "asr_rewrite": {
        "source": "Do you want to join my world?",
        "result": "Do you wanna join my world?",
        "invoke": lambda translator, source: translator.rewrite_asr(
            source,
            "language_exchange",
            language_hint="en",
            context_source="mic",
        ),
    },
    "typed_rewrite": {
        "source": "Do you want to join my world?",
        "result": "Would you like to join my world?",
        "invoke": lambda translator, source: translator.rewrite_asr(
            source,
            "language_exchange",
            language_hint="en",
            context_source="manual",
        ),
    },
    "forward_translation": {
        "source": "Do you want to join my world?",
        "result": "你想加入我的世界吗？",
        "invoke": lambda translator, source: translator.translate(
            source,
            "en",
            "zh",
            context_source="mic",
        ),
    },
    "reverse_translation": {
        "source": "你想加入我的世界吗？",
        "result": "Do you want to join my world?",
        "invoke": lambda translator, source: translator.translate(
            source,
            "zh",
            "en",
            context_source="listen",
        ),
    },
}


class _DummyTranslator(BaseTranslator):
    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        return text


class _SequencedChatCompletions:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        output = self.outputs.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=output),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )


class _SequencedResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(output_text=self.outputs.pop(0))


class _SequencedAnthropicMessages:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        output = self.outputs.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(text=output)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=8, output_tokens=8),
        )


def _install_openai_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    outputs: list[str],
) -> tuple[OpenAITranslator, list[dict[str, object]]]:
    config = _OPENAI_PROVIDER_CONFIGS[provider]
    instances: list[object] = []

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.chat_completions = _SequencedChatCompletions(outputs)
            self.responses_api = _SequencedResponses(outputs)
            self.chat = SimpleNamespace(completions=self.chat_completions)
            self.responses = self.responses_api
            instances.append(self)

        def close(self) -> None:
            return

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    if config.get("responses_api"):
        monkeypatch.setenv("MIO_TRANSLATOR_USE_RESPONSES_API", "1")
    else:
        monkeypatch.delenv("MIO_TRANSLATOR_USE_RESPONSES_API", raising=False)

    translator = OpenAITranslator(
        api_key="test-key",
        model=str(config["model"]),
        base_url=str(config["base_url"]),
        provider_id=str(config["provider_id"]),
        streaming=False,
    )
    client = instances[0]
    calls = (
        client.responses_api.calls
        if translator._use_responses_api
        else client.chat_completions.calls
    )
    return translator, calls


def _install_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
    outputs: list[str],
) -> tuple[AnthropicTranslator, list[dict[str, object]]]:
    instances: list[object] = []

    class _FakeAnthropic:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.messages = _SequencedAnthropicMessages(outputs)
            instances.append(self)

        def close(self) -> None:
            return

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=_FakeAnthropic),
    )
    translator = AnthropicTranslator(
        api_key="test-key",
        model="claude-sonnet-5",
        provider_id="anthropic",
        streaming=False,
    )
    return translator, instances[0].messages.calls


def _assert_structured_retry(calls: list[dict[str, object]]) -> None:
    assert len(calls) == 2
    retry_request = repr(calls[1])
    assert "Deterministic validation rejected" in retry_request
    assert 'Return exactly {"result"' in retry_request


@pytest.mark.parametrize("provider", tuple(_OPENAI_PROVIDER_CONFIGS))
@pytest.mark.parametrize("operation", tuple(_OPERATIONS))
def test_openai_family_rejects_conversation_and_retries_transformation_only(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    operation: str,
):
    case = _OPERATIONS[operation]
    expected = str(case["result"])
    translator, calls = _install_openai_provider(
        monkeypatch,
        provider,
        [_CONVERSATIONAL_REPLY, f'{{"result":"{expected}"}}'],
    )
    try:
        result = case["invoke"](translator, str(case["source"]))

        assert result == expected
        assert _CONVERSATIONAL_REPLY not in result
        _assert_structured_retry(calls)
        metrics = translator.translation_metrics()
        assert metrics["transformation_attempts"] == 2
        assert metrics["output_retries"] == 1
        if provider == "qwen" and operation == "forward_translation":
            assert calls[0]["messages"] == [
                {"role": "user", "content": case["source"]}
            ]
            assert "translation_options" in calls[0]["extra_body"]
            assert "translation_options" not in calls[1].get("extra_body", {})
    finally:
        translator.close()


@pytest.mark.parametrize("operation", tuple(_OPERATIONS))
def test_anthropic_rejects_conversation_and_retries_transformation_only(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    case = _OPERATIONS[operation]
    expected = str(case["result"])
    translator, calls = _install_anthropic_provider(
        monkeypatch,
        [_CONVERSATIONAL_REPLY, f'{{"result":"{expected}"}}'],
    )
    try:
        result = case["invoke"](translator, str(case["source"]))

        assert result == expected
        assert _CONVERSATIONAL_REPLY not in result
        _assert_structured_retry(calls)
        metrics = translator.translation_metrics()
        assert metrics["transformation_attempts"] == 2
        assert metrics["output_retries"] == 1
    finally:
        translator.close()


@pytest.mark.parametrize("provider", ("openai_compatible", "deepseek"))
def test_openai_family_never_returns_a_second_conversational_reply(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
):
    translator, calls = _install_openai_provider(
        monkeypatch,
        provider,
        [
            _CONVERSATIONAL_REPLY,
            '{"result":"Sure, I would be happy to help."}',
        ],
    )
    try:
        with pytest.raises(TransformationOutputRejected, match="conversational_reply"):
            translator.translate(
                "Do you want to join my world?",
                "en",
                "zh",
                context_source="mic",
            )
        _assert_structured_retry(calls)
    finally:
        translator.close()


def test_anthropic_never_returns_a_second_conversational_reply(
    monkeypatch: pytest.MonkeyPatch,
):
    translator, calls = _install_anthropic_provider(
        monkeypatch,
        [
            "我觉得这个主意很好。",
            '{"result":"我觉得这个主意很好。"}',
        ],
    )
    try:
        with pytest.raises(TransformationOutputRejected, match="conversational_reply"):
            translator.rewrite_asr(
                "你想加入我的世界吗？",
                "language_exchange",
                language_hint="zh",
                context_source="manual",
            )
        _assert_structured_retry(calls)
    finally:
        translator.close()


@pytest.mark.parametrize("provider", tuple(_OPENAI_PROVIDER_CONFIGS))
def test_openai_family_retries_rhetorical_reply_as_same_speaker_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
):
    expected = "难道就没有傲娇一点的吗？"
    translator, calls = _install_openai_provider(
        monkeypatch,
        provider,
        ["哼，谁说没有的？", f'{{"result":"{expected}"}}'],
    )
    try:
        result = translator.rewrite_asr(
            "没有傲娇吗？",
            "anime_tsundere_classmate",
            language_hint="zh",
            context_source="manual",
        )

        assert result == expected
        _assert_structured_retry(calls)
    finally:
        translator.close()


def test_anthropic_retries_rhetorical_reply_as_same_speaker_rewrite(
    monkeypatch: pytest.MonkeyPatch,
):
    expected = "难道就没有傲娇一点的吗？"
    translator, calls = _install_anthropic_provider(
        monkeypatch,
        ["哼，谁说没有的？", f'{{"result":"{expected}"}}'],
    )
    try:
        result = translator.rewrite_asr(
            "没有傲娇吗？",
            "anime_tsundere_classmate",
            language_hint="zh",
            context_source="manual",
        )

        assert result == expected
        _assert_structured_retry(calls)
    finally:
        translator.close()


@pytest.mark.parametrize(
    ("source", "candidate", "reason"),
    (
        (
            "Where do you want to go?",
            "Translation: Where would you like to go?",
            "label_or_explanation_prefix",
        ),
        (
            "Where do you want to go?",
            '"Where would you like to go?"',
            "decorative_quotes",
        ),
        (
            "Where is she?",
            "Based on the previous context, she is in the lobby.",
            "history_commentary",
        ),
        (
            "Where do you want to go?",
            "The lobby is crowded.",
            "question_answered_or_lost",
        ),
        (
            "Can you open the door?",
            "I can open the door?",
            "speaker_perspective_shift",
        ),
        (
            "你想加入我的世界吗？",
            "我觉得这个主意很好。",
            "conversational_reply",
        ),
        (
            "没有傲娇吗？",
            "哼，谁说没有的？",
            "reactive_rhetorical_reply",
        ),
    ),
)
def test_validator_rejects_non_transformational_output_shapes(
    source: str,
    candidate: str,
    reason: str,
):
    translator = _DummyTranslator()

    with pytest.raises(TransformationOutputRejected, match=reason):
        translator._validated_translation_output(candidate, source_text=source)


@pytest.mark.parametrize(
    ("source", "candidate"),
    (
        ("Of course I want to join.", "当然，我想加入。"),
        ("Sure, I can help you.", "当然，我可以帮你。"),
        ("I think this avatar is cute.", "我觉得这个虚拟形象很可爱。"),
        # そちら is also "that way", and 君 after a name is an honorific; a
        # first-person sentence translated with them is not a perspective shift.
        ("我先去那边等着。", "先にそちらで待ってます。"),
        ("我和小明一起去。", "小明君と一緒に行く。"),
    ),
)
def test_validator_preserves_player_authored_reply_like_phrases(
    source: str,
    candidate: str,
):
    translator = _DummyTranslator()

    assert (
        translator._validated_translation_output(candidate, source_text=source)
        == candidate
    )


def test_validator_accepts_same_speaker_tsundere_question_replacement():
    translator = _DummyTranslator()

    assert translator._validated_asr_rewrite_output(
        "难道就没有傲娇一点的吗？",
        source_text="没有傲娇吗？",
    ) == "难道就没有傲娇一点的吗？"


def test_structured_retry_requires_exact_single_result_field():
    translator = _DummyTranslator()

    with pytest.raises(TransformationOutputRejected, match="invalid_structured_fields"):
        translator._validated_translation_output(
            '{"result":"Where are you?","explanation":"translated"}',
            source_text="Where are you?",
            structured=True,
        )


def test_plain_translation_accepts_single_field_local_mt_json_envelope():
    translator = _DummyTranslator()

    assert (
        translator._validated_translation_output(
            '{"translation":"Bonjour !"}',
            source_text="Hello!",
        )
        == "Bonjour !"
    )


@pytest.mark.parametrize(
    ("target", "candidate"),
    (
        ("es", "これは日本語の返答です。"),
        ("fr", "The answer is still in English."),
        ("ru", "The answer is still in English."),
        ("ko", "これは日本語の返答です。"),
    ),
)
def test_validator_rejects_obvious_target_language_fallbacks(target, candidate):
    translator = _DummyTranslator()

    with pytest.raises(TransformationOutputRejected, match="target_language_mismatch"):
        translator._validated_translation_output(
            candidate,
            source_text="Please translate this sentence.",
            target_language=target,
        )


def test_translation_prompt_declares_code_and_forbidden_default_languages():
    translator = _DummyTranslator()

    prompt = translator._build_prompt(
        "没有傲娇吗？",
        "zh",
        "fr",
    )

    assert '"target_language_code":"fr"' in prompt
    assert '"mandatory_target_language":"French"' in prompt
    assert '"Japanese","English"' in prompt
