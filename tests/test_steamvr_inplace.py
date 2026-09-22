"""The label sheet and the selection frame against a fake OpenVR runtime."""

from __future__ import annotations

import sys
import types

import pytest

from src.core import steamvr_inplace
from src.core.in_place_layout import HeadAnchor
from src.core.steamvr_inplace import SteamVRLabelSheet, SteamVRSelectionFrame


class _Matrix:
    def __init__(self):
        self.rows = [[0.0] * 4 for _ in range(3)]

    def __getitem__(self, r):
        return self.rows[r]


class _Vec2:
    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = x, y


class _Overlay:
    def __init__(self):
        self.calls: list[tuple] = []
        self.events: list = []
        self.handles = 0

    def createOverlay(self, key, name):
        self.handles += 1
        self.calls.append(("create", key))
        return self.handles

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
            return 0

        return record

    def pollNextOverlayEvent(self, handle, event):
        if not self.events:
            return (False, event)
        kind, x, y = self.events.pop(0)
        event.eventType = kind
        event.data.mouse.x = x
        event.data.mouse.y = y
        return (True, event)


class _FakeOpenVR(types.ModuleType):
    VREvent_MouseButtonDown = 300
    VREvent_MouseMove = 301
    VREvent_MouseButtonUp = 302
    VROverlayInputMethod_Mouse = 1
    VROverlayFlags_MakeOverlaysInteractiveIfVisible = 2
    TrackingUniverseStanding = 1
    k_unTrackedDeviceIndex_Hmd = 0
    k_unMaxTrackedDeviceCount = 64
    Eye_Left = 0
    HmdMatrix34_t = _Matrix
    HmdVector2_t = _Vec2

    class VREvent_t:
        def __init__(self):
            self.eventType = 0
            self.data = types.SimpleNamespace(mouse=types.SimpleNamespace(x=0.0, y=0.0))

    def __init__(self):
        super().__init__("openvr")
        self.overlay = _Overlay()

    def IVROverlay(self):
        return self.overlay


@pytest.fixture
def fake_openvr(monkeypatch):
    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    return module


def _anchor():
    return HeadAnchor(
        pose=((1, 0, 0, 0), (0, 1, 0, 1.6), (0, 0, 1, 0)),
        eye_offset=(-0.034, 0.0, 0.0),
        tangents=(-1.28, 1.28, -1.28, 1.28),
    )


class TestLabelSheet:
    def test_show_places_the_sheet_in_the_world_and_uploads(self, fake_openvr):
        sheet = SteamVRLabelSheet()
        assert sheet.start()

        shown = sheet.show(b"\x00" * 16, 2, 2, _anchor(), depth=2.0, fov_scale=1.0)

        assert shown
        names = [call[0] for call in fake_openvr.overlay.calls]
        assert "setOverlayTransformAbsolute" in names
        assert "setOverlayWidthInMeters" in names
        assert "setOverlayRaw" in names
        assert names[-1] == "showOverlay"
        width = next(c for c in fake_openvr.overlay.calls if c[0] == "setOverlayWidthInMeters")[2]
        assert width == pytest.approx(2.56 * 2.0)

    def test_without_a_pose_nothing_is_shown(self, fake_openvr, monkeypatch):
        sheet = SteamVRLabelSheet()
        assert sheet.start()
        monkeypatch.setattr(sheet, "snapshot_head_anchor", lambda: None)

        assert sheet.show(b"", 1, 1, None) is False
        assert "showOverlay" not in [c[0] for c in fake_openvr.overlay.calls]

    def test_stop_destroys_the_overlay(self, fake_openvr):
        sheet = SteamVRLabelSheet()
        sheet.start()
        sheet.stop()

        assert "destroyOverlay" in [c[0] for c in fake_openvr.overlay.calls]
        assert not sheet.available

    def test_no_runtime_means_not_available(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "openvr", None)
        sheet = SteamVRLabelSheet()

        assert sheet.start() is False
        assert sheet.show(b"", 1, 1, _anchor()) is False


class TestSelectionFrame:
    def _frame(self, fake_openvr):
        frame = SteamVRSelectionFrame()
        assert frame.start()
        # Opened with the trigger up, so the first press is a real selection.
        assert frame.show(b"\x00" * 16, 2, 2, _anchor(), button_held=False)
        return frame

    def test_it_is_head_locked_and_interactive(self, fake_openvr):
        self._frame(fake_openvr)

        names = [c[0] for c in fake_openvr.overlay.calls]
        assert "setOverlayTransformTrackedDeviceRelative" in names
        assert "setOverlayInputMethod" in names
        assert "setOverlayTransformAbsolute" not in names

    def test_a_drag_reports_moves_and_the_release_in_image_coordinates(self, fake_openvr):
        frame = self._frame(fake_openvr)
        drags: list = []
        releases: list = []
        frame.on_drag = lambda start, end: drags.append((start, end))
        frame.on_release = lambda start, end: releases.append((start, end))
        # OpenVR v runs upward; the frame reports v downward.
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonDown, 0.2, 0.9),
            (fake_openvr.VREvent_MouseMove, 0.5, 0.6),
            (fake_openvr.VREvent_MouseButtonUp, 0.7, 0.3),
        ]

        frame.poll()

        assert drags == [((0.2, pytest.approx(0.1)), (0.5, pytest.approx(0.4)))]
        assert releases == [((0.2, pytest.approx(0.1)), (0.7, pytest.approx(0.7)))]
        assert not frame.dragging

    def test_moves_without_a_press_are_ignored(self, fake_openvr):
        frame = self._frame(fake_openvr)
        drags: list = []
        frame.on_drag = lambda *a: drags.append(a)
        fake_openvr.overlay.events = [(fake_openvr.VREvent_MouseMove, 0.5, 0.5)]

        frame.poll()

        assert drags == []

    def test_moves_without_a_press_are_reported_as_hovering(self, fake_openvr):
        frame = self._frame(fake_openvr)
        hovers: list = []
        frame.on_hover = hovers.append
        fake_openvr.overlay.events = [(fake_openvr.VREvent_MouseMove, 0.5, 0.9)]

        frame.poll()

        # Reported in image coordinates: v down from the top.
        assert hovers == [(0.5, pytest.approx(0.1))]
        assert not frame.dragging

    def test_hovering_is_reported_even_while_the_chord_trigger_is_still_held(self, fake_openvr):
        frame = SteamVRSelectionFrame()
        assert frame.start()
        assert frame.show(b"\x00" * 16, 2, 2, _anchor(), button_held=True)
        hovers: list = []
        frame.on_hover = hovers.append
        fake_openvr.overlay.events = [(fake_openvr.VREvent_MouseMove, 0.3, 0.5)]

        frame.poll()

        assert hovers == [(0.3, pytest.approx(0.5))]
        assert frame.awaiting_release

    def test_poll_is_bounded_even_if_events_never_stop(self, fake_openvr, monkeypatch):
        """pollNextOverlayEvent returns a tuple that is always truthy."""

        frame = self._frame(fake_openvr)
        calls = {"n": 0}

        def endless(handle, event):
            calls["n"] += 1
            event.eventType = 0
            return (True, event)

        monkeypatch.setattr(fake_openvr.overlay, "pollNextOverlayEvent", endless)

        frame.poll()

        assert calls["n"] == steamvr_inplace.MAX_EVENTS_PER_POLL

    def test_hide_forgets_a_drag_in_progress(self, fake_openvr):
        frame = self._frame(fake_openvr)
        fake_openvr.overlay.events = [(fake_openvr.VREvent_MouseButtonDown, 0.2, 0.2)]
        frame.poll()
        assert frame.dragging

        frame.hide()

        assert not frame.dragging


class TestChordTail:
    """The trigger is still down when the frame opens; that is not a selection."""

    def _frame(self, fake_openvr, *, button_held=True):
        frame = SteamVRSelectionFrame()
        assert frame.start()
        assert frame.show(b"\x00" * 16, 2, 2, _anchor(), button_held=button_held)
        return frame

    def test_releasing_the_chord_trigger_does_not_finish_a_selection(self, fake_openvr):
        frame = self._frame(fake_openvr)
        releases: list = []
        frame.on_release = lambda s, e: releases.append((s, e))
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonDown, 0.4, 0.4),
            (fake_openvr.VREvent_MouseMove, 0.5, 0.5),
            (fake_openvr.VREvent_MouseButtonUp, 0.5, 0.5),
        ]

        frame.poll()

        assert releases == []
        assert not frame.dragging

    def test_a_fresh_press_after_the_release_is_a_selection(self, fake_openvr):
        frame = self._frame(fake_openvr)
        releases: list = []
        frame.on_release = lambda s, e: releases.append((s, e))
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonUp, 0.5, 0.5),
            (fake_openvr.VREvent_MouseButtonDown, 0.2, 0.8),
            (fake_openvr.VREvent_MouseButtonUp, 0.6, 0.4),
        ]

        frame.poll()

        assert len(releases) == 1

    def test_a_frame_opened_without_the_button_held_selects_at_once(self, fake_openvr):
        frame = self._frame(fake_openvr, button_held=False)
        releases: list = []
        frame.on_release = lambda s, e: releases.append((s, e))
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonDown, 0.2, 0.8),
            (fake_openvr.VREvent_MouseButtonUp, 0.6, 0.4),
        ]

        frame.poll()

        assert len(releases) == 1
