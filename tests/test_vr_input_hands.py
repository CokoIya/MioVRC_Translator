"""Per-hand buttons, push-to-talk, vibration and the binding page."""

from __future__ import annotations

import ctypes
import json
import sys
import types
from pathlib import Path

import pytest

from src.core import vr_input
from src.core.vr_input import (
    APP_KEY,
    HAND_SOURCES,
    HAPTIC_ACTION,
    HOLD_GRIP_ACTION,
    HOLD_TRIGGER_ACTION,
    PUSH_TO_TALK_ACTION,
    TRANSLATE_ACTION,
    VRActionInput,
)


class _Data:
    def __init__(self, active=True, state=False):
        self.bActive = active
        self.bState = state


class _Input:
    """Actions with per-source state: ``states[(action, source)]``."""

    def __init__(self):
        self.actions = [TRANSLATE_ACTION, HOLD_TRIGGER_ACTION, HOLD_GRIP_ACTION, PUSH_TO_TALK_ACTION, HAPTIC_ACTION]
        self.handles = {name: index + 1 for index, name in enumerate(self.actions)}
        self.sources = {path: 100 + index for index, path in enumerate(HAND_SOURCES.values())}
        self.states: dict[tuple[str, int], _Data] = {}
        self.vibrations: list = []
        self.binding_ui: list = []

    def setActionManifestPath(self, path):
        return None

    def getActionSetHandle(self, name):
        return 99

    def getActionHandle(self, name):
        return self.handles[name]

    def getInputSourceHandle(self, path):
        return self.sources[path]

    def updateActionState(self, sets):
        return None

    def getDigitalActionData(self, handle, restrict):
        name = next(n for n, h in self.handles.items() if h == handle)
        return self.states.get((name, restrict), _Data(active=True, state=False))

    def triggerHapticVibrationAction(self, handle, start, seconds, frequency, amplitude, source):
        self.vibrations.append((handle, seconds, frequency, amplitude, source))

    def openBindingUI(self, app_key, action_set, device, show_on_desktop):
        self.binding_ui.append((app_key, action_set, show_on_desktop))


class _System:
    def __init__(self):
        self.pulses: list = []

    def getTrackedDeviceIndexForControllerRole(self, role):
        return {1: 3, 2: 4}.get(role, 0xFFFFFFFF)

    def triggerHapticPulse(self, index, axis, micros):
        self.pulses.append((index, micros))


@pytest.fixture
def hands_input(monkeypatch, tmp_path):
    module = types.ModuleType("openvr")
    fake = _Input()
    system = _System()
    module.VRInput = lambda: fake
    module.VRSystem = lambda: system
    module.k_ulInvalidInputValueHandle = 0
    module.k_unTrackedDeviceIndexInvalid = 0xFFFFFFFF
    module.TrackedControllerRole_LeftHand = 1
    module.TrackedControllerRole_RightHand = 2

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
    source = VRActionInput()
    assert source.start()
    return source, fake, system


def test_each_hand_reports_its_own_trigger_and_grip(hands_input):
    source, fake, _system = hands_input
    right = fake.sources[HAND_SOURCES["right"]]
    fake.states[(HOLD_GRIP_ACTION, right)] = _Data(state=True)
    fake.states[(HOLD_TRIGGER_ACTION, right)] = _Data(state=True)

    source.poll()

    assert source.hand("right").grip is True
    assert source.hand("right").trigger is True
    assert source.hand("left").grip is False
    assert source.hand("LEFT").trigger is False


def test_an_unbound_hand_reads_as_unknown(hands_input):
    source, fake, _system = hands_input
    left = fake.sources[HAND_SOURCES["left"]]
    fake.states[(HOLD_GRIP_ACTION, left)] = _Data(active=False)

    source.poll()

    assert source.hand("left").grip is None


def test_push_to_talk_is_unknown_until_bound(hands_input):
    source, fake, _system = hands_input
    fake.states[(PUSH_TO_TALK_ACTION, 0)] = _Data(active=False)
    source.poll()
    assert source.push_to_talk is None

    fake.states[(PUSH_TO_TALK_ACTION, 0)] = _Data(active=True, state=True)
    source.poll()
    assert source.push_to_talk is True


def test_a_pulse_uses_the_haptic_action_once_bindings_are_live(hands_input):
    source, fake, system = hands_input
    fake.states[(TRANSLATE_ACTION, 0)] = _Data(active=True)
    source.poll()

    assert source.pulse("right", seconds=0.02, amplitude=0.4)

    handle, seconds, _frequency, amplitude, target = fake.vibrations[-1]
    assert handle == fake.handles[HAPTIC_ACTION]
    assert target == fake.sources[HAND_SOURCES["right"]]
    assert (seconds, amplitude) == (0.02, 0.4)
    assert system.pulses == []


def test_a_pulse_falls_back_to_the_device_when_bindings_never_went_live(hands_input):
    source, fake, system = hands_input
    fake.states[(TRANSLATE_ACTION, 0)] = _Data(active=False)
    source.poll()

    assert source.pulse("left", seconds=0.01, amplitude=1.0)

    assert fake.vibrations == []
    assert system.pulses == [(3, 3999)]


def test_the_binding_page_opens_for_mio(hands_input):
    source, fake, _system = hands_input

    assert source.open_binding_ui()

    assert fake.binding_ui == [(APP_KEY, 99, True)]


def test_stop_forgets_the_hands(hands_input):
    source, fake, _system = hands_input
    fake.states[(HOLD_GRIP_ACTION, fake.sources[HAND_SOURCES["left"]])] = _Data(state=True)
    source.poll()

    source.stop()

    assert source.hand("left").grip is None
    assert source.push_to_talk is None
    assert not source.pulse("left")
    assert not source.open_binding_ui()


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "openvr"


def test_the_manifest_declares_talk_and_vibration():
    manifest = json.loads((ASSETS / "action_manifest.json").read_text(encoding="utf-8"))
    actions = {action["name"]: action["type"] for action in manifest["actions"]}

    assert actions[PUSH_TO_TALK_ACTION] == "boolean"
    assert actions[HAPTIC_ACTION] == "vibration"


@pytest.mark.parametrize("name", ["binding_generic.json", "binding_knuckles.json", "binding_oculus_touch.json", "binding_pico.json", "binding_vive.json"])
def test_every_binding_vibrates_both_hands_and_leaves_talk_unbound(name):
    binding = json.loads((ASSETS / name).read_text(encoding="utf-8"))
    mio = binding["bindings"]["/actions/mio"]

    haptics = {(entry["output"], entry["path"]) for entry in mio.get("haptics", [])}
    assert (HAPTIC_ACTION, "/user/hand/left/output/haptic") in haptics
    assert (HAPTIC_ACTION, "/user/hand/right/output/haptic") in haptics
    outputs = json.dumps(mio.get("sources", []))
    # Every obvious button already belongs to the game.
    assert PUSH_TO_TALK_ACTION not in outputs
