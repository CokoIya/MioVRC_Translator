# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""SteamVR overlay backend for the reverse-translation subtitles.

Implements the same four methods as the desktop floating window, so the
existing overlay pipeline routes to a headset without any change upstream.

Two properties matter more than anything else here:

* **A player without SteamVR must never notice this exists.** Every OpenVR
  entry point is behind ``_safe``; a failure disables the backend and records a
  reason for the settings page rather than raising into the audio pipeline.
* **The panel's transparency is per-pixel, not per-overlay.**
  ``setOverlayAlpha`` fades the text along with the plate, which is the
  opposite of readable. The alpha is baked into the RGBA buffer instead and the
  overlay itself stays fully opaque.
* **The panel must never be interactive while it is merely visible.**
  ``VROverlayFlags_MakeOverlaysInteractiveIfVisible`` hands the controllers to
  the overlay for as long as it is shown - and the subtitle panel is shown all
  the time, so the player could not move, grab or open menus in the game at
  all. Interaction is switched on only for a short *edit mode* the player asks
  for, and off again the moment the drag ends or times out.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from src.core.overlay_texture import OverlayTextureUploader
from src.core.vr_geometry import (
    REACH_GAIN,
    clamp_radius,
    facing_transform,
    pitch_of,
    position_on_sphere,
    radius_of,
    reach_of,
    snap_to_sphere,
    sphere_hit,
    yaw_of,
)

logger = logging.getLogger(__name__)

OVERLAY_KEY = "mio.translator.subtitles"
OVERLAY_NAME = "Mio Translator Subtitles"

# A board, not a card: at the default distance this spans about the width
# OVR Overlay Translator's subtitle board does.
DEFAULT_WIDTH_METERS = 1.6
MIN_WIDTH_METERS = 0.3
MAX_WIDTH_METERS = 4.0
# Sizes a player can pick without touching a slider. Keys are stable config
# values; the labels live with the UI.
SIZE_PRESETS = (
    ("small", 0.7),
    ("medium", 1.1),
    ("large", DEFAULT_WIDTH_METERS),
    ("xlarge", 2.2),
)
DEFAULT_CURVATURE = 0.12
# Straight ahead, a little below the eye line, far enough not to strain focus.
DEFAULT_POSITION = (0.0, -0.32, -1.5)
MIN_DISTANCE = 0.4
MAX_DISTANCE = 6.0
DEFAULT_DISTANCE_FALLBACK = 1.5
# Live subtitles change several times a second while someone speaks; the
# texture is never re-uploaded faster than this, and the latest picture
# always lands on the next tick.
PUSH_MIN_INTERVAL_S = 0.08
# Fixed texture sizes (see overlay_texture): the board's width and its
# tallest allowed panel; the hand panel's width with room for a tall card.
# vr_overlay_panel's sizes must fit inside these.
PANEL_CANVAS = (1200, 1100)
HAND_CANVAS = (768, 1100)
# Ask the runtime for poses a little ahead so the panel keeps up with the hand.
POSE_PREDICTION_S = 0.02
# Drain at most this many events per tick so one busy frame cannot stall the
# UI thread.
MAX_EVENTS_PER_POLL = 64


def clamp_width_meters(value: object, default: float = DEFAULT_WIDTH_METERS) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return max(MIN_WIDTH_METERS, min(MAX_WIDTH_METERS, parsed))


def clamp_position(value: object) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return DEFAULT_POSITION
    try:
        x, y, z = (float(component) for component in value)
    except (TypeError, ValueError):
        return DEFAULT_POSITION
    if any(component != component for component in (x, y, z)):
        return DEFAULT_POSITION
    if abs(x) < 1e-6 and abs(z) < 1e-6:
        # Straight above or below the head has no direction to face.
        return (0.0, max(-2.0, min(2.0, y)), -DEFAULT_DISTANCE_FALLBACK)
    # The panel lives on a sphere around the head: any direction, at a
    # usable reading distance. A position already on its sphere is kept
    # exactly, so saved values do not drift through rounding.
    snapped = snap_to_sphere((x, y, z))
    if all(abs(a - b) < 1e-6 for a, b in zip(snapped, (x, y, z))):
        return (x, y, z)
    return snapped


class SteamVROverlayBackend:
    """Overlay backend that draws subtitles inside the headset."""

    def __init__(
        self,
        panel_factory: Callable[[], Any],
        *,
        width_meters: float = DEFAULT_WIDTH_METERS,
        position: tuple[float, float, float] = DEFAULT_POSITION,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._panel_factory = panel_factory
        self._panel: Any | None = None
        self._width_meters = clamp_width_meters(width_meters)
        self._position = clamp_position(position)
        self._on_error = on_error

        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._available = False
        self._visible = False
        self._unavailable_reason = ""

        self._interactive = False
        self._edit_mode = False
        self._uploader: OverlayTextureUploader | None = None
        self._push_pending = False
        self._last_push_at = 0.0
        self._press_device: int | None = None
        self._press_started_at = 0.0
        self._grabbing = False
        self._grab_offset: tuple[float, float, float] | None = None
        self._grab_start_position: tuple[float, float, float] | None = None
        self._grab_reach = 0.0
        self._grab_radius = 0.0
        self._on_position_committed: Callable[[tuple[float, float, float]], None] | None = None

    # ------------------------------------------------------------------ status
    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def _fail(self, reason: str) -> None:
        self._unavailable_reason = reason
        self._available = False
        logger.info("SteamVR overlay unavailable: %s", reason)
        if callable(self._on_error):
            try:
                self._on_error(reason)
            except Exception:
                logger.debug("SteamVR overlay error callback failed", exc_info=True)

    def _safe(self, action: Callable[[], Any], label: str) -> Any:
        """Run one OpenVR call; a headset problem must never reach the pipeline."""

        try:
            return action()
        except Exception as exc:
            logger.debug("SteamVR overlay %s failed", label, exc_info=True)
            self._fail(f"{label}: {type(exc).__name__}")
            return None

    def _ok(self, action: Callable[[], Any], label: str) -> bool:
        """True when the call ran without raising.

        pyopenvr's setters are void and report failure by raising, so their
        return value (always None) says nothing; only the exception does.
        """

        try:
            action()
        except Exception as exc:
            logger.debug("SteamVR overlay %s failed", label, exc_info=True)
            self._fail(f"{label}: {type(exc).__name__}")
            return False
        return True

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        if self._available:
            return True
        try:
            import openvr
        except Exception as exc:
            self._fail(f"openvr import failed: {type(exc).__name__}")
            return False
        self._openvr = openvr

        # Ask as a background app first. That call never launches SteamVR,
        # where an overlay init starts it - and a PC player who merely left
        # the headset switch on must not get SteamVR opened for them every
        # time Mio starts. The retry timer picks the headset up later, once
        # SteamVR is actually running.
        background = getattr(openvr, "VRApplication_Background", None)
        if background is not None:
            try:
                openvr.init(background)
            except Exception as exc:
                self._fail(_init_reason(exc))
                self._openvr = None
                return False
            try:
                openvr.shutdown()
            except Exception:
                logger.debug("openvr shutdown after the background probe failed", exc_info=True)

        try:
            openvr.init(openvr.VRApplication_Overlay)
        except Exception as exc:
            # The usual case: SteamVR simply is not running.
            self._fail(_init_reason(exc))
            return False

        try:
            self._overlay = openvr.IVROverlay()
            self._handle = self._overlay.createOverlay(OVERLAY_KEY, OVERLAY_NAME)
        except Exception as exc:
            self._fail(f"createOverlay: {type(exc).__name__}")
            self._shutdown_runtime()
            return False
        self._uploader = OverlayTextureUploader(
            openvr, self._overlay, self._handle, canvas=PANEL_CANVAS
        )
        self._applied_scale = 1.0

        self._available = True
        self._unavailable_reason = ""
        # Build the panel now. Doing it on the first subtitle costs ~120 ms,
        # which lands as a visible hitch on the very first thing a player sees.
        self._ensure_panel()
        self._apply_geometry()
        return True

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
                logger.debug("Failed to destroy SteamVR overlay", exc_info=True)
        self._interactive = False
        self._edit_mode = False
        self._press_device = None
        self._grabbing = False
        self._grab_offset = None
        self._shutdown_runtime()
        panel, self._panel = self._panel, None
        if panel is not None:
            try:
                panel.deleteLater()
            except Exception:
                logger.debug("Failed to release VR panel", exc_info=True)

    def _shutdown_runtime(self) -> None:
        module, self._openvr = self._openvr, None
        if module is None:
            return
        try:
            module.shutdown()
        except Exception:
            logger.debug("openvr shutdown failed", exc_info=True)

    # ------------------------------------------------------------------ interaction
    def set_position_committed_callback(
        self, callback: Callable[[tuple[float, float, float]], None] | None
    ) -> None:
        """Called once when the player lets go, so the move can be persisted."""

        self._on_position_committed = callback

    @property
    def grabbing(self) -> bool:
        return self._grabbing

    @property
    def edit_mode(self) -> bool:
        return self._edit_mode

    @property
    def openvr_module(self) -> Any | None:
        """The imported runtime, for callers that need the compositor."""

        return self._openvr

    def dashboard_visible(self) -> bool:
        overlay = self._overlay
        if overlay is None:
            return False
        try:
            return bool(overlay.isDashboardVisible())
        except Exception:
            return False

    @property
    def locked(self) -> bool:
        """A locked panel cannot be dragged: edit mode is refused."""

        return bool(getattr(self, "_locked", False))

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        if self._locked and self._edit_mode:
            self.set_edit_mode(False)

    def set_edit_mode(self, enabled: bool) -> bool:
        """Hand the controllers to the panel (True) or back to the game (False).

        While on, SteamVR turns the laser and trigger into mouse events on the
        overlay so it can be dragged; the game receives nothing, which is why
        this must be brief and explicit. Returns the mode actually in effect.
        A locked panel never enters it.
        """

        enabled = bool(enabled)
        if enabled and self.locked:
            return False
        openvr = self._openvr
        overlay, handle = self._overlay, self._handle
        if openvr is None or overlay is None or handle is None:
            self._edit_mode = False
            self._interactive = False
            return False
        if enabled == self._edit_mode and enabled == self._interactive:
            return self._edit_mode

        def apply() -> None:
            overlay.setOverlayFlag(
                handle,
                openvr.VROverlayFlags_MakeOverlaysInteractiveIfVisible,
                enabled,
            )
            overlay.setOverlayInputMethod(
                handle,
                openvr.VROverlayInputMethod_Mouse
                if enabled
                else openvr.VROverlayInputMethod_None,
            )

        applied = self._ok(apply, "setEditMode")
        self._interactive = enabled and applied
        self._edit_mode = enabled and applied
        if not self._edit_mode:
            self._end_grab()
        return self._edit_mode

    def poll(self) -> None:
        """Pump overlay events and advance a drag. Safe to call every tick."""

        if not self._available or self._overlay is None or self._handle is None:
            return
        openvr = self._openvr
        overlay, handle = self._overlay, self._handle
        event = openvr.VREvent_t()
        try:
            # pyopenvr returns (has_event, event), and a tuple is always truthy;
            # looping on the call itself spins forever and freezes the UI thread.
            # The bound is a second guard: a pointer resting on the overlay
            # produces a steady stream of mouse-move events.
            for _ in range(MAX_EVENTS_PER_POLL):
                has_event, event = overlay.pollNextOverlayEvent(handle, event)
                if not has_event:
                    break
                self._handle_event(event)
        except Exception:
            logger.debug("SteamVR overlay event poll failed", exc_info=True)
            return
        self._advance_grab()
        if self._push_pending and time.monotonic() - self._last_push_at >= PUSH_MIN_INTERVAL_S:
            self._upload_panel()

    def _pointing_device(self, event: Any) -> int | None:
        """The controller whose laser produced a mouse event.

        Mouse events do not reliably carry the controller index, so fall back
        to the device SteamVR says holds the pointer.
        """

        openvr = self._openvr
        overlay = self._overlay
        invalid = int(getattr(openvr, "k_unTrackedDeviceIndexInvalid", 0xFFFFFFFF))
        index = int(getattr(event, "trackedDeviceIndex", invalid) or 0)
        if 0 < index < invalid:
            return index
        if overlay is None:
            return None
        try:
            index = int(overlay.getPrimaryDashboardDevice())
        except Exception:
            logger.debug("getPrimaryDashboardDevice failed", exc_info=True)
            return None
        return index if 0 < index < invalid else None

    def _handle_event(self, event: Any) -> None:
        openvr = self._openvr
        kind = int(getattr(event, "eventType", 0))
        if kind == openvr.VREvent_MouseButtonDown:
            self._press_device = self._pointing_device(event)
            self._press_started_at = time.monotonic()
        elif kind in (openvr.VREvent_MouseButtonUp, openvr.VREvent_FocusLeave):
            self._end_grab()
        elif kind == openvr.VREvent_Quit:
            # SteamVR is shutting down; let go of the runtime before it goes.
            logger.info("SteamVR is quitting; releasing the subtitle overlay")
            self.stop()

    def _device_ray_in_hmd_space(
        self, device_index: int
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """A controller's position and pointing direction relative to the head."""

        openvr = self._openvr
        if openvr is None:
            return None
        try:
            poses = openvr.VRSystem().getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding,
                POSE_PREDICTION_S,
                openvr.k_unMaxTrackedDeviceCount,
            )
        except Exception:
            logger.debug("Failed to read tracked device poses", exc_info=True)
            return None
        hmd = poses[openvr.k_unTrackedDeviceIndex_Hmd]
        device = poses[device_index] if 0 <= device_index < len(poses) else None
        if device is None or not hmd.bPoseIsValid or not device.bPoseIsValid:
            return None
        head = hmd.mDeviceToAbsoluteTracking
        hand = device.mDeviceToAbsoluteTracking
        dx = hand[0][3] - head[0][3]
        dy = hand[1][3] - head[1][3]
        dz = hand[2][3] - head[2][3]
        # A device points down its own -z axis.
        fx, fy, fz = -hand[0][2], -hand[1][2], -hand[2][2]

        # The head rotation is orthonormal, so its inverse is its transpose.
        def to_head(x: float, y: float, z: float) -> tuple[float, float, float]:
            return (
                head[0][0] * x + head[1][0] * y + head[2][0] * z,
                head[0][1] * x + head[1][1] * y + head[2][1] * z,
                head[0][2] * x + head[1][2] * y + head[2][2] * z,
            )

        return to_head(dx, dy, dz), to_head(fx, fy, fz)

    def _device_in_hmd_space(self, device_index: int) -> tuple[float, float, float] | None:
        """Where a controller sits relative to the headset, in metres."""

        ray = self._device_ray_in_hmd_space(device_index)
        return ray[0] if ray is not None else None

    def _advance_grab(self) -> None:
        """Move the panel over its sphere to wherever the laser points.

        Edit mode is explicit, so the drag starts on the press itself; the
        old hold-to-grab delay only made the panel feel stuck. The laser's
        landing spot on the panel is remembered so the panel keeps its
        offset under the pointer instead of snapping its centre to it, and
        the arm's reach sets the distance: push the hand out and the panel
        moves away, pull it back and the panel comes closer.
        """

        if self._press_device is None:
            return
        ray = self._device_ray_in_hmd_space(self._press_device)
        if ray is None:
            return
        origin, direction = ray
        if not self._grabbing:
            hit = sphere_hit(origin, direction, radius_of(self._position))
            if hit is None:
                return
            self._grabbing = True
            self._grab_start_position = self._position
            self._grab_reach = reach_of(origin)
            self._grab_radius = radius_of(self._position)
            self._grab_offset = (
                yaw_of(self._position) - yaw_of(hit),
                pitch_of(self._position) - pitch_of(hit),
                0.0,
            )
            return
        radius = clamp_radius(
            self._grab_radius + (reach_of(origin) - self._grab_reach) * REACH_GAIN
        )
        hit = sphere_hit(origin, direction, radius)
        if hit is None:
            return
        offset = self._grab_offset or (0.0, 0.0, 0.0)
        self.set_position(
            position_on_sphere(yaw_of(hit) + offset[0], pitch_of(hit) + offset[1], radius)
        )

    def _end_grab(self) -> None:
        was_grabbing = self._grabbing
        start = self._grab_start_position
        self._press_device = None
        self._grabbing = False
        self._grab_offset = None
        self._grab_start_position = None
        # A tap that never moved the panel is not a move; it must not
        # rewrite the saved position.
        moved = start is None or any(
            abs(a - b) > 1e-6 for a, b in zip(start, self._position)
        )
        if was_grabbing and moved and callable(self._on_position_committed):
            try:
                self._on_position_committed(self._position)
            except Exception:
                logger.debug("Failed to persist the VR panel position", exc_info=True)

    # ------------------------------------------------------------------ geometry
    def set_width_meters(self, meters: float) -> None:
        self._width_meters = clamp_width_meters(meters)
        self._apply_geometry()

    def set_position(self, position: tuple[float, float, float]) -> None:
        self._position = clamp_position(position)
        self._apply_geometry()

    @property
    def width_meters(self) -> float:
        return self._width_meters

    @property
    def position(self) -> tuple[float, float, float]:
        return self._position

    def _picture_scale(self) -> float:
        uploader = self._uploader
        try:
            return float(uploader.scale) if uploader is not None else 1.0
        except (TypeError, ValueError):
            return 1.0

    def _apply_geometry(self) -> None:
        if not self._available or self._overlay is None or self._handle is None:
            return
        openvr = self._openvr
        overlay, handle = self._overlay, self._handle

        def apply() -> None:
            # The picture sits centred on a fixed canvas; the overlay is as
            # wide as the canvas, so the width scales with the picture.
            self._applied_scale = self._picture_scale()
            overlay.setOverlayWidthInMeters(handle, self._width_meters * self._applied_scale)
            overlay.setOverlayCurvature(handle, DEFAULT_CURVATURE)
            # The plate's transparency lives in the pixels, so the overlay
            # itself must stay fully opaque or the text fades with it.
            overlay.setOverlayAlpha(handle, 1.0)
            matrix = openvr.HmdMatrix34_t()
            rows = facing_transform(self._position)
            for r in range(3):
                for c in range(4):
                    matrix[r][c] = float(rows[r][c])
            overlay.setOverlayTransformTrackedDeviceRelative(
                handle, openvr.k_unTrackedDeviceIndex_Hmd, matrix
            )

        self._safe(apply, "setOverlayGeometry")

    # ------------------------------------------------------------------ panel
    def _ensure_panel(self) -> Any | None:
        if self._panel is None:
            try:
                self._panel = self._panel_factory()
            except Exception:
                logger.exception("Failed to build the VR subtitle panel")
                return None
        return self._panel

    def _push_panel(self) -> None:
        """Show the panel's current picture, no faster than the upload cap."""

        if not self._available or self._overlay is None or self._handle is None:
            return
        if time.monotonic() - self._last_push_at < PUSH_MIN_INTERVAL_S:
            # The next poll tick will upload the latest picture.
            self._push_pending = True
            return
        self._upload_panel()

    def _upload_panel(self) -> None:
        self._push_pending = False
        panel = self._ensure_panel()
        if panel is None:
            return
        self._last_push_at = time.monotonic()
        uploader = self._uploader
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        try:
            render_image = getattr(panel, "render_image", None)
            if uploader is not None and callable(render_image):
                if uploader.upload_image(render_image()):
                    self._note_upload(True)
                    self._follow_picture_scale()
                    return
            buffer, width, height = panel.render_rgba()
        except Exception:
            logger.exception("Failed to render the VR subtitle panel")
            return
        self._note_upload(
            self._ok(
                lambda: overlay.setOverlayRaw(handle, buffer, width, height, 4),
                "setOverlayRaw",
            )
        )
        self._follow_picture_scale()

    def _follow_picture_scale(self) -> None:
        """Re-apply the width when the picture's share of the canvas changed."""

        if abs(self._picture_scale() - getattr(self, "_applied_scale", 1.0)) > 1e-6:
            self._apply_geometry()

    def _note_upload(self, ok: bool) -> None:
        """Say at INFO when the panel's picture stops reaching the headset.

        Every upload used to fail at DEBUG only, so a player's log showed a
        panel that "never refreshed" with nothing to explain it.
        """

        failures = int(getattr(self, "_upload_failures", 0))
        if ok:
            if failures:
                logger.info("VR subtitle panel uploads work again after %d failures", failures)
            self._upload_failures = 0
            return
        failures += 1
        self._upload_failures = failures
        if failures == 1 or failures % 100 == 0:
            logger.info(
                "VR subtitle panel upload failed (%d in a row): %s",
                failures,
                getattr(self, "unavailable_reason", "") or "no reason recorded",
            )

    # ------------------------------------------------------------------ backend
    def show_message(self, message: Any) -> bool | None:
        panel = self._ensure_panel()
        if panel is None:
            return False
        translated = str(getattr(message, "translated_text", "") or "").strip()
        display = str(getattr(message, "display_text", "") or "").strip()
        original = str(getattr(message, "original_text", "") or "").strip()
        source = str(getattr(message, "source", "listen") or "listen")
        if not panel.add_message(
            translated=translated or display,
            original=original if translated else "",
            source=source,
        ):
            return False
        if not self._visible:
            self.reveal()
        else:
            self._push_panel()
        return True

    def set_listen_status(self, listening: bool) -> None:
        # The headset panel deliberately shows no status chrome; a waiting
        # indicator floating in the player's view is noise, not information.
        return None

    def reveal(self) -> None:
        if not self._available and not self.start():
            return
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        self._push_panel()
        if self._ok(lambda: overlay.showOverlay(handle), "showOverlay"):
            self._visible = True

    def hide(self) -> None:
        overlay, handle = self._overlay, self._handle
        self._visible = False
        if overlay is None or handle is None:
            return
        self._safe(lambda: overlay.hideOverlay(handle), "hideOverlay")


def _init_reason(exc: Exception) -> str:
    """Turn an OpenVR init failure into something a player can act on.

    pyopenvr reports these as ``VRInitError_Init_*`` names, so the comparison
    strips separators rather than looking for prose.
    """

    token = "".join(ch for ch in str(exc).lower() if ch.isalnum())
    if "pathregistrynotfound" in token or "installationnotfound" in token:
        return "steamvr_not_installed"
    if "notrunning" in token or "noserverforbackgroundapp" in token:
        return "steamvr_not_running"
    if "hmdnotfound" in token or "hmdnotfoundpresencefailed" in token:
        return "headset_not_found"
    return f"init_failed:{type(exc).__name__}"


HAND_OVERLAY_KEY = "mio.translator.screenshot"
HAND_OVERLAY_NAME = "Mio Translator Screenshot"
DEFAULT_HAND_WIDTH_METERS = 0.32
# Sitting just above the controller, tilted back toward the face, the way a
# wrist display reads without the player having to twist their arm.
DEFAULT_HAND_OFFSET = (0.0, 0.06, -0.14)


class SteamVRHandPanel:
    """A small panel pinned to one controller, for screenshot results.

    Separate from the subtitle overlay on purpose: it is attached to a hand
    rather than the view, it is dismissed rather than replaced, and a failure
    to show it must not disturb subtitles that are already working.
    """

    def __init__(
        self,
        panel_factory: Callable[[], Any],
        *,
        hand: str = "left",
        width_meters: float = DEFAULT_HAND_WIDTH_METERS,
    ) -> None:
        self._panel_factory = panel_factory
        self._panel: Any | None = None
        self._hand = "right" if str(hand).lower() == "right" else "left"
        self._width_meters = max(0.1, min(1.5, float(width_meters)))
        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._uploader: OverlayTextureUploader | None = None
        self._available = False
        self._visible = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def panel(self) -> Any | None:
        return self._panel

    def set_hand(self, hand: str) -> None:
        self._hand = "right" if str(hand).lower() == "right" else "left"
        self._attach()

    def start(self) -> bool:
        """Attach to the runtime SteamVROverlayBackend already initialised."""

        if self._available:
            return True
        try:
            import openvr
        except Exception:
            logger.debug("openvr unavailable for the hand panel", exc_info=True)
            return False
        self._openvr = openvr
        try:
            self._overlay = openvr.IVROverlay()
            self._handle = self._overlay.createOverlay(
                HAND_OVERLAY_KEY, HAND_OVERLAY_NAME
            )
            self._overlay.setOverlayWidthInMeters(self._handle, self._width_meters)
            self._overlay.setOverlayAlpha(self._handle, 1.0)
        except Exception:
            logger.debug("Failed to create the hand panel overlay", exc_info=True)
            self._overlay = None
            self._handle = None
            return False
        self._uploader = OverlayTextureUploader(
            openvr, self._overlay, self._handle, canvas=HAND_CANVAS
        )
        self._applied_scale = 1.0
        self._available = True
        self._ensure_panel()
        self._attach()
        return True

    def _apply_width(self) -> None:
        """The overlay is as wide as its canvas; scale so the picture keeps its width."""

        if self._overlay is None or self._handle is None:
            return
        uploader = self._uploader
        scale = float(getattr(uploader, "scale", 1.0) or 1.0) if uploader is not None else 1.0
        if abs(scale - getattr(self, "_applied_scale", 1.0)) <= 1e-6:
            return
        try:
            self._overlay.setOverlayWidthInMeters(self._handle, self._width_meters * scale)
            self._applied_scale = scale
        except Exception:
            logger.debug("Failed to resize the hand panel", exc_info=True)

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
                logger.debug("Failed to destroy the hand panel overlay", exc_info=True)
        panel, self._panel = self._panel, None
        if panel is not None:
            try:
                panel.deleteLater()
            except Exception:
                logger.debug("Failed to release the hand panel", exc_info=True)

    def _controller_index(self) -> int | None:
        openvr = self._openvr
        if openvr is None:
            return None
        role = (
            openvr.TrackedControllerRole_RightHand
            if self._hand == "right"
            else openvr.TrackedControllerRole_LeftHand
        )
        try:
            index = openvr.VRSystem().getTrackedDeviceIndexForControllerRole(role)
        except Exception:
            logger.debug("Failed to resolve the controller index", exc_info=True)
            return None
        if index == openvr.k_unTrackedDeviceIndexInvalid:
            return None
        return int(index)

    def _attach(self) -> None:
        if not self._available or self._overlay is None or self._handle is None:
            return
        openvr = self._openvr
        index = self._controller_index()
        if index is None:
            return
        try:
            matrix = openvr.HmdMatrix34_t()
            x, y, z = DEFAULT_HAND_OFFSET
            # Tilt the panel back about 45 degrees so it faces the player when
            # the controller is held naturally.
            matrix[0][0], matrix[0][1], matrix[0][2], matrix[0][3] = 1.0, 0.0, 0.0, x
            matrix[1][0], matrix[1][1], matrix[1][2], matrix[1][3] = 0.0, 0.7071, 0.7071, y
            matrix[2][0], matrix[2][1], matrix[2][2], matrix[2][3] = 0.0, -0.7071, 0.7071, z
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                self._handle, index, matrix
            )
        except Exception:
            logger.debug("Failed to attach the hand panel", exc_info=True)

    def _ensure_panel(self) -> Any | None:
        if self._panel is None:
            try:
                self._panel = self._panel_factory()
            except Exception:
                logger.exception("Failed to build the screenshot panel")
                return None
        return self._panel

    def push(self) -> None:
        if not self._available or self._overlay is None or self._handle is None:
            return
        panel = self._ensure_panel()
        if panel is None:
            return
        try:
            render_image = getattr(panel, "render_image", None)
            if self._uploader is not None and callable(render_image):
                if self._uploader.upload_image(render_image()):
                    self._apply_width()
                    return
            buffer, width, height = panel.render_rgba()
            self._overlay.setOverlayRaw(self._handle, buffer, width, height, 4)
            self._applied_scale = 1.0
            self._overlay.setOverlayWidthInMeters(self._handle, self._width_meters)
        except Exception:
            logger.debug("Failed to push the hand panel", exc_info=True)

    def show(self) -> None:
        if not self._available and not self.start():
            return
        self._attach()
        self.push()
        try:
            self._overlay.showOverlay(self._handle)
            self._visible = True
        except Exception:
            logger.debug("Failed to show the hand panel", exc_info=True)

    def hide(self) -> None:
        self._visible = False
        if self._overlay is None or self._handle is None:
            return
        try:
            self._overlay.hideOverlay(self._handle)
        except Exception:
            logger.debug("Failed to hide the hand panel", exc_info=True)
