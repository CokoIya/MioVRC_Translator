"""Trigger and grip kept from the game while Mio uses them; pull thresholds; swap."""

from __future__ import annotations

import ctypes
import json
import sys
import types
from pathlib import Path

import pytest

from src.core import vr_input
from src.core.vr_input import (
    BLOCK_ACTION_SET,
    BLOCK_GRIP_ACTION,
    BLOCK_GRIP_VALUE_ACTION,
    BLOCK_TRIGGER_ACTION,
    BLOCK_TRIGGER_VALUE_ACTION,
    GRIP_VALUE_ACTION,
    HAND_SOURCES,
    HOLD_GRIP_ACTION,
    HOLD_TRIGGER_ACTION,
    OVERLAY_GLOBAL_PRIORITY,
    TRANSLATE_ACTION,
    TRIGGER_VALUE_ACTION,
    VRActionInput,
    pressed,
)


class _Digital:
    def __init__(self, active=True, state=False):
        self.bActive = active
        self.bState = state


class _Analog:
    def __init__(self, active=True, x=0.0):
        self.bActive = active
        self.x = x


class _Input:
    def __init__(self):
        self.actions = [
            TRANSLATE_ACTION,
            HOLD_TRIGGER_ACTION,
            HOLD_GRIP_ACTION,
            TRIGGER_VALUE_ACTION,
            GRIP_VALUE_ACTION,
            BLOCK_TRIGGER_ACTION,
            BLOCK_GRIP_ACTION,
            BLOCK_TRIGGER_VALUE_ACTION,
            BLOCK_GRIP_VALUE_ACTION,
        ]
        self.handles = {name: index + 1 for index, name in enumerate(self.actions)}
        self.sets = {"/actions/mio": 99, BLOCK_ACTION_SET: 77}
        self.sources = {path: 100 + index for index, path in enumerate(HAND_SOURCES.values())}
        self.digital: dict = {}
        self.analog: dict = {}
        self.updates: list = []

    def setActionManifestPath(self, path):
        return None

    def getActionSetHandle(self, name):
        return self.sets[name]

    def getActionHandle(self, name):
        return self.handles[name]

    def getInputSourceHandle(self, path):
        return self.sources[path]

    def updateActionState(self, sets):
        self.updates.append(
            [(entry.ulActionSet, entry.ulRestrictedToDevice, entry.nPriority) for entry in sets]
        )

    def _name(self, handle):
        return next(name for name, value in self.handles.items() if value == handle)

    def getDigitalActionData(self, handle, source):
        return self.digital.get((self._name(handle), source), _Digital(state=False))

    def getAnalogActionData(self, handle, source):
        return self.analog.get((self._name(handle), source), _Analog(active=False))


class _Set(ctypes.Structure):
    _fields_ = [
        ("ulActionSet", ctypes.c_ulonglong),
        ("ulRestrictedToDevice", ctypes.c_ulonglong),
        ("ulSecondaryActionSet", ctypes.c_ulonglong),
        ("unPadding", ctypes.c_ulong),
        ("nPriority", ctypes.c_long),
    ]


@pytest.fixture
def blocking_input(monkeypatch, tmp_path):
    module = types.ModuleType("openvr")
    fake = _Input()
    module.VRInput = lambda: fake
    module.k_ulInvalidInputValueHandle = 0
    module.VRActiveActionSet_t = _Set
    monkeypatch.setitem(sys.modules, "openvr", module)
    manifest = tmp_path / "action_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(vr_input, "manifest_path", lambda: str(manifest))
    source = VRActionInput()
    assert source.start()
    return source, fake


def _source(fake, hand):
    return fake.sources[HAND_SOURCES[hand]]


def test_a_value_past_the_threshold_counts_and_a_click_is_the_fallback():
    assert pressed(0.6, False, 0.5) is True
    assert pressed(0.3, True, 0.5) is False
    assert pressed(None, True, 0.5) is True
    assert pressed(None, None, 0.5) is None


def test_nothing_is_blocked_by_default(blocking_input):
    source, fake = blocking_input

    source.poll()

    assert fake.updates[-1] == [(99, 0, 0)]
    assert source.blocking == frozenset()


def test_a_blocked_hand_gets_the_blocking_set_at_overlay_priority(blocking_input):
    source, fake = blocking_input
    source.set_blocking({"right", "nonsense"})

    source.poll()

    assert fake.updates[-1] == [(99, 0, 0), (77, _source(fake, "right"), OVERLAY_GLOBAL_PRIORITY)]
    assert source.blocking == frozenset({"right"})

    source.set_blocking(set())
    source.poll()
    assert fake.updates[-1] == [(99, 0, 0)]


def test_a_blocked_hand_is_read_from_the_blocking_set(blocking_input):
    source, fake = blocking_input
    right = _source(fake, "right")
    # SteamVR suppresses Mio's own main-set binding on that hand while it is
    # blocked: the main set reads released, the blocking set reads held.
    fake.digital[(BLOCK_GRIP_ACTION, right)] = _Digital(state=True)
    fake.digital[(BLOCK_TRIGGER_ACTION, right)] = _Digital(state=True)
    source.set_blocking({"right"})

    source.poll()

    assert source.hand("right").grip is True
    assert source.hand("right").trigger is True
    assert source.trigger_down is True


def test_the_pull_threshold_decides_before_the_click(blocking_input):
    source, fake = blocking_input
    left = _source(fake, "left")
    fake.analog[(GRIP_VALUE_ACTION, left)] = _Analog(x=0.45)
    fake.digital[(HOLD_GRIP_ACTION, left)] = _Digital(state=False)

    source.grip_threshold = 0.4
    source.poll()
    assert source.hand("left").grip is True

    source.grip_threshold = 0.6
    source.poll()
    assert source.hand("left").grip is False


def test_a_blocked_hand_uses_the_blocking_sets_values(blocking_input):
    source, fake = blocking_input
    left = _source(fake, "left")
    fake.analog[(BLOCK_TRIGGER_VALUE_ACTION, left)] = _Analog(x=0.9)
    fake.analog[(BLOCK_GRIP_VALUE_ACTION, left)] = _Analog(x=0.1)
    source.set_blocking({"left"})

    source.poll()

    assert source.hand("left").trigger is True
    assert source.hand("left").grip is False


def test_swapping_trigger_and_grip(blocking_input):
    source, fake = blocking_input
    right = _source(fake, "right")
    fake.digital[(HOLD_TRIGGER_ACTION, right)] = _Digital(state=True)
    source.swap_trigger_grip = True

    source.poll()

    assert source.hand("right").grip is True
    assert source.hand("right").trigger is False


def test_a_manifest_without_the_blocking_set_just_does_not_block(monkeypatch, blocking_input):
    source, fake = blocking_input
    del fake.sets[BLOCK_ACTION_SET]
    source.stop()
    assert source.start()
    source.set_blocking({"left", "right"})

    source.poll()

    assert fake.updates[-1] == [(99, 0, 0)]
    assert source.blocking == frozenset()


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "openvr"
BINDINGS = ["binding_generic.json", "binding_knuckles.json", "binding_oculus_touch.json", "binding_pico.json", "binding_vive.json"]


def test_the_manifest_declares_the_blocking_set_and_the_pull_values():
    manifest = json.loads((ASSETS / "action_manifest.json").read_text(encoding="utf-8"))
    actions = {action["name"]: action["type"] for action in manifest["actions"]}

    assert {entry["name"] for entry in manifest["action_sets"]} >= {"/actions/mio", BLOCK_ACTION_SET}
    for name in (TRIGGER_VALUE_ACTION, GRIP_VALUE_ACTION, BLOCK_TRIGGER_VALUE_ACTION, BLOCK_GRIP_VALUE_ACTION):
        assert actions[name] == "vector1"
    for name in (BLOCK_TRIGGER_ACTION, BLOCK_GRIP_ACTION):
        assert actions[name] == "boolean"


def _outputs(sources, path):
    found = set()
    for entry in sources:
        if entry["path"] == path:
            found.update(value["output"] for value in entry["inputs"].values())
    return found


@pytest.mark.parametrize("name", BINDINGS)
def test_every_binding_covers_what_it_reads_in_both_sets(name):
    binding = json.loads((ASSETS / name).read_text(encoding="utf-8"))
    main = binding["bindings"]["/actions/mio"]["sources"]
    block = binding["bindings"][BLOCK_ACTION_SET]["sources"]

    for hand in ("left", "right"):
        trigger = f"/user/hand/{hand}/input/trigger"
        assert _outputs(main, trigger) >= {HOLD_TRIGGER_ACTION, TRIGGER_VALUE_ACTION}
        assert _outputs(block, trigger) >= {BLOCK_TRIGGER_ACTION, BLOCK_TRIGGER_VALUE_ACTION}
        grip = f"/user/hand/{hand}/input/grip"
        if HOLD_GRIP_ACTION in _outputs(main, grip):
            # Whatever the main set reads from the grip, the blocking set
            # reads too, or a blocked hand would lose its grip.
            assert BLOCK_GRIP_ACTION in _outputs(block, grip)
            if GRIP_VALUE_ACTION in _outputs(main, grip):
                assert BLOCK_GRIP_VALUE_ACTION in _outputs(block, grip)
    # Nothing but trigger and grip is ever taken from the game.
    for entry in block:
        assert entry["path"].endswith(("/input/trigger", "/input/grip"))
