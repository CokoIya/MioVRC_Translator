"""Saving settings restarts listening only when the pipeline's inputs changed."""

from __future__ import annotations

import copy
import types

import pytest

from src.ui_qt import main_window as main_window_module
from src.ui_qt.main_window import MainWindow, pipeline_config_fingerprint

BASE = {
    "app_mode": "translation",
    "asr": {"engine": "sensevoice"},
    "audio": {"push_to_talk": False, "vad_min_rms": 0.012},
    "translation": {"backend": "google_web", "send_to_chatbox": True},
    "tts": {"enabled": False, "engine": "edge"},
    "ui": {"language": "zh-CN", "theme": "dark"},
    "hotkeys": {"mic_mute": "ctrl+m"},
    "osc": {"sync_mute_self": True},
    "vrc_listen": {
        "enabled": True,
        "vr_overlay": {"enabled": False},
        "screenshot_translation": {"enabled": False},
        "send_to_chatbox": False,
    },
}


def _changed(path, value):
    config = copy.deepcopy(BASE)
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return config


@pytest.mark.parametrize(
    "path, value",
    [
        (("ui", "theme"), "light"),
        (("hotkeys", "mic_mute"), "ctrl+k"),
        (("vrc_listen", "vr_overlay", "enabled"), True),
        (("vrc_listen", "screenshot_translation", "enabled"), True),
        (("audio", "push_to_talk"), True),
        (("translation", "send_to_chatbox"), False),
        (("tts", "enabled"), True),
        (("osc", "sync_mute_self"), False),
    ],
)
def test_live_settings_do_not_touch_the_pipeline(path, value):
    assert pipeline_config_fingerprint(_changed(path, value)) == pipeline_config_fingerprint(BASE)


@pytest.mark.parametrize(
    "path, value",
    [
        (("translation", "backend"), "deepl"),
        (("asr", "engine"), "qwen3_asr"),
        (("audio", "vad_min_rms"), 0.02),
        (("tts", "engine"), "qwen_vc"),
        (("vrc_listen", "enabled"), False),
        (("ui", "language"), "ja"),
        (("app_mode",), "simultaneous"),
    ],
)
def test_pipeline_settings_do(path, value):
    assert pipeline_config_fingerprint(_changed(path, value)) != pipeline_config_fingerprint(BASE)


_CALLS = (
    "_apply_osc_listener_config",
    "_clear_cached_translator",
    "_close_asr_providers",
    "_close_osc_sender",
    "_do_stop",
    "_load_devices_async",
    "_refresh_open_window_languages",
    "_refresh_static_texts",
    "_register_hotkeys",
    "_reload_theme_style",
    "_reset_translation_failure_backoff",
    "_reset_tts_manager",
    "_resolve_virtual_output_async",
    "_restart_background_provider_initialization",
    "_retheme_vr_panels",
    "_schedule_pipeline_start_retry",
    "_schedule_settings_preload",
    "_stop_hotkeys",
    "_sync_settings_window_vrc_listen_state",
    "_sync_tts_enabled_from_config",
)


@pytest.fixture
def window(monkeypatch):
    win = MainWindow.__new__(MainWindow)
    win._config = copy.deepcopy(BASE)
    win._running = True
    win._startup_thread = None
    win._ui_lang = "zh-CN"
    win._current_tgt_lang = "ja"
    win._current_tgt_lang_2 = "en"
    win._current_tgt_lang_3 = ""
    win._text_input_window = None
    win._floating_window = None
    win._overlay_service = None
    calls: list = []
    for name in _CALLS:
        monkeypatch.setattr(win, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr(win, "_t", lambda key, **kw: key)
    monkeypatch.setattr(win, "_set_bottom", lambda text, *a: calls.append(("bottom", text)))
    monkeypatch.setattr(win, "centralWidget", lambda: None)
    monkeypatch.setattr(win, "_background_image_path", lambda: "")
    monkeypatch.setattr(main_window_module, "apply_application_font", lambda *a, **k: None)
    monkeypatch.setattr(
        main_window_module,
        "ModeManager",
        lambda config: types.SimpleNamespace(apply_current_mode=lambda: types.SimpleNamespace(changed=False)),
    )
    MainWindow._remember_pipeline_config(win)
    win._calls = calls
    return win


def test_a_theme_change_keeps_listening(window):
    window._config["ui"]["theme"] = "light"

    MainWindow._on_config_saved(window)

    assert "_do_stop" not in window._calls
    assert "_schedule_pipeline_start_retry" not in window._calls
    assert "_clear_cached_translator" not in window._calls
    assert "_close_osc_sender" not in window._calls
    assert ("bottom", "settings_saved") in window._calls


def test_a_new_translation_service_restarts_listening(window):
    window._config["translation"]["backend"] = "deepl"

    MainWindow._on_config_saved(window)

    assert "_do_stop" in window._calls
    assert "_clear_cached_translator" in window._calls
    assert "_schedule_pipeline_start_retry" in window._calls


def test_an_osc_change_reopens_only_the_sender(window):
    window._config["osc"]["sync_mute_self"] = False

    MainWindow._on_config_saved(window)

    assert "_close_osc_sender" in window._calls
    assert "_do_stop" not in window._calls


def test_while_stopped_everything_is_rebuilt_as_before(window):
    window._running = False

    MainWindow._on_config_saved(window)

    assert "_do_stop" not in window._calls
    assert "_clear_cached_translator" in window._calls
    assert "_restart_background_provider_initialization" in window._calls
    assert "_schedule_pipeline_start_retry" not in window._calls


def test_without_a_snapshot_a_running_pipeline_is_restarted(window):
    window._pipeline_config_snapshot = None

    MainWindow._on_config_saved(window)

    assert "_do_stop" in window._calls
