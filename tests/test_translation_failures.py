import threading

from src.ui.main_window import DESKTOP_SOURCE, MainWindow
from src.utils.translation_error_formatter import (
    FriendlyTranslationError,
    format_translation_error,
)


def _window_for_backoff():
    window = object.__new__(MainWindow)
    window._translation_state_lock = threading.Lock()
    window._active_translation_jobs = 0
    window._translation_failure_streak = 0
    window._translation_cooldown_until = 0.0
    window._translation_cooldown_category = None
    return window


def _friendly(category: str) -> FriendlyTranslationError:
    return FriendlyTranslationError(
        short_message="short",
        inline_message="inline",
        detailed_message="detail",
        category=category,
        detail="Connection error.",
    )


def test_engine_overloaded_429_is_treated_as_quota_error():
    friendly = format_translation_error(
        "Error code: 429 - {'error': {'message': 'The engine is currently overloaded', "
        "'type': 'engine_overloaded_error'}}",
        backend="kimi",
        ui_language="zh-CN",
    )

    assert friendly.category == "quota"


def test_translation_failure_backoff_cools_down_and_resets(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.ui.main_window.time.monotonic", lambda: now[0])
    window = _window_for_backoff()

    first = window._record_translation_failure(_friendly("network"))
    assert first == 12.0
    assert window._translation_cooldown_active("mic") is True

    now[0] += first + 0.1
    assert window._translation_cooldown_active("mic") is False

    second = window._record_translation_failure(_friendly("network"))
    assert second == 24.0

    window._record_translation_success()
    assert window._translation_cooldown_remaining() == 0.0
    assert window._translation_failure_streak == 0


def test_translation_cooldown_only_skips_segments_that_need_api():
    window = _window_for_backoff()
    window._current_tgt_lang = "ja"
    window._listen_translation_source_language = lambda selected: selected or "auto"
    window._listen_target_language = lambda: "zh"

    assert window._final_segment_may_need_translation_api(
        "mic",
        "ja",
        "translated_only",
    ) is False
    assert window._final_segment_may_need_translation_api(
        "mic",
        "auto",
        "translated_only",
    ) is True
    assert window._final_segment_may_need_translation_api(
        DESKTOP_SOURCE,
        "zh",
        "translated_only",
    ) is False
