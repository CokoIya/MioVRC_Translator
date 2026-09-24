# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""A control panel on the back of the hand.

OVR Toolkit and OVR Overlay Translator put their controls on the wrist: roll
the wrist twice and the panel is there, point the other controller at it and
pull the trigger. Mio's is the dashboard tab's picture on a controller-
attached overlay, so every control the tab has is on the wrist too.

Laser input is not taken from SteamVR. The flag that would deliver it hands
the whole controller to the overlay and the game goes dead - the fatal bug
the subtitle panel once had. Instead the ray is cast here from the pointing
controller's tip and tested against the overlay with
``computeOverlayIntersection``, and the trigger action supplies the click.
Because SteamVR draws no laser for such an overlay, one is drawn here: a
thin beam overlay along the ray and a dot on the panel where it lands. The
game keeps every button; a trigger pull aimed at the panel reaches the game
as well, which is the price OVR pays too.
"""

from __future__ import annotations

import ctypes
import logging
import math
import time
from collections.abc import Callable, Sequence
from typing import Any

from src.core.overlay_texture import OverlayTextureUploader
from src.core.vr_gesture import TWISTS_TO_TOGGLE, TwistDetector, controller_axes

logger = logging.getLogger(__name__)

WRIST_OVERLAY_KEY = "mio.translator.wrist"
WRIST_OVERLAY_NAME = "Mio Translator Wrist Panel"
LASER_OVERLAY_KEY = "mio.translator.laser"
LASER_OVERLAY_NAME = "Mio Translator Laser"
DEFAULT_WRIST_WIDTH_METERS = 0.26
# Where a watch sits: behind the grip (a controller's +Z runs from its tip
# back toward the wrist), a little above the back of the hand. The earlier
# offset ran the other way and put the panel beyond the fingertips, which
# with the arms hanging meant near the floor.
WRIST_OFFSET = (0.0, 0.02, 0.12)
# The beam: a thin strip whose height is many times its width, so the
# overlay's width sets its length. Fades toward the far end.
LASER_TEXTURE_WIDTH = 8
LASER_TEXTURE_HEIGHT = 1024
LASER_ASPECT = LASER_TEXTURE_HEIGHT / LASER_TEXTURE_WIDTH
# The panel is on the other hand, so a beam that hits nothing need not be long.
LASER_IDLE_LENGTH_METERS = 0.6
LASER_COLOR = (120, 220, 255)
# Without a trigger binding (SteamVR had not activated the app's actions yet
# - a player could not click anything until VRChat was restarted) a button
# is clicked by resting the laser on it for this long. The cursor fills up
# to show the countdown.
DWELL_SECONDS = 0.9
# How the panel comes up. "twist": roll the wrist twice (OVR Toolkit's way).
# "look": raise the wrist and look at it, like a watch (VRHandsFrame's
# wrist menu). "always": it stays on the wrist.
SHOW_MODES = ("twist", "look", "always")
DEFAULT_SHOW_MODE = "twist"
# "look": the back of the hand must face the eyes within this angle, the eyes
# look toward it within the other, and it must be at most this far away.
LOOK_FACING_DEGREES = 45.0
LOOK_GAZE_DEGREES = 30.0
LOOK_MAX_DISTANCE_M = 0.7
# Looser limits keep it up once shown, so a glance that drifts does not
# flicker it; it goes after this long outside them.
LOOK_KEEP_FACING_DEGREES = 65.0
LOOK_KEEP_GAZE_DEGREES = 50.0
LOOK_SHOW_SECONDS = 0.15
LOOK_HIDE_SECONDS = 0.5


def normalize_show_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in SHOW_MODES else DEFAULT_SHOW_MODE


def wrist_is_looked_at(
    head: Rows, controller: Rows, *, facing_degrees: float, gaze_degrees: float, max_distance: float
) -> bool:
    """The back of the hand turned to the eyes, and the eyes on it."""

    panel = compose(controller, local_transform())
    panel_pos = (panel[0][3], panel[1][3], panel[2][3])
    head_pos = (float(head[0][3]), float(head[1][3]), float(head[2][3]))
    to_head = tuple(head_pos[i] - panel_pos[i] for i in range(3))
    distance = math.sqrt(sum(c * c for c in to_head))
    if distance < 1e-6 or distance > max_distance:
        return False
    # The panel's face is the controller's +Y (see local_transform).
    normal = (float(controller[0][1]), float(controller[1][1]), float(controller[2][1]))
    facing = sum(normal[i] * to_head[i] for i in range(3)) / distance
    forward = (-float(head[0][2]), -float(head[1][2]), -float(head[2][2]))
    gaze = -sum(forward[i] * to_head[i] for i in range(3)) / distance
    return facing >= math.cos(math.radians(facing_degrees)) and gaze >= math.cos(math.radians(gaze_degrees))

Vector = tuple[float, float, float]
Ray = tuple[Vector, Vector]
Rows = Sequence[Sequence[float]]


def local_transform() -> tuple[tuple[float, float, float, float], ...]:
    """The panel relative to the controller: a watch face on the back of the hand.

    Its face points out of the back of the hand (the controller's +Y) and
    its top toward the fingers (-Z), so it reads upright with the hand
    raised in front of the face.
    """

    x, y, z = WRIST_OFFSET
    return (
        (1.0, 0.0, 0.0, x),
        (0.0, 0.0, 1.0, y),
        (0.0, -1.0, 0.0, z),
    )


def compose(pose: Rows, local: Rows) -> list[list[float]]:
    """3x4 * 3x4 with the implicit bottom row."""

    out: list[list[float]] = []
    for r in range(3):
        row: list[float] = []
        for c in range(4):
            total = sum(float(pose[r][k]) * float(local[k][c]) for k in range(3))
            if c == 3:
                total += float(pose[r][3])
            row.append(total)
        out.append(row)
    return out


def ray_from_pose(rows: Rows) -> Ray:
    """A controller pose as a ray: its position, pointing along its -Z."""

    origin = (float(rows[0][3]), float(rows[1][3]), float(rows[2][3]))
    direction = (-float(rows[0][2]), -float(rows[1][2]), -float(rows[2][2]))
    return origin, direction


def _normalize(v: Vector) -> Vector:
    length = math.sqrt(sum(c * c for c in v))
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def laser_transform(origin: Vector, direction: Vector, length: float, head: Vector) -> list[list[float]]:
    """A quad along the ray: its local +Y runs down the beam, its face turns to the head."""

    y_axis = _normalize(direction)
    if y_axis == (0.0, 0.0, 0.0):
        y_axis = (0.0, 0.0, -1.0)
    mid = tuple(origin[i] + y_axis[i] * length / 2.0 for i in range(3))
    to_head = _normalize(tuple(head[i] - mid[i] for i in range(3)))
    along = sum(to_head[i] * y_axis[i] for i in range(3))
    z_axis = _normalize(tuple(to_head[i] - y_axis[i] * along for i in range(3)))
    if z_axis == (0.0, 0.0, 0.0):
        # The head is on the beam's own line: any face will do.
        z_axis = _normalize(_cross(y_axis, (0.0, 1.0, 0.0)))
        if z_axis == (0.0, 0.0, 0.0):
            z_axis = (1.0, 0.0, 0.0)
    x_axis = _cross(y_axis, z_axis)
    return [
        [x_axis[0], y_axis[0], z_axis[0], mid[0]],
        [x_axis[1], y_axis[1], z_axis[1], mid[1]],
        [x_axis[2], y_axis[2], z_axis[2], mid[2]],
    ]


def _rows(matrix: Any) -> list[list[float]]:
    return [[float(matrix[r][c]) for c in range(4)] for r in range(3)]


def _to_matrix(openvr: Any, rows: Rows) -> Any:
    matrix = openvr.HmdMatrix34_t()
    for r in range(3):
        for c in range(4):
            matrix[r][c] = float(rows[r][c])
    return matrix


def laser_texture() -> ctypes.Array:
    """RGBA bytes of the beam: solid at the hand, fading toward the tip."""

    r, g, b = LASER_COLOR
    rows = bytearray()
    for y in range(LASER_TEXTURE_HEIGHT):
        # Row 0 is the far end for the raw path (first row at the top of
        # the picture); the beam's origin is the bottom.
        alpha = int(40 + 200 * (y / (LASER_TEXTURE_HEIGHT - 1)))
        rows.extend(bytes((r, g, b, alpha)) * LASER_TEXTURE_WIDTH)
    return (ctypes.c_char * len(rows)).from_buffer_copy(bytes(rows))


class SteamVRLaser:
    """The beam from the pointing controller, shown only while the panel is."""

    def __init__(self, key: str = LASER_OVERLAY_KEY, name: str = LASER_OVERLAY_NAME) -> None:
        self._key = str(key)
        self._name = str(name)
        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._texture: Any | None = None
        self._visible = False
        self._available = False

    @property
    def visible(self) -> bool:
        return self._visible

    def start(self) -> bool:
        if self._available:
            return True
        try:
            import openvr

            self._openvr = openvr
            self._overlay = openvr.IVROverlay()
            self._handle = self._overlay.createOverlay(self._key, self._name)
            self._texture = laser_texture()
            self._overlay.setOverlayRaw(
                self._handle, self._texture, LASER_TEXTURE_WIDTH, LASER_TEXTURE_HEIGHT, 4
            )
            self._overlay.setOverlayAlpha(self._handle, 1.0)
            self._overlay.setOverlaySortOrder(self._handle, 45)
        except Exception:
            logger.debug("Failed to create the laser overlay", exc_info=True)
            self._overlay = None
            self._handle = None
            return False
        self._available = True
        return True

    def show_ray(self, ray: Ray, length: float, head: Vector) -> None:
        if not self._available and not self.start():
            return
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return
        length = max(0.05, float(length))
        origin, direction = ray
        try:
            overlay.setOverlayWidthInMeters(handle, length / LASER_ASPECT)
            overlay.setOverlayTransformAbsolute(
                handle,
                openvr.TrackingUniverseStanding,
                _to_matrix(openvr, laser_transform(origin, direction, length, head)),
            )
            if not self._visible:
                overlay.showOverlay(handle)
                self._visible = True
        except Exception:
            logger.debug("Failed to place the laser", exc_info=True)

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        try:
            overlay.hideOverlay(handle)
        except Exception:
            logger.debug("Failed to hide the laser", exc_info=True)

    def stop(self) -> None:
        overlay, handle = self._overlay, self._handle
        self._available = False
        self._visible = False
        self._overlay = None
        self._handle = None
        if overlay is not None and handle is not None:
            try:
                overlay.hideOverlay(handle)
                overlay.destroyOverlay(handle)
            except Exception:
                logger.debug("Failed to destroy the laser overlay", exc_info=True)


class SteamVRWristPanel:
    """The dashboard panel on a controller, shown and hidden by a double twist."""

    def __init__(
        self,
        panel_factory: Callable[[], Any],
        *,
        hand: str = "left",
        width_meters: float = DEFAULT_WRIST_WIDTH_METERS,
        on_action: Callable[[str], None] | None = None,
        show_mode: str = DEFAULT_SHOW_MODE,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        self._panel_factory = panel_factory
        # Told when the gesture shows or hides the panel, for a cue.
        self._on_toggle = on_toggle
        self._show_mode = normalize_show_mode(show_mode)
        self._look_since: float | None = None
        self._unlook_since: float | None = None
        self._panel: Any | None = None
        self._hand = "right" if str(hand).lower() == "right" else "left"
        self._width_meters = max(0.1, min(1.0, float(width_meters)))
        self._on_action = on_action
        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._uploader: OverlayTextureUploader | None = None
        self._laser = SteamVRLaser()
        self._twist = TwistDetector()
        self._available = False
        self._visible = False
        self._shown = False
        # Taken out of the headset's picture for a screenshot read; the
        # twist state is kept so it comes back as it was.
        self._suspended = False
        self._attached_index: int | None = None
        self._hover: str | None = None
        self._pressed: str | None = None
        self._cursor: tuple[float, float] | None = None
        self._hit_distance: float | None = None
        self._trigger_was_down = False
        self._dirty = True
        self._dwell_target: str | None = None
        self._dwell_since = 0.0
        self._dwell_fired = False
        self._dwell_fraction = 0.0
        self._dwell_logged = False

    # ------------------------------------------------------------ status
    @property
    def available(self) -> bool:
        return self._available

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def hand(self) -> str:
        return self._hand

    @property
    def panel(self) -> Any | None:
        return self._panel

    @property
    def show_mode(self) -> str:
        return self._show_mode

    def set_show_mode(self, mode: str) -> None:
        wanted = normalize_show_mode(mode)
        if wanted == self._show_mode:
            return
        self._show_mode = wanted
        self._look_since = None
        self._unlook_since = None
        self._twist = TwistDetector()
        # "always" shows it now; the other modes start hidden until asked.
        self._shown = wanted == "always"
        self._set_visible(self._shown and not self._suspended)

    @property
    def pointing_hand(self) -> str:
        """The hand whose laser works this panel: the one not wearing it."""

        return "left" if self._hand == "right" else "right"

    @property
    def pointer_on_panel(self) -> bool:
        """True while the laser rests on the panel, so nothing else should
        take that hand's trigger."""

        return self._visible and self._cursor is not None

    def _ok(self, action: Callable[[], Any], label: str) -> bool:
        try:
            action()
        except Exception:
            logger.debug("Wrist panel %s failed", label, exc_info=True)
            return False
        return True

    # ------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        """Create the overlay. Needs an initialised OpenVR runtime."""

        if self._available:
            return True
        try:
            import openvr
        except Exception:
            logger.debug("openvr unavailable for the wrist panel", exc_info=True)
            return False
        self._openvr = openvr
        try:
            self._overlay = openvr.IVROverlay()
            self._handle = self._overlay.createOverlay(WRIST_OVERLAY_KEY, WRIST_OVERLAY_NAME)
        except Exception:
            logger.debug("Failed to create the wrist panel overlay", exc_info=True)
            self._overlay = None
            self._handle = None
            return False
        overlay, handle = self._overlay, self._handle
        self._uploader = OverlayTextureUploader(openvr, overlay, handle)
        if self._ensure_panel() is None:
            self.stop()
            return False
        self._ok(lambda: overlay.setOverlayWidthInMeters(handle, self._width_meters), "setOverlayWidthInMeters")
        self._ok(lambda: overlay.setOverlayAlpha(handle, 1.0), "setOverlayAlpha")
        # Above the subtitle panel and the card: a control the player raised
        # their hand for must not sit behind a subtitle.
        self._ok(lambda: overlay.setOverlaySortOrder(handle, 40), "setOverlaySortOrder")
        self._available = True
        self._dirty = True
        attached = self._attach()
        logger.info(
            "Wrist panel ready on the %s hand (controller %s)",
            self._hand,
            "tracked" if attached else "not tracked yet",
        )
        return True

    def stop(self) -> None:
        overlay, handle = self._overlay, self._handle
        self._available = False
        self._visible = False
        self._shown = False
        self._overlay = None
        self._handle = None
        self._attached_index = None
        self._laser.stop()
        uploader, self._uploader = self._uploader, None
        if uploader is not None:
            uploader.release()
        if overlay is not None and handle is not None:
            try:
                overlay.hideOverlay(handle)
                overlay.destroyOverlay(handle)
            except Exception:
                logger.debug("Failed to destroy the wrist panel overlay", exc_info=True)
        panel, self._panel = self._panel, None
        if panel is not None and hasattr(panel, "deleteLater"):
            try:
                panel.deleteLater()
            except Exception:
                logger.debug("Failed to release the wrist panel", exc_info=True)

    def set_hand(self, hand: str) -> None:
        wanted = "right" if str(hand).lower() == "right" else "left"
        if wanted == self._hand:
            return
        self._hand = wanted
        self._attached_index = None
        self._twist = TwistDetector()
        self._attach()

    def toggle(self) -> bool:
        """Show or hide by hand (the settings page, a test); returns the new state."""

        self._shown = not self._shown
        self._set_visible(self._shown and not self._suspended)
        return self._shown

    @property
    def suspended(self) -> bool:
        return self._suspended

    def suspend(self) -> None:
        """Take the panel and its laser out of the picture, e.g. for a capture."""

        self._suspended = True
        self._set_visible(False)

    def resume(self) -> None:
        self._suspended = False
        self._set_visible(self._shown)

    def _ensure_panel(self) -> Any | None:
        if self._panel is None:
            try:
                self._panel = self._panel_factory()
            except Exception:
                logger.exception("Failed to build the wrist panel")
                return None
        return self._panel

    # ------------------------------------------------------------ poses
    def _controller_index(self, hand: str) -> int | None:
        openvr = self._openvr
        if openvr is None:
            return None
        role = (
            openvr.TrackedControllerRole_RightHand
            if hand == "right"
            else openvr.TrackedControllerRole_LeftHand
        )
        try:
            index = openvr.VRSystem().getTrackedDeviceIndexForControllerRole(role)
        except Exception:
            return None
        if index == openvr.k_unTrackedDeviceIndexInvalid:
            return None
        return int(index)

    def _attach(self) -> bool:
        if not self._available or self._overlay is None or self._handle is None:
            return False
        index = self._controller_index(self._hand)
        if index is None:
            if self._attached_index is not None:
                logger.info("Wrist panel: the %s controller is gone", self._hand)
            self._attached_index = None
            return False
        if index == self._attached_index:
            return True
        try:
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                self._handle, index, _to_matrix(self._openvr, local_transform())
            )
        except Exception:
            logger.debug("Failed to attach the wrist panel", exc_info=True)
            return False
        self._attached_index = index
        logger.info("Wrist panel attached to the %s controller (device %d)", self._hand, index)
        return True

    def _poses(self) -> tuple[list[list[float]] | None, list[list[float]] | None, list[list[float]] | None]:
        """(head, this hand's controller, the other hand's controller), each None when not tracked."""

        openvr = self._openvr
        if openvr is None:
            return None, None, None
        try:
            poses = openvr.VRSystem().getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0.0, openvr.k_unMaxTrackedDeviceCount
            )
        except Exception:
            return None, None, None

        def pose_of(index: int | None):
            if index is None or index < 0 or index >= len(poses):
                return None
            entry = poses[index]
            if not bool(getattr(entry, "bPoseIsValid", False)):
                return None
            return _rows(entry.mDeviceToAbsoluteTracking)

        other = "left" if self._hand == "right" else "right"
        return (
            pose_of(int(openvr.k_unTrackedDeviceIndex_Hmd)),
            pose_of(self._controller_index(self._hand)),
            pose_of(self._controller_index(other)),
        )

    # ------------------------------------------------------------ content
    def set_state(self, state: dict) -> None:
        panel = self._ensure_panel()
        if panel is None:
            return
        try:
            if panel.set_state(state):
                self._dirty = True
        except Exception:
            logger.debug("Wrist panel state update failed", exc_info=True)

    def push(self) -> None:
        if not self._available or self._overlay is None or self._handle is None:
            return
        panel = self._ensure_panel()
        if panel is None:
            return
        overlay, handle = self._overlay, self._handle
        try:
            render_image = getattr(panel, "render_image", None)
            if self._uploader is not None and callable(render_image):
                extra = {"dwell": self._dwell_fraction} if self._dwell_fraction > 0 else {}
                image = render_image(hover=self._hover, pressed=self._pressed, cursor=self._cursor, **extra)
                if self._uploader.upload_image(image):
                    self._dirty = False
                    # Keep the panel its intended size if the picture had to
                    # be letterboxed on the texture pinned by the first upload.
                    quad_width = self._uploader.quad_width(self._width_meters)
                    if abs(quad_width - getattr(self, "_applied_quad_width", self._width_meters)) > 1e-4:
                        self._applied_quad_width = quad_width
                        self._ok(
                            lambda: overlay.setOverlayWidthInMeters(handle, quad_width),
                            "setOverlayWidthInMeters",
                        )
                    return
            buffer, width, height = panel.render_rgba(hover=self._hover, pressed=self._pressed)
        except Exception:
            logger.debug("Wrist panel render failed", exc_info=True)
            return
        if self._ok(lambda: overlay.setOverlayRaw(handle, buffer, width, height, 4), "setOverlayRaw"):
            self._dirty = False

    def _set_visible(self, wanted: bool) -> None:
        if wanted == self._visible or self._overlay is None or self._handle is None:
            return
        overlay, handle = self._overlay, self._handle
        if wanted:
            self._dirty = True
            if self._ok(lambda: overlay.showOverlay(handle), "showOverlay"):
                self._visible = True
        else:
            self._ok(lambda: overlay.hideOverlay(handle), "hideOverlay")
            self._visible = False
            self._hover = None
            self._pressed = None
            self._cursor = None
            self._laser.hide()

    # ------------------------------------------------------------ input
    def poll(
        self,
        *,
        pointer: Ray | None = None,
        trigger_down: bool | None = None,
        allow_input: bool = True,
        now: float | None = None,
    ) -> None:
        """Every tick: read the twist, then hover, laser and click.

        ``pointer`` is the pointing controller's ray in the standing universe
        (from the tip pose action); without one the other controller's own
        pose is used. ``trigger_down`` is that controller's trigger; a press
        that starts and ends on the same button is a click.
        """

        if not self._available:
            return
        moment = time.monotonic() if now is None else float(now)
        head, controller, other = self._poses()
        if controller is None:
            self._set_visible(False)
            self._trigger_was_down = bool(trigger_down)
            return
        self._attach()
        was_shown = self._shown
        if self._show_mode == "always":
            self._shown = True
        elif self._show_mode == "look":
            self._shown = self._update_look(head, controller, moment)
        else:
            sideways, forward = controller_axes(controller)
            banked = self._twist.count
            if self._twist.update(sideways, forward, moment):
                self._shown = not self._shown
                logger.info("Wrist panel %s by a double twist", "shown" if self._shown else "hidden")
            elif self._twist.count > banked:
                # One twist banked: says in the log how far the wrist actually rolled.
                logger.info(
                    "Wrist twist %d/%d (rolled %.0f deg)", self._twist.count, TWISTS_TO_TOGGLE, self._twist.last_peak
                )
        if self._shown != was_shown and callable(self._on_toggle):
            try:
                self._on_toggle(self._shown)
            except Exception:
                logger.debug("Wrist panel toggle callback failed", exc_info=True)
        self._set_visible(self._shown and not self._suspended)
        if not self._visible:
            self._trigger_was_down = bool(trigger_down)
            return

        ray = pointer
        if ray is None and other is not None:
            ray = ray_from_pose(other)
        hover = None
        self._hit_distance = None
        if ray is not None and allow_input:
            hover = self._hit(ray)
        elif ray is None:
            self._cursor = None
        if hover != self._hover:
            self._hover = hover
            self._dirty = True
        if ray is not None and head is not None:
            length = self._hit_distance if self._hit_distance else LASER_IDLE_LENGTH_METERS
            self._laser.show_ray(ray, length, (head[0][3], head[1][3], head[2][3]))
        else:
            self._laser.hide()
        down = bool(trigger_down)
        if allow_input and trigger_down is None:
            # No trigger to read: SteamVR has not bound the app's actions on
            # this controller. Resting the laser on a button clicks it.
            self._dwell(hover, moment)
        else:
            self._reset_dwell()
        if allow_input:
            if down and not self._trigger_was_down:
                self._pressed = hover
                self._dirty = True
            elif not down and self._trigger_was_down:
                pressed, self._pressed = self._pressed, None
                self._dirty = True
                if pressed is not None and pressed == hover and callable(self._on_action):
                    self._fire(pressed)
        elif self._pressed is not None:
            self._pressed = None
            self._dirty = True
        self._trigger_was_down = down
        if self._dirty:
            self.push()

    def _update_look(self, head, controller, now: float) -> bool:
        """"look" mode: up while the player looks at the back of the hand."""

        if head is None:
            return self._shown
        try:
            if self._shown:
                looking = wrist_is_looked_at(
                    head,
                    controller,
                    facing_degrees=LOOK_KEEP_FACING_DEGREES,
                    gaze_degrees=LOOK_KEEP_GAZE_DEGREES,
                    max_distance=LOOK_MAX_DISTANCE_M * 1.2,
                )
            else:
                looking = wrist_is_looked_at(
                    head,
                    controller,
                    facing_degrees=LOOK_FACING_DEGREES,
                    gaze_degrees=LOOK_GAZE_DEGREES,
                    max_distance=LOOK_MAX_DISTANCE_M,
                )
        except (TypeError, ValueError, IndexError):
            return self._shown
        if looking:
            self._unlook_since = None
            if self._shown:
                return True
            if self._look_since is None:
                self._look_since = now
            return now - self._look_since >= LOOK_SHOW_SECONDS
        self._look_since = None
        if not self._shown:
            return False
        if self._unlook_since is None:
            self._unlook_since = now
        return now - self._unlook_since < LOOK_HIDE_SECONDS

    def _fire(self, action: str) -> None:
        if not callable(self._on_action):
            return
        try:
            self._on_action(action)
        except Exception:
            logger.exception("Wrist panel action %s failed", action)

    def _reset_dwell(self) -> None:
        if self._dwell_fraction:
            self._dirty = True
        self._dwell_target = None
        self._dwell_since = 0.0
        self._dwell_fired = False
        self._dwell_fraction = 0.0

    def _dwell(self, hover: str | None, now: float) -> None:
        if not self._dwell_logged:
            self._dwell_logged = True
            logger.info(
                "Wrist panel: no trigger binding is active, so a button is clicked by "
                "resting the laser on it for %.1f s", DWELL_SECONDS
            )
        if hover is None or hover != self._dwell_target:
            self._reset_dwell()
            if hover is not None:
                self._dwell_target = hover
                self._dwell_since = now
            return
        if self._dwell_fired:
            return
        fraction = min(1.0, (now - self._dwell_since) / DWELL_SECONDS)
        if abs(fraction - self._dwell_fraction) > 0.02:
            self._dwell_fraction = fraction
            self._dirty = True
        if fraction >= 1.0:
            self._dwell_fired = True
            self._dwell_fraction = 0.0
            self._dirty = True
            self._fire(hover)

    def _hit(self, ray: Ray) -> str | None:
        """The button under the ray, via the overlay's own intersection test.

        Also records where the ray lands, in panel pixels, for the cursor,
        and how far away, for the laser's length.
        """

        openvr, overlay, handle, panel = self._openvr, self._overlay, self._handle, self._panel
        if openvr is None or overlay is None or handle is None or panel is None:
            return None
        try:
            params = openvr.VROverlayIntersectionParams_t()
            origin, direction = ray
            for i in range(3):
                params.vSource.v[i] = float(origin[i])
                params.vDirection.v[i] = float(direction[i])
            params.eOrigin = openvr.TrackingUniverseStanding
            hit, results = overlay.computeOverlayIntersection(handle, params)
        except Exception:
            logger.debug("Wrist panel intersection failed", exc_info=True)
            return None
        if not hit:
            if self._cursor is not None:
                self._cursor = None
                self._dirty = True
            return None
        width, height = panel.size
        u = float(results.vUVs.v[0])
        v = float(results.vUVs.v[1])
        # Texture v counts from the bottom unless the picture went up through
        # the flipped GL path, which makes it count from the top.
        uploader = self._uploader
        if not (uploader is not None and uploader.mouse_y_is_top_down):
            v = 1.0 - v
        if uploader is not None:
            # A panel picture letterboxed on the pinned texture: map through it.
            u, v = uploader.picture_fraction(u, v)
        x = u * width
        y = v * height
        cursor = (x, y)
        if self._cursor is None or abs(cursor[0] - self._cursor[0]) > 2 or abs(cursor[1] - self._cursor[1]) > 2:
            self._cursor = cursor
            self._dirty = True
        try:
            self._hit_distance = float(getattr(results, "fDistance", 0.0) or 0.0) or None
        except (TypeError, ValueError):
            self._hit_distance = None
        try:
            return panel.hit_test(x, y)
        except Exception:
            return None
