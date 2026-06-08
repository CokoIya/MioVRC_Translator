from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class RuntimeController(QObject):
    state_changed = Signal(str)
    running_changed = Signal(bool)
    muted_changed = Signal(bool)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._muted = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.running_changed.emit(True)
        self.state_changed.emit("running")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.running_changed.emit(False)
        self.state_changed.emit("idle")

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        self.muted_changed.emit(self._muted)
        self.state_changed.emit("muted" if self._muted else ("running" if self._running else "idle"))
        return self._muted

    def is_running(self) -> bool:
        return self._running

    def is_muted(self) -> bool:
        return self._muted
