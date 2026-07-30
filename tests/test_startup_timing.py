import threading

import pytest

from src.utils import startup_timing


class _CollectingLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args: object) -> None:
        self.entries.append((message, args))


def test_early_startup_events_are_buffered_then_logged_with_stage_fields():
    logger = _CollectingLogger()
    startup_timing._reset_startup_timing_for_tests(origin=10.0)

    event = startup_timing.record_startup_stage(
        "startup.config",
        started_at=10.1,
        finished_at=10.35,
    )

    assert logger.entries == []
    assert event.duration_ms == pytest.approx(250.0)
    assert event.total_ms == pytest.approx(350.0)

    startup_timing.enable_startup_timing_logging(logger)

    assert len(logger.entries) == 1
    message, args = logger.entries[0]
    assert "stage=%s" in message
    assert "duration_ms=%.1f" in message
    assert args[0] == "startup.config"
    assert args[1] == pytest.approx(250.0)
    assert args[2] == pytest.approx(350.0)
    assert args[4] == "ok"


def test_startup_stage_records_error_and_preserves_exception(monkeypatch):
    startup_timing._reset_startup_timing_for_tests(origin=20.0)
    readings = iter((20.1, 20.4))
    monkeypatch.setattr(startup_timing.time, "perf_counter", lambda: next(readings))

    with pytest.raises(RuntimeError, match="boom"):
        with startup_timing.startup_stage("startup.failure"):
            raise RuntimeError("boom")

    event = startup_timing.get_startup_timing_events()[-1]
    assert event.stage == "startup.failure"
    assert event.duration_ms == pytest.approx(300.0)
    assert event.outcome == "error"


def test_startup_timing_collection_is_thread_safe():
    startup_timing._reset_startup_timing_for_tests()
    threads = [
        threading.Thread(
            target=startup_timing.record_startup_stage,
            args=(f"worker.{index}",),
        )
        for index in range(16)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    events = startup_timing.get_startup_timing_events()
    assert {event.stage for event in events} == {
        f"worker.{index}" for index in range(16)
    }
