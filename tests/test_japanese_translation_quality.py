from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.asr.sensevoice_asr import SenseVoiceASR
from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import (
    BaseTranslator,
    TranslationContextStore,
    translation_context_scope,
)
from src.translators.openai_translator import OpenAITranslator
from src.utils.lang_detect import detect_language


class DummyTranslator(BaseTranslator):
    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        return text


class CaptureCorrector:
    def __init__(self) -> None:
        self.language = None

    def apply(self, text: str, language: str | None = None) -> str:
        self.language = language
        return text


def _openai_translator_stub(
    *,
    is_qwen: bool = True,
    uses_qwen_mt: bool = True,
    prompt_profile: dict | None = None,
):
    translator = OpenAITranslator.__new__(OpenAITranslator)
    BaseTranslator.__init__(translator, prompt_profile=prompt_profile or {})
    translator.model = "qwen-mt-flash" if uses_qwen_mt else "qwen-plus"
    translator._base_url = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
        if is_qwen
        else "https://api.openai.com/v1"
    )
    translator._is_qwen_backend = is_qwen
    translator._uses_qwen_mt_translation_options = uses_qwen_mt
    return translator


class _CaptureResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return type("Response", (), {"output_text": "ok"})()


class _CaptureClient:
    def __init__(self) -> None:
        self.responses = _CaptureResponses()


class _CaptureChatCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = type("Message", (), {"content": "ok"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _CaptureChat:
    def __init__(self) -> None:
        self.completions = _CaptureChatCompletions()


class _CaptureChatClient:
    def __init__(self) -> None:
        self.chat = _CaptureChat()


class _DeepSeekEmptyMessage:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning_content = "thinking trace"


class _DeepSeekChoice:
    def __init__(self) -> None:
        self.message = _DeepSeekEmptyMessage()
        self.finish_reason = "stop"


class _DeepSeekChatCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=0, total_tokens=12)
        return SimpleNamespace(choices=[_DeepSeekChoice()], usage=usage)


class _DeepSeekChat:
    def __init__(self) -> None:
        self.completions = _DeepSeekChatCompletions()


class _DeepSeekResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="")


class _DeepSeekOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.chat = _DeepSeekChat()
        self.responses = _DeepSeekResponses()


class _AnthropicEmptyBlock:
    def __init__(self) -> None:
        self.text = ""


class _AnthropicResponse:
    def __init__(self) -> None:
        self.content = [_AnthropicEmptyBlock()]
        self.stop_reason = "end_turn"
        self.usage = SimpleNamespace(input_tokens=11, output_tokens=0)


class _AnthropicMessages:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _AnthropicResponse()


class _AnthropicClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.messages = _AnthropicMessages()


def test_japanese_asr_cleanup_removes_kana_spacing_and_infers_language():
    corrector = CaptureCorrector()
    asr = SenseVoiceASR(corrector=corrector)
    asr._postprocess = lambda text: text

    text = asr._clean_text([{"text": "こ ん に ち は 、 VRChat 行 こ う"}])

    assert text == "こんにちは、 VRChat 行こう"
    assert corrector.language == "ja"


def test_detect_language_prefers_japanese_when_kana_is_present():
    assert detect_language("日本語を勉強中") == "ja"


def test_japanese_to_chinese_prompt_demands_natural_simplified_chinese():
    translator = DummyTranslator()

    prompt = translator._build_prompt(
        "今日はちょっと眠いかも",
        "ja",
        "zh",
        context_source="listen",
    )

    assert "Target language: Simplified Chinese" in prompt
    assert "natural Mainland Simplified Chinese" in prompt
    assert "idiomatic spoken Chinese" in prompt
    assert "avoid translationese" in prompt
    assert "correct obvious ASR mistakes" in prompt
    assert "preserve line breaks" in prompt


def test_prompt_demands_natural_conversational_english():
    translator = DummyTranslator()

    prompt = translator._build_prompt(
        "hello, today I am kind of sleepy",
        "ja",
        "en",
        context_source="mic",
    )

    assert "Target language: English" in prompt
    assert "natural conversational English" in prompt
    assert "avoid translationese" in prompt
    assert "obvious ASR" in prompt
    assert "contractions" in prompt


def test_legacy_persona_is_not_injected_into_translation_prompts():
    translator = DummyTranslator(
        prompt_profile={
            "mode": "roleplay",
            "persona_name": "Cool Senpai",
            "persona_prompt": "Use a calm, aloof style.",
        }
    )

    listen_prompt = translator._build_prompt(
        "ありがとう、助かった",
        "ja",
        "zh",
        context_source="listen",
    )
    mic_prompt = translator._build_prompt(
        "ありがとう、助かった",
        "ja",
        "zh",
        context_source="mic",
    )

    assert "Cool Senpai" not in listen_prompt
    assert "Cool Senpai" not in mic_prompt


def test_legacy_roleplay_profile_is_ignored_by_translation_prompt():
    translator = DummyTranslator(
        prompt_profile={
            "mode": "roleplay",
            "politeness": "casual",
            "tone": "cheerful",
            "persona_name": "Marin Preset",
            "persona_prompt": "Use bright, friendly wording.",
            "glossary": [
                "Cheerful and casual",
                "Do not make neutral text childish",
            ],
        }
    )

    mic_prompt = translator._build_prompt(
        "hello",
        "en",
        "ja",
        context_source="mic",
    )
    listen_prompt = translator._build_prompt(
        "hello",
        "en",
        "ja",
        context_source="listen",
    )

    assert "social style instructions" not in mic_prompt
    assert "Marin Preset" not in mic_prompt
    assert "bright, friendly wording" not in mic_prompt
    assert "Marin Preset" not in listen_prompt
    assert "social style instructions" not in listen_prompt


def test_standard_profile_does_not_add_saved_persona_to_prompt():
    translator = DummyTranslator(
        prompt_profile={
            "mode": "standard",
            "politeness": "polite",
            "tone": "cool",
            "persona_name": "Saved Disabled Preset",
            "persona_prompt": "This should not affect normal translation.",
            "glossary": ["Should not appear"],
        }
    )

    prompt = translator._build_prompt(
        "hello",
        "en",
        "ja",
        context_source="mic",
    )

    assert "Saved Disabled Preset" not in prompt
    assert "social style instructions" not in prompt


def test_openai_translator_skips_api_when_source_matches_target():
    translator = OpenAITranslator.__new__(OpenAITranslator)
    BaseTranslator.__init__(translator)

    assert translator.translate("hello", "en", "en") == "hello"


def test_anthropic_translator_skips_api_when_source_matches_target():
    translator = AnthropicTranslator.__new__(AnthropicTranslator)
    BaseTranslator.__init__(translator)

    assert translator.translate("hello", "ja", "ja") == "hello"


def test_translation_output_removes_cjk_spacing_artifacts():
    translator = DummyTranslator()

    output = translator._finalize_translation_output("我 们 去 VRChat 吧 。")

    assert output == "我们去 VRChat 吧。"


def test_translation_output_removes_model_boilerplate_prefixes():
    translator = DummyTranslator()

    assert (
        translator._finalize_translation_output('Here is the translation:\n"Hello there."')
        == "Hello there."
    )
    assert (
        translator._finalize_translation_output(
            "\u4ee5\u4e0b\u662f\u7ffb\u8bd1\uff1a\n\u4f60\u597d"
        )
        == "\u4f60\u597d"
    )


def test_qwen_prompt_adds_colloquial_chinese_calibration():
    translator = _openai_translator_stub()

    messages = translator._build_messages(
        "今日はちょっと眠いかも",
        "ja",
        "zh",
        context_source="listen",
    )

    assert "Qwen style calibration" in messages[0]["content"]
    assert "中国大陆日常聊天口吻" in messages[1]["content"]
    assert "今天有点困了" in messages[1]["content"]


def test_qwen_prompt_adds_colloquial_english_calibration():
    translator = _openai_translator_stub(uses_qwen_mt=False)

    messages = translator._build_messages(
        "hello, today I am kind of sleepy",
        "ja",
        "en",
        context_source="mic",
    )

    assert "Qwen style calibration" in messages[0]["content"]
    assert "Qwen colloquial English guide" in messages[1]["content"]
    assert "natural spoken line" in messages[1]["content"]
    assert "Avoid direct calques" in messages[1]["content"]


def test_qwen_mt_options_are_used_for_plain_mt_models():
    translator = _openai_translator_stub(uses_qwen_mt=True)

    assert translator._should_use_qwen_mt_translation_options("ja", "zh")
    assert translator._should_use_qwen_mt_translation_options("en", "zh")
    assert translator._should_use_qwen_mt_translation_options("zh", "ja")
    assert translator._should_use_qwen_mt_translation_options(
        "en",
        "ru",
        context_source="listen",
    )
    assert translator._should_use_qwen_mt_translation_options("en", "ru")


def test_qwen_mt_options_are_disabled_for_english_target_quality():
    translator = _openai_translator_stub(uses_qwen_mt=True)

    assert not translator._should_use_qwen_mt_translation_options("ja", "en")
    assert not translator._should_use_qwen_mt_translation_options("zh", "en")


def test_qwen_mt_options_remain_available_with_legacy_persona_config():
    translator = _openai_translator_stub(
        uses_qwen_mt=True,
        prompt_profile={
            "mode": "roleplay",
            "tone": "cool",
            "persona_name": "Cool Senpai",
            "persona_prompt": "Use a calm, aloof style.",
        },
    )
    translator._base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    translator._client = _CaptureChatClient()
    translator._extra_body = {}
    translator._omits_temperature = False
    translator._uses_max_completion_tokens = False
    translator._max_output_tokens = 192
    translator._is_reasoning_model = False

    assert translator._should_use_qwen_mt_translation_options("en", "ja")
    assert translator._translate_with_chat_completions(
        "hello",
        "en",
        "ja",
        context_source="mic",
    ) == "ok"

    kwargs = translator._client.chat.completions.kwargs
    assert kwargs["extra_body"]["translation_options"] == {
        "source_lang": "English",
        "target_lang": "Japanese",
    }
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


def test_qwen_mt_request_uses_dashscope_translation_shape():
    translator = _openai_translator_stub(uses_qwen_mt=True)
    translator._client = _CaptureChatClient()
    translator._extra_body = {}
    translator._omits_temperature = False
    translator._uses_max_completion_tokens = False
    translator._max_output_tokens = 192

    assert translator._translate_with_chat_completions("hello", "en", "ja") == "ok"
    kwargs = translator._client.chat.completions.kwargs

    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert kwargs["extra_body"]["translation_options"] == {
        "source_lang": "English",
        "target_lang": "Japanese",
    }
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs
    assert "max_completion_tokens" not in kwargs


def test_qwen_mt_switches_to_contextual_prompt_for_ambiguous_followup():
    translator = _openai_translator_stub(uses_qwen_mt=True)
    translator._client = _CaptureChatClient()
    translator._extra_body = {}
    translator._omits_temperature = False
    translator._uses_max_completion_tokens = False
    translator._max_output_tokens = 192

    assert translator._translate_with_chat_completions(
        "that one too",
        "en",
        "ja",
        context_snapshot=(("the blue avatar", "青いアバター"),),
    ) == "ok"
    kwargs = translator._client.chat.completions.kwargs
    assert "translation_options" not in kwargs.get("extra_body", {})
    assert "the blue avatar" in kwargs["messages"][0]["content"]


def test_dashscope_prompt_request_flattens_system_role():
    translator = _openai_translator_stub(is_qwen=True, uses_qwen_mt=False)
    translator._base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    translator._client = _CaptureChatClient()
    translator._extra_body = {}
    translator._omits_temperature = False
    translator._uses_max_completion_tokens = False
    translator._max_output_tokens = 192
    translator._is_reasoning_model = False

    assert translator._translate_with_chat_completions("hello", "en", "zh") == "ok"
    kwargs = translator._client.chat.completions.kwargs
    messages = kwargs["messages"]

    assert [message["role"] for message in messages] == ["user"]
    assert "Qwen style calibration" in messages[0]["content"]
    assert "Translate the following text" in messages[0]["content"]


def test_qwen_prompt_translation_uses_slightly_warmer_temperature():
    qwen = _openai_translator_stub(is_qwen=True, uses_qwen_mt=True)
    other = _openai_translator_stub(is_qwen=False, uses_qwen_mt=False)

    assert qwen._translation_temperature("zh") == 0.2
    assert qwen._translation_temperature("zh", uses_translation_options=True) == 0.0
    assert other._translation_temperature("zh") == 0.0


def test_openai_pro_responses_request_omits_temperature():
    translator = _openai_translator_stub(is_qwen=False, uses_qwen_mt=False)
    translator.model = "gpt-5.5"
    translator._client = _CaptureClient()
    translator._extra_body = {}
    translator._is_reasoning_model = True
    translator._max_output_tokens = 512
    translator._omits_temperature = True

    assert translator._translate_with_responses("hello", "en", "zh") == "ok"
    assert "temperature" not in translator._client.responses.kwargs


def test_openai_responses_request_ignores_legacy_roleplay_profile():
    translator = _openai_translator_stub(
        is_qwen=False,
        uses_qwen_mt=False,
        prompt_profile={
            "mode": "roleplay",
            "tone": "warm",
            "persona_name": "Rem Preset",
            "persona_prompt": "Use gentle, supportive wording.",
            "glossary": ["Warm but not melodramatic"],
        },
    )
    translator.model = "gpt-5.5"
    translator._client = _CaptureClient()
    translator._extra_body = {}
    translator._is_reasoning_model = False
    translator._max_output_tokens = 192
    translator._omits_temperature = True

    assert translator._translate_with_responses("hello", "en", "zh") == "ok"
    prompt = translator._client.responses.kwargs["input"]
    assert "Rem Preset" not in prompt
    assert "gentle, supportive" not in prompt
    assert "Persona safety" not in prompt


def test_anthropic_request_ignores_legacy_roleplay_profile(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_AnthropicClient))
    translator = AnthropicTranslator(
        api_key="test-key",
        model="claude-sonnet-5",
        prompt_profile={
            "mode": "roleplay",
            "tone": "playful",
            "persona_name": "Holo Preset",
            "persona_prompt": "Use wise, lightly playful wording.",
            "glossary": ["Light teasing only when suitable"],
        },
    )

    with pytest.raises(RuntimeError):
        translator.translate("hello", "en", "zh")

    prompt = translator._client.messages.kwargs["messages"][0]["content"]
    assert "Holo Preset" not in prompt
    assert "wise, lightly playful" not in prompt
    assert "Persona safety" not in prompt


def test_deepseek_v4_translation_disables_thinking(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )

    assert translator._extra_body["thinking"]["type"] == "disabled"

    assert translator._translate_with_chat_completions("hello", "en", "zh") == ""
    kwargs = translator._client.chat.completions.kwargs
    assert kwargs["extra_body"]["thinking"]["type"] == "disabled"


def test_openai_gpt_translation_disables_reasoning_and_uses_short_budget(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="gpt-5.6-sol",
        base_url="https://api.openai.com/v1",
        provider_id="openai",
    )
    translator._client = _CaptureChatClient()

    assert translator._translate_with_chat_completions("hello", "en", "zh") == "ok"
    kwargs = translator._client.chat.completions.kwargs
    assert kwargs["reasoning_effort"] == "none"
    assert "temperature" not in kwargs
    assert 32 <= kwargs["max_completion_tokens"] <= 64


def test_qwen_general_translation_disables_thinking(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        provider_id="qianwen",
    )
    translator._client = _CaptureChatClient()

    assert translator._translate_with_chat_completions("hello", "en", "zh") == "ok"
    assert translator._client.chat.completions.kwargs["extra_body"]["enable_thinking"] is False


def test_no_thinking_control_has_one_compatibility_fallback(monkeypatch):
    class RejectingCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "reasoning_effort" in kwargs:
                raise RuntimeError("unsupported parameter: reasoning_effort")
            message = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="gpt-5.6-terra",
        base_url="https://relay.example.com/v1",
        provider_id="openai_compatible",
    )
    completions = RejectingCompletions()
    translator._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    assert translator.translate("hello", "en", "zh") == "ok"
    assert len(completions.calls) == 2
    assert completions.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in completions.calls[1]

    assert translator.translate("goodbye", "en", "zh") == "ok"
    assert len(completions.calls) == 3
    assert "reasoning_effort" not in completions.calls[2]


def test_relay_falls_back_to_supported_completion_token_parameter(monkeypatch):
    class RejectingCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "reasoning_effort" in kwargs:
                raise RuntimeError("unsupported parameter: reasoning_effort")
            if "max_completion_tokens" in kwargs:
                raise RuntimeError("unsupported parameter: max_completion_tokens")
            message = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="gpt-5.6-luna",
        base_url="https://relay.example.com/v1",
        provider_id="openai_compatible",
    )
    completions = RejectingCompletions()
    translator._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    assert translator.translate("hello", "en", "zh") == "ok"
    assert len(completions.calls) == 3
    assert "reasoning_effort" in completions.calls[0]
    assert "max_completion_tokens" in completions.calls[1]
    assert "max_tokens" in completions.calls[2]

    assert translator.translate("goodbye", "en", "zh") == "ok"
    assert len(completions.calls) == 4
    assert "reasoning_effort" not in completions.calls[3]
    assert "max_completion_tokens" not in completions.calls[3]
    assert "max_tokens" in completions.calls[3]


def test_shared_translation_context_is_session_isolated_and_deferred():
    store = TranslationContextStore()
    first = DummyTranslator(context_store=store)
    second = DummyTranslator(context_store=store)

    with translation_context_scope(session_id=7, auto_commit=False):
        first._remember_context_turn("first", "第一", "en", "zh", "mic")
        assert second._context_snapshot("en", "zh", "mic", "and then?") == ()

    store.remember(
        session_id=7,
        text="first",
        translated="第一",
        src_lang="en",
        tgt_lang="zh",
        context_source="mic",
    )
    with translation_context_scope(session_id=7, auto_commit=False):
        assert second._context_snapshot("en", "zh", "mic", "and then?") == (
            ("first", "第一"),
        )
    with translation_context_scope(session_id=8, auto_commit=False):
        assert second._context_snapshot("en", "zh", "mic", "and then?") == ()


def test_pending_ordered_source_context_is_available_without_blocking_translation():
    store = TranslationContextStore()
    translator = DummyTranslator(context_store=store)
    store.stage_source(
        session_id=7,
        sequence=0,
        text="Alice said she would join later.",
        src_lang="en",
        tgt_lang="zh",
        context_source="mic",
    )

    with translation_context_scope(
        session_id=7,
        sequence=1,
        auto_commit=False,
    ):
        assert translator._context_snapshot("en", "zh", "mic", "What about her?") == (
            ("Alice said she would join later.", ""),
        )

    store.remember(
        session_id=7,
        sequence=0,
        text="Alice said she would join later.",
        translated="Alice will join later.",
        src_lang="en",
        tgt_lang="zh",
        context_source="mic",
    )
    with translation_context_scope(
        session_id=7,
        sequence=1,
        auto_commit=False,
    ):
        assert translator._context_snapshot("en", "zh", "mic", "What about her?") == (
            ("Alice said she would join later.", "Alice will join later."),
        )


def test_discarding_mic_sequence_does_not_remove_reverse_context_with_same_sequence():
    store = TranslationContextStore()
    store.stage_source(
        session_id=9,
        sequence=0,
        text="my microphone sentence",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
    )
    store.stage_source(
        session_id=9,
        sequence=0,
        text="the other player's sentence",
        src_lang="en",
        tgt_lang="ja",
        context_source="vrc_listen",
    )

    store.discard_staged(
        session_id=9,
        sequence=0,
        context_source="mic",
    )

    assert store.snapshot(
        session_id=9,
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
        current_text="and then?",
        before_sequence=1,
    ) == ()
    assert store.snapshot(
        session_id=9,
        src_lang="en",
        tgt_lang="ja",
        context_source="vrc_listen",
        current_text="and then?",
        before_sequence=1,
    ) == (("the other player's sentence", ""),)


def test_context_detection_skips_long_standalone_text():
    store = TranslationContextStore()
    store.remember(
        session_id="session",
        text="earlier",
        translated="之前",
        src_lang="en",
        tgt_lang="zh",
        context_source="mic",
    )
    assert store.snapshot(
        session_id="session",
        src_lang="en",
        tgt_lang="zh",
        context_source="mic",
        current_text="A" * 400,
    ) == ()


def test_realtime_context_skips_short_standalone_utterance():
    store = TranslationContextStore()
    store.remember(
        session_id="session",
        text="earlier",
        translated="before",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
    )
    assert store.snapshot(
        session_id="session",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
        current_text="hello everyone",
        before_sequence=2,
    ) == ()


def test_realtime_pending_context_is_capped_to_turn_limit():
    store = TranslationContextStore(max_turns=2)
    for sequence in range(4):
        store.stage_source(
            session_id="session",
            sequence=sequence,
            text=f"source-{sequence}",
            src_lang="en",
            tgt_lang="ja",
            context_source="mic",
        )
    assert store.snapshot(
        session_id="session",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
        current_text="and then?",
        before_sequence=4,
    ) == (("source-2", ""), ("source-3", ""))


def test_deepseek_empty_response_reports_provider_summary(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_DeepSeekOpenAI))
    translator = OpenAITranslator(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )

    with pytest.raises(RuntimeError) as excinfo:
        translator.translate("hello", "en", "zh")

    message = str(excinfo.value)
    assert "empty response" in message
    assert "deepseek-v4-flash" in message
    assert "finish_reason=stop" in message
    assert "reasoning_chars=" in message


def test_anthropic_empty_response_reports_provider_summary(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_AnthropicClient))
    translator = AnthropicTranslator(
        api_key="test-key",
        model="claude-sonnet-5",
    )

    with pytest.raises(RuntimeError) as excinfo:
        translator.translate("hello", "en", "zh")

    message = str(excinfo.value)
    assert "empty response" in message
    assert "claude-sonnet-5" in message
    assert "stop_reason=end_turn" in message
    assert "content_blocks=1" in message
    assert "text_chars=0" in message
    assert "output_tokens=0" in message
