import logging
import threading
import time

from PySide6.QtWidgets import QApplication

from src.core import manual_translation_controller
from src.core.manual_translation_controller import ManualTranslationController, ManualTranslationRequest
from src.core.output_dispatcher import OutputDispatcher
from src.core.rewrite_coordinator import (
    REWRITE_PRIORITY_TYPED,
    RewriteCallCoordinator,
    RewriteCoordinatorFullError,
)


class _Translator:
    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.rewrite_calls: list[tuple[str, str, str, str]] = []

    def translate(self, text, src, tgt, context_source=None):
        self.calls.append((text, src, tgt, context_source))
        return f"{tgt}:{text}"

    def rewrite_asr(self, text, style, *, language_hint="auto", context_source="mic"):
        self.rewrite_calls.append((text, style, language_hint, context_source))
        return f"rewritten:{text}"


def _app():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_manual_latency_aggregates_all_target_provider_phases(caplog):
    class Translator:
        def __init__(self):
            self.calls = 0
            self._metrics = {}

        def translate(self, text, _src, tgt, *, context_source=None):
            assert context_source == "manual"
            self.calls += 1
            self._metrics = {
                "pool_wait_s": self.calls / 100.0,
                "full_response_s": self.calls / 10.0,
                "first_token_s": self.calls / 20.0,
                "parse_s": self.calls / 1000.0,
            }
            return f"{tgt}:{text}"

        def translation_metrics(self):
            return dict(self._metrics)

        def close(self):
            return None

    translator = Translator()
    config = {
        "translation": {
            "output_format": "translated_only",
            "chatbox_template": (
                "{translatedText} {translatedText2} {translatedText3}"
            ),
        }
    }
    controller = ManualTranslationController(
        config,
        OutputDispatcher(config),
        translator_factory=lambda _config: translator,
    )

    with caplog.at_level(logging.INFO):
        generation = controller.start(
            ManualTranslationRequest(
                text="hello",
                source_language="en",
                target_language="ja",
                second_target_language="zh",
                third_target_language="ko",
            )
        )
        deadline = time.monotonic() + 2.0
        while controller._threads and time.monotonic() < deadline:
            time.sleep(0.01)

    assert generation is not None
    assert translator.calls == 3
    message = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Manual translation latency")
    )
    assert "provider_calls=3" in message
    assert "connection_pool_wait_ms=60.0" in message
    assert "streaming_first_token_ms=50.0" in message
    assert "full_response_ms=600.0" in message
    assert "parsing_postprocessing_ms=6.0" in message
    assert controller.close(wait_timeout_s=1.0)


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


def test_manual_translation_controller_rewrites_once_before_all_targets(qtbot):
    _app()
    config = {
        "translation": {
            "output_format": "translated_only",
            "chatbox_template": (
                "{translatedText}\n{translatedText2}\n{translatedText3}\n{text}"
            ),
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
            text="hello",
            source_language=None,
            target_language="ja",
            second_target_language="en",
            third_target_language="ko",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert translator.rewrite_calls == [
        ("hello", "catgirl", "zh", "manual"),
    ]
    assert [call[0] for call in translator.calls] == [
        "rewritten:hello",
        "rewritten:hello",
        "rewritten:hello",
    ]
    assert results[0].original_text == "rewritten:hello"
    assert results[0].translated_text == "ja:rewritten:hello"
    assert results[0].translated_text_2 == "en:rewritten:hello"
    assert results[0].translated_text_3 == "ko:rewritten:hello"


def test_manual_translation_controller_rewrite_toggle_off_skips_rewrite(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "hello",
            "en",
            "ja",
            rewrite_typed_text=False,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert translator.rewrite_calls == []
    assert translator.calls[0][0] == "hello"


def test_original_only_typed_rewrite_runs_asynchronously(qtbot):
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    created = []
    started: list[int] = []
    results = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: created.append(translator) or translator,
        language_detector=lambda _text: "en",
    )
    controller.started.connect(started.append)
    controller.succeeded.connect(results.append)
    controller.worker_finished.connect(finished.append)

    generation = controller.start(
        ManualTranslationRequest(
            "hello",
            None,
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    assert generation == 1
    assert started == [1]
    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert created == [translator]
    assert translator.rewrite_calls == [("hello", "catgirl", "en", "manual")]
    assert translator.calls == []
    assert results[0].original_text == "rewritten:hello"
    assert results[0].translated_text == "rewritten:hello"
    assert results[0].display_text == "rewritten:hello"


def test_same_language_typed_rewrite_runs_without_translation(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    results = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    controller.succeeded.connect(results.append)
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "hello",
            "en",
            "en",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert translator.rewrite_calls == [("hello", "catgirl", "en", "manual")]
    assert translator.calls == []
    assert results[0].original_text == "rewritten:hello"
    assert results[0].translated_text == "rewritten:hello"


def test_typed_rewrite_failure_fails_open_and_translation_continues(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)

    class Translator(_Translator):
        def rewrite_asr(self, text, style, *, language_hint="auto", context_source="mic"):
            self.rewrite_calls.append((text, style, language_hint, context_source))
            raise RuntimeError("rewrite unavailable")

    translator = Translator()
    results = []
    errors = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    controller.succeeded.connect(results.append)
    controller.failed.connect(errors.append)
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "hello",
            None,
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert errors == []
    assert translator.rewrite_calls == [("hello", "catgirl", "en", "manual")]
    assert translator.calls == [("hello", "en", "ja", "manual")]
    assert results[0].original_text == "hello"
    assert results[0].translated_text == "ja:hello"


def test_sequential_typed_rewrites_reuse_persistent_translator(qtbot):
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    created = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: created.append(translator) or translator,
        language_detector=lambda _text: "en",
    )
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "first",
            "en",
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )
    qtbot.waitUntil(lambda: 1 in finished, timeout=1000)
    controller.start(
        ManualTranslationRequest(
            "second",
            "en",
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )
    qtbot.waitUntil(lambda: 2 in finished, timeout=1000)

    assert created == [translator]
    assert [call[0] for call in translator.rewrite_calls] == ["first", "second"]
    controller.close()


def test_typed_rewrite_uses_injected_coordinator(qtbot):
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()

    class Coordinator:
        def __init__(self):
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["operation"]()

    coordinator = Coordinator()
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
        rewrite_coordinator=coordinator,
    )
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "hello",
            None,
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert len(coordinator.calls) == 1
    call = coordinator.calls[0]
    assert call["priority"] == REWRITE_PRIORITY_TYPED
    assert call["config"] is config
    assert call["style"] == "catgirl"
    assert call["language_hint"] == "en"
    assert call["text"] == "hello"
    assert call["wait_timeout_s"] == 2.0
    assert translator.rewrite_calls == [("hello", "catgirl", "en", "manual")]


def test_injected_coordinator_cache_avoids_duplicate_typed_rewrite(qtbot):
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()
    coordinator = RewriteCallCoordinator(cache_size=8)
    finished: list[int] = []
    results = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
        rewrite_coordinator=coordinator,
    )
    controller.worker_finished.connect(finished.append)
    controller.succeeded.connect(results.append)
    request = ManualTranslationRequest(
        "hello",
        "en",
        "ja",
        rewrite_typed_text=True,
        rewrite_style="catgirl",
    )

    controller.start(request)
    qtbot.waitUntil(lambda: 1 in finished, timeout=1000)
    controller.start(request)
    qtbot.waitUntil(lambda: 2 in finished, timeout=1000)

    assert translator.rewrite_calls == [("hello", "catgirl", "en", "manual")]
    assert [result.original_text for result in results] == [
        "rewritten:hello",
        "rewritten:hello",
    ]
    controller.close()
    coordinator.close()


def test_injected_coordinator_rejection_fails_open(qtbot):
    _app()
    config = {"translation": {"output_format": "translated_only"}}
    dispatcher = OutputDispatcher(config)
    translator = _Translator()

    class FullCoordinator:
        def execute(self, **_kwargs):
            raise RewriteCoordinatorFullError("rewrite queue full")

    results = []
    errors = []
    finished: list[int] = []
    controller = ManualTranslationController(
        config,
        dispatcher,
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
        rewrite_coordinator=FullCoordinator(),
    )
    controller.succeeded.connect(results.append)
    controller.failed.connect(errors.append)
    controller.worker_finished.connect(finished.append)

    controller.start(
        ManualTranslationRequest(
            "hello",
            None,
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )
    )

    qtbot.waitUntil(lambda: bool(finished), timeout=1000)
    assert errors == []
    assert translator.rewrite_calls == []
    assert translator.calls == [("hello", "en", "ja", "manual")]
    assert results[0].original_text == "hello"


def test_rewrite_only_manual_worker_retention_is_bounded(monkeypatch):
    _app()
    config = {"translation": {"output_format": "original_only"}}
    dispatcher = OutputDispatcher(config)
    started = threading.Event()
    release = threading.Event()

    class Translator(_Translator):
        def rewrite_asr(self, text, style, *, language_hint="auto", context_source="mic"):
            self.rewrite_calls.append((text, style, language_hint, context_source))
            started.set()
            release.wait(timeout=3)
            return f"rewritten:{text}"

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

    def request(text):
        return ManualTranslationRequest(
            text,
            "en",
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )

    try:
        assert controller.start(request("first")) == 1
        assert started.wait(timeout=1)
        assert controller.start(request("second")) == 2
        assert len(controller._threads) == 1
        assert len(failures) == 1
    finally:
        workers = tuple(controller._threads)
        release.set()
        for worker in workers:
            worker.join(timeout=1)
        controller.close()


def test_close_waits_for_active_manual_request_and_releases_client():
    _app()
    started = threading.Event()
    release = threading.Event()

    class Translator(_Translator):
        def __init__(self):
            super().__init__()
            self.cancelled = 0
            self.closed = 0

        def translate(self, text, src, tgt, context_source=None):
            started.set()
            release.wait(timeout=2)
            return super().translate(text, src, tgt, context_source)

        def cancel_pending_requests(self):
            self.cancelled += 1

        def close(self):
            self.closed += 1

    translator = Translator()
    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: translator,
        language_detector=lambda _text: "en",
    )
    assert controller.start(ManualTranslationRequest("hello", "en", "ja")) == 1
    assert started.wait(timeout=1)

    assert controller.close(wait_timeout_s=0.01) is False
    assert _wait_until(
        lambda: translator.cancelled == 1 and translator.closed == 1,
    )

    release.set()
    assert controller.close(wait_timeout_s=1.0) is True


def test_manual_timeout_cancellation_rotates_client_and_marks_late_result_stale():
    _app()
    first_started = threading.Event()
    release_first = threading.Event()
    created = []

    class Translator(_Translator):
        def __init__(self, blocking=False):
            super().__init__()
            self.blocking = blocking
            self.cancelled = 0
            self.closed = 0

        def translate(self, text, src, tgt, context_source=None):
            if self.blocking:
                first_started.set()
                release_first.wait(timeout=2)
            return super().translate(text, src, tgt, context_source)

        def cancel_pending_requests(self):
            self.cancelled += 1

        def close(self):
            self.closed += 1

    def factory(_config):
        translator = Translator(blocking=not created)
        created.append(translator)
        return translator

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=factory,
        language_detector=lambda _text: "en",
    )
    assert controller.start(ManualTranslationRequest("first", "en", "ja")) == 1
    assert first_started.wait(timeout=1)
    assert controller.cancel_active_requests() == 2
    assert _wait_until(
        lambda: created[0].cancelled == 1 and created[0].closed == 1,
    )
    assert controller.translator is None

    assert controller.start(ManualTranslationRequest("second", "en", "ja")) == 3
    workers = tuple(controller._threads)
    release_first.set()
    for worker in workers:
        worker.join(timeout=1)

    assert len(created) == 2
    assert controller.generation == 3
    assert created[0].calls[0][0] == "first"
    assert created[1].calls[0][0] == "second"
    controller.close()


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
    assert created[1].closed is False
    assert controller.translator is created[1]

    release_first.set()
    qtbot.waitUntil(
        lambda: 1 in finished and created[0].closed,
        timeout=1000,
    )
    assert controller.close(wait_timeout_s=1.0)
    assert created[0].closed is True
    assert created[1].closed is True


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


def test_detached_translator_close_is_bounded_and_tracked_until_quiescent():
    _app()
    close_started = threading.Event()
    release_close = threading.Event()
    close_thread_ids = []

    class Translator:
        def close(self):
            close_thread_ids.append(threading.get_ident())
            close_started.set()
            release_close.wait(timeout=2)

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: Translator(),
    )
    translator = Translator()
    controller.translator = translator
    caller_thread_id = threading.get_ident()

    started_at = time.monotonic()
    controller.translator = None
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert close_started.wait(timeout=1)
    assert len(close_thread_ids) == 1
    assert close_thread_ids[0] != caller_thread_id
    assert controller.close(wait_timeout_s=0.01) is False

    release_close.set()
    assert controller.close(wait_timeout_s=1.0) is True
    assert controller._cleanup_threads == set()


def test_cancel_active_requests_is_bounded_through_blocking_cancel_and_close():
    _app()
    cancel_started = threading.Event()
    release_cancel = threading.Event()
    close_started = threading.Event()
    release_close = threading.Event()
    cleanup_thread_ids = []

    class Translator:
        def cancel_pending_requests(self):
            cleanup_thread_ids.append(threading.get_ident())
            cancel_started.set()
            release_cancel.wait(timeout=2)

        def close(self):
            close_started.set()
            release_close.wait(timeout=2)

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: Translator(),
    )
    controller.translator = Translator()
    caller_thread_id = threading.get_ident()

    started_at = time.monotonic()
    assert controller.cancel_active_requests() == 1
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert cancel_started.wait(timeout=1)
    assert cleanup_thread_ids[0] != caller_thread_id
    assert controller.close(wait_timeout_s=0.01) is False

    release_cancel.set()
    assert close_started.wait(timeout=1)
    assert controller.close(wait_timeout_s=0.01) is False

    release_close.set()
    assert controller.close(wait_timeout_s=1.0) is True


def test_cleanup_thread_start_failure_uses_tracked_emergency_fallback(
    monkeypatch,
    caplog,
):
    _app()
    closed = threading.Event()
    registered_before_start = []
    secret = "cleanup-thread-start-secret"

    class Translator:
        def close(self):
            closed.set()

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: Translator(),
    )
    translator = Translator()
    controller.translator = translator
    original_start = threading.Thread.start

    def fail_cleanup_start(thread):
        if thread.name.startswith("manual-translator-cleanup-"):
            registered_before_start.append(thread in controller._cleanup_threads)
            raise RuntimeError(secret)
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_cleanup_start)
    caplog.set_level(logging.WARNING, logger="src.core.manual_translation_controller")

    controller.translator = None

    assert registered_before_start == [True]
    assert closed.wait(timeout=1)
    assert controller.close(wait_timeout_s=1.0) is True
    assert secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_total_cleanup_start_failure_remains_nonquiescent_and_retries(monkeypatch):
    _app()
    closed = threading.Event()
    close_thread_ids = []

    class Translator:
        def close(self):
            close_thread_ids.append(threading.get_ident())
            closed.set()

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: Translator(),
    )
    translator = Translator()
    controller.translator = translator
    caller_thread_id = threading.get_ident()
    original_thread_start = threading.Thread.start
    original_low_level_start = manual_translation_controller._thread.start_new_thread

    def fail_cleanup_start(thread):
        if thread.name.startswith("manual-translator-cleanup-"):
            raise RuntimeError("cleanup thread unavailable")
        return original_thread_start(thread)

    def fail_low_level_start(_function, _args):
        raise RuntimeError("emergency cleanup unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_cleanup_start)
    monkeypatch.setattr(
        manual_translation_controller._thread,
        "start_new_thread",
        fail_low_level_start,
    )

    controller.translator = None

    assert closed.is_set() is False
    assert controller._cleanup_threads == set()
    assert controller._pending_cleanup_retries
    with controller._lock:
        assert controller._quiescent_locked(threading.current_thread()) is False

    def restore_thread_creation():
        time.sleep(0.08)
        setattr(threading.Thread, "start", original_thread_start)
        setattr(
            manual_translation_controller._thread,
            "start_new_thread",
            original_low_level_start,
        )

    restorer = threading.Thread(target=restore_thread_creation, daemon=True)
    original_thread_start(restorer)
    assert controller.close(wait_timeout_s=1.0) is True
    restorer.join(timeout=1)
    assert closed.is_set()
    assert close_thread_ids and close_thread_ids[0] != caller_thread_id
    assert controller._pending_cleanup_retries == {}


def test_cleanup_failures_are_logged_without_raw_provider_prose(caplog):
    _app()
    cancel_secret = "cancel-cleanup-provider-secret"
    close_secret = "close-cleanup-provider-secret"

    class Translator:
        def cancel_pending_requests(self):
            raise RuntimeError(cancel_secret)

        def close(self):
            raise ValueError(close_secret)

    controller = ManualTranslationController(
        {"translation": {"output_format": "translated_only"}},
        OutputDispatcher({"translation": {"output_format": "translated_only"}}),
        translator_factory=lambda _config: Translator(),
    )
    controller.translator = Translator()
    caplog.set_level(logging.DEBUG, logger="src.core.manual_translation_controller")

    assert controller.close(wait_timeout_s=1.0) is True

    assert cancel_secret not in caplog.text
    assert close_secret not in caplog.text
    assert "error=type=RuntimeError" in caplog.text
    assert "error=type=ValueError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_typed_rewrite_wall_timeout_rotates_before_translation_and_recovers():
    _app()
    created = []

    class RewriteTimeoutTranslator(_Translator):
        def __init__(self):
            super().__init__()
            self.metrics = {}
            self.closed = 0

        def rewrite_asr(
            self,
            text,
            style,
            *,
            language_hint="auto",
            context_source="mic",
        ):
            self.rewrite_calls.append((text, style, language_hint, context_source))
            self.metrics = {"wall_timeout_triggered": True}
            raise TimeoutError("typed rewrite exceeded its wall timeout")

        def translate(self, *_args, **_kwargs):
            raise AssertionError("retired rewrite client must not translate")

        def translation_metrics(self):
            return dict(self.metrics)

        def close(self):
            self.closed += 1

    class FreshTranslator(_Translator):
        def __init__(self):
            super().__init__()
            self.closed = 0

        def close(self):
            self.closed += 1

    first = RewriteTimeoutTranslator()
    fresh = FreshTranslator()

    def factory(_config):
        translator = first if not created else fresh
        created.append(translator)
        return translator

    config = {"translation": {"output_format": "translated_only"}}
    controller = ManualTranslationController(
        config,
        OutputDispatcher(config),
        translator_factory=factory,
        language_detector=lambda _text: "en",
    )
    def request(text):
        return ManualTranslationRequest(
            text,
            "en",
            "ja",
            rewrite_typed_text=True,
            rewrite_style="catgirl",
        )

    assert controller.start(request("first")) == 1
    assert _wait_until(lambda: not controller._threads)
    assert fresh.calls == [("first", "en", "ja", "manual")]
    assert controller.translator is fresh
    assert first.closed == 0

    assert controller.start(request("second")) == 2
    assert _wait_until(lambda: not controller._threads)
    assert len(created) == 2
    assert fresh.rewrite_calls == [("second", "catgirl", "en", "manual")]
    assert fresh.calls[-1] == ("rewritten:second", "en", "ja", "manual")
    assert controller.close(wait_timeout_s=1.0) is True


def test_translation_wall_timeout_detaches_client_and_next_request_recovers():
    _app()
    created = []

    class TranslationTimeoutTranslator(_Translator):
        def __init__(self):
            super().__init__()
            self.retired = False
            self.metrics = {}
            self.closed = 0

        def translate(self, text, src, tgt, context_source=None):
            self.calls.append((text, src, tgt, context_source))
            self.retired = True
            self.metrics = {"wall_timeout_triggered": True}
            raise TimeoutError("translation exceeded its wall timeout")

        def _pending_requests_retired(self):
            return self.retired

        def translation_metrics(self):
            return dict(self.metrics)

        def close(self):
            self.closed += 1

    class FreshTranslator(_Translator):
        def __init__(self):
            super().__init__()
            self.closed = 0

        def close(self):
            self.closed += 1

    first = TranslationTimeoutTranslator()
    fresh = FreshTranslator()

    def factory(_config):
        translator = first if not created else fresh
        created.append(translator)
        return translator

    config = {"translation": {"output_format": "translated_only"}}
    controller = ManualTranslationController(
        config,
        OutputDispatcher(config),
        translator_factory=factory,
        language_detector=lambda _text: "en",
    )

    assert controller.start(ManualTranslationRequest("first", "en", "ja")) == 1
    assert _wait_until(lambda: not controller._threads)
    assert controller.translator is None
    assert first.closed == 0

    # MainWindow can still hold the timed-out cached object until its next
    # request. Reassigning it must not put the retired client back into service.
    controller.translator = first
    assert controller.translator is None

    assert controller.start(ManualTranslationRequest("second", "en", "ja")) == 2
    assert _wait_until(lambda: not controller._threads)
    assert len(created) == 2
    assert fresh.calls == [("second", "en", "ja", "manual")]
    assert controller.translator is fresh
    assert controller.close(wait_timeout_s=1.0) is True


def test_overlapping_requests_cache_new_client_and_retire_previous_client():
    _app()
    created = []
    first_started = threading.Event()
    release_first = threading.Event()

    class Translator(_Translator):
        def __init__(self, *, blocking):
            super().__init__()
            self.blocking = blocking
            self.closed = False

        def translate(self, text, src, tgt, context_source=None):
            if self.blocking:
                first_started.set()
                release_first.wait(timeout=2)
            return super().translate(text, src, tgt, context_source)

        def close(self):
            self.closed = True

    def factory(_config):
        translator = Translator(blocking=not created)
        created.append(translator)
        return translator

    config = {"translation": {"output_format": "translated_only"}}
    controller = ManualTranslationController(
        config,
        OutputDispatcher(config),
        translator_factory=factory,
        language_detector=lambda _text: "en",
    )

    assert controller.start(ManualTranslationRequest("first", "en", "ja")) == 1
    assert first_started.wait(timeout=1)
    assert controller.start(ManualTranslationRequest("second", "en", "ja")) == 2
    assert _wait_until(
        lambda: len(created) == 2 and bool(created[1].calls),
    )
    assert controller.translator is created[1]
    assert created[0].closed is False
    assert created[1].closed is False

    release_first.set()
    assert _wait_until(lambda: not controller._threads and created[0].closed)
    assert controller.translator is created[1]
    assert created[1].closed is False
    assert controller.close(wait_timeout_s=1.0) is True
    assert created[1].closed is True
