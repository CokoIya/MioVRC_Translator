from __future__ import annotations

import pytest

from src.core import listen_output_selection as selection


def _exact_match(left, right) -> bool:
    return bool(left) and bool(right) and left == right


def _tts(enabled=True, to_vrchat=True, device="Speakers (MIXLINE)"):
    return {"enabled": enabled, "output_to_vrchat": to_vrchat, "output_device_name": device}


def test_normalize_device_name_folds_width_case_and_spacing():
    assert selection.normalize_device_name("  Ｈｅａｄｐｈｏｎｅｓ   (USB  DAC) ") == (
        "headphones (usb dac)"
    )
    assert selection.normalize_device_name(None) == ""


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, ["VRChat.exe"]),
        ({"target_process_names": "Game.exe"}, ["Game.exe"]),
        ({"target_process_names": [" Game.exe ", "", "Game.exe", "Unity.exe"]}, ["Game.exe", "Unity.exe"]),
        ({"target_process_names": ["", "  ", None]}, ["VRChat.exe"]),
        ({"target_process_names": 42}, ["VRChat.exe"]),
    ],
)
def test_target_process_names(config, expected):
    assert selection.target_process_names(config) == expected


def test_avoids_the_device_tts_plays_into():
    assert selection.should_avoid_output_device(
        "Speakers (MIXLINE)",
        _tts(),
        match_device=lambda name: name,
        names_match=_exact_match,
    )


def test_avoids_any_mixline_device_while_tts_goes_to_vrchat():
    assert selection.should_avoid_output_device(
        "Mix Line 2",
        _tts(device=""),
        match_device=lambda name: None,
        names_match=_exact_match,
    )


@pytest.mark.parametrize(
    "tts_config",
    [_tts(enabled=False), _tts(to_vrchat=False), {}],
)
def test_nothing_is_avoided_unless_tts_plays_into_vrchat(tts_config):
    assert not selection.should_avoid_output_device(
        "Speakers (MIXLINE)",
        tts_config,
        match_device=lambda name: name,
        names_match=_exact_match,
    )


def test_blank_device_is_never_avoided():
    def fail(_name):
        raise AssertionError("no lookup for a blank name")

    assert not selection.should_avoid_output_device(
        "  ", _tts(), match_device=fail, names_match=_exact_match
    )


def test_fallback_prefers_a_physical_headphone_over_virtual_drivers():
    devices = [
        "Speakers (MIXLINE)",
        "CABLE Input (VB-Audio Virtual Cable)",
        "Speakers 03 (ASIOVADPRO Driver)",
        "Headphones (USB DAC)",
        "Realtek Digital Output",
    ]

    chosen = selection.fallback_output_device_name(
        devices,
        "Speakers (MIXLINE)",
        names_match=_exact_match,
        should_avoid=lambda name: "mixline" in name.casefold(),
    )

    assert chosen == "Headphones (USB DAC)"


def test_fallback_breaks_ties_by_device_order():
    chosen = selection.fallback_output_device_name(
        ["Unknown A", "Unknown B"],
        None,
        names_match=_exact_match,
        should_avoid=lambda name: False,
    )

    assert chosen == "Unknown A"


def test_fallback_without_candidates_is_none():
    assert (
        selection.fallback_output_device_name(
            ["Speakers (MIXLINE)"],
            "Speakers (MIXLINE)",
            names_match=_exact_match,
            should_avoid=lambda name: False,
        )
        is None
    )


def test_audio_stats_summary_keeps_counters_and_hides_raw_errors():
    stats = {
        "running": True,
        "frames_processed": 120,
        "vad_speech_ratio": 0.25,
        "device_name": "Headphones (USB DAC)",
        "last_error": "PortAudio: device unavailable",
        "last_worker_error": "",
    }

    assert selection.audio_stats_summary(stats) == {
        "running": True,
        "frames_processed": 120,
        "vad_speech_ratio": 0.25,
        "has_worker_error": False,
        "has_capture_error": True,
    }
    assert selection.audio_stats_summary(None) == {}


def test_process_audio_summary_reduces_a_snapshot_to_flags():
    snapshot = {
        "is_running": True,
        "process_ids": [7, 42],
        "has_active_audio_session": True,
        "matches": [{"device_name": "Headset", "is_active": True}],
        "default_output_device": "Speakers",
        "active_device": "Headset",
        "probe_enabled": False,
    }

    assert selection.process_audio_summary(snapshot) == {
        "is_running": True,
        "process_count": 2,
        "has_active_audio_session": True,
        "matched_device_count": 1,
        "has_default_output": True,
        "has_active_output": True,
        "probe_enabled": False,
    }
    assert selection.process_audio_summary({"process_ids": "bad"})["process_count"] == 0
    assert selection.process_audio_summary([]) == {}


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        ({"last_non_silent_at": 10.0}, "no_audio"),
        ({"last_non_silent_at": 95.0, "vad_in_speech": True}, "speech_in_progress"),
        (
            {
                "last_non_silent_at": 95.0,
                "last_segment_timing": {"segment_emitted_at": 90.0},
            },
            "segments_without_result",
        ),
        (
            {
                "last_non_silent_at": 95.0,
                "last_segment_timing": {"segment_emitted_at": 40.0},
            },
            "audio_but_nothing",
        ),
    ],
)
def test_capture_idle_state_names_ordinary_idle_cases(stats, expected):
    state = selection.capture_idle_state(
        stats,
        now=100.0,
        idle_anchor=50.0,
        recent_s=15.0,
        present_state="audio_but_nothing",
        absent_state="no_audio",
    )

    assert state == expected
