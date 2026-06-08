from PySide6.QtWidgets import QApplication

from src.core.manual_translation_controller import ManualTranslationController, ManualTranslationRequest
from src.core.output_dispatcher import OutputDispatcher


class _Translator:
    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None]] = []

    def translate(self, text, src, tgt, context_source=None):
        self.calls.append((text, src, tgt, context_source))
        return f"{tgt}:{text}"


def _app():
    return QApplication.instance() or QApplication([])


def test_manual_translation_controller_original_only_does_not_create_translator():
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    created: list[bool] = []
    results = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: created.append(True),
        language_detector=lambda _text: "en",
    )
    controller.succeeded.connect(results.append)
    controller.worker_finished.connect(finished.append)

    generation = controller.start(
        ManualTranslationRequest(
            text="hello",
            source_language=None,
            target_language="ja",
        )
    )

    assert generation == 1
    assert created == []
    assert finished == [1]
    assert results[0].translated_text == "hello"
    assert results[0].display_text == "hello"


def test_manual_translation_controller_translates_template_third_target(qtbot):
    _app()
    config = {
        "translation": {
            "output_format": "translated_only",
            "chatbox_template": "{translatedText}\n{translatedText2}\n{translatedText3}\n{text}",
        }
    }
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    results = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "zh",
    )
    controller.succeeded.connect(results.append)
    controller.worker_finished.connect(finished.append)

    generation = controller.start(
        ManualTranslationRequest(
            text="你好",
            source_language=None,
            target_language="ja",
            second_target_language="en",
            third_target_language="ko",
        )
    )

    assert generation == 1
    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert [(call[1], call[2], call[3]) for call in translator.calls] == [
        ("zh", "ja", "manual"),
        ("zh", "en", "manual"),
        ("zh", "ko", "manual"),
    ]
    assert results[0].display_text == "ja:你好\nen:你好\nko:你好"
    assert results[0].translated_text == "ja:你好"
    assert results[0].translated_text_2 == "en:你好"
    assert results[0].translated_text_3 == "ko:你好"


def test_manual_translation_controller_uses_explicit_source_language(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    finished: list[int] = []
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            text="hola",
            source_language="es",
            target_language="ja",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert [(call[1], call[2], call[3]) for call in translator.calls] == [("es", "ja", "manual")]


def test_manual_translation_controller_translates_third_when_second_not_requested(qtbot):
    _app()
    config = {
        "translation": {
            "output_format": "translated_only",
            "chatbox_template": "{translatedText}\n{translatedText3}\n{text}",
        }
    }
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    results = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "zh",
    )
    controller.succeeded.connect(results.append)
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            text="你好",
            source_language=None,
            target_language="ja",
            second_target_language="en",
            third_target_language="en",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert [(call[1], call[2], call[3]) for call in translator.calls] == [
        ("zh", "ja", "manual"),
        ("zh", "en", "manual"),
    ]
    assert results[0].translated_text_2 == ""
    assert results[0].translated_text_3 == "en:你好"
