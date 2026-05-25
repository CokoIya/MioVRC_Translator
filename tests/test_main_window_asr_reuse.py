import threading

from src.ui import main_window
from src.ui.main_window import DESKTOP_SOURCE, MIC_SOURCE, MainWindow


def test_listen_asr_reuses_main_asr_when_configuration_matches(monkeypatch):
    calls: list[str | None] = []
    sentinel = object()

    def fake_create_asr(config, engine=None):
        calls.append(engine)
        return sentinel

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)

    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        },
        "vrc_listen": {"asr_engine": "same_as_main"},
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert calls == ["qwen3-asr"]
    assert mic_asr is sentinel
    assert listen_asr is sentinel


def test_listen_asr_builds_separate_provider_when_engine_differs(monkeypatch):
    calls: list[str | None] = []

    def fake_create_asr(config, engine=None):
        calls.append(engine)
        return object()

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)

    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        },
        "vrc_listen": {"asr_engine": "webspeech"},
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert calls == ["qwen3-asr", "webspeech"]
    assert mic_asr is not listen_asr


def test_listen_asr_builds_separate_provider_when_engine_is_explicit(monkeypatch):
    calls: list[str | None] = []

    def fake_create_asr(config, engine=None):
        calls.append(engine)
        return object()

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)

    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        },
        "vrc_listen": {"asr_engine": "qwen3-asr"},
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert calls == ["qwen3-asr", "qwen3-asr"]
    assert mic_asr is not listen_asr


def test_shared_asr_instance_drops_desktop_when_mic_is_transcribing():
    class SharedASR:
        provider_id = "shared"

        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def transcribe(self, _audio, *, language=None, is_final=True):
            del language, is_final
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            self.entered.set()
            self.release.wait(timeout=1.0)
            with self.lock:
                self.active -= 1
            return "ok"

    window = object.__new__(MainWindow)
    shared = SharedASR()
    window._asr = shared
    window._listen_asr = shared
    window._refresh_asr_transcribe_locks()

    results: list[str] = []

    mic_thread = threading.Thread(
        target=lambda: results.append(
            window._transcribe_for_source(MIC_SOURCE, object(), language=None, is_final=True)
        )
    )

    mic_thread.start()
    assert shared.entered.wait(timeout=1.0)

    desktop_result = window._transcribe_for_source(
        DESKTOP_SOURCE,
        object(),
        language=None,
        is_final=True,
    )
    assert shared.max_active == 1
    assert desktop_result == ""

    shared.release.set()
    mic_thread.join(timeout=1.0)

    assert results == ["ok"]
    assert shared.max_active == 1
