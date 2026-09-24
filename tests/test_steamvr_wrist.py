"""The wrist panel: shown and hidden by a double twist of the wrist, worked
with the other controller's ray and trigger, with its own laser and cursor,
never by taking the controllers from the game."""

from __future__ import annotations

import math
import sys
import types

import pytest

from src.core import steamvr_wrist
from src.core.steamvr_wrist import (
    LASER_ASPECT,
    LASER_OVERLAY_KEY,
    SteamVRWristPanel,
    compose,
    laser_transform,
    local_transform,
    ray_from_pose,
)


class _Matrix:
    def __init__(self, rows=None):
        self.rows = [list(r) for r in rows] if rows else [[0.0] * 4 for _ in range(3)]

    def __getitem__(self, r):
        return self.rows[r]


class _Vec:
    def __init__(self, n):
        self.v = [0.0] * n


class _Params:
    def __init__(self):
        self.vSource = _Vec(3)
        self.vDirection = _Vec(3)
        self.eOrigin = 0


class _Pose:
    def __init__(self, rows, valid=True):
        self.mDeviceToAbsoluteTracking = _Matrix(rows)
        self.bPoseIsValid = valid


def _identity(x=0.0, y=0.0, z=0.0):
    return [[1, 0, 0, x], [0, 1, 0, y], [0, 0, 1, z]]


def _rolled(degrees: float):
    """A rotation about the controller's own forward axis."""

    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


def _hand(roll_degrees: float, x=0.0, y=1.4, z=-0.3):
    """A left-hand pose, pointing ahead, rolled about its own axis."""

    return compose(_identity(x, y, z), _rolled(roll_degrees))


class _Overlay:
    def __init__(self):
        self.calls: list[tuple] = []
        self.hit: tuple[float, float] | None = None
        self.handles = 0

    def createOverlay(self, key, name):
        self.handles += 1
        self.calls.append(("create", key, self.handles))
        return self.handles

    def computeOverlayIntersection(self, handle, params):
        self.calls.append(("intersect", tuple(params.vSource.v), tuple(params.vDirection.v)))
        results = types.SimpleNamespace(vUVs=_Vec(2), fDistance=0.0)
        if self.hit is None:
            return False, results
        results.vUVs.v = [self.hit[0], self.hit[1]]
        results.fDistance = 0.4
        return True, results

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
            return 0

        return record


class _System:
    def __init__(self):
        self.poses = {0: _Pose(_identity(0.0, 1.6, 0.0)), 1: None, 2: None}
        self.roles = {1: 1, 2: 2}  # device 1 = left hand, device 2 = right hand

    def getTrackedDeviceIndexForControllerRole(self, role):
        for index, r in self.roles.items():
            if r == role:
                return index
        return 0xFFFFFFFF

    def getDeviceToAbsoluteTrackingPose(self, origin, seconds, count):
        return [self.poses.get(i) or _Pose(_identity(), valid=False) for i in range(count)]


class _FakeOpenVR(types.ModuleType):
    TrackingUniverseStanding = 1
    TrackedControllerRole_LeftHand = 1
    TrackedControllerRole_RightHand = 2
    k_unTrackedDeviceIndexInvalid = 0xFFFFFFFF
    k_unTrackedDeviceIndex_Hmd = 0
    k_unMaxTrackedDeviceCount = 4
    HmdMatrix34_t = _Matrix
    VROverlayIntersectionParams_t = _Params

    def __init__(self):
        super().__init__("openvr")
        self.overlay = _Overlay()
        self.system = _System()

    def IVROverlay(self):
        return self.overlay

    def VRSystem(self):
        return self.system


class _Panel:
    size = (1000, 500)

    def __init__(self):
        self.state: dict = {}
        self.cursors: list = []

    def set_state(self, state):
        changed = state != self.state
        self.state = dict(state)
        return changed

    def hit_test(self, x, y):
        return "toggle_listen" if x < 500 else None

    def render_image(self, *, hover=None, pressed=None, cursor=None, dwell=0.0):
        self.cursors.append(cursor)
        self.dwell = dwell
        return types.SimpleNamespace(isNull=lambda: False, width=lambda: 1000, height=lambda: 500)


@pytest.fixture
def fake_openvr(monkeypatch):
    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    # No GL in tests: the raw path records setOverlayRaw instead.
    monkeypatch.setattr(
        steamvr_wrist.OverlayTextureUploader, "upload_image", lambda self, image: False, raising=False
    )
    return module


def _twist_twice(wrist, system, start=10.0):
    """Roll the wrist twice within the window."""

    for step, roll in enumerate((0.0, 70.0, 0.0, 70.0, 0.0)):
        system.poses[1] = _Pose(_hand(roll))
        wrist.poll(now=start + step * 0.2)


class TestGeometry:
    def test_compose_applies_the_controller_pose_to_the_local_offset(self):
        rows = compose(_identity(1.0, 2.0, 3.0), local_transform())

        assert rows[0][3] == pytest.approx(1.0)
        assert rows[1][3] == pytest.approx(2.02)
        assert rows[2][3] == pytest.approx(3.12)

    def test_the_panel_sits_over_the_wrist_facing_out_of_the_hand(self):
        local = local_transform()

        # Behind the grip, toward the wrist (+Z), not beyond the fingertips.
        assert local[2][3] > 0
        # Its face (local +Z column) points out of the back of the hand (+Y).
        assert (local[0][2], local[1][2], local[2][2]) == pytest.approx((0.0, 1.0, 0.0))
        # Its top (local +Y column) points toward the fingers (-Z).
        assert (local[0][1], local[1][1], local[2][1]) == pytest.approx((0.0, 0.0, -1.0))

    def test_a_ray_from_a_pose_runs_along_minus_z(self):
        origin, direction = ray_from_pose(_identity(1.0, 1.0, 1.0))

        assert origin == (1.0, 1.0, 1.0)
        assert direction == (-0.0, -0.0, -1.0)

    def test_the_laser_quad_lies_along_the_ray_and_faces_the_head(self):
        rows = laser_transform((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), 2.0, (0.0, 1.6, 0.0))

        # Centre half way along the beam; +Y column down the ray.
        assert (rows[0][3], rows[1][3], rows[2][3]) == pytest.approx((0.0, 1.0, -1.0))
        assert (rows[0][1], rows[1][1], rows[2][1]) == pytest.approx((0.0, 0.0, -1.0))
        # +Z column toward the head, perpendicular to the beam: straight up.
        assert (rows[0][2], rows[1][2], rows[2][2]) == pytest.approx((0.0, 1.0, 0.0))


class TestWristPanel:
    def _panel(self, fake_openvr, actions=None):
        panel = _Panel()
        sink = actions if actions is not None else []
        wrist = SteamVRWristPanel(lambda: panel, hand="left", on_action=sink.append)
        assert wrist.start()
        return wrist, panel

    def test_it_attaches_to_the_chosen_hand_without_taking_input(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)

        names = [c[0] for c in fake_openvr.overlay.calls]
        assert "setOverlayTransformTrackedDeviceRelative" in names
        assert "setOverlayInputMethod" not in names
        assert "setOverlayFlag" not in names
        attach = next(c for c in fake_openvr.overlay.calls if c[0] == "setOverlayTransformTrackedDeviceRelative")
        assert attach[2] == 1  # the left controller

    def test_a_double_twist_shows_it_and_another_hides_it(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)
        system = fake_openvr.system

        system.poses[1] = _Pose(_hand(0.0))
        wrist.poll(now=9.0)
        assert wrist.visible is False

        _twist_twice(wrist, system, start=10.0)
        assert wrist.visible is True
        assert ("showOverlay", 1) in fake_openvr.overlay.calls

        _twist_twice(wrist, system, start=20.0)
        assert wrist.visible is False
        assert ("hideOverlay", 1) in fake_openvr.overlay.calls

    def test_a_single_twist_or_a_slow_pair_does_nothing(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)
        system = fake_openvr.system

        for step, roll in enumerate((0.0, 70.0, 0.0)):
            system.poses[1] = _Pose(_hand(roll))
            wrist.poll(now=10.0 + step * 0.2)
        assert wrist.visible is False
        # The second twist comes too late to pair with the first.
        for step, roll in enumerate((70.0, 0.0)):
            system.poses[1] = _Pose(_hand(roll))
            wrist.poll(now=13.0 + step * 0.2)
        assert wrist.visible is False

    def test_a_twist_with_the_arm_hanging_counts_too(self, fake_openvr):
        """A hanging arm points the controller at the floor; the roll is still a roll."""

        wrist, _panel = self._panel(fake_openvr)
        system = fake_openvr.system
        hanging = [[1.0, 0.0, 0.0, 0.3], [0.0, 0.0, -1.0, 0.8], [0.0, 1.0, 0.0, 0.0]]

        for step, roll in enumerate((0.0, 70.0, 0.0, 70.0, 0.0)):
            system.poses[1] = _Pose(compose(hanging, _rolled(roll)))
            wrist.poll(now=10.0 + step * 0.2)

        assert wrist.visible is True

    def test_a_trigger_pull_on_a_button_is_a_click(self, fake_openvr):
        actions: list = []
        wrist, _panel = self._panel(fake_openvr, actions)
        _twist_twice(wrist, fake_openvr.system)
        fake_openvr.overlay.hit = (0.25, 0.5)
        ray = ((0.3, 1.5, -0.2), (0.0, -0.5, -0.8))

        wrist.poll(pointer=ray, trigger_down=False, now=12.0)
        wrist.poll(pointer=ray, trigger_down=True, now=12.1)
        assert actions == []
        wrist.poll(pointer=ray, trigger_down=False, now=12.2)

        assert actions == ["toggle_listen"]
        intersect = next(c for c in fake_openvr.overlay.calls if c[0] == "intersect")
        assert intersect[1] == pytest.approx(ray[0])

    def test_the_cursor_and_the_laser_follow_the_ray(self, fake_openvr):
        wrist, panel = self._panel(fake_openvr)
        _twist_twice(wrist, fake_openvr.system)
        fake_openvr.overlay.hit = (0.25, 0.5)
        ray = ((0.3, 1.5, -0.2), (0.0, -0.5, -0.8))

        wrist.poll(pointer=ray, trigger_down=False, now=12.0)

        # The dot: a quarter across; v counts from the bottom on the raw path.
        assert panel.cursors[-1] == pytest.approx((250.0, 250.0))
        creates = [c for c in fake_openvr.overlay.calls if c[0] == "create"]
        assert any(c[1] == LASER_OVERLAY_KEY for c in creates)
        laser_handle = next(c[2] for c in creates if c[1] == LASER_OVERLAY_KEY)
        widths = [c for c in fake_openvr.overlay.calls if c[0] == "setOverlayWidthInMeters" and c[1] == laser_handle]
        # The beam reaches the panel: 0.4 m long.
        assert widths[-1][2] == pytest.approx(0.4 / LASER_ASPECT)
        assert ("showOverlay", laser_handle) in fake_openvr.overlay.calls

    def test_the_laser_goes_when_the_panel_hides(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)
        _twist_twice(wrist, fake_openvr.system)
        ray = ((0.3, 1.5, -0.2), (0.0, -0.5, -0.8))
        wrist.poll(pointer=ray, now=12.0)
        creates = [c for c in fake_openvr.overlay.calls if c[0] == "create"]
        laser_handle = next(c[2] for c in creates if c[1] == LASER_OVERLAY_KEY)

        _twist_twice(wrist, fake_openvr.system, start=20.0)

        assert ("hideOverlay", laser_handle) in fake_openvr.overlay.calls

    def test_a_pull_that_starts_off_a_button_does_nothing(self, fake_openvr):
        actions: list = []
        wrist, _panel = self._panel(fake_openvr, actions)
        _twist_twice(wrist, fake_openvr.system)
        fake_openvr.overlay.hit = (0.75, 0.5)  # right half: no button

        wrist.poll(pointer=((0, 0, 0), (0, 0, -1)), trigger_down=True, now=12.0)
        wrist.poll(pointer=((0, 0, 0), (0, 0, -1)), trigger_down=False, now=12.1)

        assert actions == []

    def test_input_is_withheld_while_the_frame_or_the_menu_is_open(self, fake_openvr):
        actions: list = []
        wrist, _panel = self._panel(fake_openvr, actions)
        _twist_twice(wrist, fake_openvr.system)
        fake_openvr.overlay.hit = (0.25, 0.5)

        wrist.poll(pointer=((0, 0, 0), (0, 0, -1)), trigger_down=True, allow_input=False, now=12.0)
        wrist.poll(pointer=((0, 0, 0), (0, 0, -1)), trigger_down=False, allow_input=False, now=12.1)

        assert actions == []

    def test_without_a_tip_pose_the_other_controller_aims(self, fake_openvr):
        actions: list = []
        wrist, _panel = self._panel(fake_openvr, actions)
        _twist_twice(wrist, fake_openvr.system)
        fake_openvr.system.poses[2] = _Pose(_identity(0.4, 1.3, -0.1))
        fake_openvr.overlay.hit = (0.25, 0.5)

        wrist.poll(pointer=None, trigger_down=True, now=12.0)
        wrist.poll(pointer=None, trigger_down=False, now=12.1)

        assert actions == ["toggle_listen"]
        intersect = next(c for c in fake_openvr.overlay.calls if c[0] == "intersect")
        assert intersect[1] == pytest.approx((0.4, 1.3, -0.1))

    def test_a_missing_controller_hides_the_panel(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)
        _twist_twice(wrist, fake_openvr.system)
        assert wrist.visible

        fake_openvr.system.poses[1] = None
        wrist.poll(now=13.0)

        assert wrist.visible is False

    def test_toggle_by_hand(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)

        assert wrist.toggle() is True
        assert wrist.visible is True
        assert wrist.toggle() is False

    def test_stop_destroys_the_overlay(self, fake_openvr):
        wrist, _panel = self._panel(fake_openvr)

        wrist.stop()

        assert ("destroyOverlay", 1) in fake_openvr.overlay.calls
        assert wrist.available is False


class TestConfig:
    def test_the_wrist_panel_defaults_on_and_left(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config: dict = {}
        _ensure_vrc_listen_config(config, loaded={})
        wrist = config["vrc_listen"]["vr_wrist"]

        assert wrist == {"enabled": True, "hand": "left", "show_mode": "twist"}

    def test_a_bad_hand_falls_back_to_left(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config = {"vrc_listen": {"vr_wrist": {"enabled": False, "hand": "foot"}}}
        _ensure_vrc_listen_config(config, loaded=config)

        assert config["vrc_listen"]["vr_wrist"] == {"enabled": False, "hand": "left", "show_mode": "twist"}


class TestCaptureSuspend:
    """A screenshot read must not photograph the wrist panel or its laser."""

    def test_suspend_hides_panel_and_laser_and_resume_brings_them_back(self, fake_openvr):
        panel = _Panel()
        wrist = SteamVRWristPanel(lambda: panel, hand="left")
        assert wrist.start()
        _twist_twice(wrist, fake_openvr.system)
        ray = ((0.3, 1.5, -0.2), (0.0, -0.5, -0.8))
        wrist.poll(pointer=ray, now=12.0)
        creates = [c for c in fake_openvr.overlay.calls if c[0] == "create"]
        laser_handle = next(c[2] for c in creates if c[1] == LASER_OVERLAY_KEY)
        fake_openvr.overlay.calls.clear()

        wrist.suspend()

        assert wrist.suspended and not wrist.visible
        assert ("hideOverlay", 1) in fake_openvr.overlay.calls
        assert ("hideOverlay", laser_handle) in fake_openvr.overlay.calls

        # The poll that runs while the picture is taken must not bring it back.
        wrist.poll(pointer=ray, now=12.1)
        assert ("showOverlay", 1) not in fake_openvr.overlay.calls
        assert not wrist.visible

        wrist.resume()
        assert wrist.visible
        assert ("showOverlay", 1) in fake_openvr.overlay.calls

    def test_a_panel_that_was_hidden_stays_hidden_after_resume(self, fake_openvr):
        wrist = SteamVRWristPanel(lambda: _Panel(), hand="left")
        assert wrist.start()

        wrist.suspend()
        wrist.resume()

        assert not wrist.visible
