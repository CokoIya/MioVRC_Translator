# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""The two-hand frame, VRHandsFrame's way of saying "read this".

Hold both grips and bring the hands up in front of the face so the two
controllers mark opposite corners of a rectangle - the way one frames a shot
with thumbs and fingers. A frame appears between the hands, following them.
Hold it still for a moment and what it covers is read and translated into
the frame itself; pull a trigger and the reading is kept as a panel; let go
of a grip and the frame is gone.

This module is the pure part: it turns ticks of (hand positions, buttons,
head pose) into a region of the eye's view and a handful of events. The
headset overlay that draws the frame and the reads themselves live in
``steamvr_frame`` and the main window.

Telling the gesture from play: both grips, both hands in front of the face
at arm's length, set apart diagonally (not side by side, not stacked) and
held like that for a moment. Holding two things in front of the face is the
one thing that can still look like it, which is why the gesture can be
switched off from the wrist.

VRHandsFrame's players complained most about the pose not being recognised,
so the limits scale with a sensitivity (:func:`tuning_for_sensitivity`),
the delays are the player's to set, and there is a quick mode: trigger and
grip held on both hands opens a frame from any hand position, padded to a
usable size, so the pose no longer has to be a diagonal.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.core.in_place_layout import HeadAnchor, view_fraction

# The pose must hold this long before the frame appears.
ARM_SECONDS = 0.3
# The frame survives this long when tracking or a condition flickers.
LOST_GRACE_SECONDS = 0.25
# A controller this close to or far from the head is not framing.
MIN_HAND_DISTANCE_M = 0.14
MAX_HAND_DISTANCE_M = 0.95
# Hands this close together are not two corners of anything.
MIN_HANDS_APART_M = 0.12
# The rectangle, as fractions of the view, must be at least this wide and
# tall - two hands side by side or one above the other make no frame.
MIN_REGION_WIDTH = 0.05
MIN_REGION_HEIGHT = 0.035
# How far outside the view a corner may sit (the hands are at the edge).
VIEW_SLACK = 0.12
# Corner smoothing per tick: follows a moving hand without jitter.
SMOOTHING = 0.35
# Held within this (fraction of the view) for STILL_SECONDS: read it.
STILL_TOLERANCE = 0.012
STILL_SECONDS = 0.6
# Moved this far after a read: the reading no longer covers what it read.
MOVE_TOLERANCE = 0.03

# Quick mode: a frame narrower or shorter than this (view fractions) is
# padded around its middle instead of refused.
QUICK_REGION_SIZE = (0.30, 0.18)
DEFAULT_SENSITIVITY = 0.5
# The stick scales an open frame about its middle: a full push doubles (or
# halves) it each second, within these limits. Pushes under the dead zone
# are drift, not intent.
STICK_DEAD_ZONE = 0.25
STICK_SCALE_PER_SECOND = math.log(2.0)
MIN_STICK_SCALE = 0.4
MAX_STICK_SCALE = 3.0

Vector = tuple[float, float, float]
Region = tuple[float, float, float, float]


@dataclass(frozen=True)
class GestureTuning:
    """The limits that tell a frame from play."""

    min_hand_distance: float = MIN_HAND_DISTANCE_M
    max_hand_distance: float = MAX_HAND_DISTANCE_M
    min_hands_apart: float = MIN_HANDS_APART_M
    min_region_width: float = MIN_REGION_WIDTH
    min_region_height: float = MIN_REGION_HEIGHT
    view_slack: float = VIEW_SLACK


DEFAULT_TUNING = GestureTuning()


def tuning_for_sensitivity(sensitivity: float) -> GestureTuning:
    """0 = strict (fewer false frames), 1 = lenient (easier to make); 0.5
    gives the default limits exactly."""

    try:
        s = max(0.0, min(1.0, float(sensitivity)))
    except (TypeError, ValueError):
        s = DEFAULT_SENSITIVITY
    return GestureTuning(
        min_hand_distance=0.18 - 0.08 * s,
        max_hand_distance=0.85 + 0.20 * s,
        min_hands_apart=0.16 - 0.08 * s,
        min_region_width=0.075 - 0.05 * s,
        min_region_height=0.0525 - 0.035 * s,
        view_slack=0.06 + 0.12 * s,
    )


@dataclass
class FrameInputs:
    """One tick of what the headset reports."""

    anchor: HeadAnchor | None
    left: Vector | None
    right: Vector | None
    left_grip: bool | None
    right_grip: bool | None
    left_trigger: bool | None = None
    right_trigger: bool | None = None
    now: float = 0.0
    # The stronger of the two sticks' up/down, -1..1.
    stick: float = 0.0


@dataclass
class FrameState:
    """What the frame is doing, for drawing it."""

    active: bool = False
    region: Region | None = None
    depth: float = 0.0
    # "ready" (framing), "reading" (a read is running), "shown" (a reading
    # covers the region).
    phase: str = "ready"
    # How far toward the auto read the stillness got, 0..1.
    still_fraction: float = 0.0
    events: list[str] = field(default_factory=list)


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def region_of(
    anchor: HeadAnchor,
    left: Vector,
    right: Vector,
    tuning: GestureTuning = DEFAULT_TUNING,
    *,
    pad_to: tuple[float, float] | None = None,
) -> tuple[Region, float] | None:
    """The view region the two hands span, and their mean depth; None when
    they do not make a frame in front of the eye.

    With ``pad_to`` (quick mode) a span narrower or shorter than that size
    is widened around its middle instead of refused.
    """

    head = (anchor.pose[0][3], anchor.pose[1][3], anchor.pose[2][3])
    for hand in (left, right):
        distance = _distance(head, hand)
        if not tuning.min_hand_distance <= distance <= tuning.max_hand_distance:
            return None
    if _distance(left, right) < tuning.min_hands_apart:
        return None
    a = view_fraction(anchor, left)
    b = view_fraction(anchor, right)
    if a is None or b is None:
        return None
    slack = tuning.view_slack
    for u, v, _depth in (a, b):
        if not (-slack <= u <= 1.0 + slack and -slack <= v <= 1.0 + slack):
            return None
    u0, u1 = sorted((a[0], b[0]))
    v0, v1 = sorted((a[1], b[1]))
    if pad_to is not None:
        u0, u1 = _padded(u0, u1, pad_to[0])
        v0, v1 = _padded(v0, v1, pad_to[1])
    elif u1 - u0 < tuning.min_region_width or v1 - v0 < tuning.min_region_height:
        return None
    clamp = lambda value: max(0.0, min(1.0, value))  # noqa: E731
    return (clamp(u0), clamp(v0), clamp(u1), clamp(v1)), (a[2] + b[2]) / 2.0


def _padded(low: float, high: float, size: float) -> tuple[float, float]:
    if high - low >= size:
        return low, high
    middle = (low + high) / 2.0
    return middle - size / 2.0, middle + size / 2.0


def _max_delta(a: Region, b: Region) -> float:
    return max(abs(a[i] - b[i]) for i in range(4))


class FrameGestureTracker:
    """Ticks in, :class:`FrameState` out.

    Events, each reported once in ``FrameState.events`` on the tick it
    happens: ``"opened"`` (the frame appears), ``"still"`` (held still long
    enough - read it), ``"moved"`` (moved off what was read), ``"capture"``
    (a trigger was pulled while framing), ``"closed"`` (the frame is gone).
    """

    def __init__(
        self,
        *,
        still_seconds: float = STILL_SECONDS,
        auto_read: bool = True,
        sensitivity: float = DEFAULT_SENSITIVITY,
        quick: bool = False,
        arm_seconds: float = ARM_SECONDS,
        release_seconds: float = LOST_GRACE_SECONDS,
    ) -> None:
        self.still_seconds = max(0.2, float(still_seconds))
        self.auto_read = bool(auto_read)
        self.quick = bool(quick)
        self.stick_scaling = True
        self._scale = 1.0
        self._last_now: float | None = None
        self._shown_region: Region | None = None
        self.arm_seconds = max(0.0, float(arm_seconds))
        self.release_seconds = max(0.0, float(release_seconds))
        self._sensitivity = DEFAULT_SENSITIVITY
        self._tuning = DEFAULT_TUNING
        self.sensitivity = sensitivity
        self._arming_since: float | None = None
        self._arming_quick = False
        self._quick_frame = False
        self._active = False
        self._lost_since: float | None = None
        self._region: Region | None = None
        self._depth = 0.0
        self._still_anchor: Region | None = None
        self._still_since = 0.0
        self._read_region: Region | None = None
        self._phase = "ready"
        self._trigger_was = {"left": False, "right": False}
        # After a capture the grips are usually still held: the frame must
        # not come straight back over the panel that just appeared.
        self._wait_release = False

    @property
    def sensitivity(self) -> float:
        return self._sensitivity

    @sensitivity.setter
    def sensitivity(self, value: float) -> None:
        try:
            wanted = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            wanted = DEFAULT_SENSITIVITY
        if wanted != self._sensitivity:
            self._sensitivity = wanted
            self._tuning = tuning_for_sensitivity(wanted)

    @property
    def tuning(self) -> GestureTuning:
        return self._tuning

    @property
    def active(self) -> bool:
        return self._active

    @property
    def arming(self) -> bool:
        """The pose is being held but the frame has not opened yet."""

        return not self._active and self._arming_since is not None

    @property
    def region(self) -> Region | None:
        """The frame as shown: the hands' rectangle, scaled by the stick."""

        return self._shown_region

    @property
    def scale(self) -> float:
        return self._scale

    def _scaled(self, region: Region) -> Region:
        if abs(self._scale - 1.0) < 1e-6:
            return region
        u0, v0, u1, v1 = region
        cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        half_u = (u1 - u0) / 2.0 * self._scale
        half_v = (v1 - v0) / 2.0 * self._scale
        clamp = lambda value: max(0.0, min(1.0, value))  # noqa: E731
        return (clamp(cu - half_u), clamp(cv - half_v), clamp(cu + half_u), clamp(cv + half_v))

    def reading_started(self) -> None:
        """The caller began a read of the current region."""

        if self._active:
            self._phase = "reading"
            self._read_region = self._shown_region

    def reading_done(self, *, shown: bool) -> None:
        """The read finished; ``shown`` when its result now covers the frame."""

        if not self._active:
            return
        self._phase = "shown" if shown else "ready"
        if not shown:
            self._read_region = None
            self._still_anchor = self._shown_region
            self._still_since = float("inf")  # wait for a move before retrying

    def close(self, *, require_release: bool = False) -> None:
        """Put the frame away; with ``require_release`` it cannot open again
        until a grip has been let go."""

        self._reset()
        if require_release:
            self._wait_release = True

    def _reset(self) -> None:
        self._scale = 1.0
        self._shown_region = None
        self._arming_since = None
        self._arming_quick = False
        self._quick_frame = False
        self._active = False
        self._lost_since = None
        self._region = None
        self._still_anchor = None
        self._read_region = None
        self._phase = "ready"

    def update(self, inputs: FrameInputs) -> FrameState:
        events: list[str] = []
        now = float(inputs.now)
        elapsed = 0.0 if self._last_now is None else max(0.0, min(0.2, now - self._last_now))
        self._last_now = now
        found = None
        quick_now = False
        if (
            inputs.anchor is not None
            and inputs.left is not None
            and inputs.right is not None
            and bool(inputs.left_grip)
            and bool(inputs.right_grip)
        ):
            if self._active:
                quick_now = self._quick_frame
            else:
                quick_now = self.quick and bool(inputs.left_trigger) and bool(inputs.right_trigger)
            found = region_of(
                inputs.anchor,
                inputs.left,
                inputs.right,
                self._tuning,
                pad_to=QUICK_REGION_SIZE if quick_now else None,
            )

        if self._wait_release:
            if bool(inputs.left_grip) and bool(inputs.right_grip):
                return FrameState(events=events)
            self._wait_release = False

        triggers = {"left": bool(inputs.left_trigger), "right": bool(inputs.right_trigger)}
        pulled = [hand for hand in ("left", "right") if triggers[hand] and not self._trigger_was[hand]]
        self._trigger_was = triggers

        if not self._active:
            if found is None:
                self._arming_since = None
                self._arming_quick = False
                return FrameState(events=events)
            if self._arming_since is None or self._arming_quick != quick_now:
                self._arming_since = now
                self._arming_quick = quick_now
            self._region, self._depth = found
            if now - self._arming_since < self.arm_seconds:
                return FrameState(events=events)
            self._active = True
            self._quick_frame = self._arming_quick
            self._scale = 1.0
            self._lost_since = None
            self._phase = "ready"
            self._still_anchor = self._region
            self._still_since = now
            events.append("opened")
        elif found is None:
            if self._lost_since is None:
                self._lost_since = now
            if now - self._lost_since >= self.release_seconds:
                self._reset()
                events.append("closed")
                return FrameState(events=events)
        else:
            self._lost_since = None
            region, depth = found
            previous = self._region or region
            self._region = tuple(
                previous[i] + (region[i] - previous[i]) * SMOOTHING for i in range(4)
            )  # type: ignore[assignment]
            self._depth = self._depth + (depth - self._depth) * SMOOTHING

        assert self._region is not None
        stick = float(inputs.stick or 0.0)
        if self.stick_scaling and abs(stick) > STICK_DEAD_ZONE and elapsed > 0.0:
            self._scale = max(
                MIN_STICK_SCALE,
                min(MAX_STICK_SCALE, self._scale * math.exp(stick * STICK_SCALE_PER_SECOND * elapsed)),
            )
        region = self._scaled(self._region)
        self._shown_region = region
        # Stillness: measured against where the frame settled, not tick to tick.
        if self._still_anchor is None or _max_delta(region, self._still_anchor) > STILL_TOLERANCE:
            self._still_anchor = region
            self._still_since = now
        if self._read_region is not None and _max_delta(region, self._read_region) > MOVE_TOLERANCE:
            self._read_region = None
            self._phase = "ready"
            self._still_anchor = region
            self._still_since = now
            events.append("moved")
        still_for = now - self._still_since
        fraction = 0.0
        if self._phase == "ready" and self.auto_read and still_for < float("inf"):
            fraction = max(0.0, min(1.0, still_for / self.still_seconds))
            if fraction >= 1.0:
                events.append("still")
                fraction = 1.0
        if pulled:
            events.append("capture")
        return FrameState(
            active=True,
            region=region,
            depth=self._depth,
            phase=self._phase,
            still_fraction=fraction,
            events=events,
        )
