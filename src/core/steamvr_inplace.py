# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Headset overlays for screenshot translation: the labels and the frame.

Two overlays, both tied to the SteamVR runtime the subtitle backend already
initialised:

* **The label sheet** is one world-anchored plane carrying every translated
  plate at once. One texture upload puts all the labels over their signs, and
  a single plane cannot drift apart the way six independent overlays could.
* **The frame** is a head-locked plane spanning the same field of view, shown
  while the player is choosing what to read. It takes laser input so a trigger
  drag on it becomes a rectangle in frame coordinates.

Every OpenVR call is wrapped: a headset problem must never reach the pipeline
that is also feeding the desktop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.core.in_place_layout import (
    DEFAULT_DEPTH_METERS,
    DEFAULT_FOV_SCALE,
    HeadAnchor,
    card_transform,
    in_place_transform,
)
from src.core.overlay_texture import OverlayTextureUploader
from src.core.vr_eye_capture import DEFAULT_VIEW_EYE, eye_index

logger = logging.getLogger(__name__)

LABEL_OVERLAY_KEY = "mio.translator.inplace"
LABEL_OVERLAY_NAME = "Mio Translator Screenshot Labels"
FRAME_OVERLAY_KEY = "mio.translator.selection"
FRAME_OVERLAY_NAME = "Mio Translator Selection Frame"
# Drain at most this many events per tick so one busy frame cannot stall the
# UI thread. pollNextOverlayEvent returns a (result, event) tuple that is
# always truthy, so the loop must be bounded by count, never by the result.
MAX_EVENTS_PER_POLL = 64


def _identity_pose() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
    )


def _to_hmd_matrix(openvr: Any, rows) -> Any:
    matrix = openvr.HmdMatrix34_t()
    for r in range(3):
        for c in range(4):
            matrix[r][c] = float(rows[r][c])
    return matrix


class _RuntimeOverlay:
    """Shared plumbing: attach to the running OpenVR, own one overlay handle."""

    key = ""
    name = ""
    # The fixed texture size (see overlay_texture); None takes the first
    # picture's size, right for an overlay whose pictures never change size.
    canvas: tuple[int, int] | None = None

    def __init__(self) -> None:
        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._uploader: OverlayTextureUploader | None = None
        self._available = False
        self._visible = False
        # The eye reads are taken from (the player's dominant one); the
        # owner keeps it in step with the setting.
        self.view_eye = DEFAULT_VIEW_EYE

    def _quad_width(self, picture_width_meters: float) -> float:
        """The overlay width that shows the last picture at the given width."""

        uploader = self._uploader
        if uploader is None:
            return max(0.01, float(picture_width_meters))
        return uploader.quad_width(picture_width_meters)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def visible(self) -> bool:
        return self._visible

    def _safe(self, action: Callable[[], Any], label: str) -> Any:
        try:
            return action()
        except Exception:
            logger.debug("%s %s failed", self.name, label, exc_info=True)
            return None

    def _ok(self, action: Callable[[], Any], label: str) -> bool:
        """True when the call ran without raising.

        pyopenvr's setters are void and report failure by raising, so their
        return value (always None) says nothing; only the exception does.
        """

        try:
            action()
        except Exception:
            logger.debug("%s %s failed", self.name, label, exc_info=True)
            return False
        return True

    def start(self) -> bool:
        """Attach to the runtime SteamVROverlayBackend already initialised."""

        if self._available:
            return True
        try:
            import openvr
        except Exception:
            logger.debug("openvr unavailable for %s", self.name, exc_info=True)
            return False
        self._openvr = openvr
        try:
            self._overlay = openvr.IVROverlay()
            self._handle = self._overlay.createOverlay(self.key, self.name)
            self._overlay.setOverlayAlpha(self._handle, 1.0)
        except Exception:
            logger.debug("Failed to create %s", self.name, exc_info=True)
            self._overlay = None
            self._handle = None
            return False
        self._uploader = OverlayTextureUploader(
            openvr, self._overlay, self._handle, canvas=self.canvas
        )
        self._available = True
        self._configure()
        return True

    def _configure(self) -> None:
        return None

    def stop(self) -> None:
        overlay, handle = self._overlay, self._handle
        self._available = False
        self._visible = False
        self._overlay = None
        self._handle = None
        uploader, self._uploader = self._uploader, None
        if uploader is not None:
            uploader.release()
        if overlay is not None and handle is not None:
            try:
                overlay.hideOverlay(handle)
                overlay.destroyOverlay(handle)
            except Exception:
                logger.debug("Failed to destroy %s", self.name, exc_info=True)

    def hide(self) -> None:
        self._visible = False
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        self._safe(lambda: overlay.hideOverlay(handle), "hideOverlay")

    def _show(self) -> None:
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        if self._ok(lambda: overlay.showOverlay(handle), "showOverlay"):
            self._visible = True

    def _upload(self, buffer: Any, width: int, height: int) -> bool:
        """Upload a picture: a QImage goes through the in-place GL texture,
        a ctypes buffer through the raw path."""

        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return False
        if hasattr(buffer, "isNull") and self._uploader is not None:
            if self._uploader.upload_image(buffer):
                return True
            return False
        return self._ok(
            lambda: overlay.setOverlayRaw(handle, buffer, int(width), int(height), 4),
            "setOverlayRaw",
        )

    # ------------------------------------------------------------- head pose
    def snapshot_head_anchor(self, eye: str | None = None) -> HeadAnchor | None:
        """Where the head is right now, plus how the view eye projects.

        Read at capture time so the labels can be pinned to the world where the
        frame was seen, however the player turns afterwards.
        """

        openvr = self._openvr
        if openvr is None:
            return None
        try:
            system = openvr.VRSystem()
            poses = system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0.0, openvr.k_unMaxTrackedDeviceCount
            )
            hmd = poses[openvr.k_unTrackedDeviceIndex_Hmd]
            if not hmd.bPoseIsValid:
                return None
            m = hmd.mDeviceToAbsoluteTracking
            pose = tuple(tuple(float(m[r][c]) for c in range(4)) for r in range(3))
            index = eye_index(openvr, eye or self.view_eye)
            eye_pose = system.getEyeToHeadTransform(index)
            eye_offset = (float(eye_pose[0][3]), float(eye_pose[1][3]), float(eye_pose[2][3]))
            tangents = tuple(float(v) for v in system.getProjectionRaw(index))
        except Exception:
            logger.debug("Failed to read the head pose", exc_info=True)
            return None
        if len(tangents) != 4:
            return None
        return HeadAnchor(pose=pose, eye_offset=eye_offset, tangents=tangents)  # type: ignore[arg-type]


class SteamVRLabelSheet(_RuntimeOverlay):
    """The translated plates, pinned to the world over the signs they came from."""

    key = LABEL_OVERLAY_KEY
    name = LABEL_OVERLAY_NAME
    # Toasts are view-sized (1280 wide) and cards come in every size; both
    # go on one big canvas so the texture never changes size.
    canvas = (2048, 2048)

    def __init__(self) -> None:
        super().__init__()
        self.last_transform: list[list[float]] | None = None

    def set_transform(self, rows) -> bool:
        """Place the sheet in room space; used at show time and on re-anchoring."""

        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        matrix = _to_hmd_matrix(openvr, rows)
        if not self._ok(
            lambda: overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseStanding, matrix
            ),
            "setOverlayTransformAbsolute",
        ):
            return False
        self.last_transform = [[float(rows[r][c]) for c in range(4)] for r in range(3)]
        return True

    def _configure(self) -> None:
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return
        # Draw over the subtitle panel, not under it: a label the player just
        # asked for must not be hidden by a line someone said meanwhile.
        self._safe(lambda: overlay.setOverlaySortOrder(handle, 10), "setOverlaySortOrder")

    def show(
        self,
        buffer: Any,
        width: int,
        height: int,
        anchor: HeadAnchor | None,
        *,
        depth: float = DEFAULT_DEPTH_METERS,
        fov_scale: float = DEFAULT_FOV_SCALE,
    ) -> bool:
        if not self._available and not self.start():
            return False
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        if anchor is None:
            # No pose at capture time: fall back to "where the head is now".
            anchor = self.snapshot_head_anchor()
        if anchor is None:
            return False
        rows, meters = in_place_transform(anchor, depth, fov_scale)
        if not self.set_transform(rows):
            return False
        # Upload first: the width depends on how much of the canvas the
        # picture took.
        if not self._upload(buffer, width, height):
            return False
        quad = self._quad_width(meters)
        self._safe(lambda: overlay.setOverlayWidthInMeters(handle, quad), "setOverlayWidthInMeters")
        self._show()
        return self._visible

    def show_card(
        self,
        buffer: Any,
        width: int,
        height: int,
        anchor: HeadAnchor | None,
        centre_uv: tuple[float, float],
        width_meters: float,
        *,
        depth: float = DEFAULT_DEPTH_METERS,
    ) -> bool:
        """A picture on a card hung along the direction of frame point ``centre_uv``.

        The same overlay as the sheet, used the way VRHandsFrame shows a
        crop: the picture carries its own labels, so nothing has to line up
        with the world.
        """

        if not self._available and not self.start():
            return False
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        if anchor is None:
            anchor = self.snapshot_head_anchor()
        if anchor is None:
            return False
        if not self.set_transform(card_transform(anchor, centre_uv, depth)):
            return False
        meters = max(0.05, float(width_meters))
        if not self._upload(buffer, width, height):
            return False
        quad = self._quad_width(meters)
        self._safe(lambda: overlay.setOverlayWidthInMeters(handle, quad), "setOverlayWidthInMeters")
        self._show()
        return self._visible


class SteamVRSelectionFrame(_RuntimeOverlay):
    """A head-locked frame the player draws a rectangle on with the laser.

    Mouse-style events arrive in overlay UV (0..1, v up from the bottom in
    OpenVR); they are flipped to frame fractions (v down from the top) before
    they leave this class, so callers think in image coordinates only.
    """

    key = FRAME_OVERLAY_KEY
    name = FRAME_OVERLAY_NAME

    def __init__(self) -> None:
        super().__init__()
        self._dragging = False
        self._start: tuple[float, float] | None = None
        self._current: tuple[float, float] | None = None
        # The frame opens on a chord that includes the trigger, so the trigger
        # is still down when the frame appears. Until it has been let go once,
        # its events are the tail of the chord, not the start of a selection -
        # treating them as one closed the frame the moment the player let go.
        self._await_release = False
        self.on_drag: Callable[[tuple[float, float], tuple[float, float]], None] | None = None
        self.on_release: Callable[[tuple[float, float], tuple[float, float]], None] | None = None
        # Where the laser points while nothing is pressed, so the frame can
        # draw the box that a click will read.
        self.on_hover: Callable[[tuple[float, float]], None] | None = None

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def awaiting_release(self) -> bool:
        return self._await_release

    def release_arm(self) -> None:
        """The trigger from the chord has been let go: the next press counts.

        SteamVR does not always report a release for a press that began
        before the frame had focus, so waiting for its mouse-up event alone
        cost the player an extra click; the controller's own trigger state
        settles it.
        """

        self._await_release = False

    def _configure(self) -> None:
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return
        self._safe(
            lambda: overlay.setOverlayInputMethod(handle, openvr.VROverlayInputMethod_Mouse),
            "setOverlayInputMethod",
        )
        self._safe(
            lambda: overlay.setOverlayFlag(
                handle, openvr.VROverlayFlags_MakeOverlaysInteractiveIfVisible, True
            ),
            "setOverlayFlag",
        )
        self._safe(lambda: overlay.setOverlaySortOrder(handle, 20), "setOverlaySortOrder")

    def show(
        self,
        buffer: Any,
        width: int,
        height: int,
        anchor: HeadAnchor | None,
        *,
        depth: float = DEFAULT_DEPTH_METERS,
        fov_scale: float = DEFAULT_FOV_SCALE,
        button_held: bool = True,
    ) -> bool:
        """Show the frame in front of the head, locked to it.

        ``button_held`` says the trigger is down as the frame opens (the usual
        case after the chord); its release is then swallowed and only a fresh
        press starts a selection.
        """

        if not self._available and not self.start():
            return False
        self._dragging = False
        self._start = None
        self._current = None
        self._await_release = bool(button_held)
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        if anchor is None:
            anchor = self.snapshot_head_anchor()
        if anchor is None:
            return False
        # Head-locked: the same local transform as the label sheet, but relative
        # to the HMD instead of the world, so it follows the player's gaze.
        local = HeadAnchor(pose=_identity_pose(), eye_offset=anchor.eye_offset, tangents=anchor.tangents)
        rows, meters = in_place_transform(local, depth, fov_scale)
        matrix = _to_hmd_matrix(openvr, rows)
        if not self._ok(
            lambda: overlay.setOverlayTransformTrackedDeviceRelative(
                handle, openvr.k_unTrackedDeviceIndex_Hmd, matrix
            ),
            "setOverlayTransformTrackedDeviceRelative",
        ):
            return False
        self._safe(
            lambda: overlay.setOverlayMouseScale(handle, openvr.HmdVector2_t(1.0, 1.0)),
            "setOverlayMouseScale",
        )
        # Upload first: a picture letterboxed on the pinned texture needs a
        # wider quad to appear at ``meters``.
        if not self._upload(buffer, width, height):
            return False
        self._frame_meters = meters
        self._safe(
            lambda: overlay.setOverlayWidthInMeters(handle, self._quad_width(meters)),
            "setOverlayWidthInMeters",
        )
        self._show()
        return self._visible

    def push(self, buffer: Any, width: int, height: int) -> None:
        """Replace the texture while the frame stays where it is."""

        if not self._upload(buffer, width, height):
            return
        meters = getattr(self, "_frame_meters", None)
        overlay, handle = self._overlay, self._handle
        if meters and overlay is not None and handle is not None:
            self._safe(
                lambda: overlay.setOverlayWidthInMeters(handle, self._quad_width(meters)),
                "setOverlayWidthInMeters",
            )

    def hide(self) -> None:
        self._dragging = False
        self._start = None
        self._current = None
        self._await_release = False
        super().hide()

    # ------------------------------------------------------------- input
    def poll(self) -> None:
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None or not self._visible:
            return
        event = openvr.VREvent_t()
        for _ in range(MAX_EVENTS_PER_POLL):
            try:
                result = overlay.pollNextOverlayEvent(handle, event)
            except Exception:
                logger.debug("pollNextOverlayEvent failed", exc_info=True)
                return
            has_event = result[0] if isinstance(result, tuple) else result
            if not has_event:
                return
            self._handle_event(event)

    def _point(self, event: Any) -> tuple[float, float]:
        mouse = event.data.mouse
        u = max(0.0, min(1.0, float(mouse.x)))
        v = max(0.0, min(1.0, float(mouse.y)))
        # Mouse y is in texture space; whether that counts from the top or
        # the bottom of the picture depends on how the picture was uploaded.
        uploader = self._uploader
        if uploader is None or not uploader.mouse_y_is_top_down:
            v = 1.0 - v
        if uploader is not None:
            u, v = uploader.picture_fraction(u, v)
        return (max(0.0, min(1.0, u)), max(0.0, min(1.0, v)))

    def _handle_event(self, event: Any) -> None:
        openvr = self._openvr
        if openvr is None:
            return
        kind = int(event.eventType)
        if kind == openvr.VREvent_MouseMove and not self._dragging:
            # The laser wandering over the frame: the box follows it whether
            # or not the chord's trigger has been let go yet.
            if callable(self.on_hover):
                try:
                    self.on_hover(self._point(event))
                except Exception:
                    logger.debug("Selection hover callback failed", exc_info=True)
            if not self._await_release:
                return
        if self._await_release:
            # Tail of the chord: ignore the held trigger until it is released.
            if kind == openvr.VREvent_MouseButtonUp:
                self._await_release = False
            return
        if kind == openvr.VREvent_MouseButtonDown:
            self._start = self._point(event)
            self._current = self._start
            self._dragging = True
        elif kind == openvr.VREvent_MouseMove and self._dragging and self._start is not None:
            self._current = self._point(event)
            if callable(self.on_drag):
                try:
                    self.on_drag(self._start, self._current)
                except Exception:
                    logger.debug("Selection drag callback failed", exc_info=True)
        elif kind == openvr.VREvent_MouseButtonUp and self._dragging and self._start is not None:
            end = self._point(event)
            start = self._start
            self._dragging = False
            self._start = None
            self._current = None
            if callable(self.on_release):
                try:
                    self.on_release(start, end)
                except Exception:
                    logger.debug("Selection release callback failed", exc_info=True)
