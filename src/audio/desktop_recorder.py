# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from .recorder import AudioRecorder
from .device_inventory import (
    default_output_device_name as inventory_default_output_device_name,
    device_inventory_diagnostics,
    list_output_devices as list_sounddevice_output_devices,
    normalize_device_name,
    unique_device_name_match,
)
from .vad_detector import (
    SileroVADDetector,
    VADDetector,
    silero_vad_model_available,
)

logger = logging.getLogger(__name__)

# PortAudio is a process-global library: Pa_Terminate() tears down ALL active
# streams, not just the ones owned by the calling PyAudio instance.  Concurrent
# PyAudio instantiation (enumeration vs capture) therefore crashes.  This lock
# serialises every PyAudio lifecycle so at most one instance exists at a time.
_pyaudio_lock = threading.Lock()
_loopback_cache_lock = threading.RLock()
_last_loopback_devices: list[dict[str, object]] = []
_last_loopback_diagnostics: dict[str, object] = {}
_consecutive_empty_loopback_scans = 0

_MAX_DESKTOP_CAPTURE_CHANNELS = 2
_COMMON_DESKTOP_CAPTURE_RATES = (
    48000,
    44100,
    16000,
)
_CAPTURE_START_TIMEOUT_S = 5.0


class _SoundCardFallbackRequested(RuntimeError):
    """Internal signal to retry the same endpoint through PyAudioWPatch."""


# ---------------------------------------------------------------------------
# PyAudioWPatch import helper
# ---------------------------------------------------------------------------

def _import_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "Desktop audio capture component is unavailable in the current runtime. "
            "If you are running from source, install `PyAudioWPatch`; "
            "if you are using the packaged app, rebuild the bundle with PyAudioWPatch included."
        ) from exc
    return pyaudio


def _import_soundcard():
    try:
        import soundcard as sc
    except ImportError as exc:
        raise RuntimeError(
            "Desktop audio capture component is unavailable in the current runtime. "
            "Install `SoundCard` or rebuild the bundle with SoundCard included."
        ) from exc
    return sc


# ---------------------------------------------------------------------------
# Public device enumeration (uses the PyAudioWPatch loopback devices opened for capture)
# ---------------------------------------------------------------------------

def _display_loopback_name(name: object) -> str:
    return re.sub(r"\s*\[loopback\]\s*$", "", str(name or "").strip(), flags=re.IGNORECASE).strip()


def _copy_loopback_devices(devices: list[dict[str, object]]) -> list[dict[str, object]]:
    # Keep backend handle objects by reference; callers may need them to open a
    # SoundCard recorder. The dictionaries themselves are isolated from UI code.
    return [dict(device) for device in devices]


def _cache_loopback_devices(
    devices: list[dict[str, object]],
    *,
    backend: str,
    errors: list[str] | None = None,
) -> list[dict[str, object]]:
    global _last_loopback_devices, _last_loopback_diagnostics
    global _consecutive_empty_loopback_scans
    snapshot = _copy_loopback_devices(devices)
    diagnostics = {
        "backend": backend,
        "device_count": len(snapshot),
        "from_cache": False,
        "errors": list(errors or []),
        "frozen_runtime": bool(getattr(sys, "frozen", False)),
        "scanned_at": time.monotonic(),
        "devices": [
            {
                key: value
                for key, value in device.items()
                if not str(key).startswith("_")
            }
            for device in snapshot
        ],
    }
    with _loopback_cache_lock:
        _last_loopback_devices = snapshot
        _last_loopback_diagnostics = diagnostics
        if snapshot:
            _consecutive_empty_loopback_scans = 0
    return _copy_loopback_devices(snapshot)


def _cached_loopback_devices(*, reason: str) -> list[dict[str, object]]:
    global _last_loopback_diagnostics
    with _loopback_cache_lock:
        cached = _copy_loopback_devices(_last_loopback_devices)
        diagnostics = dict(_last_loopback_diagnostics)
        scanned_at = float(diagnostics.get("scanned_at", 0.0) or 0.0)
        diagnostics.update(
            {
                "from_cache": True,
                "cache_reason": reason,
                "cache_age_s": round(max(0.0, time.monotonic() - scanned_at), 3),
            }
        )
        errors = list(diagnostics.get("errors", []))
        errors.append(reason)
        diagnostics["errors"] = errors
        _last_loopback_diagnostics = diagnostics
    if cached:
        logger.warning("Using cached desktop output devices: %s", reason)
    return cached


def _refresh_cached_loopbacks_from_sounddevice(
    cached: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Refresh names/defaults while PyAudioWPatch owns the PortAudio lock."""

    try:
        active_outputs = list_sounddevice_output_devices(
            force_refresh=True,
            sounddevice_module=sd,
        )
    except Exception:
        return cached
    if not active_outputs:
        return cached

    cached_names = [str(item.get("name", "") or "").strip() for item in cached]
    refreshed: list[dict[str, object]] = []
    used_cached: set[int] = set()
    for output in active_outputs:
        output_name = str(output.get("name", "") or "").strip()
        matched_name = unique_device_name_match(output_name, cached_names)
        cached_index = next(
            (
                index
                for index, candidate in enumerate(cached_names)
                if index not in used_cached and candidate == matched_name
            ),
            None,
        )
        if cached_index is not None:
            item = dict(cached[cached_index])
            used_cached.add(cached_index)
            item["is_default"] = bool(output.get("is_default"))
            refreshed.append(item)
            continue
        refreshed.append(
            {
                "index": int(output.get("index", -1)),
                "name": output_name,
                "is_default": bool(output.get("is_default")),
                "backend": "sounddevice_inventory",
                "enumeration_only": True,
            }
        )
    return refreshed or cached


def loopback_device_diagnostics() -> dict[str, object]:
    with _loopback_cache_lock:
        result = dict(_last_loopback_diagnostics)
    try:
        result["sounddevice_inventory"] = device_inventory_diagnostics()
    except Exception:
        logger.debug("Failed to attach sounddevice inventory diagnostics", exc_info=True)
    return result


def _reset_loopback_device_cache_for_tests() -> None:
    global _last_loopback_devices, _last_loopback_diagnostics
    global _consecutive_empty_loopback_scans
    with _loopback_cache_lock:
        _last_loopback_devices = []
        _last_loopback_diagnostics = {}
        _consecutive_empty_loopback_scans = 0


def _windows_render_endpoints() -> list[dict[str, object]]:
    if sys.platform != "win32":
        return []
    try:
        from .windows_audio import list_audio_endpoints

        return [
            endpoint
            for endpoint in list_audio_endpoints(include_inactive=True)
            if str(endpoint.get("flow", "")) == "render"
        ]
    except Exception:
        return []


def _windows_endpoint_selectable(
    device_id: str,
    name: str,
    endpoints: list[dict[str, object]] | None = None,
) -> bool:
    if sys.platform != "win32":
        return True
    if endpoints is None:
        endpoints = _windows_render_endpoints()
    if not endpoints:
        return True
    if device_id:
        exact_id = [
            endpoint
            for endpoint in endpoints
            if str(endpoint.get("id", "") or "").strip() == device_id
        ]
        if exact_id:
            return any(bool(endpoint.get("active")) for endpoint in exact_id)
    normalized = normalize_device_name(name)
    exact_name = [
        endpoint
        for endpoint in endpoints
        if normalize_device_name(endpoint.get("name")) == normalized
    ]
    if exact_name:
        return any(bool(endpoint.get("active")) for endpoint in exact_name)
    return True


def _select_default_loopback_index(
    devices: list[dict[str, object]],
    *,
    default_name: str = "",
    default_id: str = "",
) -> int | None:
    if default_id:
        id_matches = [
            index
            for index, device in enumerate(devices)
            if str(device.get("id", "") or "").strip() == default_id
        ]
        if len(id_matches) == 1:
            return id_matches[0]

    names = [str(device.get("name", "") or "").strip() for device in devices]
    matched_name = unique_device_name_match(default_name, names)
    if matched_name:
        matches = [index for index, name in enumerate(names) if name == matched_name]
        if len(matches) == 1:
            return matches[0]

    # Prefix candidates such as localized "Speakers" are only safe when they
    # identify one endpoint. Never mark every speaker as the default.
    default_candidates = set(_loopback_name_candidates(default_name)) if default_name else set()
    candidate_matches = [
        index
        for index, name in enumerate(names)
        if default_candidates
        and default_candidates.intersection(_loopback_name_candidates(name))
    ]
    return candidate_matches[0] if len(candidate_matches) == 1 else None


def _enumerate_pyaudio_loopback_devices(p, pa) -> list[dict[str, object]]:
    raw_devices = _enumerate_wasapi_loopback_devices(p, pa)

    default_name = _default_output_device_name_from_sounddevice()
    result: list[dict[str, object]] = []
    by_name: dict[str, dict[str, object]] = {}
    windows_endpoints = _windows_render_endpoints()

    for raw in raw_devices:
        try:
            index = int(raw.get("index", -1))
        except Exception:
            continue
        raw_name = str(raw.get("name", "")).strip()
        if not raw_name or not _is_genuine_loopback_name(raw_name):
            continue
        name = _display_loopback_name(raw_name)
        if not name:
            continue
        if not _windows_endpoint_selectable("", name, windows_endpoints):
            logger.debug("Skipping inactive PyAudio loopback endpoint: %s", name)
            continue
        item: dict[str, object] = {
            **dict(raw),
            "index": index,
            "name": name,
            "raw_name": raw_name,
            "is_default": False,
        }
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = item
            result.append(item)

    default_index = _select_default_loopback_index(
        result,
        default_name=default_name,
    )
    if default_index is not None:
        result[default_index]["is_default"] = True

    return result


def _enumerate_soundcard_loopback_devices(sc) -> list[dict[str, object]]:
    try:
        raw_devices = list(sc.all_microphones(include_loopback=True))
    except Exception as exc:
        logger.warning("Failed to enumerate SoundCard loopback devices: %s", exc)
        return []

    try:
        default_speaker = sc.default_speaker()
        default_name = str(getattr(default_speaker, "name", "") or "").strip()
        default_id = str(getattr(default_speaker, "id", "") or "").strip()
    except Exception:
        default_name = ""
        default_id = ""

    result: list[dict[str, object]] = []
    by_name: dict[str, dict[str, object]] = {}
    windows_endpoints = _windows_render_endpoints()

    for index, raw in enumerate(raw_devices):
        if not bool(getattr(raw, "isloopback", False)):
            continue
        name = str(getattr(raw, "name", "") or "").strip()
        if not name:
            continue
        device_id = str(getattr(raw, "id", "") or "").strip()
        if not _windows_endpoint_selectable(device_id, name, windows_endpoints):
            logger.debug(
                "Skipping inactive SoundCard loopback endpoint id=%s name=%s",
                device_id,
                name,
            )
            continue
        try:
            channels = int(getattr(raw, "channels", 2) or 2)
        except Exception:
            channels = 2
        item: dict[str, object] = {
            "index": index,
            "name": name,
            "id": device_id,
            "is_default": False,
            "maxInputChannels": max(channels, 1),
            "defaultSampleRate": 48000,
            "_soundcard_device": raw,
            "backend": "soundcard",
        }
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = item
            result.append(item)

    inventory_default = _default_output_device_name_from_sounddevice()
    default_index = _select_default_loopback_index(
        result,
        default_name=default_name or inventory_default,
        default_id=default_id,
    )
    if default_index is None and inventory_default and inventory_default != default_name:
        default_index = _select_default_loopback_index(
            result,
            default_name=inventory_default,
        )
    if default_index is not None:
        result[default_index]["is_default"] = True

    return result


def list_output_devices() -> list[dict[str, object]]:
    """Return WASAPI loopback output devices.

    SoundCard is the preferred backend. PyAudioWPatch is kept only as a
    compatibility fallback when SoundCard is unavailable, because some Windows
    driver stacks crash inside PyAudioWPatch/PortAudio while opening loopback
    streams.
    """
    errors: list[str] = []
    try:
        sc = _import_soundcard()
        devices = _enumerate_soundcard_loopback_devices(sc)
        logger.debug("Enumerated %s SoundCard loopback output devices", len(devices))
        if devices:
            return _cache_loopback_devices(
                devices,
                backend="soundcard",
                errors=errors,
            )
        message = "SoundCard returned no active loopback output devices"
        errors.append(message)
        logger.warning(message)
    except Exception as exc:
        errors.append(f"SoundCard: {exc}")
        logger.warning("SoundCard loopback enumeration unavailable: %s", exc)

    # Hold _pyaudio_lock for the entire PyAudio lifecycle so that device
    # enumeration and the capture stream are never active at the same time.
    if not _pyaudio_lock.acquire(blocking=False):
        logger.debug("PyAudio lock held by capture stream; skipping loopback enumeration")
        cached = _cached_loopback_devices(
            reason="PyAudioWPatch enumeration deferred while its capture stream is active"
        )
        if cached:
            return _refresh_cached_loopbacks_from_sounddevice(cached)
        with _loopback_cache_lock:
            _last_loopback_diagnostics.update(
                {
                    "backend": "unavailable",
                    "device_count": 0,
                    "from_cache": False,
                    "errors": [*errors, "PyAudioWPatch capture lock is busy"],
                    "frozen_runtime": bool(getattr(sys, "frozen", False)),
                }
            )
        return []
    p = None
    try:
        pa = _import_pyaudio()
        p = pa.PyAudio()
        devices = _enumerate_pyaudio_loopback_devices(p, pa)
    except Exception as exc:
        errors.append(f"PyAudioWPatch: {exc}")
        logger.warning("Failed to enumerate PyAudioWPatch WASAPI loopback devices: %s", exc)
        cached = _cached_loopback_devices(reason=str(exc))
        if cached:
            return cached
        devices = []
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
        _pyaudio_lock.release()

    logger.debug("Enumerated %s PyAudioWPatch loopback output devices", len(devices))
    if devices:
        return _cache_loopback_devices(
            devices,
            backend="pyaudiowpatch",
            errors=errors,
        )

    global _consecutive_empty_loopback_scans
    with _loopback_cache_lock:
        _consecutive_empty_loopback_scans += 1
        empty_scan_count = _consecutive_empty_loopback_scans
    if empty_scan_count <= 2:
        cached = _cached_loopback_devices(
            reason=(
                "Transient empty desktop-device scan "
                f"({empty_scan_count}/2); awaiting hot-plug stabilization"
            )
        )
        if cached:
            return cached
    _cache_loopback_devices([], backend="unavailable", errors=errors)
    logger.error("No desktop output device detected; diagnostics=%s", loopback_device_diagnostics())
    return []


def default_output_device_name() -> str | None:
    devices = list_output_devices()
    for device in devices:
        if device.get("is_default"):
            return str(device.get("name", "")).strip() or None
    inventory_default = _default_output_device_name_from_sounddevice()
    matched = _fuzzy_match_loopback(inventory_default, devices) if inventory_default else None
    if isinstance(matched, dict):
        return str(matched.get("name", "")).strip() or None
    if devices:
        return str(devices[0].get("name", "")).strip() or None
    return None


def desktop_audio_supported() -> bool:
    return bool(list_output_devices())


def auto_select_virtual_device() -> str | None:
    """Automatically detect and select the best available virtual audio device.

    Returns the device name if found, None otherwise.
    Prioritizes MixLine only.
    """
    devices = list_output_devices()
    if not devices:
        return None

    for device in devices:
        name = str(device.get("name", "")).lower()
        if "mixline" in name or "mix line" in name:
            return str(device.get("name", ""))

    return None


# ---------------------------------------------------------------------------
# Name-matching helpers
# ---------------------------------------------------------------------------

def _loopback_name_candidates(name: str) -> list[str]:
    """Return normalized candidate forms for cross-library name matching.

    PyAudioWPatch loopback names are "<output_name> [Loopback]", so the full
    lowercased name and the outer prefix ("speakers") serve as match
    candidates. The inner parenthesised model name (e.g. "hecate g2 pro")
    is intentionally NOT used — multiple endpoints (speaker / mic / chat
    headset) on the same hardware share that token, which previously caused
    a microphone endpoint to be picked as the "loopback" and crashed PortAudio.
    """
    s = normalize_device_name(name)
    # Strip trailing " [loopback]" suffix so candidates represent the output name
    s = re.sub(r"\s*\[loopback\]\s*$", "", s).strip()
    results: list[str] = [" ".join(s.split())]
    # outer prefix before first "(" → "speakers"
    prefix = re.split(r"\s*\(", s, maxsplit=1)[0].strip()
    if prefix and prefix not in results:
        results.append(prefix)
    return results


def _is_genuine_loopback_name(name: str) -> bool:
    """Reject loopback candidates whose name lacks an explicit loopback marker.

    On some drivers PyAudioWPatch's loopback enumerator returns capture-only
    endpoints (e.g. a microphone) that share a hardware token with a speaker.
    Trying to open such a device as a loopback triggers a host-API crash.
    """
    return "[loopback]" in str(name or "").lower()


def _fuzzy_match_loopback(preferred_name: str, loopbacks: list) -> object | None:
    """Return the first loopback device whose name matches *preferred_name*.

    Works with both dicts (PyAudioWPatch) and attribute-style objects.
    Passes run in decreasing specificity so that an exact match for
    "スピーカー(MIXLINE)" is not shadowed by an earlier device that
    only shares the generic prefix "スピーカー".
    """
    pref_candidates = _loopback_name_candidates(preferred_name)
    pref_full = pref_candidates[0] if pref_candidates else ""

    def _name(item) -> str:
        if isinstance(item, dict):
            return str(item.get("name", "") or "").strip()
        return str(getattr(item, "name", "") or "").strip()

    # Pass 0: exact full-name match (highest priority — prevents prefix-only
    # collisions when multiple devices share the same generic prefix).
    if pref_full:
        for lb in loopbacks:
            lb_full = _loopback_name_candidates(_name(lb))
            if lb_full and lb_full[0] == pref_full:
                return lb

    # Pass 1: candidate-set intersection is only safe if it is unique. Generic
    # prefixes such as "Speakers" occur on many Windows endpoints.
    candidate_matches = [
        lb
        for lb in loopbacks
        if set(pref_candidates) & set(_loopback_name_candidates(_name(lb)))
    ]
    if len(candidate_matches) == 1:
        return candidate_matches[0]

    # Pass 2: substring (catches truncated sounddevice names vs. full COM names)
    substring_matches = []
    for lb in loopbacks:
        lb_name = normalize_device_name(_name(lb))
        if any(
            pc
            and min(len(pc), len(lb_name)) >= 18
            and (pc in lb_name or lb_name in pc)
            for pc in pref_candidates
        ):
            substring_matches.append(lb)
    if len(substring_matches) == 1:
        return substring_matches[0]

    return None


def _enumerate_wasapi_loopback_devices(p, pa) -> list[dict]:
    """Enumerate WASAPI loopback endpoints by scanning the device table.

    This avoids the PyAudioWPatch loopback generator path, which has been more
    fragile on some drivers than a direct device scan.
    """
    try:
        wasapi_info = p.get_host_api_info_by_type(pa.paWASAPI)
    except OSError:
        return []

    try:
        wasapi_index = int(wasapi_info.get("index", -1))
    except Exception:
        return []

    try:
        device_count = int(p.get_device_count())
    except Exception:
        device_count = 0

    loopbacks: list[dict] = []
    rejected: list[str] = []

    for index in range(device_count):
        try:
            info = p.get_device_info_by_index(index)
        except Exception:
            continue
        try:
            if int(info.get("hostApi", -1)) != wasapi_index:
                continue
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
        except Exception:
            continue

        name = str(info.get("name", "")).strip()
        if not _is_genuine_loopback_name(name):
            if name:
                rejected.append(name)
            continue
        loopbacks.append(info)

    if rejected:
        logger.debug(
            "Rejected %s non-loopback entries from device scan: %s",
            len(rejected),
            rejected,
        )
    logger.debug(
        "Enumerated %s loopback devices via device scan: %s",
        len(loopbacks),
        [str(lb.get("name", "")).strip() for lb in loopbacks],
    )
    return loopbacks


# ---------------------------------------------------------------------------
# DesktopAudioRecorder
# ---------------------------------------------------------------------------

class DesktopAudioRecorder(AudioRecorder):
    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        vad_sensitivity: int = 1,
        silence_threshold_s: float = 1.2,
        vad_speech_ratio: float = 0.6,
        vad_activation_threshold_s: float = 0.2,
        output_device_name: str | None = None,
        on_vad_state: Optional[Callable[[bool], None]] = None,
        pre_speech_s: float = 0.30,
        on_chunk: Optional[Callable[[np.ndarray], None]] = None,
        chunk_interval_ms: int = 250,
        chunk_window_s: float = 1.6,
        ring_buffer_s: float = 4.0,
        recent_speech_hold_s: float = 0.8,
        min_segment_s: float = 0.45,
        partial_min_speech_s: float = 0.45,
        vad_min_rms: float = 0.05,
        max_segment_s: float = 6.0,
        denoise_strength: float = 0.0,
        silero_speech_threshold: float = 0.5,
        vad_type: str = "silero",  # silero is tolerant of background music and SFX; change to "webrtc" if you need maximum sensitivity to quiet speech
        on_runtime_error: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(
            on_segment=on_segment,
            sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms,
            vad_sensitivity=vad_sensitivity,
            silence_threshold_s=silence_threshold_s,
            vad_speech_ratio=vad_speech_ratio,
            vad_activation_threshold_s=vad_activation_threshold_s,
            input_device=None,
            on_vad_state=on_vad_state,
            pre_speech_s=pre_speech_s,
            on_chunk=on_chunk,
            chunk_interval_ms=chunk_interval_ms,
            chunk_window_s=chunk_window_s,
            ring_buffer_s=ring_buffer_s,
            recent_speech_hold_s=recent_speech_hold_s,
            min_segment_s=min_segment_s,
            partial_min_speech_s=partial_min_speech_s,
            vad_min_rms=vad_min_rms,
            max_segment_s=max_segment_s,
            denoise_strength=denoise_strength,
        )
        # VAD selection for desktop audio:
        #   - "silero" (default): Neural VAD - tolerant of background music and SFX,
        #     significantly reduces false positives from game audio.
        #   - "webrtc": WebRTC VAD - same as mic, well-tested, permissive.
        #     May produce false positives from game music/SFX.
        vad_type_normalized = str(vad_type or "silero").strip().lower()
        if vad_type_normalized == "silero" and silero_vad_model_available():
            self.vad = SileroVADDetector(
                sample_rate=sample_rate,
                frame_duration_ms=frame_duration_ms,
                silence_threshold_s=silence_threshold_s,
                speech_ratio=vad_speech_ratio,
                activation_threshold_s=vad_activation_threshold_s,
                min_rms=vad_min_rms,
                max_speech_s=max_segment_s,
                speech_threshold=silero_speech_threshold,
            )
            logger.info("DesktopAudioRecorder VAD: Silero (threshold=%.2f min_rms=%.4f)",
                        silero_speech_threshold, vad_min_rms)
        else:
            if vad_type_normalized == "silero":
                logger.warning(
                    "Pinned Silero VAD model is unavailable; using WebRTC VAD"
                )
            # WebRTC VAD. Sensitivity in webrtcvad is INVERTED from what the
            # name suggests: 0 is least aggressive filter (most permissive, most
            # frames marked as speech), 3 is most aggressive (least permissive).
            # For VRChat VoIP we want to catch quiet speech, so use 0.
            self.vad = VADDetector(
                sample_rate=sample_rate,
                frame_duration_ms=frame_duration_ms,
                sensitivity=0,
                silence_threshold_s=silence_threshold_s,
                speech_ratio=vad_speech_ratio,
                activation_threshold_s=vad_activation_threshold_s,
                min_rms=vad_min_rms,
                max_speech_s=max_segment_s,
            )
            logger.info(
                "DesktopAudioRecorder VAD: WebRTC (sensitivity=0 min_rms=%.4f)",
                vad_min_rms,
            )
        self._output_device_name = str(output_device_name or "").strip()
        self._capture_thread: Optional[threading.Thread] = None
        self._vad_prewarm_thread: Optional[threading.Thread] = None
        self._capture_stream = None
        self._pyaudio_module = None
        self._pyaudio_instance = None
        self._loopback_device_info: Optional[dict] = None
        self._on_runtime_error = on_runtime_error
        self._last_error: str | None = None
        self._capture_ready_event: threading.Event | None = None
        self._capture_start_error: BaseException | None = None
        self._stats_lock = threading.Lock()
        self._last_frame_at = 0.0
        self._last_non_silent_at = 0.0
        self._last_rms = 0.0
        self._last_prepared_rms = 0.0
        self._last_downmix_log_at = 0.0
        self._total_frames = 0
        self._non_silent_frames = 0
        self._stream_config: Optional[dict[str, int]] = None

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _report_runtime_error(self, exc: BaseException | str) -> None:
        # 记录错误并通过回调通知 UI
        message = str(exc).strip()
        if not message:
            message = exc.__class__.__name__ if isinstance(exc, BaseException) else "Unknown error"
        self._last_error = message
        logger.warning("DesktopAudioRecorder runtime error: %s", message)
        if self._on_runtime_error is not None:
            try:
                self._on_runtime_error(message)
            except Exception:
                pass

    @staticmethod
    def _portaudio_error_code(exc: BaseException) -> int | None:
        for arg in getattr(exc, "args", ()):
            if isinstance(arg, int):
                return arg
        return None

    @classmethod
    def _is_transient_capture_error(cls, exc: BaseException) -> bool:
        message = str(exc).strip().lower()
        if "unanticipated host error" in message:
            return True
        return cls._portaudio_error_code(exc) == -9999

    def start(self):
        with self._lifecycle_lock:
            if self._running:
                return
            for name in ("_capture_thread", "_worker_thread"):
                previous = getattr(self, name)
                if previous is None:
                    continue
                if previous.is_alive():
                    raise RuntimeError(
                        "Previous desktop audio thread is still stopping"
                    )
                setattr(self, name, None)
            if (
                self._vad_prewarm_thread is not None
                and not self._vad_prewarm_thread.is_alive()
            ):
                self._vad_prewarm_thread = None

        self._loopback_device_info = None
        self._stream_config = None
        self._pyaudio_module = None
        self._pyaudio_instance = None
        self._capture_stream = None
        self._capture_start_error = None
        self._capture_ready_event = threading.Event()
        self.input_device = None
        self.extra_settings = None
        self._active_device_name = None
        self._last_error = None
        self._capture_rate = self.sample_rate
        self._reset_streaming_resampler()
        self._capture_channels = 1
        self._capture_dtype = "float32"
        self._running = True
        self.vad.reset()
        self._buffer.clear()
        self._pre_speech_buffer.clear()
        self._speech_samples = 0
        self._was_in_speech = False
        self._clear_frame_queue()
        self._denoiser.reset()
        if self._chunk_streamer is not None:
            self._chunk_streamer.reset()
        with self._stats_lock:
            self._last_frame_at = 0.0
            self._last_non_silent_at = 0.0
            self._last_rms = 0.0
            self._last_prepared_rms = 0.0
            self._total_frames = 0
            self._non_silent_frames = 0

        try:
            # Pre-warm Silero VAD model in the background while capture
            # initializes. Thread creation is part of the same exception-safe
            # lifecycle transaction as capture and processing startup.
            if (
                isinstance(self.vad, SileroVADDetector)
                and self._vad_prewarm_thread is None
            ):
                self._vad_prewarm_thread = threading.Thread(
                    target=self.vad.prewarm,
                    name="vad-prewarm",
                    daemon=True,
                )
                self._vad_prewarm_thread.start()

            self._worker_thread = threading.Thread(
                target=self._worker_main,
                daemon=True,
                name="desktop-audio-worker",
            )
            self._worker_thread.start()
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="desktop-audio-capture",
            )
            self._capture_thread.start()

            if not self._capture_ready_event.wait(timeout=_CAPTURE_START_TIMEOUT_S):
                error = RuntimeError(
                    "Desktop audio capture did not become ready in time"
                )
                self._capture_start_error = error
                self._report_runtime_error(error)
                raise error

            if self._capture_start_error is not None:
                raise self._capture_start_error
        except BaseException:
            # Thread creation, backend initialization, and readiness failures
            # must leave no live capture/worker state behind. The owner creates
            # a fresh recorder for the next bounded recovery attempt.
            self._running = False
            self._enqueue_frame(None)
            try:
                self.stop()
            except Exception:
                logger.debug(
                    "DesktopAudioRecorder startup cleanup failed",
                    exc_info=True,
                )
            raise

        logger.info(
            "DesktopAudioRecorder started (requested_output=%s loopback_device=%s rate=%s channels=%s)",
            self._output_device_name or "auto",
            self._loopback_device_info.get("name") if self._loopback_device_info else None,
            self._capture_rate,
            self._capture_channels,
        )

    def stop(self):
        self._running = False
        capture_stopped = self._stop_capture_thread()
        super().stop()
        prewarm_stopped = self._join_vad_prewarm_thread()
        worker_stopped = self._worker_thread is None or not self._worker_thread.is_alive()
        if capture_stopped:
            # Capture backends normally release these in their thread's
            # finally block. Only repeat termination after that thread exited;
            # Pa_Terminate while it is still active can crash the process.
            self._terminate_pyaudio()
            self._loopback_device_info = None
            self._stream_config = None
            self._active_device_name = None
            self._capture_stream = None
            self.input_device = None
            self.extra_settings = None
        if capture_stopped and worker_stopped and prewarm_stopped:
            close_vad = getattr(self.vad, "close", None)
            if callable(close_vad):
                close_vad()
        logger.info("DesktopAudioRecorder stopped (requested_output=%s)", self._output_device_name or "auto")

    def _stop_capture_thread(self) -> bool:
        thread = self._capture_thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=2)
        if thread.is_alive():
            logger.warning(
                "Desktop audio capture thread did not stop in time; closing stream from caller"
            )
            self._close_capture_stream()
            thread.join(timeout=1)
        stopped = not thread.is_alive()
        if stopped:
            if self._capture_thread is thread:
                self._capture_thread = None
        else:
            logger.warning(
                "Desktop audio capture thread remains alive; native backend ownership is retained"
            )
        return stopped

    def _join_vad_prewarm_thread(self) -> bool:
        thread = self._vad_prewarm_thread
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout=1)
        stopped = not thread.is_alive()
        if stopped and self._vad_prewarm_thread is thread:
            self._vad_prewarm_thread = None
        return stopped

    def diagnostics_snapshot(self) -> dict[str, object]:
        activation_window = getattr(self.vad, "_activation_window", None)
        try:
            activation_ratio = (
                sum(bool(item) for item in activation_window) / len(activation_window)
                if activation_window is not None and len(activation_window) > 0
                else 0.0
            )
        except Exception:
            activation_ratio = 0.0
        with self._stats_lock:
            snapshot = {
                "running": self.is_running,
                "last_frame_at": self._last_frame_at,
                "last_non_silent_at": self._last_non_silent_at,
                "last_rms": self._last_rms,
                "last_frame_rms": round(float(getattr(self, "_last_frame_rms", self._last_prepared_rms) or self._last_prepared_rms), 6),
                "peak_frame_rms": round(float(getattr(self, "_peak_frame_rms", self._last_prepared_rms) or self._last_prepared_rms), 6),
                "last_prepared_rms": self._last_prepared_rms,
                "total_frames": self._total_frames,
                "frames_processed": self._total_frames,
                "non_silent_frames": self._non_silent_frames,
                "segments_emitted": int(getattr(self, "_segments_emitted", 0) or 0),
                "requested_output_device": self._output_device_name or None,
                "loopback_device": (
                    str(self._loopback_device_info.get("name", "")).strip()
                    if self._loopback_device_info is not None
                    else None
                ),
                "active_output_device": self._active_device_name,
                "active_device": self._active_device_name,
                "backend": (
                    self._loopback_device_info.get("backend")
                    if isinstance(self._loopback_device_info, dict)
                    else None
                ),
                "capture_rate": self._capture_rate,
                "target_rate": self.sample_rate,
                "capture_channels": self._capture_channels,
                "channels": self._capture_channels,
                "dtype": self._capture_dtype,
                "vad_min_rms": getattr(self.vad, "_min_rms", None),
                "vad_in_speech": bool(getattr(self.vad, "in_speech", False)),
                "vad_speech_ratio": getattr(self.vad, "_speech_ratio", None),
                "vad_activation_ratio": round(float(activation_ratio), 3),
                "last_error": self._last_error,
            }
        try:
            snapshot["device_inventory"] = loopback_device_diagnostics()
        except Exception:
            logger.debug("Failed to attach desktop device diagnostics", exc_info=True)
        return snapshot

    @staticmethod
    def _close_stream_object(stream) -> None:
        if stream is None:
            return
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _close_capture_stream(self) -> None:
        stream = self._capture_stream
        self._capture_stream = None
        self._close_stream_object(stream)

    def _terminate_pyaudio(self) -> None:
        p = self._pyaudio_instance
        self._pyaudio_instance = None
        self._pyaudio_module = None
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass

    def _open_stream_with_config(
        self,
        p,
        pa,
        device_info: dict,
        stream_config: dict[str, int],
        stream_callback=None,
    ):
        return p.open(
            format=pa.paFloat32,
            channels=int(stream_config["channels"]),
            rate=int(stream_config["rate"]),
            input=True,
            input_device_index=int(device_info["index"]),
            frames_per_buffer=int(stream_config["blocksize"]),
            stream_callback=stream_callback,
        )

    def _open_loopback_stream(self, p, pa, device_info: dict, stream_callback=None):
        device_index = int(device_info["index"])
        native_rate = int(round(float(device_info.get("defaultSampleRate", 48000))))
        max_channels = max(
            min(int(device_info.get("maxInputChannels", 2) or 2), _MAX_DESKTOP_CAPTURE_CHANNELS),
            1,
        )
        last_error: BaseException | None = None

        for rate, channels in self._candidate_stream_configs(native_rate, max_channels):
            blocksize = max(int(rate * self.frame_duration_ms / 1000), 1)
            stream_config = {
                "rate": int(rate),
                "channels": int(channels),
                "blocksize": int(blocksize),
            }
            try:
                self._validate_stream_config(
                    p,
                    pa,
                    device_index=device_index,
                    rate=rate,
                    channels=channels,
                    blocksize=blocksize,
                )
                stream = self._open_stream_with_config(
                    p,
                    pa,
                    device_info,
                    stream_config,
                    stream_callback=stream_callback,
                )
                logger.info(
                    "Selected desktop capture config (device=%s rate=%s channels=%s blocksize=%s)",
                    device_info.get("name"),
                    rate,
                    channels,
                    blocksize,
                )
                return stream, stream_config
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "Desktop capture open attempt failed (device=%s rate=%s channels=%s): %s",
                    device_info.get("name"),
                    rate,
                    channels,
                    exc,
                )

        if last_error is None:
            last_error = RuntimeError("No compatible desktop audio capture configuration was found")
        raise last_error

    def _reopen_capture_stream(self) -> bool:
        p = self._pyaudio_instance
        pa = self._pyaudio_module
        device_info = self._loopback_device_info
        stream_config = self._stream_config
        if p is None or pa is None or device_info is None or stream_config is None:
            return False
        self._close_capture_stream()
        try:
            self._capture_stream = self._open_stream_with_config(p, pa, device_info, stream_config)
            return True
        except Exception as exc:
            logger.warning("Failed to reopen desktop audio stream: %s", exc)
            return False

    def _update_capture_stats(self, arr: np.ndarray) -> None:
        now = time.monotonic()
        rms = float(np.sqrt(np.mean(np.square(arr, dtype=np.float64)))) if arr.size else 0.0
        with self._stats_lock:
            self._last_frame_at = now
            self._last_rms = rms
            self._total_frames += 1
            if rms >= 0.002:
                self._last_non_silent_at = now
                self._non_silent_frames += 1

    def _prepare_frame(self, frame: np.ndarray) -> np.ndarray:
        audio = np.asarray(frame)
        if audio.ndim == 2 and audio.shape[1] > 1:
            audio = self._downmix_desktop_channels(audio)
        prepared = super()._prepare_frame(audio)
        prepared_rms = (
            float(np.sqrt(np.mean(np.square(prepared, dtype=np.float64))))
            if prepared.size
            else 0.0
        )
        with self._stats_lock:
            self._last_prepared_rms = prepared_rms
        return prepared

    def _downmix_desktop_channels(self, audio: np.ndarray) -> np.ndarray:
        """Downmix loopback audio without cancelling virtual-device channels."""
        channels = np.asarray(audio, dtype=np.float32)
        if channels.ndim != 2 or channels.shape[1] <= 1:
            return channels.reshape(-1).astype(np.float32, copy=False)

        mono = channels.mean(axis=1)
        channel_rms = np.sqrt(np.mean(np.square(channels, dtype=np.float64), axis=0))
        if channel_rms.size == 0:
            return mono.astype(np.float32, copy=False)
        strongest_index = int(np.argmax(channel_rms))
        strongest_rms = float(channel_rms[strongest_index])
        if strongest_rms <= 1e-8:
            return mono.astype(np.float32, copy=False)

        mono_rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        if mono_rms >= strongest_rms * 0.75:
            return mono.astype(np.float32, copy=False)

        now = time.monotonic()
        if now - self._last_downmix_log_at >= 10.0:
            self._last_downmix_log_at = now
            logger.info(
                "Desktop loopback downmix using strongest channel to avoid phase cancellation "
                "(mono_rms=%.5f strongest_rms=%.5f channels=%s)",
                mono_rms,
                strongest_rms,
                channels.shape[1],
            )
        return channels[:, strongest_index].astype(np.float32, copy=False)

    # ------------------------------------------------------------------
    # Capture loop — runs in a dedicated daemon thread
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        try:
            self._capture_loop_soundcard()
        except _SoundCardFallbackRequested as exc:
            logger.warning(
                "SoundCard capture unavailable; falling back to PyAudioWPatch: %s",
                exc,
            )
            self._running = True
            self._capture_start_error = None
            self._capture_loop_pyaudio()

    def _capture_loop_soundcard(self) -> None:
        ready_event = self._capture_ready_event
        runtime_error: BaseException | None = None
        startup_completed = False
        fallback_requested = False
        try:
            logger.info(
                "DesktopAudioRecorder SoundCard capture initializing (requested_output=%s)",
                self._output_device_name or "auto",
            )
            sc = _import_soundcard()
            loopback_device = self._resolve_loopback_device_info(
                _enumerate_soundcard_loopback_devices(sc)
            )
            mic = loopback_device.get("_soundcard_device")
            if mic is None:
                raise RuntimeError("Selected SoundCard loopback device is unavailable")

            native_rate = int(round(float(loopback_device.get("defaultSampleRate", 48000))))
            max_channels = max(
                min(
                    int(loopback_device.get("maxInputChannels", 2) or 2),
                    _MAX_DESKTOP_CAPTURE_CHANNELS,
                ),
                1,
            )
            last_error: BaseException | None = None

            for rate, channels in self._candidate_stream_configs(native_rate, max_channels):
                blocksize = max(int(rate * self.frame_duration_ms / 1000), 1)
                stream_config = {
                    "rate": int(rate),
                    "channels": int(channels),
                    "blocksize": int(blocksize),
                }
                try:
                    with mic.recorder(
                        samplerate=int(rate),
                        channels=int(channels),
                        blocksize=int(blocksize),
                    ) as recorder:
                        self._loopback_device_info = loopback_device
                        self._stream_config = stream_config
                        self._capture_stream = recorder
                        self.input_device = int(loopback_device["index"])
                        self.extra_settings = None
                        self._active_device_name = str(loopback_device.get("name", "")).strip() or None
                        self._last_error = None
                        self._capture_rate = int(rate)
                        self._capture_channels = int(channels)
                        self._capture_dtype = "float32"
                        startup_completed = True
                        logger.info(
                            "Selected desktop capture config (backend=soundcard device=%s rate=%s channels=%s blocksize=%s)",
                            loopback_device.get("name"),
                            rate,
                            channels,
                            blocksize,
                        )
                        if ready_event is not None:
                            ready_event.set()

                        while self._running:
                            data = recorder.record(numframes=blocksize)
                            if not self._running:
                                break
                            arr = np.asarray(data, dtype=np.float32)
                            if arr.size == 0:
                                continue
                            if arr.ndim == 1:
                                frame = arr
                                stats_arr = arr
                            else:
                                if arr.shape[1] > channels:
                                    arr = arr[:, :channels]
                                frame = arr
                                stats_arr = arr.reshape(-1)
                            self._update_capture_stats(stats_arr)
                            self._enqueue_frame(frame.copy())
                        return
                except Exception as exc:
                    if startup_completed:
                        if self._running:
                            raise _SoundCardFallbackRequested(str(exc)) from exc
                        break
                    last_error = exc
                    logger.debug(
                        "SoundCard desktop capture open attempt failed (device=%s rate=%s channels=%s): %s",
                        loopback_device.get("name"),
                        rate,
                        channels,
                        exc,
                    )

            if not startup_completed:
                if last_error is None:
                    last_error = RuntimeError("No compatible SoundCard desktop audio capture configuration was found")
                raise last_error
        except Exception as exc:
            if not self._running and startup_completed:
                runtime_error = None
            else:
                fallback_requested = True
                raise _SoundCardFallbackRequested(str(exc)) from exc
        finally:
            self._capture_stream = None
            if not fallback_requested:
                self._running = False
                self._enqueue_frame(None)
                if ready_event is not None and not startup_completed:
                    ready_event.set()
                if runtime_error is not None:
                    self._report_runtime_error(runtime_error)

    def _capture_loop_pyaudio(self) -> None:
        ready_event = self._capture_ready_event
        callback_errors: list[BaseException] = []
        callback_error_lock = threading.Lock()
        runtime_error: BaseException | None = None
        startup_completed = False
        # Acquire the module-level lock for the entire PyAudio lifetime so that
        # list_output_devices() (which also uses PyAudio) can detect the conflict
        # and skip enumeration rather than calling Pa_Terminate() while a stream
        # is active (which would crash PortAudio at the C level).
        _pyaudio_lock.acquire()
        try:
            logger.info(
                "DesktopAudioRecorder capture thread initializing (requested_output=%s)",
                self._output_device_name or "auto",
            )
            pa = _import_pyaudio()
            p = pa.PyAudio()
            self._pyaudio_module = pa
            self._pyaudio_instance = p
            logger.info("DesktopAudioRecorder enumerating WASAPI loopback devices")
            loopback_device = self._resolve_loopback_device_info(
                _enumerate_pyaudio_loopback_devices(p, pa)
            )
            stream_config = self._select_stream_config(p, pa, loopback_device)
            channels = int(stream_config["channels"])

            def _stream_callback(in_data, frame_count, time_info, status):
                del frame_count, time_info
                if not self._running:
                    return (None, getattr(pa, "paComplete", 1))
                try:
                    if status:
                        logger.debug("Desktop audio callback status: %s", status)
                    if in_data:
                        arr = np.frombuffer(in_data, dtype=np.float32)
                        if arr.size and (channels <= 1 or arr.size % channels == 0):
                            self._update_capture_stats(arr)
                            frame = arr.reshape(-1, channels) if channels > 1 else arr
                            self._enqueue_frame(frame.copy())
                except Exception as exc:
                    with callback_error_lock:
                        callback_errors.append(exc)
                    return (None, getattr(pa, "paComplete", 1))
                return (None, getattr(pa, "paContinue", 0))

            stream = self._open_stream_with_config(
                p,
                pa,
                loopback_device,
                stream_config,
                stream_callback=_stream_callback,
            )

            self._loopback_device_info = loopback_device
            self._stream_config = stream_config
            self._capture_stream = stream
            self.input_device = int(loopback_device["index"])
            self.extra_settings = None
            self._active_device_name = str(loopback_device.get("name", "")).strip() or None
            self._last_error = None
            self._capture_rate = int(stream_config["rate"])
            self._capture_channels = int(stream_config["channels"])
            self._capture_dtype = "float32"
            startup_completed = True
            if ready_event is not None:
                ready_event.set()

            while self._running:
                with callback_error_lock:
                    if callback_errors:
                        runtime_error = callback_errors[0]
                        break
                stream = self._capture_stream
                if stream is None:
                    break
                try:
                    if hasattr(stream, "is_active") and not stream.is_active():
                        if self._running:
                            runtime_error = RuntimeError("Desktop audio stream stopped")
                        break
                except Exception:
                    pass
                time.sleep(0.05)
        except Exception as exc:
            runtime_error = exc
            if not startup_completed:
                self._capture_start_error = exc
        finally:
            self._running = False
            self._close_capture_stream()
            self._terminate_pyaudio()
            self._enqueue_frame(None)
            if ready_event is not None and not startup_completed:
                ready_event.set()
            if runtime_error is not None:
                self._report_runtime_error(runtime_error)
            _pyaudio_lock.release()

    def _select_stream_config(self, p, pa, device_info: dict) -> dict[str, int]:
        device_index = int(device_info["index"])
        native_rate = int(round(float(device_info.get("defaultSampleRate", 48000))))
        max_channels = max(
            min(int(device_info.get("maxInputChannels", 2) or 2), _MAX_DESKTOP_CAPTURE_CHANNELS),
            1,
        )
        last_error: BaseException | None = None

        for rate, channels in self._candidate_stream_configs(native_rate, max_channels):
            blocksize = max(int(rate * self.frame_duration_ms / 1000), 1)
            try:
                self._validate_stream_config(
                    p,
                    pa,
                    device_index=device_index,
                    rate=rate,
                    channels=channels,
                    blocksize=blocksize,
                )
                logger.info(
                    "Selected desktop capture config (device=%s rate=%s channels=%s blocksize=%s)",
                    device_info.get("name"),
                    rate,
                    channels,
                    blocksize,
                )
                return {
                    "rate": int(rate),
                    "channels": int(channels),
                    "blocksize": int(blocksize),
                }
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "Desktop capture config failed (device=%s rate=%s channels=%s): %s",
                    device_info.get("name"),
                    rate,
                    channels,
                    exc,
                )

        if last_error is None:
            last_error = RuntimeError("No compatible desktop audio capture configuration was found")
        raise last_error

    def _candidate_stream_configs(
        self,
        native_rate: int,
        max_channels: int,
    ) -> list[tuple[int, int]]:
        rate_candidates: list[int] = []
        for candidate in (native_rate, *_COMMON_DESKTOP_CAPTURE_RATES, self.sample_rate):
            rate = int(candidate)
            if rate > 0 and rate not in rate_candidates:
                rate_candidates.append(rate)

        max_channels = max(min(int(max_channels), _MAX_DESKTOP_CAPTURE_CHANNELS), 1)
        channel_candidates: list[int] = []
        for candidate in (max_channels, 2, 1):
            channels = max(min(int(candidate), max_channels), 1)
            if channels not in channel_candidates:
                channel_candidates.append(channels)

        return [(rate, channels) for rate in rate_candidates for channels in channel_candidates]

    @staticmethod
    def _validate_stream_config(
        p,
        pa,
        *,
        device_index: int,
        rate: int,
        channels: int,
        blocksize: int,
    ) -> None:
        del blocksize
        try:
            supported = p.is_format_supported(
                rate,
                input_device=device_index,
                input_channels=channels,
                input_format=pa.paFloat32,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if supported is False:
            raise RuntimeError("Desktop audio stream format is not supported")

    # ------------------------------------------------------------------
    # Loopback device resolution
    # ------------------------------------------------------------------

    def _resolve_loopback_device_info(
        self,
        output_devices: list[dict[str, object]] | None = None,
    ) -> dict:
        """Find the WASAPI loopback device for the selected output device."""
        if output_devices is None:
            output_devices = list_output_devices()
        if not output_devices:
            raise RuntimeError(
                "No WASAPI loopback devices found. "
                "Ensure you are on Windows with an active WASAPI output device."
            )

        preferred_names: list[str] = []
        if self._output_device_name:
            preferred_names.append(self._output_device_name)
        sd_default = _default_output_device_name_from_sounddevice()
        if sd_default and sd_default not in preferred_names:
            preferred_names.append(sd_default)

        # Also add the actual default device from the device list
        default_from_list = next((d.get("name") for d in output_devices if d.get("is_default")), None)
        if default_from_list and default_from_list not in preferred_names:
            preferred_names.append(default_from_list)

        logger.info(
            "Resolving loopback device (requested_output=%s preferred_names=%s)",
            self._output_device_name or None,
            preferred_names,
        )

        # Use _fuzzy_match_loopback for all matching so that exact full-name
        # matches (Pass 0) take priority over generic prefix collisions.
        for pref in preferred_names:
            if not pref:
                continue
            matched = _fuzzy_match_loopback(pref, output_devices)
            if matched is not None:
                logger.info("Matched loopback device: %s", matched.get("name"))
                return matched

        # Better fallback: Try to find default device first, then any device
        default_device = next((device for device in output_devices if device.get("is_default")), None)
        if default_device:
            logger.info("Using default loopback device: %s", default_device.get("name"))
            return default_device

        if output_devices:
            logger.warning("No default device found, using first available: %s", output_devices[0].get("name"))
            return output_devices[0]

        logger.error("No loopback devices available at all!")
        return None


# ---------------------------------------------------------------------------
# Sounddevice helpers (mic path still uses sounddevice — keep these)
# ---------------------------------------------------------------------------

def _default_output_device_name_from_sounddevice() -> str:
    try:
        return str(
            inventory_default_output_device_name(
                force_refresh=False,
                sounddevice_module=sd,
            )
            or ""
        ).strip()
    except Exception:
        try:
            default_out = sd.default.device[1]
            if default_out is None or int(default_out) < 0:
                return ""
            return str(sd.query_devices(int(default_out))["name"]).strip()
        except Exception:
            return ""
