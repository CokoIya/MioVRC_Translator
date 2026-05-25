"""Application mode coordination for translation and simultaneous interpretation."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class AppMode(Enum):
    """User-facing runtime modes."""

    TRANSLATION = "translation"
    SIMULTANEOUS = "simultaneous"

    @classmethod
    def from_value(cls, value: object) -> "AppMode":
        text = str(value or "").strip().lower()
        for mode in cls:
            if mode.value == text:
                return mode
        return cls.TRANSLATION


VirtualDeviceResolver = Callable[[], tuple[int, str] | None]
ModeListener = Callable[["ModeChange"], None]


SIMUL_MODE_DEFAULTS = {
    "tts_backend": "edge",
    "tts_strategy": "queue",
    "vad_silence_ms": 300,
    "show_subtitle": True,
    "subtitle_position": "bottom",
    "aggressive_chunking": False,
    "merge_window_ms": 800,
}


@dataclass(frozen=True)
class ModeChange:
    old_mode: AppMode
    new_mode: AppMode
    changed: bool
    tts_changed: bool
    output_device_changed: bool


class ModeManager:
    """Apply high-level mode choices to the existing runtime configuration."""

    def __init__(
        self,
        config: dict,
        *,
        virtual_device_resolver: VirtualDeviceResolver | None = None,
    ) -> None:
        self._config = config
        self._virtual_device_resolver = virtual_device_resolver
        self._listeners: list[ModeListener] = []
        self._ensure_mode_config()
        self._mode = AppMode.from_value(self._config.get("app_mode"))

    @property
    def mode(self) -> AppMode:
        return self._mode

    def add_listener(self, callback: ModeListener) -> None:
        self._listeners.append(callback)

    def set_mode(self, mode: AppMode | str) -> ModeChange:
        next_mode = AppMode.from_value(mode.value if isinstance(mode, AppMode) else mode)
        old_mode = self._mode

        # Apply mode changes with rollback on failure
        try:
            self._mode = next_mode
            change = self.apply_current_mode(old_mode=old_mode)
        except Exception as exc:
            # Rollback mode on failure
            self._mode = old_mode
            logger.error("Mode change failed, rolled back to %s: %s", old_mode.value, exc)
            raise

        # Notify listeners after successful mode change
        for callback in tuple(self._listeners):
            try:
                callback(change)
            except Exception as exc:
                logger.error("Mode change listener failed: %s", exc)

        return change

    def apply_current_mode(self, *, old_mode: AppMode | None = None) -> ModeChange:
        old = old_mode or self._mode
        changed = self._set_config("app_mode", self._mode.value)
        tts_changed = False
        output_device_changed = False
        self._ensure_mode_config()

        tts_cfg = self._tts_config()
        if self._mode is AppMode.SIMULTANEOUS:
            entering_simultaneous = old is not self._mode
            tts_changed |= self._set_tts_config(tts_cfg, "enabled", True)
            if entering_simultaneous or "auto_read" not in tts_cfg:
                tts_changed |= self._set_tts_config(tts_cfg, "auto_read", True)
            if entering_simultaneous or "output_to_vrchat" not in tts_cfg:
                output_device_changed |= self._set_tts_config(
                    tts_cfg,
                    "output_to_vrchat",
                    True,
                )
            if bool(tts_cfg.get("output_to_vrchat", False)):
                output_device_changed |= self._select_virtual_output(tts_cfg)
        else:
            tts_changed |= self._set_tts_config(tts_cfg, "enabled", False)

        changed = changed or tts_changed or output_device_changed or old is not self._mode
        return ModeChange(
            old_mode=old,
            new_mode=self._mode,
            changed=changed,
            tts_changed=tts_changed,
            output_device_changed=output_device_changed,
        )

    def _ensure_mode_config(self) -> bool:
        changed = False
        normalized_mode = AppMode.from_value(self._config.get("app_mode")).value
        if self._config.get("app_mode") != normalized_mode:
            self._config["app_mode"] = normalized_mode
            changed = True

        simul_cfg = self._config.get("simul_mode", {})
        if "simul_mode" not in self._config or not isinstance(simul_cfg, dict):
            simul_cfg = {}
            self._config["simul_mode"] = simul_cfg
            changed = True
        for key, value in SIMUL_MODE_DEFAULTS.items():
            if key not in simul_cfg:
                simul_cfg[key] = value
                changed = True
        return changed

    def _tts_config(self) -> dict:
        tts_cfg = self._config.get("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
            self._config["tts"] = tts_cfg
        return tts_cfg

    def _select_virtual_output(self, tts_cfg: dict) -> bool:
        if self._virtual_device_resolver is None:
            return False
        resolved = self._virtual_device_resolver()
        if resolved is None:
            return False
        device_id, device_name = resolved
        changed = self._set_tts_config(tts_cfg, "output_device", device_id)
        changed |= self._set_tts_config(tts_cfg, "output_device_name", device_name)
        return changed

    def _set_config(self, key: str, value: object) -> bool:
        if self._config.get(key) == value:
            return False
        self._config[key] = value
        return True

    @staticmethod
    def _set_tts_config(tts_cfg: dict, key: str, value: object) -> bool:
        if tts_cfg.get(key) == value:
            return False
        tts_cfg[key] = value
        return True
