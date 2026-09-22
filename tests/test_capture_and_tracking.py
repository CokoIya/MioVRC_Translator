"""In a headset the desktop is never a substitute for the game's own picture."""

from __future__ import annotations

from src.core import screen_capture
from src.core.screen_capture import Capture


class TestCaptureFallback:
    def _no_vrchat(self, monkeypatch):
        monkeypatch.setattr(screen_capture, "find_vrchat_window", lambda: 0)
        monkeypatch.setattr(
            screen_capture, "capture_primary_screen", lambda: Capture(b"desk", 10, 10, "screen")
        )

    def test_a_desktop_player_falls_back_to_the_screen(self, monkeypatch):
        self._no_vrchat(monkeypatch)

        capture = screen_capture.capture_for_translation(allow_screen_fallback=True)

        assert capture.ok and capture.source == "screen"

    def test_a_headset_player_gets_an_error_instead_of_the_desktop(self, monkeypatch):
        """Reading IDE text over the player's view was the bug."""

        self._no_vrchat(monkeypatch)

        capture = screen_capture.capture_for_translation(allow_screen_fallback=False)

        assert not capture.ok
        assert capture.source == "vrchat_missing"

    def test_an_unreadable_vrchat_window_is_also_an_error_in_the_headset(self, monkeypatch):
        monkeypatch.setattr(screen_capture, "find_vrchat_window", lambda: 123)
        monkeypatch.setattr(screen_capture, "capture_window", lambda hwnd: Capture(b"", 0, 0, "vrchat"))
        monkeypatch.setattr(
            screen_capture, "capture_primary_screen", lambda: Capture(b"desk", 10, 10, "screen")
        )

        capture = screen_capture.capture_for_translation(allow_screen_fallback=False)

        assert not capture.ok
        assert capture.source == "vrchat"

    def test_a_minimised_window_is_restored_read_and_put_back(self, monkeypatch):
        calls: list = []

        class _User32:
            def IsIconic(self, hwnd):
                return True

            def ShowWindow(self, hwnd, how):
                calls.append(("show", how))

        drawn = {"n": 0}

        class _Image:
            def __init__(self, blank):
                self._blank = blank

            def isNull(self):
                return False

            def width(self):
                return 10

            def height(self):
                return 10

            def pixel(self, x, y):
                return 0 if self._blank else (x * 31 + y * 17)

        def print_window(hwnd):
            drawn["n"] += 1
            # Black for the first two reads, then real content.
            return _Image(blank=drawn["n"] < 3)

        monkeypatch.setattr(screen_capture, "_user32", lambda: _User32())
        monkeypatch.setattr(screen_capture, "_print_window_image", print_window)
        monkeypatch.setattr(screen_capture, "_client_origin", lambda user32, hwnd: (5, 6))
        monkeypatch.setattr(screen_capture, "_RESTORE_POLL_S", 0.0)
        monkeypatch.setattr(
            screen_capture, "_to_png", lambda image, source, origin=(0, 0), hwnd=0: Capture(b"ok", 10, 10, source, origin=origin)
        )

        capture = screen_capture.capture_window(77)

        assert capture.ok
        assert calls[0] == ("show", screen_capture._SW_SHOWNOACTIVATE)
        assert calls[-1] == ("show", screen_capture._SW_SHOWMINNOACTIVE)
        assert drawn["n"] == 3
