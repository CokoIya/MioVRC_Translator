"""Fixes for what players ran into (interaction review, 2026-09-23).

Muted must not read as "listening"; the avatar's "translating" flag must
actually rise; the avatar mic switch mutes instead of stopping everything;
the wizard's VR mode switches on the headset side; VRChat MuteSelf and
the avatar's target language parameter.
"""

from __future__ import annotations

import threading
import types

import pytest

from src.ui_qt.main_window import MainWindow


class _Sender:
    def __init__(self):
        self.bools: list = []
        self.ints: list = []

    def send_avatar_bool(self, name, value, *, force=False):
        self.bools.append((name, value))
        return True

    def send_avatar_int(self, name, value, *, force=False):
        self.ints.append((name, value))
        return True


def _window(monkeypatch, **config):
    window = MainWindow.__new__(MainWindow)
    window._config = {"osc": {"avatar_sync": {"enabled": True}}, **config}
    window._translation_state_lock = threading.Lock()
    window._active_translation_jobs = 0
    sender = _Sender()
    monkeypatch.setattr(window, "_ensure_sender", lambda: sender)
    monkeypatch.setattr(window, "_call_in_ui", lambda fn: fn())
    return window, sender


class TestTranslatingFlag:
    def test_the_flag_rises_with_the_first_job_and_falls_with_the_last(self, monkeypatch):
        window, sender = _window(monkeypatch)

        MainWindow._note_translation_job(window, 1)
        MainWindow._note_translation_job(window, 1)
        MainWindow._note_translation_job(window, -1)
        MainWindow._note_translation_job(window, -1)

        assert sender.bools == [("MioTranslating", True), ("MioTranslating", False)]

    def test_the_count_never_goes_negative(self, monkeypatch):
        window, _sender = _window(monkeypatch)

        MainWindow._note_translation_job(window, -1)

        assert window._active_translation_jobs == 0

    def test_the_scheduler_stage_is_counted_even_when_it_fails(self, monkeypatch):
        window, sender = _window(monkeypatch)

        def boom(*_args):
            assert window._active_translation_jobs == 1
            raise RuntimeError("provider down")

        monkeypatch.setattr(window, "_run_scheduler_translation_stage", boom)
        with pytest.raises(RuntimeError):
            MainWindow._scheduler_translation_stage(window, object(), "text", object(), threading.Event())

        assert window._active_translation_jobs == 0
        assert sender.bools[-1] == ("MioTranslating", False)


class TestStatus:
    def _status_window(self, monkeypatch, *, running, muted):
        window = MainWindow.__new__(MainWindow)
        window._status_label = object()
        window._status_key = "status_running"
        window._running = running
        window._mic_muted = muted
        window._translating = False
        shown: list = []
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)
        monkeypatch.setattr(window, "_set_status", lambda text, color, key=None: shown.append((key, color)))
        monkeypatch.setattr(window, "_set_chatbox_typing", lambda typing: None)
        return window, shown

    def test_muted_is_not_shown_as_listening(self, monkeypatch):
        window, shown = self._status_window(monkeypatch, running=True, muted=True)

        MainWindow._restore_runtime_status(window)

        assert shown == [("status_muted", "warning")]

    def test_unmuted_listening(self, monkeypatch):
        window, shown = self._status_window(monkeypatch, running=True, muted=False)

        MainWindow._restore_runtime_status(window)

        assert shown == [("status_running", "accent")]


class TestAvatarControls:
    def _control_window(self, monkeypatch, *, running=True, muted=False):
        window = MainWindow.__new__(MainWindow)
        window._config = {"osc": {"allow_avatar_control": True}}
        window._running = running
        window._mic_muted = muted
        calls: list = []
        monkeypatch.setattr(window, "_do_start", lambda: calls.append("start"))
        monkeypatch.setattr(window, "_do_stop", lambda: calls.append("stop"))

        def set_muted(value, **_kwargs):
            calls.append(("muted", value))
            window._mic_muted = value

        monkeypatch.setattr(window, "_set_mic_muted", set_muted)
        return window, calls

    def test_the_mic_switch_mutes_instead_of_stopping_everything(self, monkeypatch):
        window, calls = self._control_window(monkeypatch)

        MainWindow._handle_osc_avatar_parameter(window, "MioToggleMic", False)

        assert calls == [("muted", True)]

    def test_the_mic_switch_on_starts_and_unmutes(self, monkeypatch):
        window, calls = self._control_window(monkeypatch, running=False, muted=True)

        MainWindow._handle_osc_avatar_parameter(window, "MioToggleMic", True)

        assert calls == ["start", ("muted", False)]

    def test_the_frame_switch_follows_the_avatar(self, monkeypatch):
        window, _calls = self._control_window(monkeypatch)
        toggled: list = []
        monkeypatch.setattr(window, "_frame_gesture_enabled", lambda: False)
        monkeypatch.setattr(window, "_set_frame_gesture_enabled", toggled.append)

        MainWindow._handle_osc_avatar_parameter(window, "MioFrameGesture", True)

        assert toggled == [True]

    def test_a_target_language_of_zero_leaves_it_alone(self, monkeypatch):
        window, _calls = self._control_window(monkeypatch)
        picked: list = []
        monkeypatch.setattr(window, "_set_target_language_from_avatar", picked.append)

        MainWindow._handle_osc_avatar_parameter(window, "MioTargetLanguage", 0)

        assert picked == [0]
        window._current_tgt_lang = "en"
        window._target_lang_codes = {}
        MainWindow._set_target_language_from_avatar(window, 0)  # nothing to do, no error


class TestWizard:
    def test_the_vr_mode_switches_on_the_board_and_the_hand_frame(self, monkeypatch):
        window = MainWindow.__new__(MainWindow)
        window._config = {"translation": {}, "vrc_listen": {}, "tts": {}, "ui": {}}
        for name in (
            "_set_app_mode",
            "_sync_avatar_overlay_state",
            "_sync_tts_enabled_from_config",
            "_refresh_mode_buttons",
            "_refresh_desktop_capture_button",
            "_refresh_listen_overlay_button",
            "_sync_settings_window_vrc_listen_state",
            "_refresh_overlay_backend",
            "_schedule_config_save",
            "_set_bottom",
            "_desktop_overlay_surface",
        ):
            monkeypatch.setattr(window, name, lambda *a, **k: None)
        monkeypatch.setattr(window, "_t", lambda key, **kw: key)
        monkeypatch.setattr(
            window,
            "_ensure_overlay_service",
            lambda **kw: types.SimpleNamespace(set_enabled=lambda *a, **k: None),
        )
        monkeypatch.setattr(window, "_overlay_output_enabled", lambda: True)

        MainWindow._apply_mode_wizard_result(window, "overlay")

        listen = window._config["vrc_listen"]
        assert listen["vr_overlay"]["enabled"] is True
        assert listen["screenshot_translation"]["enabled"] is True
        assert listen["screenshot_translation"]["frame_gesture"] is True
        # Other players' speech is not posted under the player's name.
        assert listen["send_to_chatbox"] is False
