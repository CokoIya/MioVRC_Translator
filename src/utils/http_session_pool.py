# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors

"""Small thread-local ``requests.Session`` lifecycle helper.

``requests.Session`` keeps TCP/TLS connections warm, but the mutable Session
object is not a safe unit to share between independent worker threads.  This
pool gives every long-lived worker its own reusable session and still lets an
engine close all remaining sessions deterministically during shutdown.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class ThreadLocalSessionPool:
    """Own one lazily-created HTTP session per calling thread."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        if not callable(factory):
            raise TypeError("Session factory must be callable")
        self._factory = factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: dict[int, Any] = {}
        self._closed = False

    def get(self) -> Any:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session

        with self._lock:
            if self._closed:
                raise RuntimeError("HTTP session pool is closed")

        session = self._factory()
        try:
            with self._lock:
                if self._closed:
                    raise RuntimeError("HTTP session pool is closed")
                self._sessions[id(session)] = session
                self._local.session = session
                return session
        except Exception:
            self._close_session(session)
            raise

    def close_current(self) -> None:
        session = getattr(self._local, "session", None)
        if session is None:
            return
        try:
            del self._local.session
        except AttributeError:
            pass
        with self._lock:
            self._sessions.pop(id(session), None)
        self._close_session(session)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        try:
            del self._local.session
        except AttributeError:
            pass
        for session in sessions:
            self._close_session(session)

    @staticmethod
    def _close_session(session: Any) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup must not hide the original request/shutdown outcome.
                pass
