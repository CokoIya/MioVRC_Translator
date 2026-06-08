from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class AppCommandType(str, Enum):
    """Command names for the future service-oriented runtime coordinator."""

    START_MIC_PIPELINE = "start_mic_pipeline"
    STOP_MIC_PIPELINE = "stop_mic_pipeline"
    START_LISTEN_PIPELINE = "start_listen_pipeline"
    STOP_LISTEN_PIPELINE = "stop_listen_pipeline"
    TRANSLATE_MANUAL_TEXT = "translate_manual_text"
    SEND_CHATBOX_TEXT = "send_chatbox_text"
    SET_MIC_MUTED = "set_mic_muted"
    SET_OUTPUT_FORMAT = "set_output_format"
    START_OVERLAY = "start_overlay"
    RUN_VAD_CALIBRATION = "run_vad_calibration"
    DOWNLOAD_MODEL = "download_model"


@dataclass(frozen=True)
class AppCommand:
    """Small immutable command envelope shared by UI and services."""

    type: AppCommandType
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = "ui"


@dataclass(frozen=True)
class SetMicMuted(AppCommand):
    def __init__(self, muted: bool, *, source: str = "ui") -> None:
        object.__setattr__(self, "type", AppCommandType.SET_MIC_MUTED)
        object.__setattr__(self, "payload", {"muted": bool(muted)})
        object.__setattr__(self, "source", source)


@dataclass(frozen=True)
class TranslateManualText(AppCommand):
    def __init__(self, text: str, *, send_to_chatbox: bool = False, source: str = "ui") -> None:
        object.__setattr__(self, "type", AppCommandType.TRANSLATE_MANUAL_TEXT)
        object.__setattr__(self, "payload", {"text": str(text or ""), "send_to_chatbox": bool(send_to_chatbox)})
        object.__setattr__(self, "source", source)


@dataclass(frozen=True)
class RunVadCalibration(AppCommand):
    def __init__(self, target: str, *, source: str = "ui") -> None:
        object.__setattr__(self, "type", AppCommandType.RUN_VAD_CALIBRATION)
        object.__setattr__(self, "payload", {"target": str(target or "mic")})
        object.__setattr__(self, "source", source)
