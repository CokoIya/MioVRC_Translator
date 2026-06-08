from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger(__name__)

DEFAULT_TEXT_INPUT_HOTKEY = "Alt+X"
DEFAULT_MIC_MUTE_HOTKEY = "Alt+C"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
_REQUIRED_MODIFIERS = MOD_ALT | MOD_CONTROL | MOD_WIN

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIER_ALIASES = {
    "alt": ("Alt", MOD_ALT),
    "option": ("Alt", MOD_ALT),
    "ctrl": ("Ctrl", MOD_CONTROL),
    "control": ("Ctrl", MOD_CONTROL),
    "shift": ("Shift", MOD_SHIFT),
    "win": ("Win", MOD_WIN),
    "windows": ("Win", MOD_WIN),
    "cmd": ("Win", MOD_WIN),
    "super": ("Win", MOD_WIN),
}

_SPECIAL_KEYS = {
    "space": ("Space", 0x20),
    "tab": ("Tab", 0x09),
    "enter": ("Enter", 0x0D),
    "return": ("Enter", 0x0D),
    "esc": ("Esc", 0x1B),
    "escape": ("Esc", 0x1B),
    "backspace": ("Backspace", 0x08),
    "delete": ("Delete", 0x2E),
    "del": ("Delete", 0x2E),
    "insert": ("Insert", 0x2D),
    "ins": ("Insert", 0x2D),
    "home": ("Home", 0x24),
    "end": ("End", 0x23),
    "pageup": ("PageUp", 0x21),
    "pgup": ("PageUp", 0x21),
    "pagedown": ("PageDown", 0x22),
    "pgdn": ("PageDown", 0x22),
    "up": ("Up", 0x26),
    "down": ("Down", 0x28),
    "left": ("Left", 0x25),
    "right": ("Right", 0x27),
}


class HotkeyError(ValueError):
    pass


def _split_hotkey(value: str) -> list[str]:
    normalized = str(value or "").replace("-", "+")
    return [part.strip() for part in normalized.split("+") if part.strip()]


def parse_hotkey(value: str) -> tuple[str, int, int] | None:
    parts = _split_hotkey(value)
    if not parts:
        return None

    modifiers = 0
    display_mods: list[str] = []
    key_name = ""
    vk = 0

    for raw_part in parts:
        part = raw_part.strip().lower()
        if part in _MODIFIER_ALIASES:
            label, bit = _MODIFIER_ALIASES[part]
            if not modifiers & bit:
                modifiers |= bit
                display_mods.append(label)
            continue
        if vk:
            raise HotkeyError("Only one main key is allowed")
        if len(part) == 1 and part.isalpha():
            key_name = part.upper()
            vk = ord(key_name)
        elif len(part) == 1 and part.isdigit():
            key_name = part
            vk = ord(part)
        elif part.startswith("f") and part[1:].isdigit():
            index = int(part[1:])
            if 1 <= index <= 24:
                key_name = f"F{index}"
                vk = 0x70 + index - 1
        elif part in _SPECIAL_KEYS:
            key_name, vk = _SPECIAL_KEYS[part]
        if not vk:
            raise HotkeyError(f"Unsupported key: {raw_part}")

    if not vk:
        raise HotkeyError("Missing main key")
    if not (modifiers & _REQUIRED_MODIFIERS):
        raise HotkeyError("Use Ctrl, Alt, or Win with the main key")
    display = "+".join([*display_mods, key_name])
    return display, modifiers, vk


def normalize_hotkey(value: str) -> str:
    parsed = parse_hotkey(value)
    if parsed is None:
        return ""
    return parsed[0]


class GlobalHotkey:
    def __init__(
        self,
        hotkey: str,
        callback: Callable[[], None],
        *,
        name: str = "global-hotkey",
        hotkey_id: int = 0x4D10,
    ) -> None:
        self.hotkey = normalize_hotkey(hotkey)
        self._callback = callback
        self._name = name
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hotkey_id = hotkey_id
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._registered = False

    @property
    def registered(self) -> bool:
        return self._registered

    def start(self) -> bool:
        if sys.platform != "win32" or not self.hotkey:
            return False
        if self._thread is not None:
            return self._registered
        self._stop_event.clear()
        self._ready_event.clear()
        self._registered = False
        self._thread = threading.Thread(
            target=self._run,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=1.5)
        return self._registered

    def stop(self) -> None:
        self._stop_event.set()
        if sys.platform == "win32" and self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread_id,
                    WM_QUIT,
                    0,
                    0,
                )
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0
        self._registered = False

    def _run(self) -> None:
        parsed = parse_hotkey(self.hotkey)
        if parsed is None:
            self._ready_event.set()
            return

        display, modifiers, vk = parsed
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = bool(
            user32.RegisterHotKey(
                None,
                self._hotkey_id,
                modifiers | MOD_NOREPEAT,
                vk,
            )
        )
        self._registered = registered
        self._ready_event.set()
        if not registered:
            logger.warning("Failed to register hotkey: %s (may be in use by another application)", display)
            return

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
            ]

        msg = MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == self._hotkey_id:
                    try:
                        self._callback()
                    except Exception:
                        logger.exception("Global hotkey callback failed")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                if not user32.UnregisterHotKey(None, self._hotkey_id):
                    err = ctypes.windll.kernel32.GetLastError()
                    logger.warning(
                        "UnregisterHotKey failed for id=%s (GetLastError=%s)",
                        self._hotkey_id,
                        err,
                    )
            except Exception:
                logger.exception("UnregisterHotKey raised unexpectedly")
            self._registered = False
