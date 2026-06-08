from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.asr.sensevoice_asr import SenseVoiceASR
from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import BaseTranslator
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
    translator.model = "qwen-mt-flash" if uses_qwen_mt else "qwen3.7-max"
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


def test_listen_prompt_does_not_apply_user_persona():
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
    assert "Cool Senpai" in mic_prompt


def test_roleplay_profile_adds_persona_tone_and_safety_to_mic_prompt():
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

    assert "follow the social style instructions below" in mic_prompt
    assert "Social mode: roleplay" in mic_prompt
    assert "bright, friendly, energetic" in mic_prompt
    assert "Persona name: Marin Preset" in mic_prompt
    assert "Persona notes: Use bright, friendly wording." in mic_prompt
    assert "Preferred glossary" in mic_prompt
    assert "Persona safety" in mic_prompt
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


def test_qwen_mt_options_are_disabled_when_persona_is_active():
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

    assert not translator._should_use_qwen_mt_translation_options("en", "ja")
    assert translator._translate_with_chat_completions(
        "hello",
        "en",
        "ja",
        context_source="mic",
    ) == "ok"

    kwargs = translator._client.chat.completions.kwargs
    assert "extra_body" not in kwargs
    assert "Cool Senpai" in kwargs["messages"][0]["content"]
    assert "translation_options" not in kwargs["messages"][0]["content"]


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


def test_openai_responses_request_includes_roleplay_profile():
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
    assert "Rem Preset" in prompt
    assert "gentle, supportive" in prompt
    assert "Persona safety" in prompt


def test_anthropic_request_includes_roleplay_profile(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_AnthropicClient))
    translator = AnthropicTranslator(
        api_key="test-key",
        model="claude-sonnet-4-20250514",
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
    assert "Holo Preset" in prompt
    assert "wise, lightly playful" in prompt
    assert "Persona safety" in prompt


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
        model="claude-sonnet-4-20250514",
    )

    with pytest.raises(RuntimeError) as excinfo:
        translator.translate("hello", "en", "zh")

    message = str(excinfo.value)
    assert "empty response" in message
    assert "claude-sonnet-4-20250514" in message
    assert "stop_reason=end_turn" in message
    assert "content_blocks=1" in message
    assert "text_chars=0" in message
    assert "output_tokens=0" in message
