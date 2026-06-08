from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from src.utils.global_hotkey import GlobalHotkey


class HotkeyService(QObject):
    hotkey_pressed = Signal(str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._hotkeys: dict[str, GlobalHotkey] = {}

    def start(self, callback: Callable[[str], None] | None = None) -> None:
        if callback is not None:
            self.hotkey_pressed.connect(callback)
        for hotkey in self._hotkeys.values():
            hotkey.start()

    def stop(self) -> None:
        for hotkey in self._hotkeys.values():
            hotkey.stop()

    def set_hotkey(self, key: str, action: str) -> None:
        action = str(action or "").strip()
        if not action:
            return
        old = self._hotkeys.pop(action, None)
        if old is not None:
            old.stop()
        hotkey = GlobalHotkey(
            key,
            lambda action=action: self.hotkey_pressed.emit(action),
            name=f"qt-{action}",
            hotkey_id=100 + len(self._hotkeys),
        )
        self._hotkeys[action] = hotkey
        try:
            hotkey.start()
        except Exception as exc:
            self.error.emit(str(exc))
