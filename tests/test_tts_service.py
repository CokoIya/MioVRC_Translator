from __future__ import annotations

import threading

from src.core import tts_service as tts_service_module
from src.core.tts_service import TtsService


class _FakeManager:
    def __init__(self) -> None:
        self.callback = None
        self.request_context = None
        self.clear_calls = []
        self.stop_playback_calls = 0
        self.close_calls = 0
        self.close_timeout = None
        self.close_result = object()

    def speak(self, _text, **kwargs):
        self.callback = kwargs["callback"]
        self.request_context = kwargs.get("request_context")
        return True

    def clear_queue(self, **kwargs):
        self.clear_calls.append(kwargs)

    def stop_playback(self):
        self.stop_playback_calls += 1

    def close(self, timeout_seconds=None):
        self.close_calls += 1
        self.close_timeout = timeout_seconds
        return self.close_result


def test_tts_service_finishes_only_after_manager_terminal_callback():
    service = TtsService(
        {
            "tts": {
                "engine": "qwen_tts",
                "qwen_tts": {"voice": "Cherry", "rate": 1.0, "volume": 0.8},
            }
        }
    )
    manager = _FakeManager()
    service._manager = manager
    started = []
    finished = []
    errors = []
    service.speech_started.connect(lambda: started.append(True))
    service.speech_finished.connect(lambda: finished.append(True))
    service.error.connect(errors.append)

    assert service.speak(
        "hello",
        request_context={"source": "mic", "sequence": 9},
    ) is True
    assert started == [True]
    assert finished == []
    assert manager.request_context == {"source": "mic", "sequence": 9}

    manager.callback(True, "")
    assert finished == [True]
    assert errors == []


def test_tts_service_stop_drains_queue_and_close_releases_manager():
    service = TtsService({"tts": {"engine": "qwen_tts", "qwen_tts": {}}})
    manager = _FakeManager()
    service._manager = manager

    service.stop()

    assert manager.clear_calls == [
        {"message": "TTS service stopped.", "error_code": "stopped"}
    ]
    assert manager.stop_playback_calls == 1

    close_result = service.close(timeout_seconds=0.25)
    assert manager.close_calls == 1
    assert manager.close_timeout == 0.25
    assert close_result is manager.close_result
    assert service._manager is None


def test_tts_service_never_emits_finished_before_started_signal_returns():
    callback_release = threading.Event()
    callback_done = threading.Event()
    callback_thread = None

    class RacingManager(_FakeManager):
        def speak(self, _text, **kwargs):
            nonlocal callback_thread
            callback = kwargs["callback"]

            def finish_immediately_after_admission():
                callback_release.wait(timeout=1)
                callback(True, "")
                callback_done.set()

            callback_thread = threading.Thread(
                target=finish_immediately_after_admission
            )
            callback_thread.start()
            return True

    service = TtsService(
        {"tts": {"engine": "qwen_tts", "qwen_tts": {"voice": "Cherry"}}}
    )
    service._manager = RacingManager()
    events = []

    def started_slot():
        callback_release.set()
        assert callback_done.wait(timeout=1)
        events.append("started")

    service.speech_started.connect(started_slot)
    service.speech_finished.connect(lambda: events.append("finished"))

    assert service.speak("hello") is True
    assert callback_thread is not None
    callback_thread.join(timeout=1)
    assert events == ["started", "finished"]


def test_tts_service_speak_failure_hides_raw_exception_prose(caplog):
    secret = "raw provider body with relay/tenant-secret and player text"

    class FailingManager:
        def speak(self, *_args, **_kwargs):
            raise RuntimeError(secret)

    service = TtsService(
        {"tts": {"engine": "qwen_tts", "qwen_tts": {"voice": "Cherry"}}}
    )
    service._manager = FailingManager()
    errors: list[str] = []
    service.error.connect(errors.append)
    caplog.set_level("WARNING", logger="src.core.tts_service")

    assert service.speak("hello") is False

    assert errors == ["tts_error:provider"]
    assert secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_tts_service_initialization_failure_hides_raw_exception_prose(
    monkeypatch,
    caplog,
):
    from src.tts import manager as manager_module

    secret = "raw initialization failure with relay/private-path"

    class FailingManager:
        def __init__(self, **_kwargs):
            raise RuntimeError(secret)

    monkeypatch.setattr(manager_module, "TTSManager", FailingManager)
    service = TtsService({"tts": {"engine": "qwen_tts", "qwen_tts": {}}})
    caplog.set_level("WARNING", logger="src.core.tts_service")

    assert service._ensure_manager() is False

    assert secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_tts_service_unavailable_manager_is_cleaned_once_with_safe_log(
    monkeypatch,
    caplog,
):
    from src.tts import manager as manager_module

    secret = "raw unavailable cleanup secret and player text"
    instances = []

    class UnavailableManager:
        def __init__(self, **_kwargs):
            self.stop_calls = 0
            self.stop_timeout = None
            instances.append(self)

        def is_available(self):
            return False

        def start(self):
            raise AssertionError("unavailable manager must not start")

        def stop(self, timeout_seconds=None):
            self.stop_calls += 1
            self.stop_timeout = timeout_seconds
            raise RuntimeError(secret)

    monkeypatch.setattr(manager_module, "TTSManager", UnavailableManager)
    service = TtsService({"tts": {"engine": "qwen_tts", "qwen_tts": {}}})
    errors: list[str] = []
    service.error.connect(errors.append)
    caplog.set_level("WARNING", logger="src.core.tts_service")

    assert service.speak("hello") is False

    assert len(instances) == 1
    manager = instances[0]
    assert manager.stop_calls == 1
    assert manager.stop_timeout == (
        tts_service_module._FAILED_MANAGER_CLEANUP_TIMEOUT_SECONDS
    )
    assert service._manager is None
    assert errors == ["tts_error:unavailable"]
    assert secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_tts_service_start_failure_closes_partial_manager_once_and_redacts(
    monkeypatch,
    caplog,
):
    from src.tts import manager as manager_module

    secret = "raw partial-start failure with relay/private-path"
    instances = []

    class StartFailingManager:
        def __init__(self, **_kwargs):
            self.start_calls = 0
            self.close_calls = 0
            self.close_timeout = None
            instances.append(self)

        def is_available(self):
            return True

        def start(self):
            self.start_calls += 1
            raise RuntimeError(secret)

        def close(self, timeout_seconds=None):
            self.close_calls += 1
            self.close_timeout = timeout_seconds

    monkeypatch.setattr(manager_module, "TTSManager", StartFailingManager)
    service = TtsService({"tts": {"engine": "qwen_tts", "qwen_tts": {}}})
    errors: list[str] = []
    service.error.connect(errors.append)
    caplog.set_level("WARNING", logger="src.core.tts_service")

    assert service.speak("hello") is False

    assert len(instances) == 1
    manager = instances[0]
    assert manager.start_calls == 1
    assert manager.close_calls == 1
    assert manager.close_timeout == (
        tts_service_module._FAILED_MANAGER_CLEANUP_TIMEOUT_SECONDS
    )
    assert service._manager is None
    assert errors == ["tts_error:unavailable"]
    assert secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
