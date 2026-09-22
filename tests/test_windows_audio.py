from __future__ import annotations

import os
import sys
import threading

import pytest

import src.audio.windows_audio as windows_audio

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Core Audio and Toolhelp helpers only exist on Windows",
)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (0x1, "active"),
        (0x2, "disabled"),
        (0x4, "not_present"),
        (0x8, "unplugged"),
        (None, "unknown"),
        (0x1 | 0x8, "active|unplugged"),
        (0x2 | 0x4, "disabled|not_present"),
        (0x10, "unknown_0x00000010"),
    ],
)
def test_device_state_name(state, expected):
    assert windows_audio._device_state_name(state) == expected


def test_list_process_ids_matches_image_names_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        windows_audio,
        "_process_image_names",
        lambda: {1: "vrchat.exe", 2: "steam.exe", 3: "other.exe"},
    )

    assert windows_audio._list_process_ids(["VRChat.EXE", " ", " steam.exe "]) == {1, 2}


def test_list_process_ids_skips_the_snapshot_without_names(monkeypatch):
    def fail():
        raise AssertionError("process snapshot should not be taken")

    monkeypatch.setattr(windows_audio, "_process_image_names", fail)

    assert windows_audio._list_process_ids(["", "  "]) == set()


def test_list_process_ids_finds_the_current_process():
    image_name = windows_audio._process_image_names()[os.getpid()]

    assert image_name.endswith(".exe")
    assert os.getpid() in windows_audio._list_process_ids([image_name.upper()])


def test_device_matches_for_process_ids_enumerates_without_raising():
    results = []

    # A fresh thread gets its own multithreaded COM apartment, independent of
    # whatever the Qt test thread already initialised.
    def enumerate_sessions():
        results.append(windows_audio._device_matches_for_process_ids({os.getpid()}))

    worker = threading.Thread(target=enumerate_sessions)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(results) == 1
    assert all(
        isinstance(name, str) and isinstance(is_active, bool)
        for name, is_active in results[0]
    )


def test_device_matches_for_no_process_ids_is_empty():
    assert windows_audio._device_matches_for_process_ids(set()) == []


def _patch_matches(monkeypatch, process_ids, matches):
    monkeypatch.setattr(windows_audio, "_list_process_ids", lambda _names: set(process_ids))
    monkeypatch.setattr(
        windows_audio,
        "_device_matches_for_process_ids",
        lambda _ids: list(matches),
    )


def test_detect_output_device_prefers_an_active_session(monkeypatch):
    _patch_matches(
        monkeypatch,
        {7},
        [("Speakers", False), ("  ", True), ("Headset", True)],
    )

    assert windows_audio.detect_process_output_device_name(["VRChat.exe"]) == "Headset"


def test_detect_output_device_falls_back_to_an_inactive_session(monkeypatch):
    _patch_matches(monkeypatch, {7}, [("", False), (" Speakers ", False)])

    assert windows_audio.detect_process_output_device_name(["VRChat.exe"]) == "Speakers"


@pytest.mark.parametrize("matches", [[], [("", True), ("   ", False)]])
def test_detect_output_device_without_named_sessions_is_none(monkeypatch, matches):
    _patch_matches(monkeypatch, {7}, matches)

    assert windows_audio.detect_process_output_device_name(["VRChat.exe"]) is None


def test_is_process_running(monkeypatch):
    _patch_matches(monkeypatch, {7}, [])
    assert windows_audio.is_process_running(["VRChat.exe"]) is True

    _patch_matches(monkeypatch, set(), [])
    assert windows_audio.is_process_running(["VRChat.exe"]) is False


def test_inspect_process_output_state_builds_a_snapshot(monkeypatch):
    _patch_matches(
        monkeypatch,
        {42, 7},
        [("Speakers", False), ("  ", True), ("Headset", True)],
    )
    monkeypatch.setattr(
        windows_audio,
        "list_audio_endpoints",
        lambda include_inactive: [
            {
                "name": "Speakers",
                "flow": "render",
                "active": True,
                "is_default": True,
                "default_roles": ["console", "multimedia"],
            },
            {"name": "Old Headset", "flow": "render", "active": False},
        ],
    )
    snapshot = windows_audio.inspect_process_output_state(
        [" VRChat.exe ", ""],
        default_output_device="Speakers",
    )

    assert snapshot == {
        "process_names": ["VRChat.exe"],
        "process_ids": [7, 42],
        "is_running": True,
        "default_output_device": "Speakers",
        "active_device": "Headset",
        "has_active_audio_session": True,
        "matches": [
            {"device_name": "Speakers", "is_active": False},
            {"device_name": "Headset", "is_active": True},
        ],
        "coreaudio": {
            "endpoint_count": 2,
            "active_count": 1,
            "inactive_count": 1,
            "defaults": [
                {
                    "name": "Speakers",
                    "flow": "render",
                    "roles": ["console", "multimedia"],
                }
            ],
        },
    }


def test_inspect_process_output_state_uses_inactive_device_when_nothing_plays(
    monkeypatch,
):
    _patch_matches(monkeypatch, {7}, [("", False), ("Speakers", False)])
    monkeypatch.setattr(windows_audio, "list_audio_endpoints", lambda include_inactive: [])

    snapshot = windows_audio.inspect_process_output_state(["VRChat.exe"])

    assert snapshot["active_device"] == "Speakers"
    assert snapshot["has_active_audio_session"] is False
    assert snapshot["default_output_device"] is None
