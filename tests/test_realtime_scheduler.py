from __future__ import annotations

import gc
import queue
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from src.core.realtime_scheduler import (
    AdmissionStatus,
    RealtimeASRQueueExpiredError,
    RealtimeTask,
    RealtimeScheduler,
    SchedulerHealth,
)


MIC = "mic"
DESKTOP = "desktop"


def _scheduler(
    *,
    asr,
    rewrite=None,
    rewrite_required=None,
    translate,
    deliver,
    rewrite_state_factory=None,
    rewrite_state_finalizer=None,
    state_factory=None,
    state_finalizer=None,
    asr_concurrency=2,
    rewrite_concurrency=2,
    translation_concurrency=2,
    ingress_limits=None,
    outstanding_limits=None,
    rewrite_queue_size=8,
    translation_queue_size=8,
    priority_burst=3,
    stale_task_age_s=30.0,
    max_asr_queue_age_s=None,
    health_check_interval_s=1.0,
    clock=time.monotonic,
):
    scheduler = RealtimeScheduler(
        sources=(MIC, DESKTOP),
        asr_handler=asr,
        rewrite_handler=rewrite,
        rewrite_required=rewrite_required,
        translation_handler=translate,
        delivery_handler=deliver,
        rewrite_state_factory=rewrite_state_factory,
        rewrite_state_finalizer=rewrite_state_finalizer,
        translation_state_factory=state_factory,
        translation_state_finalizer=state_finalizer,
        ingress_limits=ingress_limits or {MIC: 8, DESKTOP: 8},
        outstanding_limits=outstanding_limits,
        rewrite_queue_size=rewrite_queue_size,
        translation_queue_size=translation_queue_size,
        asr_concurrency=asr_concurrency,
        rewrite_concurrency=rewrite_concurrency,
        translation_concurrency=translation_concurrency,
        priority_source=MIC,
        priority_burst=priority_burst,
        thread_name_prefix="test-realtime",
        stale_task_age_s=stale_task_age_s,
        max_asr_queue_age_s=max_asr_queue_age_s,
        health_check_interval_s=health_check_interval_s,
        clock=clock,
    )
    scheduler.start()
    return scheduler


def _submit(scheduler, value, *, source=MIC, provider="provider"):
    return scheduler.submit(
        source=source,
        session_id=11,
        provider_key=provider,
        payload=value,
    )


def _wait_for(predicate, *, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_stop_cannot_leave_rewrite_work_enqueued_after_queue_drain():
    put_started = threading.Event()
    release_put = threading.Event()
    stop_finished = threading.Event()

    class BlockingQueue(queue.Queue):
        def put(self, item, block=True, timeout=None):
            put_started.set()
            release_put.wait(timeout=3)
            return super().put(item, block=block, timeout=timeout)

    scheduler = RealtimeScheduler(
        sources=(MIC,),
        asr_handler=lambda task, cancel: task.payload,
        rewrite_handler=lambda task, text, state, cancel: text,
        translation_handler=lambda task, text, state, cancel: text,
        delivery_handler=lambda completion: None,
    )
    stage_queue = BlockingQueue(maxsize=2)
    scheduler._rewrite_queue = stage_queue
    task = RealtimeTask(
        source=MIC,
        session_id=1,
        sequence=0,
        provider_key="provider",
        payload=object(),
        submitted_at=time.monotonic(),
    )
    work = SimpleNamespace(task=task)
    enqueue_result = []
    stop_result = []

    enqueue_thread = threading.Thread(
        target=lambda: enqueue_result.append(
            scheduler._put_stage_work(stage_queue, work, stage_name="rewrite")
        )
    )
    enqueue_thread.start()
    assert put_started.wait(timeout=1)

    def stop_scheduler():
        stop_result.append(scheduler.stop(timeout=1))
        stop_finished.set()

    stop_thread = threading.Thread(target=stop_scheduler)
    stop_thread.start()
    assert not stop_finished.wait(timeout=0.05)
    release_put.set()
    enqueue_thread.join(timeout=1)
    stop_thread.join(timeout=1)

    assert not enqueue_thread.is_alive()
    assert not stop_thread.is_alive()
    assert enqueue_result == [True]
    assert stop_result == [True]
    assert stage_queue.empty()
    assert scheduler._rewrite_pending_sequences == set()


def test_next_sentence_starts_asr_while_previous_sentence_translates():
    a_translating = threading.Event()
    release_a = threading.Event()
    b_asr_started = threading.Event()
    delivered = []

    def asr(task, _cancel):
        if task.payload == "B":
            b_asr_started.set()
        return task.payload

    def translate(_task, text, _state, _cancel):
        if text == "A":
            a_translating.set()
            release_a.wait(timeout=2)
        return f"translated:{text}"

    scheduler = _scheduler(asr=asr, translate=translate, deliver=delivered.append)
    try:
        assert _submit(scheduler, "A").accepted
        assert a_translating.wait(timeout=1)
        assert _submit(scheduler, "B").accepted
        assert b_asr_started.wait(timeout=1)
        release_a.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.result for item in delivered] == ["translated:A", "translated:B"]
    finally:
        release_a.set()
        assert scheduler.stop(timeout=2)


def test_rewrite_stage_runs_concurrently_and_translation_receives_source_order():
    first_rewrite_started = threading.Event()
    second_rewrite_finished = threading.Event()
    release_first = threading.Event()
    translated: list[tuple[int, str]] = []
    delivered = []

    def rewrite(task, text, _state, _cancel):
        if task.sequence == 0:
            first_rewrite_started.set()
            release_first.wait(timeout=2)
        else:
            second_rewrite_finished.set()
        return f"styled:{text}"

    def translate(task, text, _state, _cancel):
        translated.append((task.sequence, text))
        return f"translated:{text}"

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        rewrite=rewrite,
        translate=translate,
        deliver=delivered.append,
        rewrite_concurrency=2,
        translation_concurrency=2,
    )
    try:
        assert _submit(scheduler, "first", provider="p1").accepted
        assert _submit(scheduler, "second", provider="p2").accepted
        assert first_rewrite_started.wait(timeout=1)
        assert second_rewrite_finished.wait(timeout=1)
        time.sleep(0.05)
        assert translated == []
        assert scheduler.snapshot().rewritten_pending[MIC] == 1

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert translated == [(0, "styled:first"), (1, "styled:second")]
        assert [item.result for item in delivered] == [
            "translated:styled:first",
            "translated:styled:second",
        ]
        assert delivered[0].recognized_text == "first"
        assert delivered[0].rewritten_text == "styled:first"
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_rewrite_bypass_avoids_handler_and_busy_rewrite_queue_delay():
    rewrite_started = threading.Event()
    release_rewrite = threading.Event()
    bypass_translated = threading.Event()
    rewrite_calls: list[tuple[str, int]] = []
    delivered = []

    def rewrite(task, text, _state, _cancel):
        rewrite_calls.append((task.source, task.sequence))
        rewrite_started.set()
        release_rewrite.wait(timeout=2)
        return f"styled:{text}"

    def translate(task, text, _state, _cancel):
        if task.source == DESKTOP:
            bypass_translated.set()
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        rewrite=rewrite,
        rewrite_required=lambda task, _text: task.source == MIC,
        translate=translate,
        deliver=delivered.append,
        rewrite_concurrency=1,
        rewrite_queue_size=1,
        translation_concurrency=2,
    )
    try:
        assert _submit(scheduler, "mic", source=MIC).accepted
        assert rewrite_started.wait(timeout=1)

        assert _submit(scheduler, "desktop", source=DESKTOP).accepted
        assert bypass_translated.wait(timeout=1)
        assert rewrite_calls == [(MIC, 0)]

        release_rewrite.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert sorted(
            (item.task.source, item.result) for item in delivered
        ) == [(DESKTOP, "desktop"), (MIC, "styled:mic")]
    finally:
        release_rewrite.set()
        assert scheduler.stop(timeout=2)


def test_mixed_rewrite_and_bypass_reach_translation_in_source_order():
    first_rewrite_started = threading.Event()
    release_first_rewrite = threading.Event()
    translated: list[tuple[int, str]] = []
    rewrite_calls: list[int] = []
    delivered = []

    def rewrite(task, text, _state, _cancel):
        rewrite_calls.append(task.sequence)
        first_rewrite_started.set()
        release_first_rewrite.wait(timeout=2)
        return f"styled:{text}"

    def translate(task, text, _state, _cancel):
        translated.append((task.sequence, text))
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        rewrite=rewrite,
        rewrite_required=lambda task, _text: task.sequence == 0,
        translate=translate,
        deliver=delivered.append,
        rewrite_concurrency=1,
        translation_concurrency=2,
    )
    try:
        assert _submit(scheduler, "first", provider="p1").accepted
        assert _submit(scheduler, "second", provider="p2").accepted
        assert first_rewrite_started.wait(timeout=1)

        deadline = time.monotonic() + 1
        while scheduler.snapshot().rewritten_pending[MIC] != 1:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert translated == []
        assert rewrite_calls == [0]

        release_first_rewrite.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert translated == [(0, "styled:first"), (1, "second")]
        assert [item.task.sequence for item in delivered] == [0, 1]
        assert [item.result for item in delivered] == ["styled:first", "second"]
        assert delivered[1].rewrite_queue_wait_s == 0.0
        assert delivered[1].rewrite_duration_s == 0.0
    finally:
        release_first_rewrite.set()
        assert scheduler.stop(timeout=2)


def test_rewrite_error_fails_open_without_breaking_order():
    delivered = []

    def rewrite(task, text, _state, _cancel):
        if task.sequence == 0:
            raise RuntimeError("rewrite unavailable")
        return f"styled:{text}"

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        rewrite=rewrite,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
    )
    try:
        assert _submit(scheduler, "first", provider="p1").accepted
        assert _submit(scheduler, "second", provider="p2").accepted
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.result for item in delivered] == ["first", "styled:second"]
        assert isinstance(delivered[0].rewrite_error, RuntimeError)
        assert delivered[0].error is None
    finally:
        assert scheduler.stop(timeout=2)


def test_out_of_order_translations_are_delivered_in_source_order():
    release_first = threading.Event()
    second_finished = threading.Event()
    delivered = []

    def translate(_task, text, _state, _cancel):
        if text == "first":
            release_first.wait(timeout=2)
        else:
            second_finished.set()
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=translate,
        deliver=delivered.append,
    )
    try:
        assert _submit(scheduler, "first", provider="p1").accepted
        assert _submit(scheduler, "second", provider="p2").accepted
        assert second_finished.wait(timeout=1)
        time.sleep(0.05)
        assert delivered == []
        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.result for item in delivered] == ["first", "second"]
        assert [item.task.sequence for item in delivered] == [0, 1]
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_out_of_order_asr_results_enter_translation_in_source_order():
    first_asr_started = threading.Event()
    second_asr_finished = threading.Event()
    release_first_asr = threading.Event()
    translated_sequences: list[int] = []

    def asr(task, _cancel):
        if task.sequence == 0:
            first_asr_started.set()
            release_first_asr.wait(timeout=2)
        else:
            second_asr_finished.set()
        return task.payload

    def translate(task, text, _state, _cancel):
        translated_sequences.append(task.sequence)
        return text

    scheduler = _scheduler(
        asr=asr,
        translate=translate,
        deliver=lambda _completion: None,
        asr_concurrency=2,
        translation_concurrency=1,
    )
    try:
        for value in ("first", "second"):
            assert scheduler.submit(
                source=MIC,
                session_id=11,
                provider_key="parallel-cloud",
                provider_concurrency=2,
                payload=value,
            ).accepted

        assert first_asr_started.wait(timeout=1)
        assert second_asr_finished.wait(timeout=1)
        time.sleep(0.05)
        snapshot = scheduler.snapshot()
        assert translated_sequences == []
        assert snapshot.recognized_pending[MIC] == 1

        release_first_asr.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert translated_sequences == [0, 1]
    finally:
        release_first_asr.set()
        assert scheduler.stop(timeout=2)


def test_overflow_rejects_without_evicting_an_accepted_final_sentence():
    first_started = threading.Event()
    release = threading.Event()
    delivered = []

    def asr(task, _cancel):
        if task.payload == "first":
            first_started.set()
            release.wait(timeout=2)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
        asr_concurrency=1,
        ingress_limits={MIC: 1, DESKTOP: 1},
    )
    try:
        first = _submit(scheduler, "first")
        assert first.accepted
        assert first_started.wait(timeout=1)
        second = _submit(scheduler, "second")
        rejected = _submit(scheduler, "third")
        assert second.accepted
        assert rejected.status is AdmissionStatus.FULL
        assert first.task.sequence == 0
        assert second.task.sequence == 1
        assert rejected.task is None

        release.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.result for item in delivered] == ["first", "second"]
        assert scheduler.snapshot().accepted == 2
        assert scheduler.snapshot().rejected_full == 1
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_stale_asr_reaper_bounds_markers_until_ordered_head_finishes(caplog):
    first_started = threading.Event()
    release_first = threading.Event()
    asr_payloads: list[str] = []
    delivered = []

    def asr(task, _cancel):
        asr_payloads.append(task.payload)
        first_started.set()
        release_first.wait(timeout=3)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
        asr_concurrency=1,
        ingress_limits={MIC: 1, DESKTOP: 1},
        outstanding_limits={MIC: 2, DESKTOP: 2},
        max_asr_queue_age_s=0.05,
        health_check_interval_s=0.01,
    )
    try:
        assert _submit(scheduler, "running", provider="shared").accepted
        assert first_started.wait(timeout=1)

        with caplog.at_level("WARNING", logger="src.core.realtime_scheduler"):
            stale = _submit(scheduler, "stale", provider="shared")
            assert stale.accepted
            assert _submit(
                scheduler,
                "overflow",
                provider="shared",
            ).status is AdmissionStatus.FULL
            assert _wait_for(
                lambda: scheduler.snapshot().stale_asr_dropped == 1,
                timeout=1,
            )
            for index in range(20):
                assert _submit(
                    scheduler,
                    f"still-bounded-{index}",
                    provider="shared",
                ).status is AdmissionStatus.FULL

            snapshot = scheduler.snapshot()
            assert snapshot.ingress_pending[MIC] == 0
            assert snapshot.outstanding[MIC] == 2
            assert snapshot.provider_claimed == {"shared": 1}
            assert len(scheduler._completed[MIC]) == 1
            marker = scheduler._completed[MIC][stale.task.sequence]
            assert marker.task.payload is None
            assert marker.cancelled is False
            assert isinstance(marker.asr_error, RealtimeASRQueueExpiredError)

        stale_messages = [
            record.getMessage()
            for record in caplog.records
            if "Realtime ASR stale queue drop" in record.getMessage()
        ]
        assert len(stale_messages) == 1
        assert all("source=mic" in message for message in stale_messages)
        assert all("claimed=False" in message for message in stale_messages)
        assert all("queue_age_ms=" in message for message in stale_messages)
        assert all("provider_claimed=1" in message for message in stale_messages)

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        recovered = _submit(scheduler, "recovered", provider="shared")
        assert recovered.accepted
        assert scheduler.wait_until_idle(timeout=2)
        snapshot = scheduler.snapshot()
        assert asr_payloads == ["running", "recovered"]
        assert [item.task.sequence for item in delivered] == [0, 1, 2]
        assert isinstance(delivered[1].asr_error, RealtimeASRQueueExpiredError)
        assert snapshot.stale_asr_dropped == 1
        assert snapshot.stale_asr_dropped_by_source == {MIC: 1, DESKTOP: 0}
        assert snapshot.outstanding[MIC] == 0
        assert snapshot.provider_claimed == {}
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_stale_asr_marker_preserves_strict_delivery_order():
    now = [100.0]
    first_started = threading.Event()
    third_finished_asr = threading.Event()
    release_first = threading.Event()
    asr_sequences: list[int] = []
    delivered: list[tuple[int, str]] = []

    def asr(task, _cancel):
        asr_sequences.append(task.sequence)
        if task.sequence == 0:
            first_started.set()
            release_first.wait(timeout=3)
        elif task.sequence == 2:
            third_finished_asr.set()
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda completion: delivered.append(
            (completion.task.sequence, completion.result)
        ),
        asr_concurrency=2,
        translation_concurrency=2,
        max_asr_queue_age_s=1.0,
        health_check_interval_s=60.0,
        clock=lambda: now[0],
    )
    try:
        assert _submit(scheduler, "first", provider="shared").accepted
        assert first_started.wait(timeout=1)
        stale = _submit(scheduler, bytearray(b"stale-audio"), provider="shared")
        assert stale.accepted

        now[0] = 102.0
        assert _submit(scheduler, "third", provider="other").accepted
        assert third_finished_asr.wait(timeout=1)
        assert _wait_for(
            lambda: scheduler.snapshot().delivery_pending >= 1,
            timeout=1,
        )

        assert delivered == []
        assert asr_sequences == [0, 2]
        snapshot = scheduler.snapshot()
        assert snapshot.stale_asr_dropped == 1
        assert snapshot.recognized_pending[MIC] == 1
        stale_marker = scheduler._completed[MIC][stale.task.sequence]
        assert stale_marker.cancelled is False
        assert stale_marker.stale_asr is True
        assert stale_marker.task.payload is None
        assert isinstance(stale_marker.asr_error, RealtimeASRQueueExpiredError)

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert delivered == [(0, "first"), (1, None), (2, "third")]
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_claimed_task_that_turns_stale_releases_provider_claim():
    now = [10.0]
    claimed = threading.Event()
    release_claim = threading.Event()
    asr_called = threading.Event()

    scheduler = RealtimeScheduler(
        sources=(MIC,),
        asr_handler=lambda _task, _cancel: asr_called.set(),
        translation_handler=lambda _task, text, _state, _cancel: text,
        delivery_handler=lambda _completion: None,
        ingress_limits={MIC: 1},
        outstanding_limits={MIC: 1},
        asr_concurrency=1,
        translation_concurrency=1,
        max_asr_queue_age_s=1.0,
        health_check_interval_s=60.0,
        clock=lambda: now[0],
        thread_name_prefix="test-claimed-stale",
    )
    original_take = scheduler._take_asr_task

    class AudioPayload:
        pass

    payload = AudioPayload()
    payload_ref = weakref.ref(payload)

    def gated_take():
        task = original_take()
        if task is not None:
            claimed.set()
            release_claim.wait(timeout=3)
        return task

    scheduler._take_asr_task = gated_take
    scheduler.start()
    try:
        assert _submit(scheduler, payload).accepted
        del payload
        assert claimed.wait(timeout=1)
        snapshot = scheduler.snapshot()
        assert snapshot.asr_claimed == 1
        assert snapshot.provider_claimed == {"provider": 1}

        now[0] = 12.0
        release_claim.set()
        assert scheduler.wait_until_idle(timeout=2)

        snapshot = scheduler.snapshot()
        assert not asr_called.is_set()
        assert snapshot.stale_asr_dropped == 1
        assert snapshot.asr_claimed == 0
        assert snapshot.asr_running == 0
        assert snapshot.provider_claimed == {}
        assert snapshot.outstanding[MIC] == 0
        assert scheduler._asr_inflight_sequences == set()
        gc.collect()
        assert payload_ref() is None
    finally:
        release_claim.set()
        assert scheduler.stop(timeout=2)


def test_stale_marker_remains_safe_across_source_cancel_and_stop():
    now = [20.0]
    first_started = threading.Event()
    release_first = threading.Event()
    delivered = []

    def asr(task, _cancel):
        first_started.set()
        release_first.wait(timeout=3)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
        asr_concurrency=1,
        outstanding_limits={MIC: 2, DESKTOP: 2},
        max_asr_queue_age_s=1.0,
        health_check_interval_s=60.0,
        clock=lambda: now[0],
    )
    try:
        assert _submit(scheduler, "running", provider="shared").accepted
        assert first_started.wait(timeout=1)
        assert _submit(scheduler, bytearray(b"stale"), provider="shared").accepted

        now[0] = 22.0
        assert scheduler._drop_stale_asr_tasks() == 1
        assert scheduler.cancel_source(MIC) == 2
        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)

        snapshot = scheduler.snapshot()
        assert delivered == []
        assert snapshot.stale_asr_dropped == 1
        assert snapshot.outstanding[MIC] == 0
        assert snapshot.provider_claimed == {}
        assert scheduler._admitted_tasks[MIC] == {}
        assert scheduler._completed[MIC] == {}
        assert scheduler._cancelled_sequences[MIC] == set()
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)
        assert all(not thread.is_alive() for thread in scheduler.threads)


def test_same_provider_asr_concurrency_is_limited_to_one():
    release = threading.Event()
    two_claimed = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def asr(task, _cancel):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if task.payload == "two":
                two_claimed.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
    )
    try:
        assert _submit(scheduler, "one", provider="shared").accepted
        assert _submit(scheduler, "two", provider="shared").accepted
        time.sleep(0.1)
        assert maximum == 1
        assert not two_claimed.is_set()
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert maximum == 1
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_provider_declared_asr_concurrency_allows_parallel_cloud_requests():
    both_running = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def asr(task, _cancel):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_running.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=2,
    )
    try:
        for value in ("one", "two"):
            assert scheduler.submit(
                source=MIC,
                session_id=11,
                provider_key="cloud",
                provider_concurrency=2,
                payload=value,
            ).accepted
        assert both_running.wait(timeout=1)
        assert maximum == 2
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_provider_limit_changes_do_not_capture_a_worker_behind_a_gate():
    first_started = threading.Event()
    second_started = threading.Event()
    other_provider_started = threading.Event()
    release_first = threading.Event()

    def asr(task, _cancel):
        if task.payload == "first":
            first_started.set()
            release_first.wait(timeout=2)
        elif task.payload == "second":
            second_started.set()
        else:
            other_provider_started.set()
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=2,
    )
    try:
        assert scheduler.submit(
            source=MIC,
            session_id=11,
            provider_key="shared",
            provider_concurrency=1,
            payload="first",
        ).accepted
        assert first_started.wait(timeout=1)
        assert scheduler.submit(
            source=MIC,
            session_id=11,
            provider_key="shared",
            provider_concurrency=2,
            payload="second",
        ).accepted
        assert scheduler.submit(
            source=DESKTOP,
            session_id=11,
            provider_key="other",
            provider_concurrency=1,
            payload="other",
        ).accepted

        assert other_provider_started.wait(timeout=1)
        assert not second_started.is_set()
        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert second_started.is_set()
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)

def test_same_provider_tasks_stay_queued_and_start_in_sequence_order():
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    start_order = []

    def asr(task, _cancel):
        start_order.append(task.sequence)
        if task.sequence == 0:
            first_started.set()
            release_first.wait(timeout=2)
        else:
            second_started.set()
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=2,
    )
    try:
        assert _submit(scheduler, "first", provider="shared").accepted
        assert first_started.wait(timeout=1)
        assert _submit(scheduler, "second", provider="shared").accepted

        time.sleep(0.1)
        snapshot = scheduler.snapshot()
        assert snapshot.provider_claimed == {"shared": 1}
        assert snapshot.ingress_pending[MIC] == 1
        assert not second_started.is_set()

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert start_order == [0, 1]
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_busy_provider_does_not_block_an_available_provider_worker():
    first_started = threading.Event()
    blocked_provider_second_started = threading.Event()
    other_provider_started = threading.Event()
    release_first = threading.Event()

    def asr(task, _cancel):
        if task.payload == "shared-first":
            first_started.set()
            release_first.wait(timeout=2)
        elif task.payload == "shared-second":
            blocked_provider_second_started.set()
        elif task.payload == "other":
            other_provider_started.set()
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=2,
    )
    try:
        assert _submit(scheduler, "shared-first", provider="shared").accepted
        assert first_started.wait(timeout=1)
        assert _submit(scheduler, "shared-second", provider="shared").accepted
        assert _submit(
            scheduler,
            "other",
            source=DESKTOP,
            provider="other",
        ).accepted

        assert other_provider_started.wait(timeout=1)
        assert not blocked_provider_second_started.is_set()

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert blocked_provider_second_started.is_set()
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)



def test_different_asr_providers_can_run_concurrently():
    both_running = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def asr(task, _cancel):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_running.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
    )
    try:
        assert _submit(scheduler, "one", provider="p1").accepted
        assert _submit(scheduler, "two", provider="p2").accepted
        assert both_running.wait(timeout=1)
        assert maximum == 2
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_translation_workers_own_separate_state_instances():
    states_created = []
    states_used = []
    both_running = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0

    def state_factory(index):
        state = object()
        states_created.append((index, state))
        return state

    def translate(_task, text, state, _cancel):
        nonlocal active
        with lock:
            states_used.append(state)
            active += 1
            if active == 2:
                both_running.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=translate,
        deliver=lambda _completion: None,
        state_factory=state_factory,
    )
    try:
        assert _submit(scheduler, "one", provider="p1").accepted
        assert _submit(scheduler, "two", provider="p2").accepted
        assert both_running.wait(timeout=1)
        assert len(states_created) == 2
        assert len({id(state) for _, state in states_created}) == 2
        assert len({id(state) for state in states_used}) == 2
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_stop_joins_owned_threads_and_suppresses_late_delivery():
    asr_started = threading.Event()
    delivered = []

    def asr(task, cancel):
        asr_started.set()
        assert cancel.wait(timeout=2)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
    )
    assert _submit(scheduler, "late").accepted
    assert asr_started.wait(timeout=1)

    threads = scheduler.threads
    assert scheduler.stop(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert delivered == []
    assert _submit(scheduler, "after-stop").status is AdmissionStatus.STOPPED


def test_microphone_priority_has_desktop_starvation_protection():
    first_started = threading.Event()
    release_first = threading.Event()
    asr_order = []

    def asr(task, _cancel):
        asr_order.append(task.payload)
        if task.payload == "initial":
            first_started.set()
            release_first.wait(timeout=2)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=1,
        priority_burst=2,
    )
    try:
        assert _submit(scheduler, "initial").accepted
        assert first_started.wait(timeout=1)
        assert _submit(scheduler, "desktop", source=DESKTOP, provider="desktop-provider").accepted
        for index in range(5):
            assert _submit(scheduler, f"mic-{index}").accepted
        release_first.set()
        assert scheduler.wait_until_idle(timeout=3)
        tail = asr_order[1:]
        assert "desktop" in tail
        assert tail.index("desktop") <= 2
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_partial_asr_yields_to_pending_and_running_final_work():
    asr_started = threading.Event()
    release = threading.Event()

    def asr(task, _cancel):
        asr_started.set()
        release.wait(timeout=2)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=1,
    )
    try:
        assert scheduler.should_yield_partial("provider") is False
        assert _submit(scheduler, "final", provider="provider").accepted
        assert scheduler.should_yield_partial("provider") is True
        assert asr_started.wait(timeout=1)
        assert scheduler.snapshot().asr_running == 1
        assert scheduler.should_yield_partial("provider") is True
        assert scheduler.should_yield_partial("different-provider") is False
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert scheduler.should_yield_partial("provider") is False
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_snapshot_reports_oldest_task_age_and_stalled_health():
    now = [100.0]
    asr_started = threading.Event()
    release = threading.Event()

    def asr(task, _cancel):
        asr_started.set()
        release.wait(timeout=2)
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        asr_concurrency=1,
        stale_task_age_s=5.0,
        health_check_interval_s=60.0,
        clock=lambda: now[0],
    )
    try:
        assert _submit(scheduler, "slow").accepted
        assert asr_started.wait(timeout=1)
        assert scheduler.snapshot().health is SchedulerHealth.BUSY

        now[0] = 106.5
        snapshot = scheduler.snapshot()

        assert snapshot.health is SchedulerHealth.STALLED
        assert snapshot.oldest_task_age_s == pytest.approx(6.5)
        assert snapshot.oldest_by_source[MIC] == pytest.approx(6.5)
        assert snapshot.oldest_by_source[DESKTOP] == 0.0
        release.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert scheduler.snapshot().health is SchedulerHealth.HEALTHY
    finally:
        release.set()
        assert scheduler.stop(timeout=2)


def test_asr_error_and_empty_text_create_ordering_markers():
    delivered = []

    def asr(task, _cancel):
        if task.payload == "error":
            raise RuntimeError("boom")
        if task.payload == "empty":
            return ""
        return task.payload

    scheduler = _scheduler(
        asr=asr,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=delivered.append,
    )
    try:
        for value in ("error", "empty", "ok"):
            assert _submit(scheduler, value, provider=value).accepted
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.task.sequence for item in delivered] == [0, 1, 2]
        assert isinstance(delivered[0].asr_error, RuntimeError)
        assert delivered[1].empty_asr is True
        assert delivered[2].result == "ok"
    finally:
        assert scheduler.stop(timeout=2)



def test_outstanding_limit_bounds_out_of_order_completion_buffer():
    release_first = threading.Event()
    later_finished = {"second": threading.Event(), "third": threading.Event()}
    delivered = []

    def translate(_task, text, _state, _cancel):
        if text == "first":
            release_first.wait(timeout=3)
        else:
            later_finished[text].set()
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=translate,
        deliver=delivered.append,
        asr_concurrency=3,
        translation_concurrency=3,
        outstanding_limits={MIC: 3, DESKTOP: 3},
    )
    try:
        assert _submit(scheduler, "first", provider="p1").accepted
        assert _submit(scheduler, "second", provider="p2").accepted
        assert _submit(scheduler, "third", provider="p3").accepted
        assert later_finished["second"].wait(timeout=1)
        assert later_finished["third"].wait(timeout=1)

        deadline = time.monotonic() + 1
        while scheduler.snapshot().delivery_pending < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        snapshot = scheduler.snapshot()
        assert snapshot.delivery_pending == 2
        assert snapshot.outstanding[MIC] == 3
        assert _submit(scheduler, "fourth", provider="p4").status is AdmissionStatus.FULL

        release_first.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert [item.result for item in delivered] == ["first", "second", "third"]
    finally:
        release_first.set()
        assert scheduler.stop(timeout=2)


def test_delivery_round_robin_prevents_desktop_starvation():
    first_delivery_started = threading.Event()
    release_first_delivery = threading.Event()
    delivered_sources: list[str] = []

    def deliver(completion):
        delivered_sources.append(completion.task.source)
        if len(delivered_sources) == 1:
            first_delivery_started.set()
            release_first_delivery.wait(timeout=3)

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=deliver,
        asr_concurrency=3,
        translation_concurrency=3,
    )
    try:
        assert _submit(scheduler, "mic-0", source=MIC, provider="mic-0").accepted
        assert first_delivery_started.wait(timeout=1)
        assert _submit(
            scheduler,
            "desktop-0",
            source=DESKTOP,
            provider="desktop-0",
        ).accepted
        for index in range(1, 6):
            assert _submit(
                scheduler,
                f"mic-{index}",
                source=MIC,
                provider=f"mic-{index}",
            ).accepted

        deadline = time.monotonic() + 1
        while scheduler.snapshot().delivery_pending < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        release_first_delivery.set()
        assert scheduler.wait_until_idle(timeout=3)

        assert delivered_sources[0] == MIC
        assert delivered_sources[1] == DESKTOP
        assert delivered_sources.count(DESKTOP) == 1
    finally:
        release_first_delivery.set()
        assert scheduler.stop(timeout=2)


def test_stop_timeout_is_respected_when_delivery_callback_is_blocked():
    delivery_started = threading.Event()
    release_delivery = threading.Event()

    def deliver(_completion):
        delivery_started.set()
        release_delivery.wait(timeout=3)

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=deliver,
    )
    assert _submit(scheduler, "blocked-delivery").accepted
    assert delivery_started.wait(timeout=1)

    started_at = time.monotonic()
    stopped = scheduler.stop(timeout=0.1)
    elapsed = time.monotonic() - started_at

    assert stopped is False
    assert elapsed < 0.5

    release_delivery.set()
    assert scheduler.stop(timeout=2)
    assert all(not thread.is_alive() for thread in scheduler.threads)


def test_translation_worker_state_is_finalized_exactly_once_on_stop():
    states: list[object] = []
    finalized: list[object] = []

    def make_state(_index):
        state = object()
        states.append(state)
        return state

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=lambda _task, text, _state, _cancel: text,
        deliver=lambda _completion: None,
        state_factory=make_state,
        state_finalizer=finalized.append,
        translation_concurrency=3,
    )

    assert scheduler.stop(timeout=2)
    assert len(states) == 3
    assert len(finalized) == 3
    assert {id(state) for state in finalized} == {id(state) for state in states}


def test_slow_reverse_translation_keeps_a_worker_reserved_for_microphone():
    first_reverse_started = threading.Event()
    second_reverse_started = threading.Event()
    microphone_started = threading.Event()
    release_reverse = threading.Event()

    def translate(task, text, _state, _cancel):
        if task.source == DESKTOP and task.sequence == 0:
            first_reverse_started.set()
            release_reverse.wait(timeout=3)
        elif task.source == DESKTOP:
            second_reverse_started.set()
        else:
            microphone_started.set()
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=translate,
        deliver=lambda _completion: None,
        asr_concurrency=3,
        translation_concurrency=2,
        translation_queue_size=4,
    )
    try:
        assert _submit(scheduler, "reverse-0", source=DESKTOP, provider="d0").accepted
        assert _submit(scheduler, "reverse-1", source=DESKTOP, provider="d1").accepted
        assert first_reverse_started.wait(timeout=1)
        time.sleep(0.05)
        assert not second_reverse_started.is_set()

        assert _submit(scheduler, "mic", source=MIC, provider="mic").accepted
        assert microphone_started.wait(timeout=1)
        assert not second_reverse_started.is_set()

        release_reverse.set()
        assert scheduler.wait_until_idle(timeout=2)
        assert second_reverse_started.is_set()
    finally:
        release_reverse.set()
        assert scheduler.stop(timeout=2)


def test_cancel_source_drops_stale_reverse_result_without_affecting_microphone():
    reverse_started = threading.Event()
    release_reverse = threading.Event()
    delivered: list[tuple[str, int, str]] = []

    def translate(task, text, _state, _cancel):
        if task.source == DESKTOP and task.sequence == 0:
            reverse_started.set()
            release_reverse.wait(timeout=3)
        return text

    scheduler = _scheduler(
        asr=lambda task, _cancel: task.payload,
        translate=translate,
        deliver=lambda completion: delivered.append(
            (completion.task.source, completion.task.sequence, completion.result)
        ),
        asr_concurrency=3,
        translation_concurrency=2,
    )
    try:
        assert _submit(scheduler, "stale-reverse", source=DESKTOP, provider="d0").accepted
        assert reverse_started.wait(timeout=1)
        assert scheduler.cancel_source(DESKTOP) == 1

        assert _submit(scheduler, "mic-current", source=MIC, provider="mic").accepted
        assert _submit(scheduler, "reverse-current", source=DESKTOP, provider="d1").accepted
        release_reverse.set()
        assert scheduler.wait_until_idle(timeout=2)

        assert (DESKTOP, 0, "stale-reverse") not in delivered
        assert (MIC, 0, "mic-current") in delivered
        assert (DESKTOP, 1, "reverse-current") in delivered
        assert scheduler._cancelled_sequences[DESKTOP] == set()
        assert scheduler._translation_active_sequences == set()
    finally:
        release_reverse.set()
        assert scheduler.stop(timeout=2)
