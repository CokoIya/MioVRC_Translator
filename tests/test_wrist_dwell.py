"""Clicking the wrist panel without a trigger binding, and nudging SteamVR
while the bindings stay inactive.

A player's first session had no active bindings at all (they came alive
only after VRChat was restarted), so nothing on the wrist panel could be
clicked while the SteamVR menu's copy worked. Resting the laser on a
button now clicks it, and the action manifest is handed over again every
half minute until SteamVR binds it.
"""

from __future__ import annotations

import sys
import types

import pytest

from src.core import steamvr_wrist
from src.core.steamvr_wrist import DWELL_SECONDS, SteamVRWristPanel
from src.core.vr_input import BINDING_REFRESH_SECONDS, VRActionInput
from tests.test_steamvr_wrist import _FakeOpenVR, _Panel, _twist_twice


@pytest.fixture
def fake_openvr(monkeypatch):
    module = _FakeOpenVR()
    monkeypatch.setitem(sys.modules, "openvr", module)
    monkeypatch.setattr(
        steamvr_wrist.OverlayTextureUploader, "upload_image", lambda self, image: False, raising=False
    )
    return module


def _shown_panel(fake_openvr, actions):
    panel = _Panel()
    wrist = SteamVRWristPanel(lambda: panel, hand="left", on_action=actions.append)
    assert wrist.start()
    _twist_twice(wrist, fake_openvr.system)
    fake_openvr.overlay.hit = (0.25, 0.5)  # on the button
    return wrist, panel


RAY = ((0.3, 1.5, -0.2), (0.0, -0.5, -0.8))


class TestDwellClick:
    def test_resting_the_laser_on_a_button_clicks_it_once(self, fake_openvr):
        actions: list = []
        wrist, panel = _shown_panel(fake_openvr, actions)

        wrist.poll(pointer=RAY, trigger_down=None, now=20.0)
        wrist.poll(pointer=RAY, trigger_down=None, now=20.0 + DWELL_SECONDS / 2)
        assert actions == []
        assert 0.4 < panel.dwell < 0.6  # the ring is filling
        wrist.poll(pointer=RAY, trigger_down=None, now=20.0 + DWELL_SECONDS + 0.05)
        assert actions == ["toggle_listen"]

        # Staying on the button does not click again.
        wrist.poll(pointer=RAY, trigger_down=None, now=20.0 + 3 * DWELL_SECONDS)
        assert actions == ["toggle_listen"]
        wrist.stop()

    def test_leaving_and_returning_arms_a_new_click(self, fake_openvr):
        actions: list = []
        wrist, _panel = _shown_panel(fake_openvr, actions)
        wrist.poll(pointer=RAY, trigger_down=None, now=20.0)
        wrist.poll(pointer=RAY, trigger_down=None, now=21.0)
        assert actions == ["toggle_listen"]

        fake_openvr.overlay.hit = (0.75, 0.5)  # off the buttons
        wrist.poll(pointer=RAY, trigger_down=None, now=21.2)
        fake_openvr.overlay.hit = (0.25, 0.5)
        wrist.poll(pointer=RAY, trigger_down=None, now=21.4)
        wrist.poll(pointer=RAY, trigger_down=None, now=21.4 + DWELL_SECONDS + 0.05)

        assert actions == ["toggle_listen", "toggle_listen"]
        wrist.stop()

    def test_a_working_trigger_never_dwell_clicks(self, fake_openvr):
        actions: list = []
        wrist, panel = _shown_panel(fake_openvr, actions)

        for step in range(6):
            wrist.poll(pointer=RAY, trigger_down=False, now=20.0 + step * 0.5)

        assert actions == []
        assert panel.dwell == 0.0
        wrist.stop()

    def test_moving_between_buttons_restarts_the_countdown(self, fake_openvr):
        actions: list = []
        wrist, _panel = _shown_panel(fake_openvr, actions)
        panel = wrist.panel
        panel.hit_test = lambda x, y: "a" if x < 250 else ("b" if x < 500 else None)

        fake_openvr.overlay.hit = (0.1, 0.5)
        wrist.poll(pointer=RAY, trigger_down=None, now=20.0)
        wrist.poll(pointer=RAY, trigger_down=None, now=20.6)
        fake_openvr.overlay.hit = (0.4, 0.5)
        wrist.poll(pointer=RAY, trigger_down=None, now=20.7)
        wrist.poll(pointer=RAY, trigger_down=None, now=21.2)
        assert actions == []
        wrist.poll(pointer=RAY, trigger_down=None, now=20.7 + DWELL_SECONDS + 0.05)

        assert actions == ["b"]
        wrist.stop()


class TestBindingRefresh:
    def _input(self):
        vr_input = VRActionInput()
        calls: list = []
        vr_input._input = types.SimpleNamespace(setActionManifestPath=lambda path: calls.append(path))
        return vr_input, calls

    def test_inactive_bindings_are_re_registered_every_half_minute(self):
        vr_input, calls = self._input()

        vr_input._note_binding_state(False, now=100.0)
        vr_input._note_binding_state(False, now=110.0)
        assert calls == []
        vr_input._note_binding_state(False, now=100.0 + BINDING_REFRESH_SECONDS + 1)
        assert len(calls) == 1
        vr_input._note_binding_state(False, now=100.0 + BINDING_REFRESH_SECONDS + 5)
        assert len(calls) == 1
        vr_input._note_binding_state(False, now=100.0 + 2 * BINDING_REFRESH_SECONDS + 3)
        assert len(calls) == 2

    def test_active_bindings_are_left_alone(self):
        vr_input, calls = self._input()

        for step in range(5):
            vr_input._note_binding_state(True, now=100.0 + step * 40)

        assert calls == []
        assert vr_input.active is True

    def test_a_failing_registration_is_swallowed(self):
        vr_input = VRActionInput()
        vr_input._input = types.SimpleNamespace(
            setActionManifestPath=lambda path: (_ for _ in ()).throw(RuntimeError("busy"))
        )

        vr_input._note_binding_state(False, now=100.0)
        vr_input._note_binding_state(False, now=200.0)

        assert vr_input.active is False
