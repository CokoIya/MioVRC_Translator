import unittest
from unittest.mock import patch

from src.utils.global_hotkey import (
    DEFAULT_TEXT_INPUT_HOTKEY,
    GlobalHotkey,
    HotkeyError,
    normalize_hotkey,
)


class GlobalHotkeyTests(unittest.TestCase):
    def test_default_hotkey_uses_safe_modifier(self):
        self.assertEqual(normalize_hotkey(DEFAULT_TEXT_INPUT_HOTKEY), "Ctrl+Alt+X")

    def test_rejects_shift_only_or_single_key_hotkeys(self):
        with self.assertRaises(HotkeyError):
            normalize_hotkey("Shift+X")
        with self.assertRaises(HotkeyError):
            normalize_hotkey("X")

    def test_start_resets_reusable_thread_events(self):
        hotkey = GlobalHotkey(DEFAULT_TEXT_INPUT_HOTKEY, lambda: None)
        hotkey._ready_event.set()
        hotkey._stop_event.set()
        observed = {}

        def fake_run():
            observed["stop_was_set"] = hotkey._stop_event.is_set()
            hotkey._registered = True
            hotkey._ready_event.set()

        hotkey._run = fake_run

        with patch("src.utils.global_hotkey.sys.platform", "win32"):
            self.assertTrue(hotkey.start())

        hotkey.stop()
        self.assertFalse(observed["stop_was_set"])


if __name__ == "__main__":
    unittest.main()
