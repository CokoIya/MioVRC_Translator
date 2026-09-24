"""How the wrist panel comes up: a double twist, a look at the wrist, or always."""

from __future__ import annotations

import math
import sys

import pytest

from src.core import steamvr_wrist
from src.core.steamvr_wrist import (
    LOOK_HIDE_SECONDS,
    LOOK_SHOW_SECONDS,
    SteamVRWristPanel,
    normalize_show_mode,
    wrist_is_looked_at,
)
from tests.test_steamvr_wrist import _FakeOpenVR, _Panel, _Pose, _identity, _twist_twice


@pytest.fixture
def fake_openvr(monkeypatch):
    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    monkeypatch.setattr(
        steamvr_wrist.OverlayTextureUploader, "upload_image", lambda self, image: False, raising=False
    )
    return module


def _pitched(degrees, x=0.0, y=0.0, z=0.0):
    """Rotated about +X: positive tips -Z (forward) down toward -Y."""

    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[1.0, 0.0, 0.0, x], [0.0, c, s, y], [0.0, -s, c, z]]


def _raised_wrist():
    """A left controller held up with the back of the hand toward the eyes."""

    c, s = math.cos(math.radians(49.5)), math.sin(math.radians(49.5))
    return [[1.0, 0.0, 0.0, 0.0], [0.0, c, -s, 1.3], [0.0, s, c, -0.35]]


HEAD_LOOKING_DOWN = _pitched(56, 0.0, 1.6, 0.0)
# The arm hanging at the side: the back of the hand faces outward.
HANGING = [[0.0, -1.0, 0.0, -0.3], [1.0, 0.0, 0.0, 0.9], [0.0, 0.0, 1.0, 0.0]]


class TestLookGeometry:
    def test_looking_at_a_raised_wrist_counts(self):
        assert wrist_is_looked_at(
            HEAD_LOOKING_DOWN, _raised_wrist(), facing_degrees=45, gaze_degrees=30, max_distance=0.7
        )

    def test_a_wrist_out_of_sight_does_not(self):
        straight_ahead = _identity(0.0, 1.6, 0.0)

        assert not wrist_is_looked_at(
            straight_ahead, _raised_wrist(), facing_degrees=45, gaze_degrees=30, max_distance=0.7
        )

    def test_a_hand_hanging_down_does_not(self):
        assert not wrist_is_looked_at(
            HEAD_LOOKING_DOWN, HANGING, facing_degrees=45, gaze_degrees=30, max_distance=0.7
        )

    def test_unknown_modes_mean_the_twist(self):
        assert normalize_show_mode("LOOK") == "look"
        assert normalize_show_mode("nonsense") == "twist"
        assert normalize_show_mode(None) == "twist"


class TestModes:
    def test_look_shows_while_looked_at_and_hides_after(self, fake_openvr):
        toggles: list = []
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left", show_mode="look", on_toggle=toggles.append)
        assert wrist.start()
        system = fake_openvr.system
        system.poses[0] = _Pose(HEAD_LOOKING_DOWN)
        system.poses[1] = _Pose(_raised_wrist())

        wrist.poll(now=10.0)
        assert not wrist.visible  # a glance is not a look yet
        wrist.poll(now=10.0 + LOOK_SHOW_SECONDS + 0.01)
        assert wrist.visible

        system.poses[1] = _Pose(HANGING)
        wrist.poll(now=11.0)
        assert wrist.visible  # a moment's drift does not flicker it
        wrist.poll(now=11.0 + LOOK_HIDE_SECONDS + 0.01)
        assert not wrist.visible
        assert toggles == [True, False]

    def test_look_ignores_the_twist(self, fake_openvr):
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left", show_mode="look")
        assert wrist.start()

        _twist_twice(wrist, fake_openvr.system)

        assert not wrist.visible

    def test_always_is_always_up(self, fake_openvr):
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left", show_mode="always")
        assert wrist.start()
        fake_openvr.system.poses[1] = _Pose(_identity(0.0, 1.0, -0.3))

        wrist.poll(now=1.0)

        assert wrist.visible

    def test_twist_still_toggles_and_reports_it(self, fake_openvr):
        toggles: list = []
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left", on_toggle=toggles.append)
        assert wrist.start()

        _twist_twice(wrist, fake_openvr.system)

        assert wrist.visible
        assert toggles == [True]

    def test_switching_modes_at_runtime(self, fake_openvr):
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left")
        assert wrist.start()
        fake_openvr.system.poses[1] = _Pose(_identity(0.0, 1.0, -0.3))

        wrist.set_show_mode("always")
        assert wrist.show_mode == "always" and wrist.visible
        wrist.set_show_mode("twist")
        assert not wrist.visible

    def test_the_laser_hand_and_whether_it_is_on_the_panel(self, fake_openvr):
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left", show_mode="always")
        assert wrist.start()
        fake_openvr.system.poses[1] = _Pose(_identity(0.0, 1.0, -0.3))

        wrist.poll(now=1.0, pointer=((0.3, 1.2, -0.2), (0.0, -0.5, -0.8)))
        assert wrist.pointing_hand == "right"
        assert not wrist.pointer_on_panel

        fake_openvr.overlay.hit = (0.2, 0.5)
        wrist.poll(now=1.1, pointer=((0.3, 1.2, -0.2), (0.0, -0.5, -0.8)))
        assert wrist.pointer_on_panel


@pytest.mark.parametrize("mode", ["twist", "look", "always"])
def test_every_mode_survives_config_normalisation(mode):
    from src.utils.config_manager import _ensure_vrc_listen_config

    config = {"vrc_listen": {"vr_wrist": {"show_mode": mode}}}
    _ensure_vrc_listen_config(config)

    assert config["vrc_listen"]["vr_wrist"]["show_mode"] == mode
