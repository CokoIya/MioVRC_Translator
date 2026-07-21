from __future__ import annotations

import threading
import time

import pytest

from src.translators.base import (
    BaseTranslator,
    ProviderWallTimeoutError,
    TranslationContextStore,
    provider_background_work_in_progress,
)
from src.translators import base as translator_base
from src.translators.factory import FallbackTranslator


class _Resource:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _Translator(BaseTranslator):
    def __init__(self) -> None:
        super().__init__()
        self._client = _Resource()

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del src_lang, tgt_lang, context_source
        return text


class _FailingTranslator(_Translator):
    def __init__(self, wall_timeout_s: float) -> None:
        super().__init__()
        self._wall_timeout_s = wall_timeout_s

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del text, src_lang, tgt_lang, context_source
        raise RuntimeError("primary failed")


class _BlockingResource(_Resource):
    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self._release = release

    def close(self) -> None:
        super().close()
        self._release.set()


class _BlockingTranslator(BaseTranslator):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.released = threading.Event()
        self.finished = threading.Event()
        self._client = _BlockingResource(self.released)

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del src_lang, tgt_lang, context_source
        self.started.set()
        self.released.wait(timeout=2.0)
        self.finished.set()
        return text


class _SlowCloseResource(_Resource):
    def __init__(self, release: threading.Event, delay_s: float) -> None:
        super().__init__()
        self._release = release
        self._delay_s = delay_s
        self.close_started = threading.Event()
        self.close_finished = threading.Event()

    def close(self) -> None:
        super().close()
        self.close_started.set()
        time.sleep(self._delay_s)
        self._release.set()
        self.close_finished.set()


class _HardWallTranslator(BaseTranslator):
    def __init__(self, wall_timeout_s: float, close_delay_s: float) -> None:
        super().__init__()
        self._wall_timeout_s = wall_timeout_s
        self.started = threading.Event()
        self.released = threading.Event()
        self.finished = threading.Event()
        self._client = _SlowCloseResource(self.released, close_delay_s)

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del src_lang, tgt_lang, context_source

        def operation():
            self.started.set()
            self.released.wait(timeout=2.0)
            self.finished.set()
            return text

        return self._run_with_wall_timeout(
            operation,
            timeout_s=self._wall_timeout_s,
        )


class _ReleaseThenFailTranslator(BaseTranslator):
    def __init__(self, wall_timeout_s: float) -> None:
        super().__init__()
        self._wall_timeout_s = wall_timeout_s
        self.started = threading.Event()
        self.released = threading.Event()
        self.finished = threading.Event()
        self._client = _BlockingResource(self.released)

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del text, src_lang, tgt_lang, context_source
        self.started.set()
        self.released.wait(timeout=2.0)
        self.finished.set()
        raise RuntimeError("primary released after cancellation")


class _MetricFailingTranslator(_Translator):
    def __init__(self, provider_s: float, message: str) -> None:
        super().__init__()
        self._provider_s = provider_s
        self._message = message

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del text, src_lang, tgt_lang, context_source
        self._reset_translation_metrics()
        self._record_translation_metrics(
            provider_s=self._provider_s,
            full_response_s=self._provider_s,
            total_s=self._provider_s,
        )
        raise RuntimeError(self._message)


def test_base_translator_closes_owned_client_once():
    translator = _Translator()
    resource = translator._client

    translator.close()
    translator.close()

    assert resource.close_calls == 1


def test_cancel_pending_requests_is_idempotent_destructive_close():
    translator = _Translator()
    resource = translator._client

    translator.cancel_pending_requests()
    translator.cancel_pending_requests()

    assert resource.close_calls == 1


def test_provider_background_registry_retains_thread_pending_start():
    pending = threading.Thread(target=lambda: None, daemon=True)
    with translator_base._PROVIDER_BACKGROUND_LOCK:
        translator_base._PROVIDER_BACKGROUND_THREADS.add(pending)
    try:
        assert pending.ident is None
        assert provider_background_work_in_progress() is True
    finally:
        with translator_base._PROVIDER_BACKGROUND_LOCK:
            translator_base._PROVIDER_BACKGROUND_THREADS.discard(pending)


def test_cleanup_thread_start_failure_uses_supervised_emergency_worker(monkeypatch):
    translator = _Translator()
    original_start = threading.Thread.start

    def fail_cleanup_start(thread):
        if thread.name.startswith("mio-provider-cancel-"):
            raise RuntimeError("thread start failed")
        return original_start(thread)

    emergency_calls: list[tuple[object, tuple]] = []

    def run_emergency(target, args):
        emergency_calls.append((target, args))
        target(*args)
        return 1

    monkeypatch.setattr(threading.Thread, "start", fail_cleanup_start)
    monkeypatch.setattr(translator_base._thread, "start_new_thread", run_emergency)

    translator._retire_pending_requests()
    translator._cancel_pending_requests_async(sequence=7)

    assert len(emergency_calls) == 1
    assert translator._client.close_calls == 1
    assert translator._active_wall_call_count() == 0
    assert provider_background_work_in_progress() is False


def test_cleanup_thread_total_start_failure_retries_without_blocking_caller(
    monkeypatch,
):
    translator = _Translator()

    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("thread start failed")),
    )
    monkeypatch.setattr(
        translator_base._thread,
        "start_new_thread",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("low-level start failed")),
    )

    translator._retire_pending_requests()
    translator._cancel_pending_requests_async(sequence=8)

    assert translator._client.close_calls == 0
    assert translator._active_wall_call_count() == 1
    assert provider_background_work_in_progress() is True

    def run_emergency(target, args):
        target(*args)
        return 1

    monkeypatch.setattr(translator_base._thread, "start_new_thread", run_emergency)

    assert provider_background_work_in_progress() is False
    assert translator._client.close_calls == 1
    assert translator._active_wall_call_count() == 0


def test_fallback_translator_closes_primary_and_created_fallbacks_once():
    primary = _Translator()
    fallback = _Translator()
    translator = FallbackTranslator(primary, [])
    translator._fallbacks["secondary"] = fallback

    translator.close()
    translator.close()

    assert primary._client.close_calls == 1
    assert fallback._client.close_calls == 1


def test_fallback_cancel_pending_requests_closes_all_initialized_clients_once():
    primary = _Translator()
    fallback = _Translator()
    translator = FallbackTranslator(primary, [])
    translator._fallbacks["secondary"] = fallback

    translator.cancel_pending_requests()
    translator.cancel_pending_requests()

    assert primary._client.close_calls == 1
    assert fallback._client.close_calls == 1


def test_closed_fallback_without_wall_timeout_rejects_reuse():
    translator = FallbackTranslator(_Translator(), [])
    translator.close()

    with pytest.raises(RuntimeError, match="client is closed"):
        translator.translate("hello", "en", "ja")


def test_fallback_chain_uses_primary_wall_budget_and_releases_blocked_fallback():
    wall_timeout_s = 0.2
    primary = _FailingTranslator(wall_timeout_s)
    fallback = _BlockingTranslator()
    translator = FallbackTranslator(
        primary,
        [("secondary", lambda: fallback)],
    )

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.05
    assert fallback.started.is_set()
    assert fallback.finished.wait(timeout=0.5)
    assert fallback._client.close_calls == 1
    assert translator._active_wall_call_count() == 0


def test_hard_wall_timeout_does_not_wait_for_blocking_client_close():
    wall_timeout_s = 0.1
    translator = _HardWallTranslator(
        wall_timeout_s=wall_timeout_s,
        close_delay_s=0.3,
    )

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.1
    assert translator._client.close_started.wait(timeout=0.5)
    assert translator._resources_closed is True
    assert provider_background_work_in_progress() is True
    with pytest.raises(RuntimeError, match="client is closed"):
        translator.translate("again", "en", "ja")
    assert translator._client.close_finished.wait(timeout=1.0)
    assert translator.finished.wait(timeout=0.5)
    deadline = time.monotonic() + 0.5
    while translator._active_wall_call_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert translator._active_wall_call_count() == 0
    assert provider_background_work_in_progress() is False


def test_cancelled_fallback_chain_does_not_create_untracked_late_client():
    primary = _ReleaseThenFailTranslator(wall_timeout_s=0.1)
    created: list[_Translator] = []

    def create_fallback() -> _Translator:
        fallback = _Translator()
        created.append(fallback)
        return fallback

    translator = FallbackTranslator(
        primary,
        [("secondary", create_fallback)],
    )

    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")

    assert primary.finished.wait(timeout=0.5)
    assert created == []
    assert translator._fallbacks == {}


def test_fallback_metrics_include_every_failed_provider_attempt():
    primary = _MetricFailingTranslator(0.1, "primary failed")
    fallback = _MetricFailingTranslator(0.2, "fallback failed")
    translator = FallbackTranslator(
        primary,
        [("secondary", lambda: fallback)],
    )

    with pytest.raises(RuntimeError, match="primary failed"):
        translator.translate("hello", "en", "ja")

    metrics = translator.translation_metrics()
    assert metrics["provider_calls"] == 2
    assert metrics["provider_s"] == pytest.approx(0.3)
    assert metrics["full_response_s"] == pytest.approx(0.3)
    assert metrics["total_s"] == pytest.approx(0.3)


def test_translation_context_store_bounds_distinct_session_keys():
    store = TranslationContextStore(max_context_keys=3)

    for session_id in range(8):
        store.remember(
            session_id=session_id,
            text=f"source-{session_id}",
            translated=f"target-{session_id}",
            src_lang="en",
            tgt_lang="ja",
            context_source="mic",
        )

    retained_keys = set(store._recent) | set(store._pending_sources)
    assert len(retained_keys) == 3
    assert {key[0] for key in retained_keys} == {5, 6, 7}


def test_base_translator_close_clears_owned_context_history():
    translator = _Translator()
    translator._context_store.remember(
        session_id="session",
        text="hello",
        translated="こんにちは",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
    )
    assert translator._context_store._recent

    translator.close()

    assert translator._context_store._recent == {}
    assert translator._context_store._pending_sources == {}
