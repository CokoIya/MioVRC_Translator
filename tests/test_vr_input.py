"""The screenshot button only counts as a chord: trigger + grip held, then B/Y."""

from __future__ import annotations

import sys
import types

import pytest

from src.core import vr_input
from src.core.vr_input import (
    DISMISS_ACTION,
    HOLD_GRIP_ACTION,
    HOLD_TRIGGER_ACTION,
    TRANSLATE_ACTION,
    VRActionInput,
)


class _Data:
    def __init__(self, active=True, state=False):
        self.bActive = active
        self.bState = state


class _Input:
    def __init__(self):
        self.states: dict[str, _Data] = {
            TRANSLATE_ACTION: _Data(),
            HOLD_TRIGGER_ACTION: _Data(),
            HOLD_GRIP_ACTION: _Data(),
            DISMISS_ACTION: _Data(),
        }
        self.handles = {name: index + 1 for index, name in enumerate(self.states)}

    def setActionManifestPath(self, path):
        return None

    def getActionSetHandle(self, name):
        return 99

    def getActionHandle(self, name):
        return self.handles[name]

    def updateActionState(self, sets):
        return None

    def getDigitalActionData(self, handle, restrict):
        name = next(n for n, h in self.handles.items() if h == handle)
        return self.states[name]


@pytest.fixture
def action_input(monkeypatch, tmp_path):
    module = types.ModuleType("openvr")
    fake = _Input()
    module.VRInput = lambda: fake
    module.k_ulInvalidInputValueHandle = 0

    import ctypes

    class _Set(ctypes.Structure):
        _fields_ = [
            ("ulActionSet", ctypes.c_ulonglong),
            ("ulRestrictedToDevice", ctypes.c_ulonglong),
            ("ulSecondaryActionSet", ctypes.c_ulonglong),
            ("unPadding", ctypes.c_ulong),
            ("nPriority", ctypes.c_long),
        ]

    module.VRActiveActionSet_t = _Set
    monkeypatch.setitem(sys.modules, "openvr", module)
    manifest = tmp_path / "action_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(vr_input, "manifest_path", lambda: str(manifest))
    fired: list[int] = []
    source = VRActionInput(on_translate=lambda: fired.append(1))
    assert source.start()
    return source, fake, fired


def _press(fake: _Input, *, button: bool, trigger: bool, grip: bool) -> None:
    fake.states[TRANSLATE_ACTION].bState = button
    fake.states[HOLD_TRIGGER_ACTION].bState = trigger
    fake.states[HOLD_GRIP_ACTION].bState = grip


def test_any_button_counts_once_per_press(action_input):
    """A shown translation goes away on the first press of anything."""

    source, fake, _fired = action_input

    source.poll()
    assert source.any_button_pressed is False

    _press(fake, button=False, trigger=True, grip=False)
    source.poll()
    assert source.any_button_pressed is True
    # Still held: not a new press.
    source.poll()
    assert source.any_button_pressed is False

    _press(fake, button=False, trigger=False, grip=False)
    source.poll()
    assert source.any_button_pressed is False

    fake.states[DISMISS_ACTION].bState = True
    source.poll()
    assert source.any_button_pressed is True
    fake.states[DISMISS_ACTION].bState = False
    source.poll()
    assert source.any_button_pressed is False


def test_a_manifest_without_the_any_button_action_still_counts_the_others(monkeypatch, action_input):
    source, fake, _fired = action_input
    del fake.handles[DISMISS_ACTION]
    del fake.states[DISMISS_ACTION]
    source.stop()
    assert source.start()

    _press(fake, button=False, trigger=False, grip=True)
    source.poll()

    assert source.any_button_pressed is True


def test_the_pointer_ray_comes_from_the_tip_pose(action_input):
    from src.core.vr_input import POINTER_RIGHT_ACTION

    source, fake, _fired = action_input

    class _Pose:
        bPoseIsValid = True
        # Column 3 is the position; column 2 the +Z axis, so the ray runs -Z.
        mDeviceToAbsoluteTracking = [[1.0, 0.0, 0.0, 0.5], [0.0, 1.0, 0.0, 1.2], [0.0, 0.0, 1.0, -0.3]]

    calls: list = []

    def pose_data(action, origin, seconds, restrict):
        calls.append(action)
        return types.SimpleNamespace(bActive=True, pose=_Pose())

    fake.handles[POINTER_RIGHT_ACTION] = 42
    fake.getPoseActionDataRelativeToNow = pose_data
    source.stop()
    assert source.start()

    origin, direction = source.pointer_ray("right")

    assert origin == (0.5, 1.2, -0.3)
    assert direction == (-0.0, -0.0, -1.0)
    assert calls == [42]
    # No tip pose bound for the other hand: nothing to aim with.
    assert source.pointer_ray("left") is None


def test_the_button_alone_does_nothing(action_input):
    source, fake, fired = action_input

    _press(fake, button=True, trigger=False, grip=False)
    source.poll()

    assert fired == []


def test_the_chord_fires_once_per_press(action_input):
    source, fake, fired = action_input

    _press(fake, button=True, trigger=True, grip=True)
    source.poll()
    source.poll()
    _press(fake, button=False, trigger=True, grip=True)
    source.poll()
    _press(fake, button=True, trigger=True, grip=True)
    source.poll()

    assert fired == [1, 1]


def test_half_a_chord_is_not_a_chord(action_input):
    source, fake, fired = action_input

    _press(fake, button=True, trigger=True, grip=False)
    source.poll()
    _press(fake, button=False, trigger=True, grip=False)
    source.poll()
    _press(fake, button=True, trigger=False, grip=True)
    source.poll()

    assert fired == []


def test_unbound_hold_actions_fall_back_to_a_plain_press(action_input):
    """A headset whose bindings lack the hold actions must still work."""

    source, fake, fired = action_input
    fake.states[HOLD_TRIGGER_ACTION].bActive = False
    fake.states[HOLD_GRIP_ACTION].bActive = False

    _press(fake, button=True, trigger=False, grip=False)
    source.poll()

    assert fired == [1]


def test_an_inactive_button_reports_as_unbound(action_input):
    source, fake, fired = action_input
    fake.states[TRANSLATE_ACTION].bActive = False

    _press(fake, button=True, trigger=True, grip=True)
    source.poll()

    assert fired == []
    assert source.active is False
