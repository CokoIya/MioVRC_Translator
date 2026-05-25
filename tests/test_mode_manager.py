"""Tests for high-level application mode switching."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.mode_manager import AppMode, ModeManager


def test_mode_manager_defaults_to_translation_and_simul_defaults():
    config: dict = {}

    manager = ModeManager(config)

    assert manager.mode is AppMode.TRANSLATION
    assert config["app_mode"] == "translation"
    assert config["simul_mode"]["tts_strategy"] == "queue"
    assert config["simul_mode"]["vad_silence_ms"] == 300


def test_simultaneous_mode_enables_tts_and_virtual_output():
    config = {"tts": {"enabled": False, "output_to_vrchat": False}}
    manager = ModeManager(
        config,
        virtual_device_resolver=lambda: (7, "MixLine Input (Logitech G MixLine)"),
    )

    change = manager.set_mode(AppMode.SIMULTANEOUS)

    assert change.changed is True
    assert change.tts_changed is True
    assert change.output_device_changed is True
    assert config["app_mode"] == "simultaneous"
    assert config["tts"]["enabled"] is True
    assert config["tts"]["auto_read"] is True
    assert config["tts"]["output_to_vrchat"] is True
    assert config["tts"]["output_device"] == 7
    assert config["tts"]["output_device_name"] == "MixLine Input (Logitech G MixLine)"


def test_simultaneous_apply_preserves_explicit_tts_preferences():
    config = {
        "app_mode": "simultaneous",
        "tts": {
            "enabled": False,
            "auto_read": False,
            "output_to_vrchat": False,
            "output_device": None,
            "output_device_name": "",
        },
    }
    manager = ModeManager(
        config,
        virtual_device_resolver=lambda: (7, "MixLine Input (Logitech G MixLine)"),
    )

    change = manager.apply_current_mode()

    assert change.tts_changed is True
    assert change.output_device_changed is False
    assert config["tts"]["enabled"] is True
    assert config["tts"]["auto_read"] is False
    assert config["tts"]["output_to_vrchat"] is False
    assert config["tts"]["output_device"] is None


def test_translation_mode_disables_tts_without_erasing_virtual_output():
    config = {
        "app_mode": "simultaneous",
        "tts": {
            "enabled": True,
            "output_to_vrchat": True,
            "output_device": 7,
            "output_device_name": "MixLine Input (Logitech G MixLine)",
        },
    }
    manager = ModeManager(config)

    change = manager.set_mode("translation")

    assert change.changed is True
    assert change.tts_changed is True
    assert config["app_mode"] == "translation"
    assert config["tts"]["enabled"] is False
    assert config["tts"]["output_to_vrchat"] is True
    assert config["tts"]["output_device"] == 7


def test_mode_listener_receives_change():
    config: dict = {}
    manager = ModeManager(config)
    events = []
    manager.add_listener(events.append)

    manager.set_mode(AppMode.SIMULTANEOUS)

    assert len(events) == 1
    assert events[0].old_mode is AppMode.TRANSLATION
    assert events[0].new_mode is AppMode.SIMULTANEOUS
