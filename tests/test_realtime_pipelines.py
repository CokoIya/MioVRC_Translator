import pytest

from src.core.output_dispatcher import OutputDispatcher
from src.core.realtime_pipelines import ListenPipeline, MicPipeline


class _Translator:
    def __init__(self):
        self.calls = []
        self._metrics = {}

    def translate(self, text, src, tgt, *, context_source=None):
        self.calls.append((text, src, tgt, context_source))
        call_number = len(self.calls)
        self._metrics = {
            "pool_wait_s": call_number / 100.0,
            "full_response_s": call_number / 10.0,
            "first_token_s": call_number / 20.0,
            "connection_reused": call_number > 1,
        }
        return f"{tgt}:{text}"

    def translation_metrics(self):
        return dict(self._metrics)


def test_mic_pipeline_translates_second_and_third_targets_from_template():
    dispatcher = OutputDispatcher(
        {
            "translation": {
                "output_format": "translated_only",
                "chatbox_template": "{translatedText}\n{translatedText2}\n{translatedText3}\n{text}",
            }
        }
    )
    pipeline = MicPipeline({}, dispatcher)
    translator = _Translator()

    plan = pipeline.create_plan(
        "你好",
        source_language="zh",
        target_language="ja",
        second_target_language="en",
        third_target_language="ko",
    )
    result, returned_translator = pipeline.translate_plan(plan, translator)

    assert returned_translator is translator
    assert result.translated_text == "ja:你好"
    assert result.translated_text_2 == "en:你好"
    assert result.translated_text_3 == "ko:你好"
    assert result.display_text == "ja:你好\nen:你好\nko:你好"
    assert result.chatbox_text == "ja:你好\nen:你好\nko:你好\n你好"
    assert result.output_message.source == "mic"
    assert translator.calls == [
        ("你好", "zh", "ja", "mic"),
        ("你好", "zh", "en", "mic"),
        ("你好", "zh", "ko", "mic"),
    ]
    assert result.provider_metrics["provider_calls"] == 3
    assert result.provider_metrics["pool_wait_s"] == 0.06
    assert result.provider_metrics["full_response_s"] == pytest.approx(0.6)
    assert result.provider_metrics["first_token_s"] == 0.05
    assert result.provider_metrics["connection_reused_calls"] == 2


def test_mic_pipeline_original_only_skips_translator():
    dispatcher = OutputDispatcher({"translation": {"output_format": "original_only"}})
    pipeline = MicPipeline({}, dispatcher)

    plan = pipeline.create_plan("hello", source_language="en", target_language="ja")
    result, returned_translator = pipeline.translate_plan(plan, None)

    assert returned_translator is None
    assert result.api_translation_used is False
    assert result.translated_text == "hello"
    assert result.chatbox_text == "hello"


def test_mic_pipeline_original_only_read_translation_translates_for_tts_only():
    config = {
        "translation": {
            "output_format": "original_only_read_translation",
            "chatbox_template": "{translatedText2}\n{text}",
        }
    }
    dispatcher = OutputDispatcher(config)
    pipeline = MicPipeline(config, dispatcher)
    translator = _Translator()

    plan = pipeline.create_plan(
        "hello",
        source_language="en",
        target_language="ja",
        second_target_language="zh",
    )
    result, returned_translator = pipeline.translate_plan(plan, translator)

    assert returned_translator is translator
    assert result.api_translation_used is True
    assert result.translated_text == "ja:hello"
    assert result.translated_text_2 == ""
    assert result.display_text == "hello"
    assert result.chatbox_text == "hello"
    assert translator.calls == [("hello", "en", "ja", "mic")]


def test_listen_pipeline_builds_prefixed_chatbox_text():
    dispatcher = OutputDispatcher({})
    pipeline = ListenPipeline({}, dispatcher)
    translator = _Translator()

    plan = pipeline.create_plan(
        "hello",
        source_language="en",
        target_language="ja",
        listen_prefix="[Listen]",
    )
    result, returned_translator = pipeline.translate_plan(plan, translator)

    assert returned_translator is translator
    assert result.translated_text == "ja:hello"
    assert result.display_text == "hello（ja:hello）"
    assert result.chatbox_text == "[Listen] hello（ja:hello）"
    assert result.output_message.source == "listen"
    assert translator.calls == [("hello", "en", "ja", "listen")]


def test_listen_pipeline_same_language_stays_local():
    dispatcher = OutputDispatcher({})
    pipeline = ListenPipeline({}, dispatcher)

    plan = pipeline.create_plan(
        "こんにちは",
        source_language="ja",
        target_language="ja",
        listen_prefix="[Listen]",
    )
    result, returned_translator = pipeline.translate_plan(plan, None)

    assert returned_translator is None
    assert result.api_translation_used is False
    assert result.display_text == "こんにちは"
    assert result.chatbox_text == "[Listen] こんにちは"


def test_mic_pipeline_closes_internally_created_translator_on_failure():
    dispatcher = OutputDispatcher({"translation": {"output_format": "translated_only"}})

    class FailingTranslator:
        def __init__(self):
            self.closed = False

        def translate(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def close(self):
            self.closed = True

    translator = FailingTranslator()
    pipeline = MicPipeline(
        {},
        dispatcher,
        translator_factory=lambda _config: translator,
    )
    plan = pipeline.create_plan("hello", source_language="en", target_language="ja")

    try:
        pipeline.translate_plan(plan)
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("translation failure was not propagated")

    assert translator.closed is True


def test_listen_pipeline_closes_internally_created_translator_on_failure():
    dispatcher = OutputDispatcher({})

    class FailingTranslator:
        def __init__(self):
            self.closed = False

        def translate(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def close(self):
            self.closed = True

    translator = FailingTranslator()
    pipeline = ListenPipeline(
        {},
        dispatcher,
        translator_factory=lambda _config: translator,
    )
    plan = pipeline.create_plan(
        "hello",
        source_language="en",
        target_language="ja",
        listen_prefix="[Listen]",
    )

    try:
        pipeline.translate_plan(plan)
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("translation failure was not propagated")

    assert translator.closed is True
