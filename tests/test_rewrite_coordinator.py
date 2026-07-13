from __future__ import annotations

import threading

import pytest

from src.core.rewrite_coordinator import (
    REWRITE_PRIORITY_REALTIME,
    REWRITE_PRIORITY_TYPED,
    RewriteCallCoordinator,
    RewriteCoordinatorClosedError,
    RewriteCoordinatorFullError,
    RewriteCoordinatorTimeoutError,
    provider_runtime_config_signature,
    runtime_translation_config_signature,
)


def _config(api_key: str = "secret-one") -> dict:
    return {
        "translation": {
            "backend": "openai",
            "openai": {
                "api_key": api_key,
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-test",
            },
        }
    }


def test_provider_runtime_signature_ignores_request_scoped_rewrite_settings():
    config = _config()
    baseline = provider_runtime_config_signature(config)

    config["translation"].update(
        {
            "asr_rewrite_style": "catgirl",
            "rewrite_typed_text": True,
            "output_format": "translated_with_original",
            "target_language": "ja",
            "social": {"mode": "roleplay", "persona_name": "legacy"},
        }
    )

    assert provider_runtime_config_signature(config) == baseline
    config["translation"]["openai"]["model"] = "gpt-other"
    assert provider_runtime_config_signature(config) != baseline


def _execute(
    coordinator: RewriteCallCoordinator,
    text: str,
    operation,
    *,
    priority: str = REWRITE_PRIORITY_TYPED,
    config: dict | None = None,
    timeout: float = 2.0,
):
    return coordinator.execute(
        priority=priority,
        config=config or _config(),
        style="catgirl",
        language_hint="en",
        text=text,
        operation=operation,
        wait_timeout_s=timeout,
    )


def _start_call(
    coordinator: RewriteCallCoordinator,
    text: str,
    operation,
    *,
    priority: str = REWRITE_PRIORITY_TYPED,
):
    results = []
    errors = []

    def run():
        try:
            results.append(
                _execute(
                    coordinator,
                    text,
                    operation,
                    priority=priority,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, errors


def _wait_for_followers(
    coordinator: RewriteCallCoordinator,
    expected: int = 1,
) -> None:
    for _ in range(100):
        with coordinator._condition:
            flights = tuple(coordinator._flights.values())
            if flights and flights[0].followers >= expected:
                return
        threading.Event().wait(0.005)
    raise AssertionError("Follower did not join the in-flight rewrite")


def test_runtime_signature_changes_with_translation_config_without_retaining_secret():
    first = runtime_translation_config_signature(_config("credential-alpha"))
    second = runtime_translation_config_signature(_config("credential-beta"))

    assert first != second
    assert len(first) == 64
    assert "credential-alpha" not in first
    assert "credential-beta" not in second


def test_result_cache_uses_digest_keys_and_lru_eviction():
    coordinator = RewriteCallCoordinator(cache_size=2)
    calls: list[str] = []

    def operation(value):
        return lambda: calls.append(value) or f"styled:{value}"

    assert _execute(coordinator, "raw-a", operation("a")) == "styled:a"
    assert _execute(coordinator, "raw-b", operation("b")) == "styled:b"
    assert _execute(coordinator, "raw-a", operation("unused")) == "styled:a"
    assert _execute(coordinator, "raw-c", operation("c")) == "styled:c"
    assert _execute(coordinator, "raw-b", operation("b-again")) == "styled:b-again"

    assert calls == ["a", "b", "c", "b-again"]
    cache_keys = tuple(coordinator._cache.keys())
    assert "secret-one" not in repr(cache_keys)
    assert "raw-a" not in repr(cache_keys)
    assert "raw-b" not in repr(cache_keys)


def test_cache_isolated_by_runtime_translation_config_signature():
    coordinator = RewriteCallCoordinator(cache_size=8)
    calls = []

    first = _execute(
        coordinator,
        "same",
        lambda: calls.append("first") or "first-result",
        config=_config("credential-one"),
    )
    second = _execute(
        coordinator,
        "same",
        lambda: calls.append("second") or "second-result",
        config=_config("credential-two"),
    )

    assert first == "first-result"
    assert second == "second-result"
    assert calls == ["first", "second"]


def test_inflight_singleflight_runs_only_one_provider_operation():
    coordinator = RewriteCallCoordinator(max_active_calls=2)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def leader_operation():
        calls.append("leader")
        started.set()
        release.wait(timeout=2)
        return "shared-result"

    leader, leader_results, leader_errors = _start_call(
        coordinator,
        "same text",
        leader_operation,
        priority=REWRITE_PRIORITY_REALTIME,
    )
    assert started.wait(timeout=1)
    follower, follower_results, follower_errors = _start_call(
        coordinator,
        "same text",
        lambda: pytest.fail("follower operation must not run"),
    )
    _wait_for_followers(coordinator)
    try:
        assert coordinator.snapshot().inflight == 1
    finally:
        release.set()
    leader.join(timeout=1)
    follower.join(timeout=1)

    assert not leader.is_alive()
    assert not follower.is_alive()
    assert calls == ["leader"]
    assert leader_errors == []
    assert follower_errors == []
    assert leader_results == ["shared-result"]
    assert follower_results == ["shared-result"]


def test_active_and_pending_bounds_reject_new_leader_when_full():
    coordinator = RewriteCallCoordinator(
        max_active_calls=1,
        max_pending_leaders=1,
    )
    active_started = threading.Event()
    release = threading.Event()

    def active_operation():
        active_started.set()
        release.wait(timeout=2)
        return "active"

    active, _, active_errors = _start_call(
        coordinator,
        "active",
        active_operation,
        priority=REWRITE_PRIORITY_REALTIME,
    )
    assert active_started.wait(timeout=1)
    pending, pending_results, pending_errors = _start_call(
        coordinator,
        "pending",
        lambda: "pending",
    )
    try:
        for _ in range(100):
            if coordinator.snapshot().pending_typed == 1:
                break
            threading.Event().wait(0.005)
        assert coordinator.snapshot().pending_typed == 1
        with pytest.raises(RewriteCoordinatorFullError):
            _execute(coordinator, "overflow", lambda: "overflow")
    finally:
        release.set()
    active.join(timeout=1)
    pending.join(timeout=1)

    assert active_errors == []
    assert pending_errors == []
    assert pending_results == ["pending"]


def test_pending_leader_timeout_releases_queue_capacity():
    coordinator = RewriteCallCoordinator(
        max_active_calls=1,
        max_pending_leaders=1,
    )
    started = threading.Event()
    release = threading.Event()

    def active_operation():
        started.set()
        release.wait(timeout=2)
        return "active"

    active, _, errors = _start_call(coordinator, "active", active_operation)
    assert started.wait(timeout=1)
    try:
        with pytest.raises(RewriteCoordinatorTimeoutError):
            _execute(
                coordinator,
                "timeout",
                lambda: pytest.fail("timed-out operation must not run"),
                timeout=0.02,
            )
        assert coordinator.snapshot().pending_typed == 0
    finally:
        release.set()
    active.join(timeout=1)
    assert errors == []


def test_follower_timeout_does_not_cancel_leader_and_result_is_cached():
    coordinator = RewriteCallCoordinator()
    started = threading.Event()
    release = threading.Event()

    def leader_operation():
        started.set()
        release.wait(timeout=2)
        return "leader-result"

    leader, leader_results, leader_errors = _start_call(
        coordinator,
        "same",
        leader_operation,
    )
    assert started.wait(timeout=1)
    try:
        with pytest.raises(RewriteCoordinatorTimeoutError):
            _execute(
                coordinator,
                "same",
                lambda: pytest.fail("follower operation must not run"),
                timeout=0.02,
            )
        assert coordinator.snapshot().active_calls == 1
    finally:
        release.set()
    leader.join(timeout=1)

    assert leader_errors == []
    assert leader_results == ["leader-result"]
    assert _execute(
        coordinator,
        "same",
        lambda: pytest.fail("cached operation must not run"),
    ) == "leader-result"


def test_provider_failure_wakes_followers_and_is_not_cached():
    coordinator = RewriteCallCoordinator()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def failing_operation():
        calls.append("failed")
        started.set()
        release.wait(timeout=2)
        raise ValueError("provider failed")

    leader, _, leader_errors = _start_call(
        coordinator,
        "same",
        failing_operation,
    )
    assert started.wait(timeout=1)
    follower, _, follower_errors = _start_call(
        coordinator,
        "same",
        lambda: pytest.fail("follower operation must not run"),
    )
    _wait_for_followers(coordinator)
    release.set()
    leader.join(timeout=1)
    follower.join(timeout=1)

    assert [str(error) for error in leader_errors] == ["provider failed"]
    assert [str(error) for error in follower_errors] == ["provider failed"]
    assert _execute(
        coordinator,
        "same",
        lambda: calls.append("retry") or "recovered",
    ) == "recovered"
    assert calls == ["failed", "retry"]


def test_realtime_priority_has_burst_fairness_for_typed_leader():
    coordinator = RewriteCallCoordinator(
        max_active_calls=1,
        max_pending_leaders=8,
        realtime_burst=2,
        cache_size=0,
    )
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    order = []

    def blocker_operation():
        blocker_started.set()
        release_blocker.wait(timeout=2)
        return "blocker"

    blocker, _, blocker_errors = _start_call(
        coordinator,
        "blocker",
        blocker_operation,
        priority=REWRITE_PRIORITY_REALTIME,
    )
    assert blocker_started.wait(timeout=1)

    queued = []
    for name, priority in (
        ("typed", REWRITE_PRIORITY_TYPED),
        ("realtime-1", REWRITE_PRIORITY_REALTIME),
        ("realtime-2", REWRITE_PRIORITY_REALTIME),
        ("realtime-3", REWRITE_PRIORITY_REALTIME),
    ):
        thread, _results, errors = _start_call(
            coordinator,
            name,
            lambda value=name: order.append(value) or value,
            priority=priority,
        )
        queued.append((thread, errors))
        for _ in range(100):
            snapshot = coordinator.snapshot()
            total = snapshot.pending_typed + snapshot.pending_realtime
            if total >= len(queued):
                break
            threading.Event().wait(0.005)

    release_blocker.set()
    blocker.join(timeout=1)
    for thread, _errors in queued:
        thread.join(timeout=1)

    assert blocker_errors == []
    assert all(errors == [] for _thread, errors in queued)
    assert order == ["realtime-1", "realtime-2", "typed", "realtime-3"]


def test_close_wakes_pending_and_active_callers():
    coordinator = RewriteCallCoordinator(max_active_calls=1)
    started = threading.Event()
    release = threading.Event()

    def active_operation():
        started.set()
        release.wait(timeout=2)
        return "late"

    active, _, active_errors = _start_call(
        coordinator,
        "active",
        active_operation,
    )
    assert started.wait(timeout=1)
    pending, _, pending_errors = _start_call(
        coordinator,
        "pending",
        lambda: "never",
    )
    for _ in range(100):
        if coordinator.snapshot().pending_typed == 1:
            break
        threading.Event().wait(0.005)

    coordinator.close()
    pending.join(timeout=1)
    release.set()
    active.join(timeout=1)

    assert isinstance(active_errors[0], RewriteCoordinatorClosedError)
    assert isinstance(pending_errors[0], RewriteCoordinatorClosedError)
    with pytest.raises(RewriteCoordinatorClosedError):
        _execute(coordinator, "new", lambda: "never")
