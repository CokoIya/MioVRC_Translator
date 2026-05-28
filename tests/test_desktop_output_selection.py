from src.ui_qt.main_window import MainWindow


def _window_with_output_selection(
    *,
    configured: str = "",
    detected: str | None = None,
    tts_enabled: bool = False,
    tts_output_to_vrchat: bool = False,
    tts_output_device_name: str = "",
):
    window = MainWindow.__new__(MainWindow)
    window._desktop_devices = {
        "Configured Device": 0,
        "VRChat Active Device": 0,
        "TTS MixLine Device": 0,
    }
    window._desktop_capture_config = lambda: {"loopback_device": configured}
    window._tts_config = lambda: {
        "enabled": tts_enabled,
        "output_to_vrchat": tts_output_to_vrchat,
        "output_device_name": tts_output_device_name,
    }
    window._match_desktop_device_name = (
        lambda name: str(name or "").strip()
        if str(name or "").strip() in window._desktop_devices
        else None
    )
    window._detect_vrchat_output_device_name = lambda: detected
    window._auto_detect_listen_device_name = lambda: detected
    return window


def test_configured_output_wins_without_process_probe():
    window = _window_with_output_selection(
        configured="Configured Device",
        detected="VRChat Active Device",
        tts_enabled=True,
        tts_output_to_vrchat=True,
        tts_output_device_name="TTS MixLine Device",
    )

    assert window._desktop_output_device_name() == "Configured Device"
    assert window._listen_uses_auto_output_device() is False


def test_configured_output_is_used_when_vrchat_output_is_unavailable():
    window = _window_with_output_selection(
        configured="Configured Device",
        detected=None,
        tts_enabled=True,
        tts_output_to_vrchat=True,
        tts_output_device_name="TTS MixLine Device",
    )

    assert window._desktop_output_device_name() == "Configured Device"
    assert window._listen_uses_auto_output_device() is False


def test_missing_configured_output_uses_auto_mode_even_with_tts_virtual_output():
    window = _window_with_output_selection(
        configured="Missing Device",
        detected="VRChat Active Device",
        tts_enabled=True,
        tts_output_to_vrchat=True,
        tts_output_device_name="TTS MixLine Device",
    )

    assert window._desktop_output_device_name() == "VRChat Active Device"
    assert window._listen_uses_auto_output_device() is True


def test_missing_configured_output_uses_auto_mode_without_tts_virtual_output():
    window = _window_with_output_selection(
        configured="Missing Device",
        detected="VRChat Active Device",
    )

    assert window._desktop_output_device_name() == "VRChat Active Device"
    assert window._listen_uses_auto_output_device() is True


def test_auto_detect_uses_default_output_without_process_probe(monkeypatch):
    from src.ui_qt import main_window

    window = MainWindow.__new__(MainWindow)
    window._desktop_devices = {"Default Device": 0}
    window._desktop_capture_config = lambda: {}
    window._tts_config = lambda: {
        "enabled": False,
        "output_to_vrchat": False,
        "output_device_name": "",
    }
    window._listen_process_output_probe_enabled = lambda: False
    window._detect_vrchat_output_device_name = lambda: (_ for _ in ()).throw(
        AssertionError("process probe should not run by default")
    )
    window._match_desktop_device_name = lambda name: "Default Device" if name == "Default Device" else None
    monkeypatch.setattr(main_window, "default_output_device_name", lambda: "Default Device")

    assert window._auto_detect_listen_device_name() == "Default Device"


def test_auto_detect_avoids_tts_mixline_default_output(monkeypatch):
    from src.ui_qt import main_window

    window = MainWindow.__new__(MainWindow)
    window._desktop_devices = {
        "Speakers (MIXLINE)": 0,
        "Headphones (Realtek(R) Audio)": 1,
        "Pico Streaming Speaker": 2,
    }
    window._desktop_capture_config = lambda: {}
    window._tts_config = lambda: {
        "enabled": True,
        "output_to_vrchat": True,
        "output_device_name": "Speakers (MIXLINE)",
    }
    window._listen_process_output_probe_enabled = lambda: False
    window._detect_vrchat_output_device_name = lambda: (_ for _ in ()).throw(
        AssertionError("process probe should not run by default")
    )
    monkeypatch.setattr(main_window, "default_output_device_name", lambda: "Speakers (MIXLINE)")

    assert window._auto_detect_listen_device_name() == "Headphones (Realtek(R) Audio)"


def test_auto_detect_can_use_process_probe_when_enabled(monkeypatch):
    from src.ui_qt import main_window

    window = MainWindow.__new__(MainWindow)
    window._desktop_devices = {"VRChat Active Device": 0}
    window._desktop_capture_config = lambda: {"follow_process_output": True}
    window._tts_config = lambda: {
        "enabled": False,
        "output_to_vrchat": False,
        "output_device_name": "",
    }
    window._listen_process_output_probe_enabled = lambda: True
    window._detect_vrchat_output_device_name = lambda: "VRChat Active Device"
    window._match_desktop_device_name = lambda name: str(name or "").strip() or None
    monkeypatch.setattr(
        main_window,
        "default_output_device_name",
        lambda: (_ for _ in ()).throw(AssertionError("default output should not be needed")),
    )

    assert window._auto_detect_listen_device_name() == "VRChat Active Device"
