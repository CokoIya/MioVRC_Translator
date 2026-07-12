import threading

from PySide6.QtWidgets import QApplication

from src.core import manual_translation_controller
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


def test_overlapping_manual_requests_use_isolated_clients_and_retire_them(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    created = []
    first_started = threading.Event()
    release_first = threading.Event()

    class Translator(_Translator):
        def __init__(self, *, blocking: bool):
            super().__init__()
            self.blocking = blocking
            self.closed = False

        def translate(self, text, src, tgt, context_source=None):
            if self.blocking:
                first_started.set()
                release_first.wait(timeout=3)
            return super().translate(text, src, tgt, context_source)

        def close(self):
            self.closed = True

    def factory(_config):
        translator = Translator(blocking=not created)
        created.append(translator)
        return translator

    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=factory,
        language_detector=lambda _text: "en",
    )
    finished: list[int] = []
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest("first", "en", "ja")
    )
    assert first_started.wait(timeout=1)
    controller.start(
        ManualTranslationRequest("second", "en", "ja")
    )

    qtbot.waitUntil(lambda: 2 in finished, timeout=1000)
    assert len(created) == 2
    assert created[0].closed is False
    assert created[1].closed is True

    release_first.set()
    qtbot.waitUntil(lambda: 1 in finished, timeout=1000)
    controller.close()
    assert created[0].closed is True


def test_close_suppresses_late_worker_signals_and_releases_translator(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    started = threading.Event()
    release = threading.Event()

    class Translator(_Translator):
        def __init__(self):
            super().__init__()
            self.closed = False

        def translate(self, text, src, tgt, context_source=None):
            started.set()
            release.wait(timeout=3)
            return super().translate(text, src, tgt, context_source)

        def close(self):
            self.closed = True

    translator = Translator()
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    succeeded = []
    failed = []
    finished = []
    controller.succeeded.connect(succeeded.append)
    controller.failed.connect(failed.append)
    controller.worker_finished.connect(finished.append)

    controller.start(ManualTranslationRequest("hello", "en", "ja"))
    assert started.wait(timeout=1)
    controller.close()
    release.set()

    qtbot.waitUntil(lambda: translator.closed and not controller._threads, timeout=1000)
    QApplication.processEvents()
    assert succeeded == []
    assert failed == []
    assert finished == []


def test_close_before_translator_acquisition_does_not_create_or_retain_client():
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    detector_started = threading.Event()
    release_detector = threading.Event()
    created = []

    def detect_language(_text):
        detector_started.set()
        release_detector.wait(timeout=3)
        return "en"

    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: created.append(_Translator()) or created[-1],
        language_detector=detect_language,
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            controller.start(ManualTranslationRequest("hello", None, "ja"))
        )
    )

    worker.start()
    assert detector_started.wait(timeout=1)
    controller.close()
    release_detector.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert results == [None]
    assert created == []
    assert controller.translator is None
    assert controller._active_translator_uses == {}
    assert controller._retired_translators == {}


def test_manual_worker_retention_is_bounded(monkeypatch):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    started = threading.Event()
    release = threading.Event()

    class Translator(_Translator):
        def translate(self, text, src, tgt, context_source=None):
            started.set()
            release.wait(timeout=3)
            return super().translate(text, src, tgt, context_source)

    translator = Translator()
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    failures = []
    controller.failed.connect(failures.append)
    monkeypatch.setattr(manual_translation_controller, "_MAX_ACTIVE_WORKERS", 1)

    try:
        assert controller.start(ManualTranslationRequest("first", "en", "ja")) == 1
        assert started.wait(timeout=1)
        assert controller.start(ManualTranslationRequest("second", "en", "ja")) == 2
        assert len(controller._threads) == 1
        assert len(failures) == 1
    finally:
        workers = tuple(controller._threads)
        release.set()
        for worker in workers:
            worker.join(timeout=1)
        controller.close()
