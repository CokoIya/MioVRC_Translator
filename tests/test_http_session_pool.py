from __future__ import annotations

import threading

import pytest

from src.utils.http_session_pool import ThreadLocalSessionPool


class _Session:
    def __init__(self, identity: int) -> None:
        self.identity = identity
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_thread_local_session_pool_reuses_per_worker_and_isolates_workers():
    created: list[_Session] = []
    create_lock = threading.Lock()

    def factory() -> _Session:
        with create_lock:
            session = _Session(len(created))
            created.append(session)
            return session

    pool = ThreadLocalSessionPool(factory)
    observed: list[tuple[_Session, _Session]] = []
    observed_lock = threading.Lock()

    def worker() -> None:
        first = pool.get()
        second = pool.get()
        with observed_lock:
            observed.append((first, second))
        pool.close_current()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(observed) == 2
    assert all(first is second for first, second in observed)
    assert observed[0][0] is not observed[1][0]
    assert all(session.closed for session in created)


def test_thread_local_session_pool_close_is_idempotent_and_blocks_reopen():
    created: list[_Session] = []
    pool = ThreadLocalSessionPool(
        lambda: created.append(_Session(len(created))) or created[-1]
    )

    assert pool.get() is pool.get()
    pool.close()
    pool.close()

    assert created[0].closed is True
    with pytest.raises(RuntimeError, match="closed"):
        pool.get()


def test_thread_local_session_pool_reaps_sessions_from_finished_workers():
    created: list[_Session] = []
    pool = ThreadLocalSessionPool(
        lambda: created.append(_Session(len(created))) or created[-1]
    )

    worker = threading.Thread(target=pool.get)
    worker.start()
    worker.join(timeout=2)

    assert len(created) == 1
    assert created[0].closed is False

    # A later caller prunes sessions whose owning worker has exited.
    pool.get()

    assert created[0].closed is True
    assert len(created) == 2
    pool.close()
    assert created[1].closed is True


def test_get_does_not_return_existing_session_closed_during_stale_cleanup():
    stale_close_started = threading.Event()
    release_stale_close = threading.Event()
    owner_ready = threading.Event()
    retry_owner_get = threading.Event()
    created: list[_Session] = []

    class Session(_Session):
        def __init__(self, identity: int) -> None:
            super().__init__(identity)
            self.block_on_close = False

        def close(self) -> None:
            if self.block_on_close:
                stale_close_started.set()
                release_stale_close.wait(timeout=3)
            super().close()

    pool = ThreadLocalSessionPool(
        lambda: created.append(Session(len(created))) or created[-1]
    )
    owner_result = []
    owner_error = []

    def owner() -> None:
        pool.get()
        owner_ready.set()
        retry_owner_get.wait(timeout=3)
        try:
            owner_result.append(pool.get())
        except Exception as exc:
            owner_error.append(exc)

    owner_thread = threading.Thread(target=owner)
    owner_thread.start()
    assert owner_ready.wait(timeout=1)

    stale_thread = threading.Thread(target=pool.get)
    stale_thread.start()
    stale_thread.join(timeout=1)
    assert not stale_thread.is_alive()
    assert len(created) == 2
    created[1].block_on_close = True

    retry_owner_get.set()
    assert stale_close_started.wait(timeout=1)
    pool.close()
    release_stale_close.set()
    owner_thread.join(timeout=1)

    assert not owner_thread.is_alive()
    assert owner_result == []
    assert len(owner_error) == 1
    assert isinstance(owner_error[0], RuntimeError)
    assert "closed" in str(owner_error[0])
    assert created[0].closed is True
    assert created[1].closed is True
