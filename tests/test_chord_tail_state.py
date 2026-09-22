"""The trigger held from the chord is released by the controller's own state.

SteamVR does not always send a mouse-up for a press that began before the
frame had focus, so the frame used to wait forever and the player needed an
extra click. The trigger action's state now ends the wait as well.
"""

from __future__ import annotations

import types

import pytest

from src.ui_qt.main_window import MainWindow
from tests.test_steamvr_inplace import _FakeOpenVR  # noqa: F401  (fixture pieces)
from tests.test_steamvr_inplace import _anchor
from src.core.steamvr_inplace import SteamVRSelectionFrame


@pytest.fixture
def fake_openvr(monkeypatch):
    import sys

    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    return module


def test_the_frame_can_be_released_from_outside(fake_openvr):
    frame = SteamVRSelectionFrame()
    assert frame.start()
    assert frame.show(b"\x00" * 16, 2, 2, _anchor(), button_held=True)
    assert frame.awaiting_release

    frame.release_arm()

    assert not frame.awaiting_release
    releases: list = []
    frame.on_release = lambda s, e: releases.append((s, e))
    fake_openvr.overlay.events = [
        (fake_openvr.VREvent_MouseButtonDown, 0.2, 0.8),
        (fake_openvr.VREvent_MouseButtonUp, 0.6, 0.4),
    ]
    frame.poll()
    assert len(releases) == 1


class _Frame:
    def __init__(self):
        self.awaiting_release = True
        self.released = 0
        self.polls = 0

    def poll(self):
        self.polls += 1

    def release_arm(self):
        self.released += 1
        self.awaiting_release = False


def _window(trigger_down):
    window = MainWindow.__new__(MainWindow)
    window._vr_overlay_backend = types.SimpleNamespace(available=True, poll=lambda: None)
    window._config = {"vrc_listen": {"screenshot_translation": {"enabled": False}}}
    window._selection_active = True
    window._vr_selection_frame = _Frame()
    window._vr_input = types.SimpleNamespace(trigger_down=trigger_down, poll=lambda: True, active=True)
    window._vr_dashboard = None
    window._vr_label_sheet = None
    return window


def test_a_released_trigger_ends_the_wait(monkeypatch):
    window = _window(trigger_down=False)
    monkeypatch.setattr(window, "_stop_vr_overlay_timer", lambda: None)

    MainWindow._poll_vr_overlay(window)

    assert window._vr_selection_frame.released == 1


def test_a_still_held_trigger_keeps_waiting(monkeypatch):
    window = _window(trigger_down=True)
    monkeypatch.setattr(window, "_stop_vr_overlay_timer", lambda: None)

    MainWindow._poll_vr_overlay(window)

    assert window._vr_selection_frame.released == 0


def test_an_unbound_trigger_leaves_it_to_the_mouse_events(monkeypatch):
    window = _window(trigger_down=None)
    monkeypatch.setattr(window, "_stop_vr_overlay_timer", lambda: None)

    MainWindow._poll_vr_overlay(window)

    assert window._vr_selection_frame.released == 0
