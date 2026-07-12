from __future__ import annotations

from types import SimpleNamespace

import src.audio.device_inventory as inventory


class _FakeSoundDevice:
    def __init__(self, devices, hostapis, default=(-1, -1)) -> None:
        self._devices = devices
        self._hostapis = hostapis
        self.default = SimpleNamespace(device=list(default))

    def query_devices(self, index=None):
        if index is None:
            return self._devices
        return self._devices[int(index)]

    def query_hostapis(self):
        return self._hostapis


def test_invalid_portaudio_default_falls_back_to_wasapi_host_default():
    devices = [
        {"name": "Studio Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Studio Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Studio Mic", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Studio Speakers", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
    ]
    hostapis = [
        {"name": "MME", "default_input_device": 0, "default_output_device": 1},
        {"name": "Windows WASAPI", "default_input_device": 2, "default_output_device": 3},
    ]
    fake = _FakeSoundDevice(devices, hostapis)

    snapshot = inventory.get_device_inventory(
        force_refresh=True,
        allow_cached=False,
        sounddevice_module=fake,
    )

    assert snapshot.default_input_index == 2
    assert snapshot.default_output_index == 3
    assert [item.index for item in snapshot.inputs] == [2]
    assert [item.index for item in snapshot.outputs] == [3]


def test_inventory_deduplicates_backends_and_filters_pseudo_endpoints():
    devices = [
        {"name": "Microsoft Sound Mapper - Output", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Speakers (USB DAC)", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Primary Sound Driver", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Speakers (USB DAC)", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Speakers (USB DAC)", "hostapi": 2, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Speakers ()", "hostapi": 3, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Output ()", "hostapi": 3, "max_input_channels": 0, "max_output_channels": 2},
    ]
    hostapis = [
        {"name": "MME", "default_input_device": -1, "default_output_device": 1},
        {"name": "Windows DirectSound", "default_input_device": -1, "default_output_device": 2},
        {"name": "Windows WASAPI", "default_input_device": -1, "default_output_device": 4},
        {"name": "Windows WDM-KS", "default_input_device": -1, "default_output_device": 5},
    ]
    fake = _FakeSoundDevice(devices, hostapis, default=(-1, 1))

    snapshot = inventory.get_device_inventory(
        force_refresh=True,
        allow_cached=False,
        sounddevice_module=fake,
    )

    assert [(item.index, item.hostapi) for item in snapshot.outputs] == [
        (4, "Windows WASAPI")
    ]
    assert snapshot.default_output_index == 4
    assert snapshot.diagnostics["filtered_count"] == 4


def test_transient_empty_scan_returns_last_good_inventory(monkeypatch):
    inventory._reset_device_inventory_cache_for_tests()
    state = {"empty": False}
    devices = [
        {"name": "Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    ]

    def query_devices(index=None):
        current = [] if state["empty"] else devices
        if index is None:
            return current
        return current[int(index)]

    monkeypatch.setattr(inventory.sd, "query_devices", query_devices)
    monkeypatch.setattr(
        inventory.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI", "default_input_device": 0, "default_output_device": 1}],
    )
    monkeypatch.setattr(inventory.sd.default, "device", [0, 1])
    monkeypatch.setattr(inventory, "_windows_endpoint_diagnostics", lambda: ([], []))

    first = inventory.get_device_inventory(force_refresh=True)
    state["empty"] = True
    second = inventory.get_device_inventory(force_refresh=True)

    assert len(first.outputs) == 1
    assert second.from_cache is True
    assert [item.name for item in second.outputs] == ["Speakers"]
    assert "empty device table" in str(second.diagnostics["cache_reason"])


def test_inactive_coreaudio_endpoint_is_not_selectable(monkeypatch):
    inventory._reset_device_inventory_cache_for_tests()
    monkeypatch.setattr(
        inventory.sd,
        "query_devices",
        lambda: [
            {
                "name": "Disconnected Headset",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            }
        ],
    )
    monkeypatch.setattr(
        inventory.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI", "default_input_device": -1, "default_output_device": 0}],
    )
    monkeypatch.setattr(inventory.sd.default, "device", [-1, -1])
    monkeypatch.setattr(
        inventory,
        "_windows_endpoint_diagnostics",
        lambda: (
            [
                {
                    "id": "endpoint-id",
                    "name": "Disconnected Headset",
                    "flow": "render",
                    "active": False,
                    "state_name": "unplugged",
                    "is_default": False,
                }
            ],
            [],
        ),
    )

    snapshot = inventory.get_device_inventory(force_refresh=True, allow_cached=False)

    assert snapshot.outputs == ()
    assert snapshot.diagnostics["windows_inactive_endpoint_count"] == 1


def test_coreaudio_multimedia_default_overrides_stale_portaudio_default(monkeypatch):
    inventory._reset_device_inventory_cache_for_tests()
    monkeypatch.setattr(
        inventory.sd,
        "query_devices",
        lambda: [
            {"name": "Old Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Current Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
        ],
    )
    monkeypatch.setattr(
        inventory.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI", "default_input_device": -1, "default_output_device": 1}],
    )
    monkeypatch.setattr(inventory.sd.default, "device", [-1, 0])
    monkeypatch.setattr(
        inventory,
        "_windows_endpoint_diagnostics",
        lambda: (
            [
                {
                    "id": "current",
                    "name": "Current Speakers",
                    "flow": "render",
                    "active": True,
                    "is_default": True,
                    "default_roles": ["multimedia", "console"],
                }
            ],
            [],
        ),
    )

    snapshot = inventory.get_device_inventory(force_refresh=True, allow_cached=False)

    assert snapshot.default_output_index == 1
    assert snapshot.default_output.name == "Current Speakers"


def test_coreaudio_capture_default_matches_stable_hardware_identity(monkeypatch):
    inventory._reset_device_inventory_cache_for_tests()
    monkeypatch.setattr(
        inventory.sd,
        "query_devices",
        lambda: [
            {
                "name": "Microphone (PicoStreamingMicrophone)",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "name": "??? (Razer Seiren V2 X)",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
        ],
    )
    monkeypatch.setattr(
        inventory.sd,
        "query_hostapis",
        lambda: [
            {
                "name": "Windows WASAPI",
                "default_input_device": 0,
                "default_output_device": -1,
            }
        ],
    )
    monkeypatch.setattr(inventory.sd.default, "device", [0, -1])
    monkeypatch.setattr(
        inventory,
        "_windows_endpoint_diagnostics",
        lambda: (
            [
                {
                    "id": "razer",
                    "name": "麦克风 (Razer Seiren V2 X)",
                    "flow": "capture",
                    "active": True,
                    "is_default": True,
                    "default_roles": ["console", "multimedia"],
                }
            ],
            [],
        ),
    )

    snapshot = inventory.get_device_inventory(force_refresh=True, allow_cached=False)

    assert snapshot.default_input_index == 1
    assert snapshot.default_input.name == "??? (Razer Seiren V2 X)"


def test_unique_external_capture_session_selects_microphone_in_use(monkeypatch):
    inventory._reset_device_inventory_cache_for_tests()
    monkeypatch.setattr(
        inventory.sd,
        "query_devices",
        lambda: [
            {
                "name": "Microphone (PicoStreamingMicrophone)",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "name": "Microphone (Razer Seiren V2 X)",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
        ],
    )
    monkeypatch.setattr(
        inventory.sd,
        "query_hostapis",
        lambda: [
            {
                "name": "Windows WASAPI",
                "default_input_device": 0,
                "default_output_device": -1,
            }
        ],
    )
    monkeypatch.setattr(inventory.sd.default, "device", [0, -1])
    monkeypatch.setattr(
        inventory,
        "_windows_endpoint_diagnostics",
        lambda: (
            [
                {
                    "id": "pico",
                    "name": "Microphone (PicoStreamingMicrophone)",
                    "flow": "capture",
                    "active": True,
                    "is_default": True,
                    "default_roles": ["console", "multimedia"],
                    "has_active_session": False,
                    "has_external_active_session": False,
                },
                {
                    "id": "razer",
                    "name": "Microphone (Razer Seiren V2 X)",
                    "flow": "capture",
                    "active": True,
                    "is_default": False,
                    "default_roles": [],
                    "has_active_session": True,
                    "has_external_active_session": True,
                    "active_session_process_ids": [4242],
                },
            ],
            [],
        ),
    )

    snapshot = inventory.get_device_inventory(force_refresh=True, allow_cached=False)

    assert snapshot.default_input_index == 1
    assert snapshot.default_input.name == "Microphone (Razer Seiren V2 X)"
    assert snapshot.diagnostics["windows_active_capture_sessions"] == [
        {
            "name": "Microphone (Razer Seiren V2 X)",
            "is_default": False,
            "default_roles": [],
            "has_external_active_session": True,
            "active_session_process_ids": [4242],
        }
    ]


def test_capture_hardware_identity_match_stays_ambiguous_when_not_unique():
    assert inventory.unique_input_device_name_match(
        "Microphone (USB Audio Device)",
        [
            "Front Microphone (USB Audio Device)",
            "Rear Microphone (USB Audio Device)",
        ],
    ) is None


def test_active_mixline_capture_does_not_override_real_default_microphone():
    real_microphone = inventory.AudioEndpoint(
        index=1,
        name="Microphone (Razer Seiren V2 X)",
        hostapi="Windows WASAPI",
        hostapi_index=0,
        max_input_channels=1,
        max_output_channels=0,
        default_samplerate=48000.0,
    )
    mixline_microphone = inventory.AudioEndpoint(
        index=2,
        name="Microphone (MIXLINE Stream)",
        hostapi="Windows WASAPI",
        hostapi_index=0,
        max_input_channels=1,
        max_output_channels=0,
        default_samplerate=48000.0,
    )

    resolved = inventory._resolve_default_endpoint(
        flow="capture",
        selected=[real_microphone, mixline_microphone],
        raw_by_index={1: real_microphone, 2: mixline_microphone},
        raw_default_index=1,
        hostapis=[],
        windows_endpoints=[
            {
                "name": real_microphone.name,
                "flow": "capture",
                "active": True,
                "is_default": True,
                "default_roles": ["console", "multimedia"],
                "has_external_active_session": False,
            },
            {
                "name": mixline_microphone.name,
                "flow": "capture",
                "active": True,
                "is_default": False,
                "default_roles": [],
                "has_external_active_session": True,
            },
        ],
    )

    assert resolved == real_microphone
