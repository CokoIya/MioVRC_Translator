import threading

from PySide6.QtWidgets import QApplication

from src.core import translation_pipeline
from src.core.translation_pipeline import TranslationPipeline


def _app():
    return QApplication.instance() or QApplication([])


def test_close_suppresses_late_async_signals_and_closes_translator(
    monkeypatch,
    qtbot,
):
    _app()
    started = threading.Event()
    release = threading.Event()

    class Translator:
        def __init__(self):
            self.closed = False

        def translate(self, text, _src, _tgt, *, context_source=None):
            assert context_source == "manual"
            started.set()
            release.wait(timeout=3)
            return f"translated:{text}"

        def close(self):
            self.closed = True

    translator = Translator()
    monkeypatch.setattr(
        translation_pipeline,
        "create_translator",
        lambda _config: translator,
    )
    pipeline = TranslationPipeline(
        {
            "translation": {
                "source_language": "en",
                "target_language": "ja",
                "output_format": "translated_only",
            },
            "ui": {"language": "en"},
        }
    )
    ready = []
    errors = []
    busy = []
    pipeline.translation_ready.connect(lambda *args: ready.append(args))
    pipeline.error.connect(errors.append)
    pipeline.busy_changed.connect(busy.append)

    pipeline.translate_async("hello")
    assert started.wait(timeout=1)
    pipeline.close()
    release.set()

    qtbot.waitUntil(lambda: translator.closed and not pipeline._threads, timeout=1000)
    QApplication.processEvents()
    assert ready == []
    assert errors == []
    assert busy == [True]
