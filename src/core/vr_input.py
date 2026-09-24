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

Keeping the game out of it: while the player frames a sign or works a panel,
the trigger and grip must not also reach VRChat (a grip there grabs, a
trigger uses). VRHandsFrame blocks them the way SteamVR allows an overlay
to: a second action set bound to the same trigger and grip, activated with
an overlay-global priority and restricted to the hand in use, suppresses
those inputs in the scene application. It suppresses Mio's own main-set
bindings on those inputs too, so while a hand is blocked its trigger and
grip are read from the blocking set instead.

Trigger and grip are read as values where the controller has them, and
count as held past a threshold the player sets: a grip that only counted
when squeezed all the way made the frame hard to hold on some controllers.
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
# Hold to talk. Unbound by default: the player picks a button in SteamVR's
# binding page, because every game already uses the obvious ones.
PUSH_TO_TALK_ACTION = "/actions/mio/in/push_to_talk"
# How far each is pulled, 0..1, where the controller reports it.
TRIGGER_VALUE_ACTION = "/actions/mio/in/trigger_value"
GRIP_VALUE_ACTION = "/actions/mio/in/grip_value"
# The set that keeps the game from seeing trigger and grip (see above).
BLOCK_ACTION_SET = "/actions/mio_block"
BLOCK_TRIGGER_ACTION = "/actions/mio_block/in/trigger"
BLOCK_GRIP_ACTION = "/actions/mio_block/in/grip"
BLOCK_TRIGGER_VALUE_ACTION = "/actions/mio_block/in/trigger_value"
BLOCK_GRIP_VALUE_ACTION = "/actions/mio_block/in/grip_value"
# Shortcuts the player can bind in SteamVR's binding page (none bound by
# default: the game owns the obvious buttons), and the Touch thumb rest,
# tapped a few times in a row (VRHandsFrame's gesture on/off).
TOGGLE_FRAME_ACTION = "/actions/mio/in/toggle_frame_gesture"
GATHER_PANELS_ACTION = "/actions/mio/in/gather_panels"
CLOSE_PANELS_ACTION = "/actions/mio/in/close_panels"
THUMBREST_ACTION = "/actions/mio/in/thumbrest"
SHORTCUT_ACTIONS = {
    "toggle_frame_gesture": TOGGLE_FRAME_ACTION,
    "gather_panels": GATHER_PANELS_ACTION,
    "close_panels": CLOSE_PANELS_ACTION,
}
# The stick scales the frame while framing; it is kept from the game then
# (a push would walk), through a set of its own so a laser on a panel never
# takes the stick.
STICK_ACTION = "/actions/mio/in/stick"
BLOCK_STICK_ACTION_SET = "/actions/mio_block_stick"
BLOCK_STICK_ACTION = "/actions/mio_block_stick/in/stick"
DEFAULT_THUMBREST_TAPS = 4
# Taps further apart than this start the count again.
TAP_WINDOW_SECONDS = 0.45
# openvr's k_nActionSetOverlayGlobalPriorityMin, for runtimes that lack it.
OVERLAY_GLOBAL_PRIORITY = 0x01000000
DEFAULT_TRIGGER_THRESHOLD = 0.5
DEFAULT_GRIP_THRESHOLD = 0.4
# Vibration on either hand: feedback for the frame gesture and panel clicks.
HAPTIC_ACTION = "/actions/mio/out/haptic"
HAND_SOURCES = {"left": "/user/hand/left", "right": "/user/hand/right"}
APP_KEY = "mio.translator.overlay"
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


class TapCounter:
    """Counts quick taps (touch rising edges) and fires on the Nth in a row."""

    def __init__(self, taps: int = DEFAULT_THUMBREST_TAPS, window: float = TAP_WINDOW_SECONDS) -> None:
        self.taps = max(2, int(taps))
        self.window = float(window)
        self._count = 0
        self._last = -1e9
        self._was = False

    def update(self, touching: bool | None, now: float) -> bool:
        touching = bool(touching)
        fired = False
        if touching and not self._was:
            if now - self._last > self.window:
                self._count = 0
            self._count += 1
            self._last = now
            if self._count >= self.taps:
                self._count = 0
                fired = True
        self._was = touching
        return fired


def pressed(value: float | None, click: bool | None, threshold: float) -> bool | None:
    """Held past ``threshold`` when there is a value, else the click, else unknown."""

    if value is not None:
        return value >= threshold
    return click


class HandButtons:
    """One hand's trigger and grip, each None when SteamVR has not bound it."""

    __slots__ = ("trigger", "grip")

    def __init__(self, trigger: bool | None = None, grip: bool | None = None) -> None:
        self.trigger = trigger
        self.grip = grip


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
        self._ptt_handle: Any | None = None
        self._haptic_handle: Any | None = None
        self._hand_sources: dict[str, Any] = {}
        self._hands: dict[str, HandButtons] = {"left": HandButtons(), "right": HandButtons()}
        self._push_to_talk: bool | None = None
        # Read settings; the main window refreshes them every tick.
        self.trigger_threshold = DEFAULT_TRIGGER_THRESHOLD
        self.grip_threshold = DEFAULT_GRIP_THRESHOLD
        self.swap_trigger_grip = False
        self._value_handles: dict[str, Any] = {}
        self._block_set_handle: Any | None = None
        self._block_handles: dict[str, Any] = {}
        self._blocking_wanted: frozenset[str] = frozenset()
        self._blocking: frozenset[str] = frozenset()
        self._stick_blocking_wanted: frozenset[str] = frozenset()
        self._stick_blocking: frozenset[str] = frozenset()
        self._active_sets: dict[tuple[frozenset[str], frozenset[str]], Any] = {}
        self._shortcut_handles: dict[str, Any] = {}
        self._shortcut_was: dict[str, bool] = {}
        self._shortcuts: list[str] = []
        self._thumbrest_handle: Any | None = None
        self._tap_counters: dict[str, TapCounter] = {}
        self.thumbrest_taps = DEFAULT_THUMBREST_TAPS
        self._stick_handle: Any | None = None
        self._block_stick_set_handle: Any | None = None
        self._block_stick_handle: Any | None = None
        self._sticks: dict[str, tuple[float, float] | None] = {"left": None, "right": None}

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

    def hand(self, hand: str) -> HandButtons:
        """That hand's trigger and grip as of the last :meth:`poll`."""

        return self._hands.get("right" if str(hand).lower() == "right" else "left", HandButtons())

    @property
    def push_to_talk(self) -> bool | None:
        """Whether the talk button is held; None when it is not bound."""

        return self._push_to_talk

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
        for attr, name in (("_ptt_handle", PUSH_TO_TALK_ACTION), ("_haptic_handle", HAPTIC_ACTION)):
            try:
                setattr(self, attr, self._input.getActionHandle(name))
            except Exception:
                setattr(self, attr, None)
        self._hand_sources = {}
        for hand, path in HAND_SOURCES.items():
            try:
                self._hand_sources[hand] = self._input.getInputSourceHandle(path)
            except Exception:
                continue
        self._value_handles = {}
        for name in (TRIGGER_VALUE_ACTION, GRIP_VALUE_ACTION):
            try:
                self._value_handles[name] = self._input.getActionHandle(name)
            except Exception:
                continue
        self._block_handles = {}
        try:
            self._block_set_handle = self._input.getActionSetHandle(BLOCK_ACTION_SET)
            for name in (
                BLOCK_TRIGGER_ACTION,
                BLOCK_GRIP_ACTION,
                BLOCK_TRIGGER_VALUE_ACTION,
                BLOCK_GRIP_VALUE_ACTION,
            ):
                self._block_handles[name] = self._input.getActionHandle(name)
        except Exception:
            # A manifest without the set: nothing can be kept from the game.
            self._block_set_handle = None
            self._block_handles = {}
        self._shortcut_handles = {}
        for name, path in SHORTCUT_ACTIONS.items():
            try:
                self._shortcut_handles[name] = self._input.getActionHandle(path)
            except Exception:
                continue
        try:
            self._thumbrest_handle = self._input.getActionHandle(THUMBREST_ACTION)
        except Exception:
            self._thumbrest_handle = None
        try:
            self._stick_handle = self._input.getActionHandle(STICK_ACTION)
        except Exception:
            self._stick_handle = None
        try:
            self._block_stick_set_handle = self._input.getActionSetHandle(BLOCK_STICK_ACTION_SET)
            self._block_stick_handle = self._input.getActionHandle(BLOCK_STICK_ACTION)
        except Exception:
            self._block_stick_set_handle = None
            self._block_stick_handle = None
        self._shortcut_was = {}
        self._tap_counters = {}
        self._active_sets = {}

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
        self._ptt_handle = None
        self._haptic_handle = None
        self._hand_sources = {}
        self._hands = {"left": HandButtons(), "right": HandButtons()}
        self._push_to_talk = None
        self._value_handles = {}
        self._block_set_handle = None
        self._block_handles = {}
        self._blocking = frozenset()
        self._stick_blocking = frozenset()
        self._active_sets = {}
        self._shortcut_handles = {}
        self._shortcut_was = {}
        self._shortcuts = []
        self._thumbrest_handle = None
        self._tap_counters = {}
        self._stick_handle = None
        self._block_stick_set_handle = None
        self._block_stick_handle = None
        self._sticks = {"left": None, "right": None}
        self._action_set_handle = None
        self._openvr = None

    # ------------------------------------------------------------ blocking
    def set_blocking(self, hands, sticks=()) -> None:
        """Keep these hands' trigger and grip (and ``sticks``' sticks) from
        the game from the next poll."""

        self._blocking_wanted = frozenset(
            hand for hand in (hands or ()) if hand in HAND_SOURCES
        )
        self._stick_blocking_wanted = frozenset(
            hand for hand in (sticks or ()) if hand in HAND_SOURCES
        )

    @property
    def stick_blocking(self) -> frozenset[str]:
        return self._stick_blocking

    def stick(self, hand: str) -> tuple[float, float] | None:
        """That hand's stick (x, y) as of the last poll; None when unbound."""

        return self._sticks.get("right" if str(hand).lower() == "right" else "left")

    def take_shortcuts(self) -> list[str]:
        """Shortcuts pressed since the last call: ``toggle_frame_gesture``,
        ``gather_panels``, ``close_panels`` and ``thumbrest`` (the taps)."""

        fired, self._shortcuts = self._shortcuts, []
        return fired

    @property
    def blocking(self) -> frozenset[str]:
        """The hands whose trigger and grip the game does not see right now."""

        return self._blocking

    def _effective_blocking(self) -> frozenset[str]:
        if self._block_set_handle is None or not self._block_handles:
            return frozenset()
        return frozenset(hand for hand in self._blocking_wanted if hand in self._hand_sources)

    def _effective_stick_blocking(self) -> frozenset[str]:
        if self._block_stick_set_handle is None or self._block_stick_handle is None:
            return frozenset()
        return frozenset(hand for hand in self._stick_blocking_wanted if hand in self._hand_sources)

    def _sets_for(self, blocked: frozenset[str], sticks: frozenset[str] = frozenset()) -> Any:
        """The action sets to update: Mio's own, plus each blocking set once
        per blocked hand, restricted to that hand at overlay-global priority."""

        key = (blocked, sticks)
        cached = self._active_sets.get(key)
        if cached is not None:
            return cached
        openvr = self._openvr
        if (not blocked and not sticks) or openvr is None:
            return self._active_set
        priority = int(getattr(openvr, "k_nActionSetOverlayGlobalPriorityMin", OVERLAY_GLOBAL_PRIORITY))
        entries = []
        main = openvr.VRActiveActionSet_t()
        main.ulActionSet = self._action_set_handle
        main.ulRestrictedToDevice = openvr.k_ulInvalidInputValueHandle
        main.nPriority = 0
        entries.append(main)
        for set_handle, hands in ((self._block_set_handle, blocked), (self._block_stick_set_handle, sticks)):
            for hand in sorted(hands):
                entry = openvr.VRActiveActionSet_t()
                entry.ulActionSet = set_handle
                entry.ulRestrictedToDevice = self._hand_sources[hand]
                entry.nPriority = priority
                entries.append(entry)
        sets = (openvr.VRActiveActionSet_t * len(entries))(*entries)
        self._active_sets[key] = sets
        return sets

    def _read_stick(self, handle: Any, source: Any) -> tuple[float, float] | None:
        if handle is None or self._input is None:
            return None
        try:
            data = self._input.getAnalogActionData(handle, source)
        except Exception:
            return None
        if not bool(getattr(data, "bActive", False)):
            return None
        try:
            return (float(getattr(data, "x", 0.0)), float(getattr(data, "y", 0.0)))
        except (TypeError, ValueError):
            return None

    def _poll_shortcuts(self, now: float) -> None:
        for name, handle in self._shortcut_handles.items():
            down = bool(self._held(handle))
            if down and not self._shortcut_was.get(name, False):
                self._shortcuts.append(name)
            self._shortcut_was[name] = down
        if self._thumbrest_handle is None:
            return
        for hand, source in self._hand_sources.items():
            counter = self._tap_counters.get(hand)
            if counter is None or counter.taps != max(2, int(self.thumbrest_taps)):
                counter = TapCounter(self.thumbrest_taps)
                self._tap_counters[hand] = counter
            if counter.update(self._held(self._thumbrest_handle, source), now):
                self._shortcuts.append("thumbrest")

    def _analog(self, handle: Any, source: Any) -> float | None:
        if handle is None or self._input is None:
            return None
        try:
            data = self._input.getAnalogActionData(handle, source)
        except Exception:
            return None
        if not bool(getattr(data, "bActive", False)):
            return None
        try:
            return max(0.0, min(1.0, float(getattr(data, "x", 0.0))))
        except (TypeError, ValueError):
            return None

    def _read_hand(self, hand: str, source: Any, blocked: bool) -> HandButtons:
        if blocked:
            trigger_click = self._held(self._block_handles.get(BLOCK_TRIGGER_ACTION), source)
            grip_click = self._held(self._block_handles.get(BLOCK_GRIP_ACTION), source)
            trigger_value = self._analog(self._block_handles.get(BLOCK_TRIGGER_VALUE_ACTION), source)
            grip_value = self._analog(self._block_handles.get(BLOCK_GRIP_VALUE_ACTION), source)
        else:
            trigger_click = self._held(self._trigger_handle, source)
            grip_click = self._held(self._grip_handle, source)
            trigger_value = self._analog(self._value_handles.get(TRIGGER_VALUE_ACTION), source)
            grip_value = self._analog(self._value_handles.get(GRIP_VALUE_ACTION), source)
        trigger = pressed(trigger_value, trigger_click, float(self.trigger_threshold))
        grip = pressed(grip_value, grip_click, float(self.grip_threshold))
        if self.swap_trigger_grip:
            trigger, grip = grip, trigger
        return HandButtons(trigger=trigger, grip=grip)

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

    def pulse(
        self,
        hand: str,
        *,
        seconds: float = 0.03,
        frequency: float = 160.0,
        amplitude: float = 0.5,
    ) -> bool:
        """Buzz one controller. Never raises.

        The haptic action goes first; a controller whose bindings never went
        active (the PICO case) still gets the legacy pulse on its device.
        """

        side = "right" if str(hand).lower() == "right" else "left"
        seconds = max(0.005, min(0.5, float(seconds)))
        amplitude = max(0.0, min(1.0, float(amplitude)))
        source = self._hand_sources.get(side)
        if self._active and self._haptic_handle is not None and source is not None and self._input is not None:
            try:
                self._input.triggerHapticVibrationAction(
                    self._haptic_handle, 0.0, seconds, float(frequency), amplitude, source
                )
                return True
            except Exception:
                logger.debug("Haptic action failed; trying the legacy pulse", exc_info=True)
        openvr = self._openvr
        if openvr is None:
            return False
        try:
            role = (
                openvr.TrackedControllerRole_RightHand
                if side == "right"
                else openvr.TrackedControllerRole_LeftHand
            )
            system = openvr.VRSystem()
            index = system.getTrackedDeviceIndexForControllerRole(role)
            if index == openvr.k_unTrackedDeviceIndexInvalid:
                return False
            # The legacy call takes microseconds and caps a pulse at 3999.
            system.triggerHapticPulse(index, 0, int(min(3999, seconds * 1_000_000 * amplitude)))
            return True
        except Exception:
            logger.debug("Legacy haptic pulse failed", exc_info=True)
            return False

    def open_binding_ui(self) -> bool:
        """Open SteamVR's controller binding page for Mio. Never raises."""

        if self._input is None or self._openvr is None:
            return False
        try:
            self._input.openBindingUI(
                APP_KEY,
                self._action_set_handle,
                self._openvr.k_ulInvalidInputValueHandle,
                True,
            )
            return True
        except Exception:
            logger.info("Could not open the SteamVR binding page", exc_info=True)
            return False

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

    def _held(self, handle: Any, source: Any = None) -> bool | None:
        """True/False for a bound hold action, None when it is not bound.

        ``source`` restricts the read to one hand's input source.
        """

        if handle is None or self._input is None:
            return None
        try:
            data = self._input.getDigitalActionData(
                handle,
                source if source is not None else self._openvr.k_ulInvalidInputValueHandle,
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
        blocked = self._effective_blocking()
        stick_blocked = self._effective_stick_blocking()
        try:
            self._input.updateActionState(self._sets_for(blocked, stick_blocked))
            data = self._input.getDigitalActionData(
                self._action_handle, self._openvr.k_ulInvalidInputValueHandle
            )
        except Exception:
            logger.debug("OpenVR action poll failed", exc_info=True)
            return False

        self._note_binding_state(bool(getattr(data, "bActive", False)))
        self._blocking = blocked
        self._stick_blocking = stick_blocked
        for hand, source in self._hand_sources.items():
            self._hands[hand] = self._read_hand(hand, source, hand in blocked)
            self._sticks[hand] = self._read_stick(
                self._block_stick_handle if hand in stick_blocked else self._stick_handle, source
            )
        self._poll_shortcuts(time.monotonic())
        hand_triggers = [buttons.trigger for buttons in self._hands.values() if buttons.trigger is not None]
        hand_grips = [buttons.grip for buttons in self._hands.values() if buttons.grip is not None]
        if hand_triggers:
            self._trigger_down = any(hand_triggers)
            grip_down = any(hand_grips)
        else:
            # No per-hand sources: the actions as a whole.
            self._trigger_down = self._held(self._trigger_handle)
            grip_down = bool(self._held(self._grip_handle))
        self._push_to_talk = self._held(self._ptt_handle)
        button = bool(getattr(data, "bState", False)) and self._active
        fired = button and not self._was_pressed and self._chord_held()
        self._was_pressed = button
        any_down = (
            button
            or bool(self._trigger_down)
            or grip_down
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
