# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Where a panel sits around the player, and how a laser moves it.

The subtitle panel lives on a sphere around the head: any direction, a
chosen distance, always turned to face the player. Dragging is a ray cast -
wherever the controller's laser leaves the sphere is where the panel goes,
so the motion is one-to-one with the hand - and the distance follows the
arm: reach out and the panel moves away, pull back and it comes closer. A
position-offset drag never managed either; it lagged, and it let the panel
drift to angles where it faced nobody.

Everything here works in head-relative metres: +x right, +y up, -z forward.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Matrix34 = list[list[float]]

MIN_RADIUS = 0.4
MAX_RADIUS = 6.0
# Straight up or down has no direction to face; stop a little short of it.
MAX_PITCH = math.radians(80.0)
# How far the panel moves for every metre the hand moves toward or away
# from the head. Arms are short and rooms are not.
REACH_GAIN = 3.0


def clamp_radius(radius: float) -> float:
    return max(MIN_RADIUS, min(MAX_RADIUS, float(radius)))


def radius_of(position: Vec3) -> float:
    x, y, z = position
    return clamp_radius(math.sqrt(x * x + y * y + z * z))


def yaw_of(position: Vec3) -> float:
    """Angle around the vertical axis; 0 is straight ahead, positive to the right."""

    x, _y, z = position
    if abs(x) < 1e-9 and abs(z) < 1e-9:
        return 0.0
    return math.atan2(x, -z)


def pitch_of(position: Vec3) -> float:
    """Angle above the horizon; positive is up."""

    x, y, z = position
    horizontal = math.hypot(x, z)
    if horizontal < 1e-9 and abs(y) < 1e-9:
        return 0.0
    return max(-MAX_PITCH, min(MAX_PITCH, math.atan2(y, horizontal)))


def position_on_sphere(yaw: float, pitch: float, radius: float) -> Vec3:
    radius = clamp_radius(radius)
    pitch = max(-MAX_PITCH, min(MAX_PITCH, float(pitch)))
    horizontal = radius * math.cos(pitch)
    return (horizontal * math.sin(yaw), radius * math.sin(pitch), -horizontal * math.cos(yaw))


def angles_of(position: Vec3) -> tuple[float, float, float]:
    """(yaw degrees, pitch degrees, radius) of a panel position.

    Yaw is positive to the right, pitch positive upward: the numbers a
    settings slider can show and a player can reason about.
    """

    return (math.degrees(yaw_of(position)), math.degrees(pitch_of(position)), radius_of(position))


def position_from_angles(yaw_degrees: float, pitch_degrees: float, radius: float) -> Vec3:
    """The inverse of :func:`angles_of`, clamped to the sphere's limits."""

    pitch = max(-MAX_PITCH, min(MAX_PITCH, math.radians(float(pitch_degrees))))
    return position_on_sphere(math.radians(float(yaw_degrees)), pitch, clamp_radius(radius))


def sphere_hit(origin: Vec3, direction: Vec3, radius: float) -> Vec3 | None:
    """Where a ray leaves a sphere of ``radius`` around the head.

    The controller is inside the sphere (it is near the head), so the ray
    has exactly one exit ahead of it.
    """

    ox, oy, oz = origin
    dx, dy, dz = direction
    a = dx * dx + dy * dy + dz * dz
    if a < 1e-9:
        return None
    b = 2.0 * (ox * dx + oy * dy + oz * dz)
    c = ox * ox + oy * oy + oz * oz - radius * radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return None
    t = (-b + math.sqrt(discriminant)) / (2.0 * a)
    if t <= 0.0:
        return None
    return (ox + t * dx, oy + t * dy, oz + t * dz)


def reach_of(origin: Vec3) -> float:
    """How far the hand is from the head; the arm's extension."""

    x, y, z = origin
    return math.sqrt(x * x + y * y + z * z)


def facing_transform(position: Vec3) -> Matrix34:
    """A transform at ``position`` whose front faces the head.

    Overlays face +z of their own transform, so the rotation turns +z back
    toward the origin - around the vertical axis and then tilted up or down
    to meet the eyes.
    """

    yaw = yaw_of(position)
    pitch = pitch_of(position)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    # Right, up and front axes of the panel, expressed in head space. The
    # front points from the panel back to the head; right is the viewer's
    # right when looking at the panel (front x up for +y up).
    right = (cy, 0.0, sy)
    front = (-sy * cp, -sp, cy * cp)
    # Overlay axes are columns; ``up`` = front x right keeps the frame right-handed.
    up = (
        front[1] * right[2] - front[2] * right[1],
        front[2] * right[0] - front[0] * right[2],
        front[0] * right[1] - front[1] * right[0],
    )
    x, y, z = position
    return [
        [right[0], up[0], front[0], x],
        [right[1], up[1], front[1], y],
        [right[2], up[2], front[2], z],
    ]


def snap_to_sphere(position: Vec3) -> Vec3:
    """Project a stored position onto the sphere it implies (clamps radius and pitch)."""

    return position_on_sphere(yaw_of(position), pitch_of(position), radius_of(position))


# Older names, kept so saved positions written by the cylinder model still
# load: a point on a cylinder is a point on a sphere of the same reach.
snap_to_cylinder = snap_to_sphere
