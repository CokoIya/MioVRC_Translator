from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from src.core.output_dispatcher import OutputMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverlayStatus:
    enabled: bool
    backend: str = "desktop"
    message: str = ""


class OverlayBackend(Protocol):
    def show_message(self, message: OutputMessage) -> bool | None: ...
    def set_listen_status(self, listening: bool) -> None: ...
    def reveal(self) -> None: ...
    def hide(self) -> None: ...


class OverlayService(QObject):
    """Routes subtitle output to the active overlay backend.

    The first backend is the existing Qt floating subtitle window. A future
    OpenVR helper can implement the same methods without changing pipelines.
    """

    status_changed = Signal(object)
    message_shown = Signal(object)
    error = Signal(str)

    def __init__(self, backend: OverlayBackend | None = None, *, backend_name: str = "desktop") -> None:
        super().__init__()
        self._backend = backend
        self._backend_name = str(backend_name or "desktop")
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def set_backend(self, backend: OverlayBackend | None, *, backend_name: str = "desktop") -> None:
        self._backend = backend
        self._backend_name = str(backend_name or "desktop")

    def set_enabled(self, enabled: bool, *, reveal: bool = True) -> None:
        enabled = bool(enabled)
        if self._enabled == enabled:
            if enabled and reveal:
                self.reveal()
            elif not enabled:
                self.hide()
            return
        self._enabled = enabled
        if enabled and reveal:
            self.reveal()
        elif not enabled:
            self.hide()
        self.status_changed.emit(OverlayStatus(enabled=self._enabled, backend=self._backend_name))

    def reveal(self) -> None:
        backend = self._backend
        if backend is None:
            return
        try:
            backend.reveal()
        except Exception as exc:
            self._emit_error(exc)

    def hide(self) -> None:
        backend = self._backend
        if backend is None:
            return
        try:
            hide_from_service = getattr(backend, "hide_from_service", None)
            if callable(hide_from_service):
                hide_from_service()
            else:
                backend.hide()
        except Exception as exc:
            self._emit_error(exc)

    def set_listen_status(self, listening: bool) -> None:
        backend = self._backend
        if backend is None:
            return
        try:
            backend.set_listen_status(bool(listening))
        except Exception as exc:
            self._emit_error(exc)

    def show_message(self, message: OutputMessage) -> bool:
        if not self._enabled:
            return False
        backend = self._backend
        if backend is None:
            return False
        try:
            shown = backend.show_message(message)
        except Exception as exc:
            self._emit_error(exc)
            return False
        if shown is False:
            return False
        self.message_shown.emit(message)
        return True

    def _emit_error(self, exc: Exception) -> None:
        logger.warning("Overlay backend failed: %s", exc, exc_info=True)
        self.error.emit(str(exc) or exc.__class__.__name__)


class CompositeOverlayBackend:
    """Fans subtitle output out to several overlays at once.

    Turning the headset panel on is additive: a player in VR may still have the
    desktop window open on a monitor for someone else in the room, and losing
    it the moment VR is enabled reads as a bug. A failure in one backend must
    not stop the others from showing the line.
    """

    def __init__(self, *backends: OverlayBackend) -> None:
        self._backends = [backend for backend in backends if backend is not None]

    @property
    def backends(self) -> list[OverlayBackend]:
        return list(self._backends)

    def _each(self, action, label: str) -> list:
        results = []
        for backend in self._backends:
            try:
                results.append(action(backend))
            except Exception:
                logger.debug("Overlay backend %s failed", label, exc_info=True)
        return results

    def show_message(self, message: OutputMessage) -> bool | None:
        shown = self._each(lambda backend: backend.show_message(message), "show_message")
        return any(bool(result) for result in shown)

    def set_listen_status(self, listening: bool) -> None:
        self._each(
            lambda backend: backend.set_listen_status(listening), "set_listen_status"
        )

    def reveal(self) -> None:
        self._each(lambda backend: backend.reveal(), "reveal")

    def hide(self) -> None:
        self._each(lambda backend: backend.hide(), "hide")
