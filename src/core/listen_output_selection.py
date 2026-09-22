"""Choose the desktop output that listen mode records, and summarise it.

Listen mode records a loopback of one output device. These helpers hold the
decisions: which game processes to follow, which outputs would only record
Mio's own speech, and the best fallback. They also build the trimmed
summaries written to the log. The main window supplies its configuration and
device matchers; nothing here touches Qt or audio hardware.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import unicodedata

DEFAULT_TARGET_PROCESS_NAMES = ("VRChat.exe",)

VIRTUAL_OUTPUT_TOKENS = (
    "mixline",
    "mix line",
    "vb-audio",
    "voicemeeter",
    "cable",
    "sonar",
    "asio",
    "vadpro",
)
REAL_OUTPUT_HINTS = (
    "headphone",
    "headphones",
    "speaker",
    "speakers",
    "realtek",
    "pico",
    "quest",
    "oculus",
    "usb audio",
)

_AUDIO_STATS_FIELDS = (
    "running",
    "worker_alive",
    "worker_failure_count",
    "stream_open",
    "frame_queue_size",
    "frame_queue_capacity",
    "frame_queue_high_watermark",
    "frame_queue_dropped",
    "stale_frames_discarded",
    "frames_processed",
    "segments_emitted",
    "last_frame_rms",
    "peak_frame_rms",
    "total_frames",
    "non_silent_frames",
    "capture_rate",
    "target_rate",
    "capture_channels",
    "channels",
    "vad_in_speech",
    "vad_speech_ratio",
    "vad_activation_ratio",
)

NamesMatch = Callable[[str | None, str | None], bool]


def normalize_device_name(name: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(name or ""))
    return " ".join(normalized.casefold().split())


def target_process_names(listen_config: Mapping[str, object]) -> list[str]:
    """Return the configured game processes, deduplicated, never empty."""

    configured = listen_config.get(
        "target_process_names",
        list(DEFAULT_TARGET_PROCESS_NAMES),
    )
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        configured = []
    names: list[str] = []
    for name in configured:
        clean = str(name or "").strip()
        if clean and clean not in names:
            names.append(clean)
    return names or list(DEFAULT_TARGET_PROCESS_NAMES)


def should_avoid_output_device(
    device_name: str | None,
    tts_config: Mapping[str, object],
    *,
    match_device: Callable[[str], str | None],
    names_match: NamesMatch,
) -> bool:
    """Return whether recording *device_name* would capture Mio's own TTS."""

    name = str(device_name or "").strip()
    if not name:
        return False
    if not (
        bool(tts_config.get("enabled", False))
        and bool(tts_config.get("output_to_vrchat", False))
    ):
        return False
    tts_device = match_device(str(tts_config.get("output_device_name") or "").strip())
    if tts_device is not None and names_match(name, tts_device):
        return True
    normalized = normalize_device_name(name)
    return "mixline" in normalized or "mix line" in normalized


def fallback_output_device_name(
    device_names: Iterable[str],
    avoided_name: str | None,
    *,
    names_match: NamesMatch,
    should_avoid: Callable[[str], bool],
) -> str | None:
    """Pick the most likely physical output other than *avoided_name*."""

    candidates: list[tuple[int, int, str]] = []
    for index, name in enumerate(device_names):
        if names_match(name, avoided_name):
            continue
        if should_avoid(name):
            continue
        normalized = normalize_device_name(name)
        score = 0
        if any(token in normalized for token in REAL_OUTPUT_HINTS):
            score += 100
        if "headphone" in normalized or "headphones" in normalized:
            score += 20
        if any(token in normalized for token in VIRTUAL_OUTPUT_TOKENS):
            score -= 250
        candidates.append((score, -index, name))
    if not candidates:
        return None
    _score, _order, selected = max(candidates)
    return selected


def audio_stats_summary(stats: object) -> dict[str, object]:
    """Keep support telemetry useful without logging device inventories."""

    if not isinstance(stats, dict):
        return {}
    summary = {key: stats[key] for key in _AUDIO_STATS_FIELDS if key in stats}
    summary["has_worker_error"] = bool(stats.get("last_worker_error"))
    summary["has_capture_error"] = bool(stats.get("last_error"))
    return summary


def process_audio_summary(snapshot: object) -> dict[str, object]:
    """Reduce a process-output snapshot to counts and flags for the log."""

    if not isinstance(snapshot, dict):
        return {}
    process_ids = snapshot.get("process_ids")
    matches = snapshot.get("matches")
    return {
        "is_running": bool(snapshot.get("is_running")),
        "process_count": len(process_ids) if isinstance(process_ids, list) else 0,
        "has_active_audio_session": bool(snapshot.get("has_active_audio_session")),
        "matched_device_count": len(matches) if isinstance(matches, list) else 0,
        "has_default_output": bool(snapshot.get("default_output_device")),
        "has_active_output": bool(snapshot.get("active_device")),
        "probe_enabled": bool(snapshot.get("probe_enabled")),
    }
