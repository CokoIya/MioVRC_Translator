# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Controller gestures that need no button binding.

*The ear.* Bring a controller up to your ear and pull the trigger briefly:
the capture frame opens. It needs only the trigger, which every controller
has and which SteamVR delivers reliably, so it works on a headset whose B/Y
bindings never activate, as a PICO 4S through vrlink did. A pull that lasts
longer is left alone - the player asked for no long-press start/stop.

*The twist.* Roll the wrist twice and the wrist panel shows or hides. A roll
turns the controller about its own forward axis, so it is measured as the
angle the sideways axis has turned *about the resting forward axis* - the
projection of the sideways axis onto the plane the forward axis pierces.
Swinging the whole arm barely moves that projection, and the measure is
the same whether the arm hangs at the side, points ahead or is held up to
the face. The angle is unwrapped tick to tick, so a hand turned nearly
palm-up (near 180 degrees) does not flip between +170 and -170 and read as
twist after twist - the first live run toggled the panel on its own.

Both state machines are pure so they can be tested to the tick; where the
controllers are comes from :func:`controller_near_head`.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

# A controller this close to the centre of the head is at the ear. A hand
# held up to the temple measures about 0.15 m; a hand pointing at the world
# is 0.4 m or more away.
NEAR_HEAD_METERS = 0.24
SHORT_PRESS_MAX_SECONDS = 0.5

# A twist is the wrist rolling at least this far from where it rested and
# then either coming back to within the off angle or rolling straight
# through to the other side. There is no upper limit: any roll past the on
# angle counts, however far it goes. Two twists inside the window toggle.
TWIST_ON_DEGREES = 30.0
TWIST_OFF_DEGREES = 15.0
TWIST_WINDOW_SECONDS = 2.5
TWISTS_TO_TOGGLE = 2
# How quickly the resting orientation follows a hand that settles at a new
# angle: a fraction of the way per tick (about 1 s at 30 ticks a second).
TWIST_REST_FOLLOW = 0.03
# The forward axis swung this far: the hand went somewhere else, start over.
# Generous because the wrist turns about the forearm, which sits 30-50
# degrees off the controller's own axis, so a big twist swings the forward
# axis by 60-80 degrees without the arm going anywhere.
TWIST_RESET_DEGREES = 85.0
# A hand held rolled this long is resting at a new angle, not mid-twist.
TWIST_HOLD_RESET_SECONDS = 1.5

Vector = tuple[float, float, float]


def _distance(a: Vector, b: Vector) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def _unit(v: Sequence[float]) -> Vector | None:
    try:
        x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    except (TypeError, ValueError, IndexError):
        return None
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-6:
        return None
    return (x / length, y / length, z / length)


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _angle_degrees(a: Vector, b: Vector) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(a, b)))))


def _perpendicular(v: Vector, axis: Vector) -> Vector | None:
    """``v`` with its component along ``axis`` removed, as a unit vector."""

    along = _dot(v, axis)
    return _unit((v[0] - axis[0] * along, v[1] - axis[1] * along, v[2] - axis[2] * along))


def _blend(rest: Vector, toward: Vector, weight: float) -> Vector:
    mixed = _unit(tuple(rest[i] * (1.0 - weight) + toward[i] * weight for i in range(3)))
    return mixed if mixed is not None else rest


def _wrap_degrees(value: float) -> float:
    """Fold an angle difference into (-180, 180]."""

    while value > 180.0:
        value -= 360.0
    while value <= -180.0:
        value += 360.0
    return value


def controller_axes(rows: Sequence[Sequence[float]]) -> tuple[Vector, Vector]:
    """A controller pose's sideways (+X) and forward (+Z) axes, as columns."""

    return (
        (float(rows[0][0]), float(rows[1][0]), float(rows[2][0])),
        (float(rows[0][2]), float(rows[1][2]), float(rows[2][2])),
    )


def near_head(head: Vector, controllers: Sequence[Vector], max_distance: float = NEAR_HEAD_METERS) -> bool:
    """True when any controller is within ``max_distance`` of the head."""

    return any(_distance(head, position) <= max_distance for position in controllers)


def controller_near_head(openvr: Any, max_distance: float = NEAR_HEAD_METERS) -> bool | None:
    """Ask the runtime whether a controller is at the ear; None when it cannot say."""

    try:
        system = openvr.VRSystem()
        poses = system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, openvr.k_unMaxTrackedDeviceCount
        )
        head_pose = poses[openvr.k_unTrackedDeviceIndex_Hmd]
        if not bool(getattr(head_pose, "bPoseIsValid", False)):
            return None
        m = head_pose.mDeviceToAbsoluteTracking
        head = (float(m[0][3]), float(m[1][3]), float(m[2][3]))
        controllers: list[Vector] = []
        for role in (openvr.TrackedControllerRole_LeftHand, openvr.TrackedControllerRole_RightHand):
            index = system.getTrackedDeviceIndexForControllerRole(role)
            if index == openvr.k_unTrackedDeviceIndexInvalid or index < 0 or index >= len(poses):
                continue
            pose = poses[index]
            if not bool(getattr(pose, "bPoseIsValid", False)):
                continue
            c = pose.mDeviceToAbsoluteTracking
            controllers.append((float(c[0][3]), float(c[1][3]), float(c[2][3])))
    except Exception:
        logger.debug("Could not read controller positions for the ear gesture", exc_info=True)
        return None
    if not controllers:
        return None
    return near_head(head, controllers, max_distance)


class EarGesture:
    """Turns ticks of (at the ear, trigger down) into short presses.

    A press only counts when it began at the ear; the hand may drift a
    little while the trigger is held. It fires on release, and only if the
    trigger came back up quickly - a long pull is the game's business.
    """

    def __init__(self) -> None:
        self._down_at: float | None = None
        self._armed = False

    @property
    def holding(self) -> bool:
        return self._down_at is not None and self._armed

    def update(self, *, near_ear: bool | None, trigger_down: bool | None, now: float) -> str | None:
        down = bool(trigger_down)
        if down and self._down_at is None:
            self._down_at = now
            self._armed = bool(near_ear)
            return None
        if down or self._down_at is None:
            return None
        held = now - self._down_at
        armed = self._armed
        self._down_at = None
        self._armed = False
        if armed and held <= SHORT_PRESS_MAX_SECONDS:
            return "short"
        return None


class TwistDetector:
    """Two rolls of the wrist inside a short window.

    Fed the controller's sideways and forward axes every tick (see
    :func:`controller_axes`), it remembers where they rest and measures the
    roll: how far the sideways axis has turned about the resting forward
    axis, unwrapped so it runs past 180 degrees rather than flipping sign.
    A twist is a roll out past the on angle that comes back (or carries on
    through to the other side); the second twist inside the window fires.
    ``count`` says how many twists are banked, ``roll`` the current unwrapped
    roll and ``last_peak`` how far the last counted twist went, for logging.
    """

    def __init__(self) -> None:
        self._rest_side: Vector | None = None
        self._rest_forward: Vector | None = None
        self._sign = 0
        self._twisted_at = 0.0
        self._returns: list[float] = []
        self._raw_last = 0.0
        self._peak = 0.0
        self.roll = 0.0
        self.last_peak = 0.0

    @property
    def count(self) -> int:
        return len(self._returns)

    def reset(self) -> None:
        self._rest_side = None
        self._rest_forward = None
        self._sign = 0
        self._returns = []
        self._raw_last = 0.0
        self._peak = 0.0
        self.roll = 0.0

    def _rest_from(self, side: Vector, ahead: Vector) -> None:
        self._rest_forward = ahead
        self._rest_side = _perpendicular(side, ahead) or side
        self._sign = 0
        self._raw_last = 0.0
        self._peak = 0.0
        self.roll = 0.0

    def _roll_of(self, side: Vector) -> float:
        """Signed degrees the sideways axis has turned about the resting forward axis."""

        rz, rx = self._rest_forward, self._rest_side
        assert rz is not None and rx is not None
        ry = _cross(rz, rx)
        along = _dot(side, rz)
        flat = (side[0] - rz[0] * along, side[1] - rz[1] * along, side[2] - rz[2] * along)
        if math.sqrt(_dot(flat, flat)) < 0.2:
            # The sideways axis points down the resting forward axis: no
            # roll can be read from it; keep what we had.
            return self._raw_last
        return math.degrees(math.atan2(_dot(flat, ry), _dot(flat, rx)))

    def update(self, sideways: Sequence[float], forward: Sequence[float], now: float) -> bool:
        side = _unit(sideways)
        ahead = _unit(forward)
        if side is None or ahead is None:
            return False
        if self._rest_side is None or self._rest_forward is None:
            self._rest_from(side, ahead)
            return False
        if _angle_degrees(ahead, self._rest_forward) > TWIST_RESET_DEGREES:
            # The arm swung to point somewhere else: start over from there.
            self._rest_from(side, ahead)
            self._returns = []
            return False
        raw = self._roll_of(side)
        self.roll += _wrap_degrees(raw - self._raw_last)
        self._raw_last = raw
        roll = self.roll
        magnitude = abs(roll)
        if self._sign == 0:
            if magnitude >= TWIST_ON_DEGREES:
                self._sign = 1 if roll > 0 else -1
                self._twisted_at = now
                self._peak = magnitude
                return False
            # At rest the baseline follows slowly, so a hand held at a new
            # angle does not count as half a twist forever.
            self._rest_forward = _blend(self._rest_forward, ahead, TWIST_REST_FOLLOW)
            self._rest_side = _perpendicular(
                _blend(self._rest_side, side, TWIST_REST_FOLLOW), self._rest_forward
            ) or self._rest_side
            return False
        self._peak = max(self._peak, magnitude)
        crossed = roll * self._sign < 0
        if magnitude > TWIST_OFF_DEGREES and not crossed:
            if now - self._twisted_at > TWIST_HOLD_RESET_SECONDS:
                # Held rolled: that is the new rest, not a twist in progress.
                self._rest_from(side, ahead)
                self._returns = []
            return False
        # Back at rest, or straight through to the other side: one twist done.
        self.last_peak = self._peak
        self._returns = [stamp for stamp in self._returns if now - stamp <= TWIST_WINDOW_SECONDS]
        self._returns.append(now)
        if crossed and magnitude >= TWIST_ON_DEGREES:
            self._sign = -self._sign
            self._twisted_at = now
            self._peak = magnitude
        else:
            self._sign = 0
            self._peak = 0.0
        if len(self._returns) >= TWISTS_TO_TOGGLE:
            self._returns = []
            return True
        return False
