import unittest
from unittest.mock import patch

from src.utils.global_hotkey import (
    DEFAULT_MIC_MUTE_HOTKEY,
    DEFAULT_TEXT_INPUT_HOTKEY,
    GlobalHotkey,
    HotkeyError,
    normalize_hotkey,
)


class GlobalHotkeyTests(unittest.TestCase):
    def test_default_hotkey_uses_safe_modifier(self):
        self.assertEqual(normalize_hotkey(DEFAULT_TEXT_INPUT_HOTKEY), "Alt+X")

    def test_microphone_mute_hotkey_defaults_to_alt_c(self):
        self.assertEqual(normalize_hotkey(DEFAULT_MIC_MUTE_HOTKEY), "Alt+C")

    def test_rejects_shift_only_or_single_key_hotkeys(self):
        with self.assertRaises(HotkeyError):
            normalize_hotkey("Shift+X")
        with self.assertRaises(HotkeyError):
            normalize_hotkey("X")

    def test_hotkey_id_can_be_overridden(self):
        hotkey = GlobalHotkey(DEFAULT_MIC_MUTE_HOTKEY, lambda: None, hotkey_id=0x4D11)
        self.assertEqual(hotkey._hotkey_id, 0x4D11)

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

    def test_start_can_skip_readiness_wait(self):
        hotkey = GlobalHotkey(DEFAULT_TEXT_INPUT_HOTKEY, lambda: None)
        hotkey._run = lambda: None

        with patch("src.utils.global_hotkey.sys.platform", "win32"), patch.object(
            hotkey._ready_event,
            "wait",
            side_effect=AssertionError("nonblocking startup must not wait"),
        ):
            self.assertFalse(hotkey.start(wait_for_ready=False))

        hotkey._thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
