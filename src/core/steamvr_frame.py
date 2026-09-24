# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""The frame between the player's hands, in the headset.

A world-placed quad that covers exactly the part of the view the two hands
span (see ``vr_frame_gesture``), re-placed every tick so it follows them. It
takes no input: the gesture is read from the controllers, never through the
overlay, so the game keeps every button.

Like every Mio overlay it is part of what the compositor draws, so it is
taken down before the eye is read (``MainWindow._prepare_vr_capture``) and
put back afterwards.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.core.in_place_layout import HeadAnchor, region_quad
from src.core.steamvr_inplace import _RuntimeOverlay, _to_hmd_matrix
from src.core.vr_eye_capture import DEFAULT_VIEW_EYE, eye_index

logger = logging.getLogger(__name__)

FRAME_OVERLAY_KEY = "mio.translator.handframe"
FRAME_OVERLAY_NAME = "Mio Translator Hand Frame"
# Redraw at most this often while only the stillness bar moves.
REDRAW_INTERVAL_S = 1.0 / 15.0
# The frame's shape changed this much (relative aspect): draw it again.
ASPECT_REDRAW_TOLERANCE = 0.04


class SteamVRHandFrame(_RuntimeOverlay):
    """Place and paint the hand frame; the picture comes from a painter."""

    key = FRAME_OVERLAY_KEY
    name = FRAME_OVERLAY_NAME
    # Every frame shape is drawn centred on one square canvas, so the
    # texture never changes size (see overlay_texture).
    canvas = (1024, 1024)

    def __init__(self) -> None:
        super().__init__()
        self._drawn_key: tuple | None = None
        self._drawn_aspect = 0.0
        self._last_draw = 0.0
        self._paused = False

    def _configure(self) -> None:
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        # Above the subtitle board and cards, below the wrist panel.
        self._safe(lambda: overlay.setOverlaySortOrder(handle, 30), "setOverlaySortOrder")

    def pause(self) -> None:
        """Out of the picture for a capture; :meth:`resume` puts it back."""

        self._paused = True
        super().hide()

    def resume(self) -> None:
        self._paused = False
        self._drawn_key = None

    def hide(self) -> None:
        self._drawn_key = None
        super().hide()

    def place(
        self,
        anchor: HeadAnchor,
        region: tuple[float, float, float, float],
        depth: float,
        painter,
        *,
        key: tuple,
        now: float | None = None,
    ) -> bool:
        """Put the frame over ``region`` at ``depth``; ``painter(size)`` draws
        its picture when ``key`` (what is shown) or the shape changed."""

        if self._paused:
            return False
        if not self._available and not self.start():
            return False
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        rows, width_m, height_m = region_quad(anchor, region, depth)
        aspect = width_m / max(1e-6, height_m)
        moment = time.monotonic() if now is None else float(now)
        changed_shape = (
            self._drawn_aspect <= 0
            or abs(aspect - self._drawn_aspect) / self._drawn_aspect > ASPECT_REDRAW_TOLERANCE
        )
        # key[0] names what the frame shows (its phase or reading); the rest
        # is detail such as the stillness bar, which may lag a tick or two.
        drawn_head = self._drawn_key[0] if self._drawn_key else None
        must_draw = changed_shape or key[:1] != (drawn_head,)
        may_draw = key != self._drawn_key and moment - self._last_draw >= REDRAW_INTERVAL_S
        if must_draw or may_draw:
            try:
                image = painter((width_m, height_m))
            except Exception:
                logger.debug("Hand frame paint failed", exc_info=True)
                image = None
            if image is not None and self._upload(image, image.width(), image.height()):
                self._drawn_key = key
                self._drawn_aspect = aspect
                self._last_draw = moment
        if not self._ok(
            lambda: overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseStanding, _to_hmd_matrix(openvr, rows)
            ),
            "setOverlayTransformAbsolute",
        ):
            return False
        quad = self._quad_width(width_m)
        self._safe(lambda: overlay.setOverlayWidthInMeters(handle, quad), "setOverlayWidthInMeters")
        if not self._visible:
            self._show()
        return self._visible


Rows = list[list[float]]


class Tracking:
    """One read of the head and both controllers, in the standing universe."""

    __slots__ = ("anchor", "head", "left", "right")

    def __init__(
        self,
        anchor: HeadAnchor | None = None,
        head: Rows | None = None,
        left: Rows | None = None,
        right: Rows | None = None,
    ) -> None:
        self.anchor = anchor
        self.head = head
        self.left = left
        self.right = right

    def hand(self, hand: str) -> Rows | None:
        return self.right if str(hand).lower() == "right" else self.left

    @staticmethod
    def position(rows: Rows | None) -> tuple[float, float, float] | None:
        if rows is None:
            return None
        return (float(rows[0][3]), float(rows[1][3]), float(rows[2][3]))


def _rows(matrix: Any) -> Rows:
    return [[float(matrix[r][c]) for c in range(4)] for r in range(3)]


def read_tracking(openvr: Any, eye: str = DEFAULT_VIEW_EYE) -> Tracking:
    """Head pose with ``eye``'s projection, and both controller poses.

    One pose query for everything the frame gesture and the panels need in a
    tick. Whatever is not tracked comes back None; never raises.
    """

    try:
        system = openvr.VRSystem()
        poses = system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, openvr.k_unMaxTrackedDeviceCount
        )
    except Exception:
        logger.debug("Failed to read the tracked poses", exc_info=True)
        return Tracking()

    def pose_of(index: int | None) -> Rows | None:
        if index is None or index < 0 or index >= len(poses):
            return None
        entry = poses[index]
        if not bool(getattr(entry, "bPoseIsValid", False)):
            return None
        try:
            return _rows(entry.mDeviceToAbsoluteTracking)
        except Exception:
            return None

    head = pose_of(int(openvr.k_unTrackedDeviceIndex_Hmd))
    anchor = None
    if head is not None:
        try:
            index = eye_index(openvr, eye)
            eye_pose = system.getEyeToHeadTransform(index)
            eye_offset = (float(eye_pose[0][3]), float(eye_pose[1][3]), float(eye_pose[2][3]))
            tangents = tuple(float(v) for v in system.getProjectionRaw(index))
            if len(tangents) == 4:
                anchor = HeadAnchor(
                    pose=tuple(tuple(row) for row in head),  # type: ignore[arg-type]
                    eye_offset=eye_offset,
                    tangents=tangents,  # type: ignore[arg-type]
                )
        except Exception:
            logger.debug("Failed to read the eye projection", exc_info=True)
    hands: list[Rows | None] = []
    for role in (openvr.TrackedControllerRole_LeftHand, openvr.TrackedControllerRole_RightHand):
        try:
            index = system.getTrackedDeviceIndexForControllerRole(role)
            if index == openvr.k_unTrackedDeviceIndexInvalid:
                hands.append(None)
                continue
            hands.append(pose_of(int(index)))
        except Exception:
            hands.append(None)
    return Tracking(anchor=anchor, head=head, left=hands[0], right=hands[1])
