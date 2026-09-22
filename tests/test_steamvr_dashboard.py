"""The Mio dashboard tab: laser clicks become actions; the picture is hit-tested."""

from __future__ import annotations

import sys
import types

import pytest

from src.core.steamvr_dashboard import SteamVRDashboard
from src.ui_qt.vr_dashboard_panel import PANEL_SIZE, VRDashboardPanel


class _Overlay:
    def __init__(self):
        self.calls: list[tuple] = []
        self.events: list = []
        self.visible = True

    def createDashboardOverlay(self, key, name):
        self.calls.append(("create", key, name))
        return 11, 12

    def isOverlayVisible(self, handle):
        return self.visible

    def pollNextOverlayEvent(self, handle, event):
        if not self.events:
            return (False, event)
        kind, x, y = self.events.pop(0)
        event.eventType = kind
        event.data.mouse.x = x
        event.data.mouse.y = y
        return (True, event)

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
            return None

        return record


class _FakeOpenVR(types.ModuleType):
    VREvent_MouseMove = 301
    VREvent_MouseButtonDown = 300
    VREvent_MouseButtonUp = 302
    VREvent_FocusLeave = 304
    VREvent_OverlayShown = 500
    VREvent_OverlayHidden = 501
    VROverlayInputMethod_Mouse = 1

    class HmdVector2_t:
        def __init__(self, x=0.0, y=0.0):
            self.x, self.y = x, y

    class VREvent_t:
        def __init__(self):
            self.eventType = 0
            self.data = types.SimpleNamespace(mouse=types.SimpleNamespace(x=0.0, y=0.0))

    def __init__(self):
        super().__init__("openvr")
        self.overlay = _Overlay()

    def IVROverlay(self):
        return self.overlay


class _Panel:
    """A two-button panel: 'a' on the left half, 'b' on the right half."""

    size = (200, 100)

    def __init__(self):
        self.states: list[dict] = []
        self.renders = 0

    def set_state(self, state):
        self.states.append(dict(state))
        return True

    def hit_test(self, x, y):
        if not (0 <= y < 100):
            return None
        return "a" if x < 100 else "b"

    def render_rgba(self, *, hover=None, pressed=None):
        self.renders += 1
        return b"", 200, 100


@pytest.fixture
def fake_openvr(monkeypatch):
    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    return module


class TestDashboardOverlay:
    def test_start_creates_a_dashboard_tab_with_mouse_input(self, fake_openvr):
        dashboard = SteamVRDashboard(_Panel)

        assert dashboard.start()

        names = [c[0] for c in fake_openvr.overlay.calls]
        assert names[0] == "create"
        assert "setOverlayInputMethod" in names
        assert "setOverlayMouseScale" in names
        assert "setOverlayRaw" in names

    def test_a_click_on_a_button_fires_its_action(self, fake_openvr):
        actions: list[str] = []
        dashboard = SteamVRDashboard(_Panel, on_action=actions.append)
        dashboard.start()
        # Mouse y is measured from the bottom: y=90 is near the top row.
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonDown, 150, 90),
            (fake_openvr.VREvent_MouseButtonUp, 150, 90),
        ]

        dashboard.poll()

        assert actions == ["b"]

    def test_a_press_released_elsewhere_is_not_a_click(self, fake_openvr):
        actions: list[str] = []
        dashboard = SteamVRDashboard(_Panel, on_action=actions.append)
        dashboard.start()
        fake_openvr.overlay.events = [
            (fake_openvr.VREvent_MouseButtonDown, 50, 90),
            (fake_openvr.VREvent_MouseButtonUp, 150, 90),
        ]

        dashboard.poll()

        assert actions == []

    def test_hover_changes_redraw_only_while_visible(self, fake_openvr):
        dashboard = SteamVRDashboard(_Panel)
        dashboard.start()
        panel = dashboard.panel
        before = panel.renders
        fake_openvr.overlay.visible = False
        fake_openvr.overlay.events = [(fake_openvr.VREvent_MouseMove, 50, 90)]

        dashboard.poll()

        assert panel.renders == before
        fake_openvr.overlay.visible = True
        dashboard.poll()
        assert panel.renders == before + 1

    def test_stop_destroys_the_overlay(self, fake_openvr):
        dashboard = SteamVRDashboard(_Panel)
        dashboard.start()

        dashboard.stop()

        assert "destroyOverlay" in [c[0] for c in fake_openvr.overlay.calls]
        assert not dashboard.available

    def test_state_is_passed_to_the_panel(self, fake_openvr):
        dashboard = SteamVRDashboard(_Panel)
        dashboard.start()

        dashboard.set_state({"listening": True})

        assert dashboard.panel.states[-1] == {"listening": True}


class TestPanel:
    def test_every_action_has_a_button_and_they_do_not_overlap(self, qapp):
        panel = VRDashboardPanel("zh-CN")
        panel.set_state({"listening": False})
        panel.hit_test(0, 0)  # forces layout

        buttons = panel._buttons
        actions = [b.action for b in buttons]
        for expected in (
            "toggle_listen",
            "toggle_listen_others",
            "toggle_tts",
            "provider_prev",
            "provider_next",
            "model_prev",
            "model_next",
            "output_format_next",
            "tts_engine_next",
            "toggle_vr_overlay",
            "toggle_desktop_overlay",
            "size:small",
            "size:medium",
            "size:large",
            "size:xlarge",
            "move_panel",
            "reset_position",
            "screenshot",
        ):
            assert expected in actions
        width, height = PANEL_SIZE
        for i, first in enumerate(buttons):
            assert 0 <= first.rect.left() and first.rect.right() <= width
            assert 0 <= first.rect.top() and first.rect.bottom() <= height
            for second in buttons[i + 1 :]:
                assert not first.rect.intersects(second.rect), (first.action, second.action)

    def test_hit_test_finds_the_button_under_the_laser(self, qapp):
        panel = VRDashboardPanel("en")
        panel.set_state({})
        panel.hit_test(0, 0)
        button = next(b for b in panel._buttons if b.action == "screenshot")

        assert panel.hit_test(button.rect.center().x(), button.rect.center().y()) == "screenshot"
        assert panel.hit_test(-5, -5) is None

    def test_labels_follow_the_state(self, qapp):
        panel = VRDashboardPanel("en")
        panel.set_state({"listening": True, "vr_size": "large", "provider": "Bing"})
        panel.hit_test(0, 0)
        by_action = {b.action: b for b in panel._buttons}

        assert by_action["toggle_listen"].active
        assert "Stop" in by_action["toggle_listen"].label
        assert by_action["size:large"].active
        assert not by_action["size:medium"].active
        assert "Bing" in by_action["provider_next"].label

    def test_render_returns_a_full_rgba_buffer(self, qapp):
        panel = VRDashboardPanel("ja")
        panel.set_state({"listening": False})

        buffer, width, height = panel.render_rgba(hover="screenshot", pressed=None)

        assert (width, height) == PANEL_SIZE
        assert len(buffer) == width * height * 4

    def test_unchanged_state_is_not_a_change(self, qapp):
        panel = VRDashboardPanel("en")
        assert panel.set_state({"listening": False}) is True
        assert panel.set_state({"listening": False}) is False
