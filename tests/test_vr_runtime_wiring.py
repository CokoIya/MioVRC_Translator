"""Captions route to the desktop window, the headset, or both.

The SteamVR runtime serves two features (headset subtitles and the screenshot
button) and must outlive either switch; the desktop window and the headset
panel are independent surfaces, so switching one off never silences the other.
"""

from __future__ import annotations

import pytest

from src.ui_qt.main_window import MainWindow


class _Backend:
    def __init__(self, available=True):
        self.available = available
        self.started = 0
        self.hidden = 0
        self.revealed = 0
        self.unavailable_reason = ""
        self._panel = None

    def start(self):
        self.started += 1
        return self.available

    def hide(self):
        self.hidden += 1

    def reveal(self):
        self.revealed += 1

    def stop(self):
        self.available = False


class _Service:
    def __init__(self):
        self.backend = None
        self.name = ""
        self.enabled = None
        self.reveals = 0

    def set_backend(self, backend, *, backend_name="desktop"):
        self.backend = backend
        self.name = backend_name

    def set_enabled(self, enabled, *, reveal=True):
        self.enabled = bool(enabled)
        if enabled and reveal:
            self.reveals += 1


class _Desktop:
    def __init__(self):
        self.hidden_from_service = 0

    def hide_from_service(self):
        self.hidden_from_service += 1


def _window(
    monkeypatch,
    *,
    subtitles: bool,
    screenshot: bool,
    desktop: bool = True,
    available=True,
    existing_desktop_window: _Desktop | None = None,
    dashboard: bool = False,
):
    window = MainWindow.__new__(MainWindow)
    window._config = {
        "vrc_listen": {
            "show_overlay": desktop,
            "vr_overlay": {"enabled": subtitles},
            "screenshot_translation": {"enabled": screenshot},
            # The dashboard tab keeps the runtime up on its own; these tests
            # are about the other two features, so it stays out of the way.
            "vr_dashboard": {"enabled": dashboard},
        }
    }
    window._main_theme = "dark"
    window._listen_overlay_enabled = desktop
    window._floating_window = existing_desktop_window
    window._vr_overlay_backend = None
    backend = _Backend(available=available)
    service = _Service()
    desktop_window = existing_desktop_window or _Desktop()
    calls: list[str] = []

    def ensure_floating_window():
        window._floating_window = desktop_window
        return desktop_window

    def ensure_backend():
        window._vr_overlay_backend = backend
        return backend

    window._overlay_service = service
    monkeypatch.setattr(window, "_ensure_overlay_service", lambda **_k: service)
    monkeypatch.setattr(window, "_ensure_floating_window", ensure_floating_window)
    monkeypatch.setattr(window, "_ensure_vr_overlay_backend", ensure_backend)
    monkeypatch.setattr(window, "_apply_vr_overlay_settings", lambda: calls.append("apply"))
    monkeypatch.setattr(window, "_start_vr_overlay_timer", lambda: calls.append("timer"))
    monkeypatch.setattr(window, "_stop_vr_overlay_timer", lambda: calls.append("stop_timer"))
    monkeypatch.setattr(window, "_shutdown_vr_overlay", lambda: calls.append("shutdown"))
    monkeypatch.setattr(
        window, "_shutdown_screenshot_translation", lambda: calls.append("shot_off")
    )
    monkeypatch.setattr(window, "_sync_settings_window_vr_overlay_state", lambda: None)
    return window, backend, service, desktop_window, calls


class TestRuntimeNeeded:
    @pytest.mark.parametrize(
        ("subtitles", "screenshot", "expected"),
        [
            (False, False, False),
            (True, False, True),
            (False, True, True),
            (True, True, True),
        ],
    )
    def test_either_feature_keeps_steamvr_connected(
        self, monkeypatch, subtitles, screenshot, expected
    ):
        window, *_ = _window(monkeypatch, subtitles=subtitles, screenshot=screenshot)

        assert MainWindow._vr_runtime_needed(window) is expected


class TestRefreshOverlayBackend:
    def test_screenshot_alone_brings_the_runtime_up(self, monkeypatch):
        """This is the bug: the button did nothing unless subtitles were on."""

        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=True
        )

        MainWindow._refresh_overlay_backend(window)

        assert backend.started == 1
        assert "timer" in calls
        assert "shutdown" not in calls
        # The subtitle panel itself must stay out of the headset.
        assert backend.hidden == 1
        assert service.backend is desktop
        assert service.name == "desktop"

    def test_subtitles_alone_route_to_both_surfaces(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert service.name == "desktop+steamvr"
        assert desktop in service.backend.backends
        assert backend in service.backend.backends
        assert service.enabled is True
        assert "apply" in calls
        assert "shot_off" in calls

    def test_both_off_tears_the_runtime_down(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert calls == ["shutdown"]
        assert backend.started == 0
        assert service.backend is desktop
        assert service.enabled is True

    def test_a_runtime_that_will_not_start_falls_back_to_the_desktop(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=True, available=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert service.backend is desktop
        assert service.enabled is True
        assert "stop_timer" in calls
        assert "timer" not in calls


class TestIndependentSurfaces:
    """The player picks the desktop, the headset, or both; each switch is its own."""

    def test_headset_only_routes_straight_to_the_headset(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, desktop=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert service.backend is backend
        assert service.name == "steamvr"
        assert service.enabled is True
        # No desktop window gets built just to sit hidden.
        assert window._floating_window is None

    def test_switching_the_desktop_off_hides_its_window_but_keeps_the_headset(
        self, monkeypatch
    ):
        existing = _Desktop()
        window, backend, service, _desktop, calls = _window(
            monkeypatch,
            subtitles=True,
            screenshot=False,
            desktop=False,
            existing_desktop_window=existing,
        )

        MainWindow._refresh_overlay_backend(window)

        assert existing.hidden_from_service == 1
        assert service.backend is backend
        assert service.enabled is True

    def test_headset_only_without_steamvr_leaves_captions_with_no_surface(
        self, monkeypatch
    ):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, desktop=False, available=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert service.backend is None
        assert service.enabled is False

    def test_neither_surface_disables_the_service(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=False, desktop=False
        )

        MainWindow._refresh_overlay_backend(window)

        assert service.backend is None
        assert service.enabled is False

    def test_desktop_only_never_touches_steamvr(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=False, desktop=True
        )

        MainWindow._refresh_overlay_backend(window)

        assert backend.started == 0
        assert service.backend is desktop
        assert service.enabled is True


class TestOutputEnabled:
    def test_desktop_switch_alone_is_enough(self, monkeypatch):
        window, *_ = _window(monkeypatch, subtitles=False, screenshot=False, desktop=True)

        assert MainWindow._overlay_output_enabled(window) is True

    def test_headset_counts_only_once_its_runtime_is_up(self, monkeypatch):
        window, backend, *_ = _window(
            monkeypatch, subtitles=True, screenshot=False, desktop=False
        )

        assert MainWindow._overlay_output_enabled(window) is False
        window._vr_overlay_backend = backend
        assert MainWindow._overlay_output_enabled(window) is True
        backend.available = False
        assert MainWindow._overlay_output_enabled(window) is False

    def test_bare_instance_reads_as_off(self):
        window = MainWindow.__new__(MainWindow)

        assert MainWindow._overlay_output_enabled(window) is False


class TestSettingsChange:
    def test_turning_the_headset_on_reveals_its_panel_immediately(self, monkeypatch):
        """The player needs to see the panel to size and drag it."""

        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, desktop=False
        )
        monkeypatch.setattr(window, "_schedule_config_save", lambda: calls.append("save"))

        MainWindow.on_vr_overlay_settings_changed(window)

        assert backend.revealed == 1
        assert "save" in calls

    def test_turning_the_headset_off_does_not_reveal(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=True, desktop=True
        )
        monkeypatch.setattr(window, "_schedule_config_save", lambda: None)

        MainWindow.on_vr_overlay_settings_changed(window)

        assert backend.revealed == 0


class TestStartup:
    def test_saved_vr_features_come_back_without_a_toggle(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, desktop=False
        )
        window._destroying = False

        MainWindow._start_vr_runtime_if_configured(window)

        assert backend.started == 1
        assert service.backend is backend

    def test_nothing_saved_means_nothing_started(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=False, desktop=True
        )
        window._destroying = False

        MainWindow._start_vr_runtime_if_configured(window)

        assert backend.started == 0
        assert calls == []


class TestDashboardTab:
    """The Mio tab in the SteamVR menu lives and dies with the runtime."""

    def test_the_dashboard_alone_keeps_the_runtime_up(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=False, screenshot=False, dashboard=True
        )
        synced: list[str] = []
        monkeypatch.setattr(window, "_sync_vr_dashboard", lambda: synced.append("sync"))

        assert MainWindow._vr_runtime_needed(window) is True
        MainWindow._refresh_overlay_backend(window)

        assert backend.started == 1
        assert synced == ["sync"]
        # No subtitles were asked for: the panel stays hidden.
        assert backend.hidden == 1

    def test_sync_builds_the_tab_when_the_runtime_is_up(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._vr_overlay_backend = backend
        built: list[str] = []
        monkeypatch.setattr(window, "_ensure_vr_dashboard", lambda: built.append("built"))

        MainWindow._sync_vr_dashboard(window)

        assert built == ["built"]

    def test_sync_tears_the_tab_down_when_switched_off(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=False
        )
        window._vr_overlay_backend = backend

        class _Dash:
            stopped = 0

            def stop(self):
                _Dash.stopped += 1

        window._vr_dashboard = _Dash()

        MainWindow._sync_vr_dashboard(window)

        assert _Dash.stopped == 1
        assert window._vr_dashboard is None

    def test_state_reflects_the_switches(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._config["translation"] = {"backend": "bing", "output_format": "translated_only"}
        window._config["tts"] = {"enabled": True, "engine": "edge-tts"}
        window._config["vrc_listen"]["vr_overlay"]["width_meters"] = 1.6
        window._running = True
        window._desktop_capture_enabled = True
        window._ui_lang = "en"
        window._vr_overlay_backend = backend
        window._vr_input = None
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)

        state = MainWindow._vr_dashboard_state(window)

        assert state["listening"] is True
        assert state["listen_others"] is True
        assert state["tts"] is True
        assert state["vr_overlay"] is True
        assert state["desktop_overlay"] is True
        assert state["vr_size"] == "large"
        assert state["binding_active"] is False
        assert state["status"] == "vr_dash_status_listening"
        assert state["provider"]

    def test_a_size_button_changes_the_width_and_notifies(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._vr_overlay_backend = backend
        notified: list[str] = []
        monkeypatch.setattr(window, "on_vr_overlay_settings_changed", lambda: notified.append("changed"))
        monkeypatch.setattr(window, "_sync_settings_window_vr_overlay_controls", lambda: None)
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "size:xlarge")

        assert window._config["vrc_listen"]["vr_overlay"]["width_meters"] == 2.2
        assert notified == ["changed"]

    def test_the_move_button_toggles_edit_mode(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._vr_overlay_backend = backend
        toggled: list[str] = []
        monkeypatch.setattr(window, "_toggle_vr_edit_mode", lambda: toggled.append("toggle"))
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "move_panel")

        assert toggled == ["toggle"]

    def test_state_carries_the_chatbox_mute_and_lock_switches(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._config["translation"] = {"backend": "bing", "send_to_chatbox": False}
        window._config["vrc_listen"]["send_to_chatbox"] = True
        window._config["vrc_listen"]["vr_overlay"]["locked"] = True
        window._mic_muted = True
        window._vr_overlay_backend = backend
        window._vr_input = None
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)

        state = MainWindow._vr_dashboard_state(window)

        assert state["mic_chatbox"] is False
        assert state["listen_chatbox"] is True
        assert state["muted"] is True
        assert state["locked"] is True

    def test_the_chatbox_buttons_flip_their_switches_and_save(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._config["translation"] = {"backend": "bing"}
        # Posting others' speech is off by default now; start from on.
        window._config.setdefault("vrc_listen", {})["send_to_chatbox"] = True
        saves: list[str] = []
        monkeypatch.setattr(window, "_schedule_config_save", lambda: saves.append("save"))
        monkeypatch.setattr(window, "_sync_settings_window_vrc_listen_state", lambda: None)
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "toggle_mic_chatbox")
        MainWindow._on_vr_dashboard_action(window, "toggle_listen_chatbox")

        assert window._config["translation"]["send_to_chatbox"] is False
        assert window._config["vrc_listen"]["send_to_chatbox"] is False
        assert saves == ["save", "save"]

    def test_the_mute_button_uses_the_mic_mute_toggle(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        muted: list[str] = []
        monkeypatch.setattr(window, "_toggle_mic_mute", lambda: muted.append("mute"))
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "toggle_mute")

        assert muted == ["mute"]

    def test_the_lock_button_locks_the_panel_and_ends_a_drag(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        backend.edit_mode = True
        window._vr_overlay_backend = backend
        ended: list[str] = []
        notified: list[str] = []
        monkeypatch.setattr(window, "_end_vr_edit_mode", lambda **kw: ended.append("end"))
        monkeypatch.setattr(window, "on_vr_overlay_settings_changed", lambda: notified.append("changed"))
        monkeypatch.setattr(window, "_sync_settings_window_vr_overlay_controls", lambda: None)
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "toggle_lock")

        assert window._config["vrc_listen"]["vr_overlay"]["locked"] is True
        assert ended == ["end"]
        assert notified == ["changed"]

        backend.edit_mode = False
        MainWindow._on_vr_dashboard_action(window, "toggle_lock")

        assert window._config["vrc_listen"]["vr_overlay"]["locked"] is False
        assert ended == ["end"]

    def test_a_locked_panel_refuses_edit_mode(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=False, dashboard=True
        )
        window._config["vrc_listen"]["vr_overlay"]["locked"] = True
        window._vr_overlay_backend = backend
        backend.edit_mode = False
        started: list[str] = []
        backend.set_edit_mode = lambda enabled: started.append("start") or True
        messages: list[str] = []
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)
        monkeypatch.setattr(window, "_set_bottom", lambda text, *a, **kw: messages.append(text))

        assert MainWindow._begin_vr_edit_mode(window) is False

        assert started == []
        assert messages == ["vr_panel_locked"]

    def test_the_screenshot_button_reads_the_whole_view(self, monkeypatch):
        window, backend, service, desktop, calls = _window(
            monkeypatch, subtitles=True, screenshot=True, dashboard=True
        )
        runs: list = []
        monkeypatch.setattr(window, "_run_screenshot_translation", lambda region: runs.append(region))
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)

        MainWindow._on_vr_dashboard_action(window, "screenshot")

        assert runs == [None]


class TestEditMode:
    """Interaction is a short, explicit mode; the game gets its controllers back."""

    class _EditBackend(_Backend):
        def __init__(self):
            super().__init__()
            self.edit_mode = False
            self.modes: list[bool] = []

        def set_edit_mode(self, enabled):
            self.edit_mode = bool(enabled)
            self.modes.append(self.edit_mode)
            return self.edit_mode

    def _window(self, monkeypatch, *, subtitles=True):
        window, _backend, service, desktop, calls = _window(
            monkeypatch, subtitles=subtitles, screenshot=False
        )
        backend = self._EditBackend()
        window._vr_overlay_backend = backend
        window._vr_edit_timer = None
        messages: list[str] = []
        monkeypatch.setattr(window, "_set_bottom", lambda text, *a: messages.append(text))
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)
        monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)
        monkeypatch.setattr("src.ui_qt.main_window.QTimer", _FakeTimerClass)
        return window, backend, messages

    def test_begin_reveals_and_arms_the_timeout(self, monkeypatch):
        window, backend, messages = self._window(monkeypatch)

        assert MainWindow._begin_vr_edit_mode(window) is True

        assert backend.revealed == 1
        assert backend.modes == [True]
        assert window._vr_edit_timer.started
        assert messages == ["vr_edit_mode_on"]

    def test_end_hands_the_controllers_back(self, monkeypatch):
        window, backend, messages = self._window(monkeypatch)
        MainWindow._begin_vr_edit_mode(window)

        MainWindow._end_vr_edit_mode(window)

        assert backend.modes == [True, False]
        assert not window._vr_edit_timer.active
        assert messages[-1] == "vr_edit_mode_off"

    def test_ending_hides_a_panel_that_was_only_shown_for_editing(self, monkeypatch):
        window, backend, messages = self._window(monkeypatch, subtitles=False)
        MainWindow._begin_vr_edit_mode(window)

        MainWindow._end_vr_edit_mode(window)

        assert backend.hidden == 1

    def test_a_finished_drag_only_extends_briefly(self, monkeypatch):
        window, backend, messages = self._window(monkeypatch)
        MainWindow._begin_vr_edit_mode(window)

        MainWindow._extend_vr_edit_mode(window, 6)

        assert window._vr_edit_timer.started[-1] == 6000

    def test_no_runtime_means_no_edit_mode(self, monkeypatch):
        window, backend, messages = self._window(monkeypatch)
        backend.available = False

        assert MainWindow._begin_vr_edit_mode(window) is False
        assert backend.modes == []


class _FakeTimerClass:
    """Stands in for QTimer on a bare window: records what was armed."""

    def __init__(self, _parent=None):
        self.started: list[int] = []
        self.active = False
        self._single = False

    def setSingleShot(self, value):
        self._single = bool(value)

    class _Signal:
        def connect(self, _callback):
            return None

    timeout = _Signal()

    def start(self, ms):
        self.started.append(int(ms))
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


class TestRetheme:
    def test_a_theme_change_reaches_both_headset_panels(self, monkeypatch):
        """Desktop windows re-styled on theme change; the VR panels did not."""

        class _Panel:
            def __init__(self):
                self.themes: list[str] = []

            def set_theme(self, theme):
                self.themes.append(theme)

        class _Hand:
            def __init__(self):
                self.panel = _Panel()
                self.visible = True
                self.pushes = 0

            def push(self):
                self.pushes += 1

        window = MainWindow.__new__(MainWindow)
        window._main_theme = "light"
        backend = _Backend()
        backend._panel = _Panel()
        pushes: list[int] = []
        backend._push_panel = lambda: pushes.append(1)
        window._vr_overlay_backend = backend
        hand = _Hand()
        window._vr_hand_panel = hand

        MainWindow._retheme_vr_panels(window)

        assert backend._panel.themes == ["light"]
        assert pushes == [1]
        assert hand.panel.themes == ["light"]
        assert hand.pushes == 1

    def test_retheme_is_safe_with_no_vr_panels(self):
        window = MainWindow.__new__(MainWindow)
        window._main_theme = "dark"

        MainWindow._retheme_vr_panels(window)
