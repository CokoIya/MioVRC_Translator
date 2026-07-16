import threading

import pytest

from src.ui_qt import main_window
from src.ui_qt.main_window import DESKTOP_SOURCE, MIC_SOURCE, MainWindow


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
        "vrc_listen": {"enabled": True, "asr_engine": "webspeech"},
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
        "vrc_listen": {"enabled": True, "asr_engine": "qwen3-asr"},
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert calls == ["qwen3-asr", "qwen3-asr"]
    assert mic_asr is not listen_asr


def test_enabled_follow_main_qwen_uses_isolated_low_latency_runtime(monkeypatch):
    calls: list[tuple[str | None, float | None]] = []

    def fake_create_asr(config, engine=None):
        timeout = config.get("asr", {}).get("qwen3_asr", {}).get(
            "hard_timeout_seconds"
        )
        calls.append((engine, timeout))
        return object()

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {
                "model": "qwen3-asr-flash",
                "hard_timeout_seconds": 12.0,
            },
        },
        "vrc_listen": {
            "enabled": True,
            "asr_engine": "same_as_main",
            "asr_timeout_s": 4.5,
        },
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert mic_asr is not listen_asr
    assert calls == [("qwen3-asr", 12.0), ("qwen3-asr", 4.5)]
    assert config["asr"]["qwen3_asr"]["hard_timeout_seconds"] == 12.0


def test_disabled_listen_does_not_construct_a_second_heavy_provider(monkeypatch):
    calls: list[str | None] = []
    provider = object()

    def fake_create_asr(config, engine=None):
        del config
        calls.append(engine)
        return provider

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)
    config = {
        "asr": {"engine": "sensevoice-small"},
        "vrc_listen": {"enabled": False, "asr_engine": "qwen3-asr"},
    }

    mic_asr, listen_asr = main_window._create_asr_pair(config)

    assert calls == ["sensevoice-small"]
    assert mic_asr is provider
    assert listen_asr is provider


def test_create_asr_pair_closes_main_provider_when_listen_creation_fails(monkeypatch):
    class MainASR:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    provider = MainASR()
    calls: list[str | None] = []

    def fake_create_asr(config, engine=None):
        del config
        calls.append(engine)
        if len(calls) == 1:
            return provider
        raise RuntimeError("listen provider failed")

    monkeypatch.setattr(main_window, "create_asr", fake_create_asr)
    config = {
        "asr": {"engine": "qwen3-asr"},
        "vrc_listen": {"enabled": True, "asr_engine": "webspeech"},
    }

    with pytest.raises(RuntimeError, match="listen provider failed"):
        main_window._create_asr_pair(config)

    assert calls == ["qwen3-asr", "webspeech"]
    assert provider.close_calls == 1


def test_shared_asr_instance_serializes_both_final_transcriptions_without_dropping():
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
            self.release.wait(timeout=2.0)
            with self.lock:
                self.active -= 1
            return "ok"

    window = MainWindow.__new__(MainWindow)
    shared = SharedASR()
    window._asr = shared
    window._listen_asr = shared
    window._refresh_asr_transcribe_locks()

    results: dict[str, str] = {}
    desktop_done = threading.Event()

    mic_thread = threading.Thread(
        target=lambda: results.setdefault(
            "mic",
            window._transcribe_for_source(
                MIC_SOURCE,
                object(),
                language=None,
                is_final=True,
            ),
        )
    )

    def transcribe_desktop() -> None:
        results["desktop"] = window._transcribe_for_source(
            DESKTOP_SOURCE,
            object(),
            language=None,
            is_final=True,
        )
        desktop_done.set()

    desktop_thread = threading.Thread(target=transcribe_desktop)
    mic_thread.start()
    assert shared.entered.wait(timeout=1.0)

    desktop_thread.start()
    assert not desktop_done.wait(timeout=0.1)
    assert shared.max_active == 1

    shared.release.set()
    mic_thread.join(timeout=1.0)
    desktop_thread.join(timeout=1.0)

    assert not mic_thread.is_alive()
    assert not desktop_thread.is_alive()
    assert results == {"mic": "ok", "desktop": "ok"}
    assert shared.max_active == 1
