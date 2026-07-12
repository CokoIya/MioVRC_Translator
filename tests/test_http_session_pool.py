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
