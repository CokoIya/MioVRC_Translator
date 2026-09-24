"""Kept translations as panels in the room (VRHandsFrame style).

The manager is driven with fake overlays, lasers and a fake panel picture, so
opening, pointing, clicking, dwelling, grabbing, gathering and the capture
pause can all be checked without a headset.
"""

from __future__ import annotations

import math

import pytest

from src.core.steamvr_wrist import DWELL_SECONDS
from src.core.vr_result_panels import (
    BASE_SORT_ORDER,
    MAX_PANELS,
    MIN_PANEL_WIDTH_METERS,
    HandInput,
    ResultPanelManager,
    arc_transforms,
    face_head_transform,
    from_local_point,
    in_front_transform,
    level_forward,
    position_of,
    to_local_point,
)

HEAD = (0.0, 1.6, 0.0)


def _identity(x=0.0, y=0.0, z=0.0):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z]]


def _yawed(degrees, x=0.0, y=0.0, z=0.0):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[c, 0.0, s, x], [0.0, 1.0, 0.0, y], [-s, 0.0, c, z]]


def _looking_down(degrees, x=0.0, y=1.6, z=0.0):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    # Pitch down about +X: forward (-Z) tips toward -Y.
    return [[1.0, 0.0, 0.0, x], [0.0, c, s, y], [0.0, -s, c, z]]


class TestGeometry:
    def test_a_panel_faces_the_head_and_stays_upright(self):
        rows = face_head_transform((0.3, 1.2, -0.6), HEAD)

        to_head = (0.0 - 0.3, 1.6 - 1.2, 0.0 + 0.6)
        length = math.sqrt(sum(c * c for c in to_head))
        assert (rows[0][2], rows[1][2], rows[2][2]) == pytest.approx(tuple(c / length for c in to_head))
        # +X stays level, so text never tilts.
        assert rows[1][0] == pytest.approx(0.0)
        assert position_of(rows) == (0.3, 1.2, -0.6)

    def test_without_a_head_it_faces_the_room(self):
        rows = face_head_transform((0.0, 1.0, -1.0), None)

        assert (rows[0][2], rows[1][2], rows[2][2]) == (0.0, 0.0, 1.0)

    def test_in_front_is_level_even_when_looking_at_the_floor(self):
        rows = in_front_transform(_looking_down(60), distance=0.6, drop=0.08)

        assert level_forward(_looking_down(60)) == pytest.approx((0.0, 0.0, -1.0))
        assert position_of(rows) == pytest.approx((0.0, 1.52, -0.6))

    def test_in_front_follows_the_heading(self):
        rows = in_front_transform(_yawed(90, 0.0, 1.6, 0.0), distance=0.5, drop=0.0)

        # Yawed 90 degrees left: forward is -X.
        assert position_of(rows) == pytest.approx((-0.5, 1.6, 0.0))

    def test_gathered_panels_sit_side_by_side_in_front(self):
        rows = arc_transforms(_identity(0.0, 1.6, 0.0), 3, 0.4, distance=0.6)

        xs = [position_of(r)[0] for r in rows]
        assert xs[0] < xs[1] < xs[2]
        assert xs[1] == pytest.approx(0.0)
        assert xs[0] == pytest.approx(-xs[2])
        for r in rows:
            assert math.dist(position_of(r)[::2], (0.0, 0.0)) == pytest.approx(0.6)

    def test_local_points_round_trip_through_a_pose(self):
        pose = _yawed(35, 0.2, 1.1, -0.4)
        point = (0.5, 1.4, -0.9)

        assert from_local_point(pose, to_local_point(pose, point)) == pytest.approx(point)


class _Overlay:
    def __init__(self, slot):
        self.slot = slot
        self.placed: list = []
        self.uploads = 0
        self.visible = False
        self.shows = 0
        self.hides = 0
        self.hit = None
        self.order = None
        self.stopped = False

    def place(self, rows, width):
        self.placed.append(([list(r) for r in rows], width))
        return True

    def upload(self, image):
        self.uploads += 1
        return True

    def show(self):
        self.visible = True
        self.shows += 1

    def hide(self):
        self.visible = False
        self.hides += 1

    def intersect(self, ray):
        return self.hit if self.visible else None

    def set_sort_order(self, order):
        self.order = order

    def stop(self):
        self.stopped = True


class _Laser:
    def __init__(self):
        self.rays: list = []
        self.hidden = 0

    def show_ray(self, ray, length, head):
        self.rays.append((ray, length))

    def hide(self):
        self.hidden += 1

    def stop(self):
        pass


class _View:
    size = (1000, 800)

    def __init__(self):
        self.result = None
        self.tutorial = None
        self.pinned = False
        self.applied: list = []
        self.renders: list = []

    def set_result(self, *, title, card, picture, pairs, pinned=False, links=None):
        self.result = (title, card, picture, pairs)
        self.pinned = pinned
        self.links = list(links or [])

    def set_tutorial(self, title, lines):
        self.tutorial = (title, lines)

    def set_pinned(self, pinned):
        self.pinned = pinned

    def apply(self, action):
        self.applied.append(action)
        return True

    def hit_test(self, x, y):
        if y > 700:
            return "close" if x > 500 else "pin"
        if y < 100:
            return "view"
        return None

    def render_image(self, *, hover=None, pressed=None, cursor=None, dwell=0.0):
        self.renders.append((hover, pressed, cursor, dwell))
        return object()


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _manager(**kwargs):
    overlays: list[_Overlay] = []
    lasers: dict[str, _Laser] = {}
    views: list[_View] = []
    events: list = []
    clock = _Clock()

    def overlay_factory(slot):
        overlay = _Overlay(slot)
        overlays.append(overlay)
        return overlay

    def laser_factory(hand):
        laser = _Laser()
        lasers[hand] = laser
        return laser

    def view_factory():
        view = _View()
        views.append(view)
        return view

    manager = ResultPanelManager(
        view_factory,
        on_pin=lambda entry, pinned: events.append(("pin", entry, pinned)),
        on_close=lambda entry: events.append(("close", entry)),
        cue=lambda name, hand=None: events.append(("cue", name, hand)),
        overlay_factory=overlay_factory,
        laser_factory=laser_factory,
        clock=clock,
        **kwargs,
    )
    return manager, overlays, lasers, views, events, clock


def _open(manager, entry_id=1, rows=None, width=0.42):
    return manager.open_result(
        entry_id=entry_id,
        title=f"read {entry_id}",
        card="card",
        picture="picture",
        pairs=[("a", "b")],
        rows=rows or face_head_transform((0.0, 1.5, -0.6), HEAD),
        width_m=width,
    )


RAY = ((0.0, 1.4, -0.2), (0.0, 0.0, -1.0))


def _pointing(trigger=False, grip=False, pose=None, ray=RAY):
    return HandInput(ray=ray, pose=pose or _identity(0.0, 1.4, -0.2), trigger=trigger, grip=grip)


class TestOpening:
    def test_a_panel_is_drawn_placed_and_shown(self):
        manager, overlays, _lasers, views, _events, _clock = _manager()

        panel_id = _open(manager)

        assert panel_id == 1
        assert overlays[0].uploads == 1
        assert overlays[0].placed[0][1] == pytest.approx(0.42)
        assert overlays[0].visible
        assert views[0].result[3] == [("a", "b")]
        assert manager.entry_ids() == [1]

    def test_a_narrow_region_still_gets_a_readable_panel(self):
        manager, overlays, *_ = _manager()

        _open(manager, width=0.05)

        assert overlays[0].placed[0][1] == pytest.approx(MIN_PANEL_WIDTH_METERS)

    def test_opening_a_read_that_is_already_up_moves_it_instead(self):
        manager, overlays, *_ = _manager()
        first = _open(manager, entry_id=7)

        again = _open(manager, entry_id=7, rows=face_head_transform((0.5, 1.5, -0.5), HEAD))

        assert again == first
        assert len(overlays) == 1
        assert position_of(overlays[0].placed[-1][0]) == (0.5, 1.5, -0.5)

    def test_a_new_panel_does_not_cover_an_open_one(self):
        manager, overlays, *_ = _manager()
        _open(manager, entry_id=1)

        _open(manager, entry_id=2)

        first = position_of(overlays[0].placed[-1][0])
        second = position_of(overlays[1].placed[-1][0])
        assert math.dist(first, second) > 0.4

    def test_the_oldest_goes_when_too_many_are_up(self):
        manager, overlays, _lasers, _views, events, _clock = _manager()
        for entry in range(1, MAX_PANELS + 1):
            _open(manager, entry_id=entry)

        _open(manager, entry_id=99)

        assert manager.count == MAX_PANELS
        assert 1 not in manager.entry_ids() and 99 in manager.entry_ids()
        assert ("close", 1) in events
        # Its overlay was reused, not a fifth one created.
        assert len(overlays) == MAX_PANELS

    def test_the_newest_is_on_top(self):
        manager, overlays, *_ = _manager()
        _open(manager, entry_id=1)
        _open(manager, entry_id=2)

        assert overlays[1].order > overlays[0].order >= BASE_SORT_ORDER

    def test_the_tutorial_is_a_panel_of_its_own(self):
        manager, _overlays, _lasers, views, *_ = _manager()

        manager.open_tutorial("How", ["one", "two"], face_head_transform((0.0, 1.5, -0.7), HEAD))
        manager.open_tutorial("How", ["again"], face_head_transform((0.0, 1.5, -0.7), HEAD))

        assert manager.count == 1
        assert views[-1].tutorial == ("How", ["again"])
        assert manager.entry_ids() == []


class TestPointing:
    def test_a_trigger_pull_on_close_closes_it(self):
        manager, overlays, lasers, _views, events, _clock = _manager()
        _open(manager, entry_id=4)
        overlays[0].hit = (0.9, 0.95, 0.5)

        manager.poll({"right": _pointing(trigger=False)}, head=HEAD)
        manager.poll({"right": _pointing(trigger=True)}, head=HEAD)
        manager.poll({"right": _pointing(trigger=False)}, head=HEAD)

        assert manager.count == 0
        assert ("cue", "click", "right") in events
        assert ("close", 4) in events
        assert lasers["right"].rays  # the beam showed while it pointed

    def test_a_press_that_slides_off_the_button_does_nothing(self):
        manager, overlays, *_ = _manager()
        _open(manager)
        overlays[0].hit = (0.9, 0.95, 0.5)
        manager.poll({"right": _pointing()}, head=HEAD)
        manager.poll({"right": _pointing(trigger=True)}, head=HEAD)
        overlays[0].hit = (0.5, 0.5, 0.5)

        manager.poll({"right": _pointing(trigger=False)}, head=HEAD)

        assert manager.count == 1

    def test_pin_is_reported_for_the_history(self):
        manager, overlays, _lasers, views, events, _clock = _manager()
        _open(manager, entry_id=3)
        overlays[0].hit = (0.2, 0.95, 0.5)

        for trigger in (False, True, False):
            manager.poll({"left": _pointing(trigger=trigger)}, head=HEAD)

        assert ("pin", 3, True) in events
        assert views[0].pinned

    def test_other_buttons_go_to_the_panel_itself(self):
        manager, overlays, _lasers, views, *_ = _manager()
        _open(manager)
        overlays[0].hit = (0.5, 0.05, 0.5)

        for trigger in (False, True, False):
            manager.poll({"right": _pointing(trigger=trigger)}, head=HEAD)

        assert views[0].applied == ["view"]

    def test_without_a_trigger_binding_resting_on_a_button_clicks_it(self):
        manager, overlays, _lasers, _views, events, clock = _manager()
        _open(manager, entry_id=5)
        overlays[0].hit = (0.9, 0.95, 0.5)
        unbound = HandInput(ray=RAY, pose=_identity(0.0, 1.4, -0.2), trigger=None, grip=None)

        manager.poll({"right": unbound}, head=HEAD, now=10.0)
        manager.poll({"right": unbound}, head=HEAD, now=10.0 + DWELL_SECONDS / 2)
        assert manager.count == 1
        manager.poll({"right": unbound}, head=HEAD, now=10.0 + DWELL_SECONDS + 0.01)

        assert manager.count == 0

    def test_no_beam_when_nothing_is_hit(self):
        manager, _overlays, lasers, *_ = _manager()
        _open(manager)

        manager.poll({"right": _pointing()}, head=HEAD)

        assert lasers["right"].rays == []
        assert lasers["right"].hidden >= 1

    def test_input_is_withheld_while_something_else_owns_the_hands(self):
        manager, overlays, lasers, _views, _events, _clock = _manager()
        _open(manager)
        overlays[0].hit = (0.9, 0.95, 0.5)

        for trigger in (False, True, False):
            manager.poll({"right": _pointing(trigger=trigger)}, head=HEAD, allow_input=False)
        for trigger in (False, True, False):
            manager.poll({"right": _pointing(trigger=trigger)}, head=HEAD, skip_hands=("right",))

        assert manager.count == 1
        assert lasers["right"].rays == []

    def test_a_hovered_panel_shows_its_cursor(self):
        manager, overlays, _lasers, views, _events, clock = _manager()
        _open(manager)
        overlays[0].hit = (0.25, 0.5, 0.5)
        clock.now += 1.0

        manager.poll({"right": _pointing()}, head=HEAD)

        hover, _pressed, cursor, _dwell = views[0].renders[-1]
        assert cursor == pytest.approx((250.0, 400.0))
        assert manager.pointing("right")


class TestGrabbing:
    def test_the_grip_picks_a_panel_up_and_it_follows_the_hand(self):
        manager, overlays, _lasers, _views, events, _clock = _manager()
        _open(manager, rows=face_head_transform((0.0, 1.4, -0.7), HEAD))
        overlays[0].hit = (0.5, 0.5, 0.5)
        hand = _identity(0.0, 1.4, -0.2)

        manager.poll({"right": _pointing(pose=hand)}, head=HEAD)
        manager.poll({"right": _pointing(grip=True, pose=hand)}, head=HEAD)
        assert manager.grabbing
        assert ("cue", "grab", "right") in events

        moved = _identity(0.3, 1.5, -0.2)
        manager.poll({"right": _pointing(grip=True, pose=moved)}, head=HEAD)

        # Same offset from the hand as when it was picked up, turned to the head.
        assert position_of(overlays[0].placed[-1][0]) == pytest.approx((0.3, 1.5, -0.7))
        rows = overlays[0].placed[-1][0]
        assert rows[1][0] == pytest.approx(0.0)

        manager.poll({"right": _pointing(grip=False, pose=moved)}, head=HEAD)
        assert not manager.grabbing
        manager.poll({"right": _pointing(grip=False, pose=_identity(1.0, 1.0, 1.0))}, head=HEAD)
        assert position_of(overlays[0].placed[-1][0]) == pytest.approx((0.3, 1.5, -0.7))

    def test_a_held_panel_keeps_following_when_input_is_withheld(self):
        manager, overlays, *_ = _manager()
        _open(manager, rows=face_head_transform((0.0, 1.4, -0.7), HEAD))
        overlays[0].hit = (0.5, 0.5, 0.5)
        manager.poll({"left": _pointing()}, head=HEAD)
        manager.poll({"left": _pointing(grip=True)}, head=HEAD)

        manager.poll({"left": _pointing(grip=True, pose=_identity(0.1, 1.4, -0.2))}, head=HEAD, allow_input=False)

        assert position_of(overlays[0].placed[-1][0])[0] == pytest.approx(0.1)

    def test_a_grip_that_was_already_held_does_not_grab(self):
        manager, overlays, *_ = _manager()
        _open(manager)
        manager.poll({"right": _pointing(grip=True)}, head=HEAD)
        overlays[0].hit = (0.5, 0.5, 0.5)

        manager.poll({"right": _pointing(grip=True)}, head=HEAD)

        assert not manager.grabbing


class TestArranging:
    def test_gather_brings_every_panel_in_front(self):
        manager, overlays, *_ = _manager()
        _open(manager, entry_id=1, rows=face_head_transform((3.0, 1.0, 3.0), HEAD))
        _open(manager, entry_id=2, rows=face_head_transform((-3.0, 1.0, 3.0), HEAD))

        assert manager.gather(_identity(0.0, 1.6, 0.0)) == 2

        for overlay in overlays:
            x, y, z = position_of(overlay.placed[-1][0])
            assert z < 0 and abs(x) < 0.5

    def test_close_all(self):
        manager, overlays, _lasers, _views, events, _clock = _manager()
        _open(manager, entry_id=1)
        _open(manager, entry_id=2)

        assert manager.close_all() == 2
        assert manager.count == 0
        assert all(not overlay.visible for overlay in overlays)
        assert ("close", 1) in events and ("close", 2) in events

    def test_a_capture_takes_them_down_and_puts_them_back(self):
        manager, overlays, lasers, *_ = _manager()
        _open(manager)
        overlays[0].hit = (0.9, 0.95, 0.5)
        manager.poll({"right": _pointing()}, head=HEAD)

        manager.pause()
        assert not overlays[0].visible
        manager.poll({"right": _pointing(trigger=True)}, head=HEAD)
        manager.poll({"right": _pointing(trigger=False)}, head=HEAD)
        assert manager.count == 1  # nothing was clicked while paused

        manager.resume()
        assert overlays[0].visible

    def test_pins_set_from_the_wrist_reach_the_panel(self):
        manager, _overlays, _lasers, views, *_ = _manager()
        _open(manager, entry_id=8)

        manager.set_pinned(8, True)

        assert views[0].pinned

    def test_stop_destroys_every_overlay(self):
        manager, overlays, *_ = _manager()
        _open(manager, entry_id=1)
        _open(manager, entry_id=2)

        manager.stop()

        assert manager.count == 0
        assert all(overlay.stopped for overlay in overlays)


class TestSharing:
    def test_copy_and_chatbox_go_to_the_owner_with_the_side_shown(self):
        shared: list = []
        manager, overlays, _lasers, views, _events, _clock = _manager()
        manager._on_share = lambda entry, action, original: shared.append((entry, action, original))
        _open(manager, entry_id=6)
        views[0].show_original = True
        views[0].hit_test = lambda x, y: "copy"
        overlays[0].hit = (0.5, 0.5, 0.5)

        for trigger in (False, True, False):
            manager.poll({"right": _pointing(trigger=trigger)}, head=HEAD)

        assert shared == [(6, "copy", True)]
        assert views[0].applied == []
