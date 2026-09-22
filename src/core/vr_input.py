# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Controller button binding for screenshot translation.

A global press has to work while the player is pointing at nothing, so the
overlay's own mouse events - which only fire when the laser is on the panel -
cannot carry it. That leaves SteamVR's action system.

The legacy ``getControllerState`` path is not an option: measured against a
current driver it returns a failure code and an all-zero button mask, because
SteamVR retired it in favour of actions. Actions also give the player a
rebinding UI for free, which a hardcoded button never could.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# While SteamVR reports the actions unbound, the manifest is handed over
# again this often. A player's bindings only came alive after VRChat was
# restarted, which is when SteamVR re-read the controllers' profiles.
BINDING_REFRESH_SECONDS = 30.0

ACTION_SET = "/actions/mio"
TRANSLATE_ACTION = "/actions/mio/in/translate_screen"
# The button alone opened the frame whenever a player pressed B or Y for the
# game; the frame takes the controllers, so an accidental press locked them
# out. The button now only counts while both of these are held.
HOLD_TRIGGER_ACTION = "/actions/mio/in/hold_trigger"
HOLD_GRIP_ACTION = "/actions/mio/in/hold_grip"
# Every other button on either controller. A shown translation goes away on
# the first press of anything, so the player never has to wait it out.
DISMISS_ACTION = "/actions/mio/in/any_button"
# Where each controller's tip points, for aiming at the wrist panel. The tip
# is what SteamVR's own laser uses; a raw device pose aims too high on Touch.
POINTER_LEFT_ACTION = "/actions/mio/in/pointer_left"
POINTER_RIGHT_ACTION = "/actions/mio/in/pointer_right"
MANIFEST_NAME = "action_manifest.json"

Ray = tuple[tuple[float, float, float], tuple[float, float, float]]


def manifest_directory() -> str:
    """Where the OpenVR action manifest lives, frozen or from source."""

    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", "openvr")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "assets", "openvr"))


def manifest_path() -> str:
    return os.path.join(manifest_directory(), MANIFEST_NAME)


class VRActionInput:
    """Polls one boolean action and reports each fresh press."""

    def __init__(self, on_translate: Callable[[], None] | None = None) -> None:
        self._on_translate = on_translate
        self._openvr: Any | None = None
        self._input: Any | None = None
        self._action_set_handle: Any | None = None
        self._action_handle: Any | None = None
        self._trigger_handle: Any | None = None
        self._grip_handle: Any | None = None
        self._dismiss_handle: Any | None = None
        self._pointer_handles: dict[str, Any] = {}
        self._active_set: Any | None = None
        self._ready = False
        self._reason = ""
        self._was_pressed = False
        self._active = False
        self._inactive_since = 0.0
        self._last_binding_refresh = 0.0
        self._trigger_down: bool | None = None
        self._any_was_down = False
        self._any_pressed = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    @property
    def trigger_down(self) -> bool | None:
        """Whether the trigger is held right now; None when it is not bound."""

        return self._trigger_down

    @property
    def any_button_pressed(self) -> bool:
        """True on the poll in which some button went down, whichever it was.

        Trigger, grip, the translate button and the any-button action all
        count; a button that stays held counts once.
        """

        return self._any_pressed

    @property
    def active(self) -> bool:
        """True once SteamVR reports the action bound to a live controller.

        A manifest can load cleanly and still never go active: measured on a
        PICO 4S through the vrlink driver, no overlay binding activated at all.
        The UI uses this to point the player at the other ways to trigger.
        """

        return self._active

    def start(self) -> bool:
        """Load the manifest and resolve handles. Must run after openvr.init."""

        if self._ready:
            return True
        try:
            import openvr
        except Exception as exc:
            self._reason = f"openvr_missing:{type(exc).__name__}"
            return False
        self._openvr = openvr

        path = manifest_path()
        if not os.path.isfile(path):
            self._reason = "manifest_missing"
            logger.warning("OpenVR action manifest not found at %s", path)
            return False

        try:
            self._input = openvr.VRInput()
            self._input.setActionManifestPath(path)
            self._action_set_handle = self._input.getActionSetHandle(ACTION_SET)
            self._action_handle = self._input.getActionHandle(TRANSLATE_ACTION)
            self._trigger_handle = self._input.getActionHandle(HOLD_TRIGGER_ACTION)
            self._grip_handle = self._input.getActionHandle(HOLD_GRIP_ACTION)
        except Exception as exc:
            self._reason = f"action_setup_failed:{type(exc).__name__}"
            logger.debug("Failed to set up OpenVR actions", exc_info=True)
            return False
        try:
            self._dismiss_handle = self._input.getActionHandle(DISMISS_ACTION)
        except Exception:
            # A manifest without the action: the other buttons still count.
            self._dismiss_handle = None
        self._pointer_handles = {}
        for hand, name in (("left", POINTER_LEFT_ACTION), ("right", POINTER_RIGHT_ACTION)):
            try:
                self._pointer_handles[hand] = self._input.getActionHandle(name)
            except Exception:
                # No tip pose: the wrist panel aims with the device pose instead.
                continue

        try:
            active = openvr.VRActiveActionSet_t()
            active.ulActionSet = self._action_set_handle
            active.ulRestrictedToDevice = openvr.k_ulInvalidInputValueHandle
            active.nPriority = 0
            self._active_set = (openvr.VRActiveActionSet_t * 1)(active)
        except Exception as exc:
            self._reason = f"action_set_failed:{type(exc).__name__}"
            logger.debug("Failed to build the active action set", exc_info=True)
            return False

        self._ready = True
        self._reason = ""
        return True

    def stop(self) -> None:
        self._ready = False
        self._was_pressed = False
        self._any_was_down = False
        self._any_pressed = False
        self._input = None
        self._active_set = None
        self._action_handle = None
        self._trigger_handle = None
        self._grip_handle = None
        self._dismiss_handle = None
        self._pointer_handles = {}
        self._action_set_handle = None
        self._openvr = None

    def pointer_ray(self, hand: str) -> Ray | None:
        """Where ``hand``'s controller tip points, in the standing universe.

        ``((x, y, z), (dx, dy, dz))``, or None when the pose is unbound or
        not tracked. Read after :meth:`poll`, which refreshed the actions.
        """

        handle = self._pointer_handles.get("right" if str(hand).lower() == "right" else "left")
        if handle is None or self._input is None or self._openvr is None:
            return None
        try:
            data = self._input.getPoseActionDataRelativeToNow(
                handle,
                getattr(self._openvr, "TrackingUniverseStanding", 1),
                0.0,
                self._openvr.k_ulInvalidInputValueHandle,
            )
        except Exception:
            return None
        if not bool(getattr(data, "bActive", False)):
            return None
        pose = getattr(data, "pose", None)
        if pose is None or not bool(getattr(pose, "bPoseIsValid", False)):
            return None
        try:
            m = pose.mDeviceToAbsoluteTracking
            origin = (float(m[0][3]), float(m[1][3]), float(m[2][3]))
            direction = (-float(m[0][2]), -float(m[1][2]), -float(m[2][2]))
        except Exception:
            return None
        return origin, direction

    def _note_binding_state(self, active: bool, now: float | None = None) -> None:
        """Track whether SteamVR bound the actions; nudge it while it has not."""

        moment = time.monotonic() if now is None else float(now)
        was_active = self._active
        self._active = bool(active)
        if self._active:
            if not was_active:
                logger.info("Controller bindings are active")
            self._inactive_since = 0.0
            return
        if was_active or not getattr(self, "_inactive_since", 0.0):
            self._inactive_since = moment
            self._last_binding_refresh = moment
            return
        if moment - getattr(self, "_last_binding_refresh", moment) < BINDING_REFRESH_SECONDS:
            return
        self._last_binding_refresh = moment
        self.refresh_bindings()

    def refresh_bindings(self) -> bool:
        """Hand the action manifest to SteamVR again. Never raises."""

        if self._input is None:
            return False
        try:
            self._input.setActionManifestPath(manifest_path())
        except Exception as exc:
            logger.info("Controller bindings still inactive; re-registering the manifest failed: %s", exc)
            return False
        logger.info("Controller bindings still inactive; re-registered the action manifest")
        return True

    def _held(self, handle: Any) -> bool | None:
        """True/False for a bound hold action, None when it is not bound."""

        if handle is None or self._input is None:
            return None
        try:
            data = self._input.getDigitalActionData(
                handle, self._openvr.k_ulInvalidInputValueHandle
            )
        except Exception:
            return None
        if not bool(getattr(data, "bActive", False)):
            return None
        return bool(getattr(data, "bState", False))

    def _chord_held(self) -> bool:
        """Both hold actions down - or unbound, so a plain press still works."""

        trigger = self._held(self._trigger_handle)
        grip = self._held(self._grip_handle)
        if trigger is None and grip is None:
            return True
        return bool(trigger) and bool(grip)

    def poll(self) -> bool:
        """Update state and fire the callback on a fresh chord press. Never raises."""

        if not self._ready or self._input is None:
            return False
        try:
            self._input.updateActionState(self._active_set)
            data = self._input.getDigitalActionData(
                self._action_handle, self._openvr.k_ulInvalidInputValueHandle
            )
        except Exception:
            logger.debug("OpenVR action poll failed", exc_info=True)
            return False

        self._note_binding_state(bool(getattr(data, "bActive", False)))
        self._trigger_down = self._held(self._trigger_handle)
        pressed = bool(getattr(data, "bState", False)) and self._active
        fired = pressed and not self._was_pressed and self._chord_held()
        self._was_pressed = pressed
        any_down = (
            pressed
            or bool(self._trigger_down)
            or bool(self._held(self._grip_handle))
            or bool(self._held(self._dismiss_handle))
        )
        self._any_pressed = any_down and not self._any_was_down
        self._any_was_down = any_down
        if fired and callable(self._on_translate):
            try:
                self._on_translate()
            except Exception:
                logger.exception("Screenshot translation trigger failed")
        return fired
