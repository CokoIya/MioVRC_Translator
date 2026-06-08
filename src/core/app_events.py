from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class AppEventType(str, Enum):
    """Application event names used by services and UI adapters."""

    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    PARTIAL_TRANSCRIPT_READY = "partial_transcript_ready"
    FINAL_TRANSCRIPT_READY = "final_transcript_ready"
    TRANSLATION_READY = "translation_ready"
    CHATBOX_SEND_QUEUED = "chatbox_send_queued"
    OSC_AVATAR_STATE_CHANGED = "osc_avatar_state_changed"
    OVERLAY_STATUS_CHANGED = "overlay_status_changed"
    VAD_METER_UPDATED = "vad_meter_updated"
    MODEL_DOWNLOAD_PROGRESS = "model_download_progress"
    FRIENDLY_ERROR_RAISED = "friendly_error_raised"


@dataclass(frozen=True)
class AppEvent:
    """Small immutable event envelope for future service-oriented pipelines."""

    type: AppEventType
    source: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeStateChanged(AppEvent):
    def __init__(self, state: str, **payload: Any) -> None:
        object.__setattr__(self, "type", AppEventType.RUNTIME_STATE_CHANGED)
        object.__setattr__(self, "source", "runtime")
        object.__setattr__(self, "payload", {"state": state, **payload})


@dataclass(frozen=True)
class OscAvatarStateChanged(AppEvent):
    def __init__(self, parameter: str, value: Any) -> None:
        object.__setattr__(self, "type", AppEventType.OSC_AVATAR_STATE_CHANGED)
        object.__setattr__(self, "source", "osc")
        object.__setattr__(self, "payload", {"parameter": parameter, "value": value})


@dataclass(frozen=True)
class VadMeterUpdated(AppEvent):
    def __init__(self, source: str, **payload: Any) -> None:
        object.__setattr__(self, "type", AppEventType.VAD_METER_UPDATED)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "payload", payload)
