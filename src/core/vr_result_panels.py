# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Kept translations as panels in the room, VRHandsFrame style.

A read the player kept (a trigger pull while framing, or a read with the
"panel" display) becomes a panel hung where the text was. It stays until it
is closed - no countdown - and it can be pointed at with either controller,
its buttons pulled with the trigger, and grabbed with the grip and carried
somewhere else. Up to :data:`MAX_PANELS` are up at once; the wrist panel can
gather them in front of the player or close them all.

Input is read here the same way as for the wrist panel: the ray is cast from
the controller and tested with ``computeOverlayIntersection``, and SteamVR's
own laser mode is never switched on (it would take the controllers from the
game). A beam is drawn only while a ray actually lands on a panel.

Like every Mio overlay the panels are taken down for a screenshot read
(:meth:`ResultPanelManager.pause`) and put back afterwards.

Two hands, as in VRHandsFrame: grip a held panel with the other hand too
and pulling the hands apart or together resizes it; a trigger pulled with
both hands on a panel runs that hand's shortcut (a page, close...); a
trigger pulled by the only hand holding a panel gathers every panel in
front of the player.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.core.steamvr_inplace import _RuntimeOverlay, _to_hmd_matrix
from src.core.steamvr_wrist import DWELL_SECONDS, SteamVRLaser, ray_from_pose

logger = logging.getLogger(__name__)

PANEL_OVERLAY_KEY = "mio.translator.panel"
PANEL_OVERLAY_NAME = "Mio Translator Result Panel"
PANEL_LASER_KEY = "mio.translator.panel_laser"
PANEL_LASER_NAME = "Mio Translator Panel Laser"
MAX_PANELS = 4
DEFAULT_PANEL_WIDTH_METERS = 0.42
MIN_PANEL_WIDTH_METERS = 0.28
MAX_PANEL_WIDTH_METERS = 0.7
# Where a recalled panel or the tutorial appears: this far in front of the
# head, a little below the eyes.
FRONT_DISTANCE_METERS = 0.6
FRONT_DROP_METERS = 0.08
# Panels side by side when gathered.
GATHER_GAP_METERS = 0.03
# A panel opened where another already hangs is moved aside by this much of
# its width so both stay readable.
OVERLAP_METERS = 0.08
# Sort orders: below the hand frame (30), the wrist panel (40) and lasers (45);
# the newest or last grabbed panel on top.
BASE_SORT_ORDER = 21
# Cursor-only redraws are capped at this rate; a button change draws at once.
CURSOR_REDRAW_INTERVAL_S = 1.0 / 30.0
# What a trigger pulled while both hands hold a panel does, per hand.
TWO_HAND_ACTIONS = ("none", "page_prev", "page_next", "next_or_close", "close", "pin", "copy", "chatbox")
DEFAULT_TWO_HAND_ACTIONS = {"left": "page_prev", "right": "next_or_close"}

Vector = tuple[float, float, float]
Ray = tuple[Vector, Vector]
Rows = Sequence[Sequence[float]]
HANDS = ("left", "right")


# ------------------------------------------------------------------ geometry
def _normalize(v: Sequence[float]) -> Vector:
    length = math.sqrt(sum(float(c) * float(c) for c in v))
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (float(v[0]) / length, float(v[1]) / length, float(v[2]) / length)


def _cross(a: Sequence[float], b: Sequence[float]) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def position_of(rows: Rows) -> Vector:
    return (float(rows[0][3]), float(rows[1][3]), float(rows[2][3]))


def face_head_transform(position: Sequence[float], head: Sequence[float] | None) -> list[list[float]]:
    """An upright panel at ``position`` turned to face ``head``.

    The overlay's front is its +Z, so +Z points at the head; +X stays level.
    Without a head (or with the head straight above or below) the panel
    faces the -Z of the room.
    """

    px, py, pz = (float(position[0]), float(position[1]), float(position[2]))
    z_axis: Vector = (0.0, 0.0, 1.0)
    if head is not None:
        candidate = _normalize((float(head[0]) - px, float(head[1]) - py, float(head[2]) - pz))
        if candidate != (0.0, 0.0, 0.0):
            z_axis = candidate
    x_axis = _normalize(_cross((0.0, 1.0, 0.0), z_axis))
    if x_axis == (0.0, 0.0, 0.0):
        x_axis = (1.0, 0.0, 0.0)
    y_axis = _cross(z_axis, x_axis)
    return [
        [x_axis[0], y_axis[0], z_axis[0], px],
        [x_axis[1], y_axis[1], z_axis[1], py],
        [x_axis[2], y_axis[2], z_axis[2], pz],
    ]


def level_forward(head_pose: Rows) -> Vector:
    """Where the head looks, flattened onto the floor plane."""

    forward = (-float(head_pose[0][2]), 0.0, -float(head_pose[2][2]))
    flat = _normalize(forward)
    return flat if flat != (0.0, 0.0, 0.0) else (0.0, 0.0, -1.0)


def in_front_transform(
    head_pose: Rows,
    *,
    distance: float = FRONT_DISTANCE_METERS,
    drop: float = FRONT_DROP_METERS,
    side: float = 0.0,
) -> list[list[float]]:
    """A panel ``distance`` ahead of the head (level, not down at the floor
    when the player looks down), ``side`` metres to the right, facing it."""

    head = position_of(head_pose)
    forward = level_forward(head_pose)
    right = _normalize(_cross(forward, (0.0, 1.0, 0.0)))
    position = (
        head[0] + forward[0] * distance + right[0] * side,
        head[1] - drop,
        head[2] + forward[2] * distance + right[2] * side,
    )
    return face_head_transform(position, head)


def arc_transforms(
    head_pose: Rows, count: int, width_m: float, *, distance: float = FRONT_DISTANCE_METERS
) -> list[list[list[float]]]:
    """``count`` panels side by side on an arc in front of the head."""

    count = max(0, int(count))
    if not count:
        return []
    head = position_of(head_pose)
    forward = level_forward(head_pose)
    step = 2.0 * math.atan((max(0.05, float(width_m)) + GATHER_GAP_METERS) / 2.0 / max(0.2, distance))
    base = math.atan2(forward[0], -forward[2])
    out = []
    for index in range(count):
        angle = base + (index - (count - 1) / 2.0) * step
        position = (
            head[0] + math.sin(angle) * distance,
            head[1] - FRONT_DROP_METERS,
            head[2] - math.cos(angle) * distance,
        )
        out.append(face_head_transform(position, head))
    return out


def to_local_point(pose: Rows, point: Sequence[float]) -> Vector:
    """A world point in a rigid pose's own frame (inverse of :func:`from_local_point`)."""

    d = [float(point[i]) - float(pose[i][3]) for i in range(3)]
    return (
        float(pose[0][0]) * d[0] + float(pose[1][0]) * d[1] + float(pose[2][0]) * d[2],
        float(pose[0][1]) * d[0] + float(pose[1][1]) * d[1] + float(pose[2][1]) * d[2],
        float(pose[0][2]) * d[0] + float(pose[1][2]) * d[1] + float(pose[2][2]) * d[2],
    )


def from_local_point(pose: Rows, local: Sequence[float]) -> Vector:
    return tuple(  # type: ignore[return-value]
        sum(float(pose[r][k]) * float(local[k]) for k in range(3)) + float(pose[r][3]) for r in range(3)
    )


# ------------------------------------------------------------------ overlay
class SteamVRResultPanel(_RuntimeOverlay):
    """One panel's overlay: placed in the room, never takes SteamVR input."""

    name = PANEL_OVERLAY_NAME
    # The panel picture (1000x820) on a fixed square canvas; the size never
    # changes, so neither does the texture SteamVR pinned.
    canvas = (1024, 1024)

    def __init__(self, slot: int) -> None:
        super().__init__()
        self.slot = int(slot)
        self.key = f"{PANEL_OVERLAY_KEY}.{self.slot}"
        self.name = f"{PANEL_OVERLAY_NAME} {self.slot + 1}"
        self._sort_order = BASE_SORT_ORDER

    def _configure(self) -> None:
        overlay, handle = self._overlay, self._handle
        if overlay is None or handle is None:
            return
        order = self._sort_order
        self._safe(lambda: overlay.setOverlaySortOrder(handle, order), "setOverlaySortOrder")

    def set_sort_order(self, order: int) -> None:
        self._sort_order = int(order)
        self._configure()

    def place(self, rows: Rows, width_meters: float) -> bool:
        if not self._available and not self.start():
            return False
        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if overlay is None or handle is None or openvr is None:
            return False
        if not self._ok(
            lambda: overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseStanding, _to_hmd_matrix(openvr, rows)
            ),
            "setOverlayTransformAbsolute",
        ):
            return False
        quad = self._quad_width(width_meters)
        self._safe(lambda: overlay.setOverlayWidthInMeters(handle, quad), "setOverlayWidthInMeters")
        return True

    def upload(self, image: Any) -> bool:
        if not self._available and not self.start():
            return False
        return self._upload(image, image.width(), image.height())

    def show(self) -> None:
        if not self._visible:
            self._show()

    def intersect(self, ray: Ray) -> tuple[float, float, float] | None:
        """(u, v, distance) where the ray lands on the picture (v from the
        top), or None when it misses."""

        openvr, overlay, handle = self._openvr, self._overlay, self._handle
        if openvr is None or overlay is None or handle is None or not self._visible:
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
            logger.debug("Result panel intersection failed", exc_info=True)
            return None
        if not hit:
            return None
        u = float(results.vUVs.v[0])
        v = float(results.vUVs.v[1])
        uploader = self._uploader
        if not (uploader is not None and uploader.mouse_y_is_top_down):
            v = 1.0 - v
        if uploader is not None:
            u, v = uploader.picture_fraction(u, v)
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        try:
            distance = float(getattr(results, "fDistance", 0.0) or 0.0)
        except (TypeError, ValueError):
            distance = 0.0
        return u, v, distance


# ------------------------------------------------------------------ state
@dataclass
class HandInput:
    """One controller this tick: where it points, its pose and buttons."""

    ray: Ray | None = None
    pose: Rows | None = None
    trigger: bool | None = None
    grip: bool | None = None


@dataclass
class _Panel:
    panel_id: int
    view: Any
    overlay: Any
    entry_id: int | None
    rows: list[list[float]]
    width_m: float
    tutorial: bool = False
    dirty: bool = True
    last_draw: float = 0.0
    drawn: tuple = ()


@dataclass
class _HandState:
    hover_panel: int | None = None
    hover_action: str | None = None
    cursor: tuple[float, float] | None = None
    distance: float | None = None
    pressed: tuple[int, str] | None = None
    trigger_was: bool = False
    grip_was: bool = False
    grab_panel: int | None = None
    grab_offset: Vector = (0.0, 0.0, 0.0)
    # The second hand on a held panel resizes it: hand distance and panel
    # width when it took hold.
    scaling: bool = False
    scale_from: tuple[float, float] = (0.0, 0.0)
    dwell_target: tuple[int, str] | None = None
    dwell_since: float = 0.0
    dwell_fired: bool = False
    dwell_fraction: float = 0.0
    extra: dict = field(default_factory=dict)


class ResultPanelManager:
    """Opens, draws, moves and closes the kept panels; never raises to its caller."""

    def __init__(
        self,
        view_factory: Callable[[], Any],
        *,
        on_pin: Callable[[int, bool], None] | None = None,
        on_close: Callable[[int | None], None] | None = None,
        on_share: Callable[[int | None, str, bool], None] | None = None,
        cue: Callable[[str, str | None], None] | None = None,
        max_panels: int = MAX_PANELS,
        overlay_factory: Callable[[int], Any] | None = None,
        laser_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._view_factory = view_factory
        self._on_pin = on_pin
        self._on_close = on_close
        # Copy and send-to-chatbox: the owner has the text and the sender.
        self._on_share = on_share
        self._cue = cue
        self._max = max(1, int(max_panels))
        self._overlay_factory = overlay_factory or SteamVRResultPanel
        self._laser_factory = laser_factory or (
            lambda hand: SteamVRLaser(key=f"{PANEL_LASER_KEY}.{hand}", name=f"{PANEL_LASER_NAME} ({hand})")
        )
        self._clock = clock
        self._panels: list[_Panel] = []  # oldest first
        self._free_overlays: list[Any] = []
        self._slots_used = 0
        self._next_id = 1
        self._hands = {hand: _HandState() for hand in HANDS}
        self._lasers: dict[str, Any] = {}
        self._paused = False
        # The owner keeps these in step with the settings.
        self.two_hand_actions = dict(DEFAULT_TWO_HAND_ACTIONS)
        self.same_hand_trigger_gathers = True

    # ------------------------------------------------------------ status
    @property
    def count(self) -> int:
        return len(self._panels)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def grabbing(self) -> bool:
        return any(state.grab_panel is not None for state in self._hands.values())

    def pointing(self, hand: str) -> bool:
        """True while ``hand``'s ray rests on a panel or holds one."""

        state = self._hands.get(hand)
        return bool(state and (state.hover_panel is not None or state.grab_panel is not None))

    def panel_ids(self) -> list[int]:
        return [panel.panel_id for panel in self._panels]

    def entry_ids(self) -> list[int]:
        return [panel.entry_id for panel in self._panels if panel.entry_id is not None]

    def transform_of(self, panel_id: int) -> list[list[float]] | None:
        panel = self._find(panel_id)
        return [list(row) for row in panel.rows] if panel is not None else None

    def _find(self, panel_id: int | None) -> _Panel | None:
        for panel in self._panels:
            if panel.panel_id == panel_id:
                return panel
        return None

    def _cue_safe(self, name: str, hand: str | None = None) -> None:
        if callable(self._cue):
            try:
                self._cue(name, hand)
            except Exception:
                logger.debug("Panel cue failed", exc_info=True)

    # ------------------------------------------------------------ opening
    def _take_overlay(self) -> Any | None:
        if self._free_overlays:
            return self._free_overlays.pop()
        if self._slots_used >= self._max:
            return None
        try:
            overlay = self._overlay_factory(self._slots_used)
        except Exception:
            logger.debug("Failed to build a result panel overlay", exc_info=True)
            return None
        self._slots_used += 1
        return overlay

    def _make_room(self) -> None:
        while len(self._panels) >= self._max:
            victim = next((panel for panel in self._panels if panel.tutorial), None)
            if victim is None:
                victim = self._panels[0]
            self._close(victim, notify=True)

    def _moved_aside(self, rows: list[list[float]], width_m: float) -> list[list[float]]:
        """Slide a new panel to the right while it would cover an open one."""

        rows = [list(row) for row in rows]
        right = (float(rows[0][0]), float(rows[1][0]), float(rows[2][0]))
        for _ in range(self._max):
            here = position_of(rows)
            crowded = any(
                math.dist(here, position_of(panel.rows)) < OVERLAP_METERS for panel in self._panels
            )
            if not crowded:
                break
            shift = width_m + GATHER_GAP_METERS
            for r in range(3):
                rows[r][3] += right[r] * shift
        return rows

    def _open(self, view: Any, rows: Rows, width_m: float, *, entry_id: int | None, tutorial: bool) -> int | None:
        if entry_id is not None:
            existing = next((panel for panel in self._panels if panel.entry_id == entry_id), None)
            if existing is not None:
                # Already up: bring it to where it was asked for.
                existing.rows = [list(row) for row in rows]
                self._place(existing)
                self._raise(existing)
                return existing.panel_id
        self._make_room()
        overlay = self._take_overlay()
        if overlay is None:
            return None
        width = max(MIN_PANEL_WIDTH_METERS, min(MAX_PANEL_WIDTH_METERS, float(width_m)))
        panel = _Panel(
            panel_id=self._next_id,
            view=view,
            overlay=overlay,
            entry_id=entry_id,
            rows=self._moved_aside([list(row) for row in rows], width),
            width_m=width,
            tutorial=tutorial,
        )
        self._next_id += 1
        self._panels.append(panel)
        if not self._draw(panel, force=True) or not self._place(panel):
            self._close(panel, notify=False)
            return None
        if not self._paused:
            overlay.show()
        self._raise(panel)
        return panel.panel_id

    def open_result(
        self,
        *,
        entry_id: int | None,
        title: str,
        card: Any,
        picture: Any,
        pairs: list[tuple[str, str]],
        rows: Rows,
        width_m: float = DEFAULT_PANEL_WIDTH_METERS,
        pinned: bool = False,
        links: list[str] | None = None,
    ) -> int | None:
        try:
            view = self._view_factory()
            view.set_result(
                title=title, card=card, picture=picture, pairs=pairs, pinned=pinned, links=list(links or [])
            )
            return self._open(view, rows, width_m, entry_id=entry_id, tutorial=False)
        except Exception:
            logger.warning("Failed to open a result panel", exc_info=True)
            return None

    def open_tutorial(self, title: str, lines: list[str], rows: Rows, width_m: float = 0.5) -> int | None:
        try:
            for panel in list(self._panels):
                if panel.tutorial:
                    self._close(panel, notify=False)
            view = self._view_factory()
            view.set_tutorial(title, lines)
            return self._open(view, rows, width_m, entry_id=None, tutorial=True)
        except Exception:
            logger.warning("Failed to open the tutorial panel", exc_info=True)
            return None

    # ------------------------------------------------------------ closing
    def _close(self, panel: _Panel, *, notify: bool) -> None:
        if panel in self._panels:
            self._panels.remove(panel)
        for state in self._hands.values():
            if state.hover_panel == panel.panel_id:
                state.hover_panel = None
                state.hover_action = None
                state.cursor = None
            if state.grab_panel == panel.panel_id:
                state.grab_panel = None
            if state.pressed and state.pressed[0] == panel.panel_id:
                state.pressed = None
        try:
            panel.overlay.hide()
        except Exception:
            logger.debug("Failed to hide a result panel", exc_info=True)
        self._free_overlays.append(panel.overlay)
        if notify and callable(self._on_close):
            try:
                self._on_close(panel.entry_id)
            except Exception:
                logger.debug("Panel close callback failed", exc_info=True)

    def close(self, panel_id: int) -> bool:
        panel = self._find(panel_id)
        if panel is None:
            return False
        self._close(panel, notify=True)
        return True

    def close_all(self) -> int:
        count = len(self._panels)
        for panel in list(self._panels):
            self._close(panel, notify=True)
        self._hide_lasers()
        return count

    def stop(self) -> None:
        for panel in list(self._panels):
            self._close(panel, notify=False)
        for overlay in self._free_overlays:
            try:
                overlay.stop()
            except Exception:
                logger.debug("Failed to destroy a result panel overlay", exc_info=True)
        self._free_overlays = []
        self._slots_used = 0
        for laser in self._lasers.values():
            try:
                laser.stop()
            except Exception:
                logger.debug("Failed to destroy a panel laser", exc_info=True)
        self._lasers = {}
        self._hands = {hand: _HandState() for hand in HANDS}

    # ------------------------------------------------------------ arranging
    def gather(self, head_pose: Rows | None) -> int:
        """Bring every open panel in front of the head, side by side."""

        if head_pose is None or not self._panels:
            return 0
        width = max(panel.width_m for panel in self._panels)
        for panel, rows in zip(self._panels, arc_transforms(head_pose, len(self._panels), width)):
            panel.rows = rows
            self._place(panel)
        return len(self._panels)

    def set_pinned(self, entry_id: int, pinned: bool) -> None:
        for panel in self._panels:
            if panel.entry_id == entry_id:
                try:
                    panel.view.set_pinned(pinned)
                except Exception:
                    logger.debug("Failed to update a panel pin", exc_info=True)
                panel.dirty = True
                self._draw(panel, force=True)

    def set_view_style(self, style: Any) -> None:
        """A new look for every open panel (the settings changed)."""

        for panel in self._panels:
            setter = getattr(panel.view, "set_style", None)
            if callable(setter):
                try:
                    setter(style)
                except Exception:
                    logger.debug("Failed to restyle a panel", exc_info=True)
                panel.dirty = True
                self._draw(panel, force=True)

    def pause(self) -> None:
        """Out of the picture for a capture; :meth:`resume` puts them back."""

        self._paused = True
        for panel in self._panels:
            try:
                panel.overlay.hide()
            except Exception:
                logger.debug("Failed to hide a result panel", exc_info=True)
        self._hide_lasers()

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        for panel in self._panels:
            try:
                panel.overlay.show()
            except Exception:
                logger.debug("Failed to show a result panel", exc_info=True)

    def _raise(self, top: _Panel) -> None:
        """Newest (or last touched) on top."""

        if top in self._panels:
            self._panels.remove(top)
            self._panels.append(top)
        for rank, panel in enumerate(self._panels):
            setter = getattr(panel.overlay, "set_sort_order", None)
            if callable(setter):
                try:
                    setter(BASE_SORT_ORDER + rank)
                except Exception:
                    logger.debug("Failed to order a result panel", exc_info=True)

    def _place(self, panel: _Panel) -> bool:
        try:
            return bool(panel.overlay.place(panel.rows, panel.width_m))
        except Exception:
            logger.debug("Failed to place a result panel", exc_info=True)
            return False

    # ------------------------------------------------------------ drawing
    def _panel_input(self, panel: _Panel) -> tuple[str | None, str | None, tuple[float, float] | None, float]:
        hover = pressed = None
        cursor = None
        dwell = 0.0
        for state in self._hands.values():
            if state.hover_panel != panel.panel_id:
                continue
            if hover is None or state.pressed is not None:
                hover = state.hover_action
                cursor = state.cursor
                dwell = state.dwell_fraction
            if state.pressed and state.pressed[0] == panel.panel_id:
                pressed = state.pressed[1]
        return hover, pressed, cursor, dwell

    def _draw(self, panel: _Panel, *, force: bool = False) -> bool:
        hover, pressed, cursor, dwell = self._panel_input(panel)
        key = (hover, pressed, round(dwell, 2))
        now = self._clock()
        cursor_only = key == panel.drawn
        if not force and cursor_only and now - panel.last_draw < CURSOR_REDRAW_INTERVAL_S:
            return True
        try:
            image = panel.view.render_image(hover=hover, pressed=pressed, cursor=cursor, dwell=dwell)
        except Exception:
            logger.debug("Result panel render failed", exc_info=True)
            return False
        try:
            ok = bool(panel.overlay.upload(image))
        except Exception:
            logger.debug("Result panel upload failed", exc_info=True)
            ok = False
        if ok:
            panel.dirty = False
            panel.drawn = key
            panel.last_draw = now
        return ok

    # ------------------------------------------------------------ input
    def _laser(self, hand: str) -> Any | None:
        laser = self._lasers.get(hand)
        if laser is None:
            try:
                laser = self._laser_factory(hand)
            except Exception:
                logger.debug("Failed to build a panel laser", exc_info=True)
                return None
            self._lasers[hand] = laser
        return laser

    def _hide_lasers(self) -> None:
        for laser in self._lasers.values():
            try:
                laser.hide()
            except Exception:
                logger.debug("Failed to hide a panel laser", exc_info=True)

    def _nearest_hit(self, ray: Ray) -> tuple[_Panel, float, float, float] | None:
        best = None
        for panel in self._panels:
            try:
                hit = panel.overlay.intersect(ray)
            except Exception:
                hit = None
            if hit is None:
                continue
            u, v, distance = hit
            if best is None or distance < best[3]:
                best = (panel, u, v, distance)
        return best

    def _set_hover(self, state: _HandState, panel: _Panel | None, action: str | None, cursor) -> None:
        previous = self._find(state.hover_panel)
        if state.hover_panel != (panel.panel_id if panel else None) or state.hover_action != action:
            if previous is not None:
                previous.dirty = True
            if panel is not None:
                panel.dirty = True
        elif panel is not None and cursor != state.cursor:
            panel.dirty = True
        state.hover_panel = panel.panel_id if panel else None
        state.hover_action = action
        state.cursor = cursor

    def _reset_dwell(self, state: _HandState) -> None:
        if state.dwell_fraction:
            panel = self._find(state.hover_panel)
            if panel is not None:
                panel.dirty = True
        state.dwell_target = None
        state.dwell_since = 0.0
        state.dwell_fired = False
        state.dwell_fraction = 0.0

    def _dwell(self, hand: str, state: _HandState, now: float) -> None:
        target = (state.hover_panel, state.hover_action) if state.hover_action and state.hover_panel else None
        if target is None or target != state.dwell_target:
            self._reset_dwell(state)
            if target is not None:
                state.dwell_target = target  # type: ignore[assignment]
                state.dwell_since = now
            return
        if state.dwell_fired:
            return
        fraction = min(1.0, (now - state.dwell_since) / DWELL_SECONDS)
        if abs(fraction - state.dwell_fraction) > 0.02:
            state.dwell_fraction = fraction
            panel = self._find(state.hover_panel)
            if panel is not None:
                panel.dirty = True
        if fraction >= 1.0:
            state.dwell_fired = True
            state.dwell_fraction = 0.0
            self._fire(hand, target[0], target[1])

    def _fire(self, hand: str, panel_id: int, action: str) -> None:
        panel = self._find(panel_id)
        if panel is None:
            return
        self._cue_safe("click", hand)
        if action == "close":
            self._close(panel, notify=True)
            return
        if action in {"copy", "chatbox", "open_link"}:
            if callable(self._on_share):
                try:
                    self._on_share(panel.entry_id, action, bool(getattr(panel.view, "show_original", False)))
                except Exception:
                    logger.debug("Panel share callback failed", exc_info=True)
            return
        if action == "pin":
            if panel.entry_id is None:
                return
            pinned = not bool(getattr(panel.view, "pinned", False))
            try:
                panel.view.set_pinned(pinned)
            except Exception:
                logger.debug("Failed to pin a panel", exc_info=True)
            panel.dirty = True
            if callable(self._on_pin):
                try:
                    self._on_pin(panel.entry_id, pinned)
                except Exception:
                    logger.debug("Panel pin callback failed", exc_info=True)
            return
        try:
            if panel.view.apply(action):
                panel.dirty = True
        except Exception:
            logger.debug("Panel action %s failed", action, exc_info=True)

    def _grab_step(self, state: _HandState, inp: HandInput, head: Vector | None) -> None:
        panel = self._find(state.grab_panel)
        if panel is None or inp.pose is None:
            state.grab_panel = None
            return
        position = from_local_point(inp.pose, state.grab_offset)
        panel.rows = face_head_transform(position, head)
        self._place(panel)

    def _scale_step(self, state: _HandState, inp: HandInput, other: HandInput | None) -> None:
        panel = self._find(state.grab_panel)
        if panel is None or inp.pose is None or other is None or other.pose is None:
            return
        start_distance, start_width = state.scale_from
        distance = math.dist(position_of(inp.pose), position_of(other.pose))
        if start_distance <= 1e-3:
            return
        width = max(MIN_PANEL_WIDTH_METERS, min(MAX_PANEL_WIDTH_METERS, start_width * distance / start_distance))
        if abs(width - panel.width_m) > 0.002:
            panel.width_m = width
            self._place(panel)

    def _two_hand_trigger(self, hand: str, panel_id: int) -> None:
        """A trigger pulled while both hands hold the panel: that hand's shortcut."""

        action = str(self.two_hand_actions.get(hand, "none") or "none")
        panel = self._find(panel_id)
        if panel is None or action == "none" or action not in TWO_HAND_ACTIONS:
            return
        if action == "next_or_close":
            try:
                last_page = int(getattr(panel.view, "page", 0)) + 1 >= int(getattr(panel.view, "page_count", 1))
            except (TypeError, ValueError):
                last_page = True
            action = "close" if last_page else "page_next"
        if action == "close":
            for other in self._hands.values():
                if other.grab_panel == panel_id:
                    other.grab_panel = None
                    other.scaling = False
        self._fire(hand, panel_id, action)

    def poll(
        self,
        hands: dict[str, HandInput],
        *,
        head: Vector | None = None,
        head_pose: Rows | None = None,
        allow_input: bool = True,
        skip_hands: Sequence[str] = (),
        now: float | None = None,
    ) -> None:
        """Every tick: hover, clicks, grabs, lasers and redraws. Never raises.

        ``head_pose`` (the head's rows) lets a same-hand trigger gather the
        panels in front of the player.
        """

        try:
            self._poll(
                hands, head=head, head_pose=head_pose, allow_input=allow_input, skip_hands=skip_hands, now=now
            )
        except Exception:
            logger.debug("Result panel poll failed", exc_info=True)

    def _poll(
        self,
        hands: dict[str, HandInput],
        *,
        head: Vector | None,
        head_pose: Rows | None,
        allow_input: bool,
        skip_hands: Sequence[str],
        now: float | None,
    ) -> None:
        moment = self._clock() if now is None else float(now)
        if self._paused or not self._panels:
            self._hide_lasers()
            for hand in HANDS:
                state = self._hands[hand]
                inp = hands.get(hand) or HandInput()
                state.trigger_was = bool(inp.trigger)
                state.grip_was = bool(inp.grip)
                state.grab_panel = None
                state.scaling = False
                self._set_hover(state, None, None, None)
            return
        for hand in HANDS:
            state = self._hands[hand]
            inp = hands.get(hand) or HandInput()
            trigger = bool(inp.trigger)
            grip = bool(inp.grip)
            laser = self._laser(hand)
            if state.grab_panel is not None:
                # A held panel follows the hand until the grip opens,
                # whatever else is going on.
                if grip and inp.pose is not None:
                    other_hand = "right" if hand == "left" else "left"
                    two_hand = self._hands[other_hand].grab_panel == state.grab_panel
                    if state.scaling and not two_hand:
                        # The hand that moved it let go: this one carries it now.
                        panel = self._find(state.grab_panel)
                        state.scaling = False
                        if panel is not None:
                            state.grab_offset = to_local_point(inp.pose, position_of(panel.rows))
                    if state.scaling:
                        self._scale_step(state, inp, hands.get(other_hand))
                    else:
                        self._grab_step(state, inp, head)
                    if trigger and not state.trigger_was and state.grab_panel is not None:
                        if two_hand:
                            self._two_hand_trigger(hand, state.grab_panel)
                        elif self.same_hand_trigger_gathers and head_pose is not None:
                            state.grab_panel = None
                            self.gather(head_pose)
                            self._cue_safe("toggle", hand)
                    if laser is not None:
                        laser.hide()
                    state.trigger_was, state.grip_was = trigger, grip
                    continue
                state.grab_panel = None
                state.scaling = False
                self._cue_safe("grab", hand)
            ray = inp.ray
            if ray is None and inp.pose is not None:
                ray = ray_from_pose(inp.pose)
            if not allow_input or hand in skip_hands or ray is None:
                self._set_hover(state, None, None, None)
                self._reset_dwell(state)
                if state.pressed is not None:
                    state.pressed = None
                if laser is not None:
                    laser.hide()
                state.trigger_was, state.grip_was = trigger, grip
                continue
            hit = self._nearest_hit(ray)
            if hit is None:
                self._set_hover(state, None, None, None)
                state.distance = None
            else:
                panel, u, v, distance = hit
                width, height = panel.view.size
                x, y = u * width, v * height
                try:
                    action = panel.view.hit_test(x, y)
                except Exception:
                    action = None
                cursor = (x, y)
                if state.cursor is not None and state.hover_panel == panel.panel_id:
                    if abs(cursor[0] - state.cursor[0]) <= 2 and abs(cursor[1] - state.cursor[1]) <= 2:
                        cursor = state.cursor
                self._set_hover(state, panel, action, cursor)
                state.distance = distance or None
            if laser is not None:
                if hit is not None and head is not None:
                    try:
                        laser.show_ray(ray, max(0.05, hit[3] or 0.3), head)
                    except Exception:
                        logger.debug("Failed to draw a panel laser", exc_info=True)
                else:
                    laser.hide()
            # Grip on a panel: pick it up.
            if grip and not state.grip_was and inp.grip is not None and hit is not None and inp.pose is not None:
                panel = hit[0]
                state.grab_panel = panel.panel_id
                state.grab_offset = to_local_point(inp.pose, position_of(panel.rows))
                other_hand = "right" if hand == "left" else "left"
                other_input = hands.get(other_hand)
                state.scaling = False
                if (
                    self._hands[other_hand].grab_panel == panel.panel_id
                    and other_input is not None
                    and other_input.pose is not None
                ):
                    # Second hand on a held panel: it resizes, the first moves.
                    state.scaling = True
                    state.scale_from = (
                        math.dist(position_of(inp.pose), position_of(other_input.pose)),
                        panel.width_m,
                    )
                self._raise(panel)
                self._cue_safe("grab", hand)
                self._set_hover(state, None, None, None)
                if laser is not None:
                    laser.hide()
                state.trigger_was, state.grip_was = trigger, grip
                continue
            # Trigger: a press and release on the same button is a click.
            if inp.trigger is None:
                self._dwell(hand, state, moment)
            else:
                self._reset_dwell(state)
                if trigger and not state.trigger_was:
                    if state.hover_panel is not None and state.hover_action:
                        state.pressed = (state.hover_panel, state.hover_action)
                        panel = self._find(state.hover_panel)
                        if panel is not None:
                            panel.dirty = True
                elif not trigger and state.trigger_was and state.pressed is not None:
                    pressed, state.pressed = state.pressed, None
                    panel = self._find(pressed[0])
                    if panel is not None:
                        panel.dirty = True
                    if (state.hover_panel, state.hover_action) == pressed:
                        self._fire(hand, pressed[0], pressed[1])
            state.trigger_was, state.grip_was = trigger, grip
        for panel in list(self._panels):
            if panel.dirty:
                self._draw(panel)
