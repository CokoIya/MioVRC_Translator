from PySide6.QtWidgets import QApplication

from src.core.osc_service import OscService
from src.ui_qt.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_osc_service_emits_avatar_parameter_and_mute_self():
    _app()
    service = OscService()
    params = []
    muted = []
    service.avatar_parameter_received.connect(lambda name, value: params.append((name, value)))
    service.mute_self_changed.connect(lambda value: muted.append(value))

    service._process_avatar_parameter("/avatar/parameters/MuteSelf", (1,))

    assert params == [("MuteSelf", 1)]
    assert muted == [True]


def test_osc_service_ignores_non_avatar_and_bad_mute_values():
    _app()
    service = OscService()
    params = []
    muted = []
    service.avatar_parameter_received.connect(lambda name, value: params.append((name, value)))
    service.mute_self_changed.connect(lambda value: muted.append(value))

    service._process_avatar_parameter("/chatbox/input", (True,))
    service._process_avatar_parameter("/avatar/parameters/MuteSelf", ("maybe",))

    assert params == [("MuteSelf", "maybe")]
    assert muted == []


def test_osc_service_debounces_bursts_but_allows_later_state_reassertion(monkeypatch):
    _app()
    service = OscService()
    muted = []
    service.mute_self_changed.connect(lambda value: muted.append(value))
    timestamps = iter((10.0, 10.05, 10.06, 10.5))
    monkeypatch.setattr(
        "src.core.osc_service.time.monotonic",
        lambda: next(timestamps),
    )

    service._process_avatar_parameter("/avatar/parameters/MuteSelf", (True,))
    service._process_avatar_parameter("/avatar/parameters/muteself", (1,))
    service._process_avatar_parameter("/avatar/parameters/MuteSelf", (False,))
    service._process_avatar_parameter("/avatar/parameters/MuteSelf", (0,))

    assert muted == [True, False, False]


def test_main_window_mute_sync_starts_listener_even_when_general_control_is_off():
    window = MainWindow.__new__(MainWindow)
    calls = []

    class _Service:
        def start_listener(self, **kwargs):
            calls.append(("start", kwargs))

        def stop_listener(self):
            calls.append(("stop", None))

    window._destroying = False
    window._config = {
        "osc": {
            "listener_enabled": False,
            "sync_mute_self": True,
            "allow_avatar_control": False,
            "receive_host": "127.0.0.1",
            "receive_port": 9001,
        }
    }
    window._ensure_osc_service = lambda: _Service()
    window._set_bottom = lambda *args, **kwargs: None

    window._apply_osc_listener_config()

    assert calls == [
        (
            "start",
            {
                "host": "127.0.0.1",
                "port": 9001,
                "sync_mute_self": True,
            },
        )
    ]


def test_main_window_stops_listener_when_all_inbound_features_are_disabled():
    window = MainWindow.__new__(MainWindow)
    calls = []

    class _Service:
        def start_listener(self, **_kwargs):
            calls.append("start")

        def stop_listener(self):
            calls.append("stop")

    window._destroying = False
    window._config = {
        "osc": {
            "listener_enabled": False,
            "sync_mute_self": False,
            "allow_avatar_control": False,
        }
    }
    window._ensure_osc_service = lambda: _Service()

    window._apply_osc_listener_config()

    assert calls == ["stop"]


def test_main_window_avatar_toggle_controls_basic_switches():
    window = MainWindow.__new__(MainWindow)
    calls = []
    window._config = {
        "osc": {
            "allow_avatar_control": True,
            "control_prefix": "Mio",
            "control_params": {
                "mic": "MioToggleMic",
                "listen": "MioToggleListen",
                "tts": "MioToggleTts",
                "overlay": "MioToggleOverlay",
            },
        },
        "tts": {"enabled": False},
    }
    window._running = False
    window._desktop_capture_enabled = False
    window._listen_overlay_enabled = False
    window._do_start = lambda: calls.append("start")
    window._do_stop = lambda: calls.append("stop")
    window._set_desktop_capture_enabled = lambda enabled, *, persist: calls.append(("listen", enabled, persist))
    window._set_listen_overlay_enabled = lambda enabled, *, persist: calls.append(("overlay", enabled, persist))
    window._sync_tts_enabled_from_config = lambda: window._config["tts"].get("enabled", False)
    window._reset_tts_manager = lambda: calls.append("reset_tts")
    window._schedule_config_save = lambda: calls.append("save")
    window._set_bottom = lambda *args, **kwargs: None

    window._handle_osc_avatar_parameter("MioToggleMic", True)
    window._handle_osc_avatar_parameter("MioToggleListen", True)
    window._handle_osc_avatar_parameter("MioToggleTts", True)
    window._handle_osc_avatar_parameter("MioToggleOverlay", True)

    assert "start" in calls
    assert ("listen", True, True) in calls
    assert ("overlay", True, True) in calls
    assert window._config["tts"]["enabled"] is True
    assert "save" in calls


def test_main_window_avatar_toggle_respects_disable_flag():
    window = MainWindow.__new__(MainWindow)
    window._config = {"osc": {"allow_avatar_control": False}}
    window._running = False
    window._do_start = lambda: (_ for _ in ()).throw(AssertionError("should not start"))

    window._handle_osc_avatar_parameter("MioToggleMic", True)
