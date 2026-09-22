"""SteamVR overlay backend tests.

Everything here runs without a headset: a fake ``openvr`` module stands in for
the runtime. That matters because the suite has to pass on machines that have
never had SteamVR installed.
"""

from __future__ import annotations

import math

import sys
import types
from unittest.mock import patch

import pytest

from src.core.steamvr_overlay import (
    DEFAULT_POSITION,
    MAX_WIDTH_METERS,
    MIN_WIDTH_METERS,
    SteamVROverlayBackend,
    clamp_position,
    clamp_width_meters,
)


class _Matrix:
    def __init__(self, rows=None):
        self._rows = rows or [[0.0] * 4 for _ in range(3)]

    def __getitem__(self, index):
        return self._rows[index]


class _Pose:
    def __init__(self, matrix, valid=True):
        self.mDeviceToAbsoluteTracking = matrix
        self.bPoseIsValid = valid


class _Event:
    def __init__(self):
        self.eventType = 0
        self.trackedDeviceIndex = 0


class _FakeOverlay:
    def __init__(self, recorder):
        self._recorder = recorder
        self.events: list[tuple[int, int]] = []
        self.raw_pushes = 0

    def createOverlay(self, key, name):
        self._recorder.append(("createOverlay", key))
        return 42

    def setOverlayWidthInMeters(self, handle, meters):
        self._recorder.append(("width", meters))

    def setOverlayCurvature(self, handle, value):
        self._recorder.append(("curvature", value))

    def setOverlayAlpha(self, handle, value):
        self._recorder.append(("alpha", value))

    def setOverlayTransformTrackedDeviceRelative(self, handle, device, matrix):
        self._recorder.append(("transform", (matrix[0][3], matrix[1][3], matrix[2][3])))

    def setOverlayInputMethod(self, handle, method):
        self._recorder.append(("inputMethod", method))

    def setOverlayFlag(self, handle, flag, enabled):
        self._recorder.append(("flag", flag, enabled))

    def setOverlayRaw(self, handle, buffer, width, height, depth):
        self.raw_pushes += 1
        self._recorder.append(("raw", width, height, depth))

    def showOverlay(self, handle):
        self._recorder.append(("show",))

    def hideOverlay(self, handle):
        self._recorder.append(("hide",))

    def destroyOverlay(self, handle):
        self._recorder.append(("destroy",))

    def pollNextOverlayEvent(self, handle, event):
        if not self.events:
            return False, event
        kind, device = self.events.pop(0)
        event.eventType = kind
        event.trackedDeviceIndex = device
        return True, event


def _fake_openvr(recorder, *, poses=None, init_error=None):
    module = types.ModuleType("openvr")
    module.VRApplication_Overlay = 20
    module.VROverlayInputMethod_Mouse = 1
    module.VROverlayInputMethod_None = 0
    module.VROverlayFlags_MakeOverlaysInteractiveIfVisible = 9
    module.k_unTrackedDeviceIndexInvalid = 0xFFFFFFFF
    module.k_unTrackedDeviceIndex_Hmd = 0
    module.k_unMaxTrackedDeviceCount = 8
    module.TrackingUniverseStanding = 1
    module.VREvent_MouseButtonDown = 301
    module.VREvent_MouseButtonUp = 302
    module.VREvent_MouseMove = 300
    module.VREvent_FocusLeave = 304
    module.VREvent_Quit = 700
    module.HmdMatrix34_t = _Matrix
    module.VREvent_t = _Event

    overlay = _FakeOverlay(recorder)
    module._overlay = overlay

    def init(kind):
        recorder.append(("init", kind))
        if init_error is not None:
            raise init_error

    def shutdown():
        recorder.append(("shutdown",))

    module.init = init
    module.shutdown = shutdown
    module.IVROverlay = lambda: overlay

    identity = _Matrix([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]])
    default_poses = poses or [_Pose(identity) for _ in range(8)]

    class _System:
        def getDeviceToAbsoluteTrackingPose(self, origin, seconds, count):
            return default_poses

    module.VRSystem = _System
    return module


class _FakePanel:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self.renders = 0
        self.deleted = False

    def add_message(self, *, translated, original="", source="listen"):
        if not translated:
            return False
        self.messages.append((translated, original, source))
        return True

    def render_rgba(self):
        self.renders += 1
        return b"\x00" * 16, 2, 2

    def deleteLater(self):
        self.deleted = True


def _backend(recorder, panel=None, **kwargs):
    return SteamVROverlayBackend(lambda: panel or _FakePanel(), **kwargs)


# --------------------------------------------------------------------- clamps
@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, DEFAULT_POSITION), ("nonsense", DEFAULT_POSITION), ((1, 2), DEFAULT_POSITION)],
)
def test_a_broken_saved_position_falls_back_to_the_default(value, expected):
    assert clamp_position(value) == expected


def test_position_is_kept_at_a_readable_depth():
    """A panel at z=0 sits inside the player's face."""

    assert clamp_position((0.0, 0.0, 0.0))[2] <= -0.4
    assert clamp_position((0.0, 0.0, -99.0))[2] >= -6.0


def test_width_is_bounded():
    assert clamp_width_meters(0.01) == MIN_WIDTH_METERS
    assert clamp_width_meters(99.0) == MAX_WIDTH_METERS
    assert clamp_width_meters("nonsense") == pytest.approx(1.6)


# --------------------------------------------------------------------- startup
def test_a_machine_without_openvr_installed_degrades_quietly():
    """No headset, no SteamVR, no crash - the app must not notice."""

    recorder: list = []
    backend = _backend(recorder)
    with patch.dict(sys.modules, {"openvr": None}):
        with patch(
            "builtins.__import__", side_effect=ImportError("no module named openvr")
        ):
            assert backend.start() is False
    assert backend.available is False
    assert "openvr import failed" in backend.unavailable_reason


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("VRInitError_Init_NotRunning", "steamvr_not_running"),
        ("VRInitError_Init_NoServerForBackgroundApp", "steamvr_not_running"),
        ("VRInitError_Init_PathRegistryNotFound", "steamvr_not_installed"),
        ("VRInitError_Init_HmdNotFound", "headset_not_found"),
    ],
)
def test_init_failures_map_to_something_a_player_can_act_on(message, reason):
    recorder: list = []
    module = _fake_openvr(recorder, init_error=RuntimeError(message))
    backend = _backend(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        assert backend.start() is False
    assert backend.available is False
    assert backend.unavailable_reason == reason


def test_start_warms_the_panel_before_the_first_subtitle():
    """Building the panel lazily cost ~120 ms on the first line a player saw."""

    recorder: list = []
    panel = _FakePanel()
    backend = _backend(recorder, panel=panel)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        assert backend.start() is True
    assert backend._panel is panel


def test_the_overlay_itself_stays_opaque():
    """Transparency is per pixel; fading the overlay would fade the text too."""

    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
    assert ("alpha", 1.0) in recorder


def test_a_visible_panel_never_takes_the_controllers():
    """The fatal bug: interactive-if-visible on an always-visible panel left
    the player unable to move or open menus in the game."""

    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
    assert ("flag", 9, True) not in recorder
    assert not backend.edit_mode


def test_edit_mode_hands_the_controllers_over_and_back():
    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        assert backend.set_edit_mode(True) is True
        assert ("flag", 9, True) in recorder
        assert ("inputMethod", 1) in recorder
        assert backend.edit_mode
        assert backend.set_edit_mode(False) is False
    assert ("flag", 9, False) in recorder
    assert not backend.edit_mode


# --------------------------------------------------------------------- messages
def test_a_message_reaches_the_headset():
    recorder: list = []
    panel = _FakePanel()
    backend = _backend(recorder, panel=panel)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        message = types.SimpleNamespace(
            source="listen",
            original_text="こんにちは",
            translated_text="你好",
            display_text="こんにちは（你好）",
        )
        assert backend.show_message(message) is True
    assert panel.messages == [("你好", "こんにちは", "listen")]


def test_an_empty_message_is_dropped():
    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        message = types.SimpleNamespace(
            source="listen", original_text="", translated_text="", display_text=""
        )
        assert backend.show_message(message) is False


def test_listen_status_draws_no_chrome_in_the_headset():
    """A waiting indicator floating in the player's view is noise."""

    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        before = len(recorder)
        backend.set_listen_status(True)
        backend.set_listen_status(False)
    assert len(recorder) == before


# --------------------------------------------------------------------- events
def test_the_event_drain_terminates():
    """pyopenvr returns (has_event, event); a tuple is always truthy.

    Looping on the call itself spins forever and freezes the UI thread.
    """

    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        module._overlay.events = [(300, 1)] * 5
        backend.poll()  # must return rather than hang
    assert module._overlay.events == []


def test_steamvr_quitting_releases_the_runtime():
    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        module._overlay.events = [(700, 0)]
        backend.poll()
    assert backend.available is False
    assert ("shutdown",) in recorder


# --------------------------------------------------------------------- grabbing
def _hand_poses(hand_offset, yaw=0.0):
    """Head at the origin; a hand at ``hand_offset`` pointing forward, turned by ``yaw``."""

    identity = _Matrix([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]])
    c, s = math.cos(yaw), math.sin(yaw)
    # Rotation about +y: the device's -z (its pointing direction) turns
    # toward +x for a positive yaw, i.e. to the player's right.
    hand = _Matrix(
        [
            [c, 0, -s, hand_offset[0]],
            [0, 1.0, 0, hand_offset[1]],
            [s, 0, c, hand_offset[2]],
        ]
    )
    return [_Pose(identity), _Pose(hand)] + [_Pose(identity) for _ in range(6)]


def _radius(position):
    return math.hypot(position[0], position[2])


def test_pressing_grabs_at_once_without_teleporting_the_panel():
    """Edit mode is explicit, so the drag starts on the press; the panel must
    not jump to the laser the moment it does."""

    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder, poses=_hand_poses((0.5, 0.0, -1.0)))
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        start_position = backend.position
        module._overlay.events = [(301, 1)]
        backend.poll()
        assert backend.grabbing is True
        backend.poll()
        assert backend.position == pytest.approx(start_position, abs=1e-6)


def test_turning_the_laser_swings_the_panel_around_the_head():
    """A hand at the head turned 20 degrees right moves the panel 20 degrees
    right along its cylinder, at the same distance."""

    recorder: list = []
    backend = _backend(recorder)
    poses = _hand_poses((0.0, -0.3, 0.0))
    module = _fake_openvr(recorder, poses=poses)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        start_position = backend.position
        module._overlay.events = [(301, 1)]
        backend.poll()

        turned = _hand_poses((0.0, -0.3, 0.0), yaw=math.radians(20))
        poses[1].mDeviceToAbsoluteTracking = turned[1].mDeviceToAbsoluteTracking
        backend.poll()

    x, _y, z = backend.position
    assert math.degrees(math.atan2(x, -z)) == pytest.approx(20.0, abs=0.5)
    assert _radius(backend.position) == pytest.approx(_radius(start_position), abs=1e-6)
    assert x > 0


def test_the_panel_turns_to_face_the_head():
    recorder: list = []
    backend = _backend(recorder)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        backend.set_position((1.5, 0.0, 0.0))

    transforms = [entry for entry in recorder if entry[0] == "transform"]
    assert transforms[-1][1] == pytest.approx((1.5, 0.0, 0.0), abs=1e-6)


def test_releasing_commits_the_new_position_once():
    recorder: list = []
    committed: list = []
    backend = _backend(recorder)
    backend.set_position_committed_callback(committed.append)
    poses = _hand_poses((0.0, -0.3, 0.0))
    module = _fake_openvr(recorder, poses=poses)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        module._overlay.events = [(301, 1)]
        backend.poll()
        turned = _hand_poses((0.0, -0.3, 0.0), yaw=math.radians(15))
        poses[1].mDeviceToAbsoluteTracking = turned[1].mDeviceToAbsoluteTracking
        backend.poll()
        module._overlay.events = [(302, 1)]
        backend.poll()

    assert len(committed) == 1
    assert backend.grabbing is False


def test_a_tap_that_did_not_move_the_panel_commits_nothing():
    """A tap on the panel is not a move; it must not rewrite saved settings."""

    recorder: list = []
    committed: list = []
    backend = _backend(recorder)
    backend.set_position_committed_callback(committed.append)
    module = _fake_openvr(recorder, poses=_hand_poses((0.5, 0.0, -1.0)))
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        module._overlay.events = [(301, 1)]
        backend.poll()
        module._overlay.events = [(302, 1)]
        backend.poll()

    assert committed == []


def test_an_untracked_controller_cannot_drag_the_panel():
    recorder: list = []
    backend = _backend(recorder)
    poses = _hand_poses((0.5, 0.0, -1.0))
    poses[1].bPoseIsValid = False
    module = _fake_openvr(recorder, poses=poses)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        module._overlay.events = [(301, 1)]
        backend.poll()
        backend.poll()

    assert backend.grabbing is False
    assert backend.position == DEFAULT_POSITION


# --------------------------------------------------------------------- teardown
def test_stop_releases_everything_and_is_idempotent():
    recorder: list = []
    panel = _FakePanel()
    backend = _backend(recorder, panel=panel)
    module = _fake_openvr(recorder)
    with patch.dict(sys.modules, {"openvr": module}):
        backend.start()
        backend.stop()
        backend.stop()

    assert backend.available is False
    assert ("destroy",) in recorder
    assert ("shutdown",) in recorder
    assert panel.deleted is True


# ----------------------------------------------------------------- composite
class _RecordingBackend:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.shown: list[str] = []
        self.status: list[bool] = []
        self.revealed = 0
        self.hidden = 0

    def show_message(self, message):
        if self.fail:
            raise RuntimeError(f"{self.name} is broken")
        self.shown.append(message.translated_text)
        return True

    def set_listen_status(self, listening):
        if self.fail:
            raise RuntimeError(f"{self.name} is broken")
        self.status.append(listening)

    def reveal(self):
        self.revealed += 1

    def hide(self):
        self.hidden += 1


def test_enabling_the_headset_does_not_take_the_desktop_overlay_away():
    """Turning VR on is additive; losing the desktop window reads as a bug."""

    from src.core.overlay_service import CompositeOverlayBackend

    desktop = _RecordingBackend("desktop")
    headset = _RecordingBackend("headset")
    composite = CompositeOverlayBackend(desktop, headset)

    message = types.SimpleNamespace(translated_text="你好")
    assert composite.show_message(message) is True
    assert desktop.shown == ["你好"]
    assert headset.shown == ["你好"]


def test_one_broken_overlay_does_not_silence_the_other():
    from src.core.overlay_service import CompositeOverlayBackend

    desktop = _RecordingBackend("desktop")
    headset = _RecordingBackend("headset", fail=True)
    composite = CompositeOverlayBackend(desktop, headset)

    assert composite.show_message(types.SimpleNamespace(translated_text="你好")) is True
    assert desktop.shown == ["你好"]

    composite.set_listen_status(True)
    assert desktop.status == [True]


def test_composite_forwards_reveal_and_hide():
    from src.core.overlay_service import CompositeOverlayBackend

    desktop = _RecordingBackend("desktop")
    headset = _RecordingBackend("headset")
    composite = CompositeOverlayBackend(desktop, headset)

    composite.reveal()
    composite.hide()

    assert (desktop.revealed, desktop.hidden) == (1, 1)
    assert (headset.revealed, headset.hidden) == (1, 1)


def test_composite_ignores_a_missing_backend():
    from src.core.overlay_service import CompositeOverlayBackend

    desktop = _RecordingBackend("desktop")
    composite = CompositeOverlayBackend(desktop, None)

    assert composite.backends == [desktop]
    assert composite.show_message(types.SimpleNamespace(translated_text="hi")) is True
