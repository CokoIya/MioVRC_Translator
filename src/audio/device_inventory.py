from __future__ import annotations

import logging
import re
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable

import sounddevice as sd

logger = logging.getLogger(__name__)


_HOST_API_PRIORITY = {
    "windows wasapi": 0,
    "windows directsound": 1,
    "mme": 2,
    "windows wdm-ks": 3,
}
_CACHE_MAX_AGE_S = 0.75
_EMPTY_SCAN_CACHE_MAX_AGE_S = 30.0
_MIXLINE_CAPTURE_NAME_TOKENS = ("mixline", "mix line")

_PSEUDO_DEVICE_NAMES = {
    "microsoft sound mapper - input",
    "microsoft sound mapper - output",
    "primary sound capture driver",
    "primary sound driver",
    "primary capture driver",
    "primary playback driver",
    "microsoft サウンド マッパー - input",
    "microsoft サウンド マッパー - output",
    "プライマリ サウンド キャプチャ ドライバー",
    "プライマリ サウンド ドライバー",
    "microsoft 声音映射器 - input",
    "microsoft 声音映射器 - output",
    "主声音捕获驱动程序",
    "主声音驱动程序",
    "主要音效擷取驅動程式",
    "主要音效驅動程式",
    "기본 사운드 캡처 드라이버",
    "기본 사운드 드라이버",
}


def normalize_device_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s*\[loopback\]\s*$", "", text, flags=re.IGNORECASE)
    return " ".join(text.casefold().split()).strip()


def device_names_match(left: object, right: object) -> bool:
    """Conservatively match the same endpoint across PortAudio backends.

    Exact normalized names are preferred. Prefix matching is limited to long
    names because MME truncates names, while generic labels such as "Speakers"
    must never match every physical output endpoint.
    """

    left_norm = normalize_device_name(left)
    right_norm = normalize_device_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) < 18:
        return False
    if not longer.startswith(shorter):
        return False
    if "(" in shorter and not shorter.endswith(")"):
        return True
    return len(shorter) / max(len(longer), 1) >= 0.72


def _parenthesized_device_identities(value: object) -> frozenset[str]:
    """Extract stable hardware labels from localized endpoint names."""

    normalized = normalize_device_name(value)
    if not normalized:
        return frozenset()
    identities: set[str] = set()
    starts: list[int] = []
    for index, character in enumerate(normalized):
        if character == "(":
            starts.append(index)
        elif character == ")" and starts:
            start = starts.pop()
            identity = " ".join(normalized[start + 1 : index].split()).strip(" -_:;")
            if len(identity) >= 4 and any(character.isalnum() for character in identity):
                identities.add(identity)
    return frozenset(identities)


def input_device_names_match(left: object, right: object) -> bool:
    """Match capture endpoints even when PortAudio mangles the localized prefix.

    Windows CoreAudio may report ``Microphone (Hardware Name)`` while an MME or
    frozen PortAudio build returns a mojibake prefix with the same parenthesized
    hardware identity.  This fallback is capture-only and callers still require
    a unique match, so similarly named physical endpoints remain ambiguous.
    """

    if device_names_match(left, right):
        return True
    return bool(
        _parenthesized_device_identities(left)
        & _parenthesized_device_identities(right)
    )


def _is_mixline_capture_endpoint(value: object) -> bool:
    normalized = normalize_device_name(value)
    return any(token in normalized for token in _MIXLINE_CAPTURE_NAME_TOKENS)


def unique_device_name_match(
    target: object,
    candidates: Iterable[object],
) -> str | None:
    clean = str(target or "").strip()
    if not clean:
        return None
    values = [str(candidate or "").strip() for candidate in candidates]
    values = [candidate for candidate in values if candidate]
    normalized = normalize_device_name(clean)
    exact = [candidate for candidate in values if normalize_device_name(candidate) == normalized]
    if len(exact) == 1:
        return exact[0]
    matches = [candidate for candidate in values if device_names_match(clean, candidate)]
    return matches[0] if len(matches) == 1 else None


def unique_input_device_name_match(
    target: object,
    candidates: Iterable[object],
) -> str | None:
    clean = str(target or "").strip()
    if not clean:
        return None
    values = [str(candidate or "").strip() for candidate in candidates]
    values = [candidate for candidate in values if candidate]
    normalized = normalize_device_name(clean)
    exact = [candidate for candidate in values if normalize_device_name(candidate) == normalized]
    if len(exact) == 1:
        return exact[0]
    matches = [
        candidate for candidate in values if input_device_names_match(clean, candidate)
    ]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class AudioEndpoint:
    index: int
    name: str
    hostapi: str
    hostapi_index: int
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    is_default_input: bool = False
    is_default_output: bool = False

    @property
    def hostapi_priority(self) -> int:
        return _HOST_API_PRIORITY.get(self.hostapi.casefold(), 50)


@dataclass(frozen=True, slots=True)
class DeviceInventorySnapshot:
    inputs: tuple[AudioEndpoint, ...]
    outputs: tuple[AudioEndpoint, ...]
    default_input_index: int | None
    default_output_index: int | None
    scanned_at: float
    from_cache: bool
    diagnostics: dict[str, Any]

    @property
    def default_input(self) -> AudioEndpoint | None:
        return next(
            (item for item in self.inputs if item.index == self.default_input_index),
            None,
        )

    @property
    def default_output(self) -> AudioEndpoint | None:
        return next(
            (item for item in self.outputs if item.index == self.default_output_index),
            None,
        )


_cache_lock = threading.RLock()
_scan_lock = threading.Lock()
_last_snapshot: DeviceInventorySnapshot | None = None
_last_cache_token: tuple[Any, ...] | None = None


def _query_token(sounddevice_module: Any) -> tuple[Any, ...]:
    try:
        default_pair = tuple(sounddevice_module.default.device)
    except Exception:
        default_pair = ()
    return (
        sounddevice_module,
        getattr(sounddevice_module, "query_devices", None),
        getattr(sounddevice_module, "query_hostapis", None),
        default_pair,
    )


def _hostapi_name(hostapis: list[Any], index: int) -> str:
    try:
        return str(hostapis[index].get("name", "") or "").strip()
    except Exception:
        return ""


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_pseudo_or_malformed(name: str, hostapi: str) -> bool:
    normalized = normalize_device_name(name)
    if not normalized:
        return True
    if normalized in _PSEUDO_DEVICE_NAMES:
        return True
    if re.search(r"\(\s*\)\s*$", normalized):
        return True
    if normalized in {"input", "output", "input device", "output device"}:
        return True
    # This also catches localized/mis-decoded MME Sound Mapper labels while
    # retaining real devices whose product names happen to contain Microsoft.
    if (
        hostapi.casefold() == "mme"
        and normalized.startswith("microsoft ")
        and re.search(r" - (?:input|output)$", normalized)
    ):
        return True
    return False


def _windows_endpoint_diagnostics() -> tuple[list[dict[str, Any]], list[str]]:
    if sys.platform != "win32":
        return [], []
    try:
        from .windows_audio import list_audio_endpoints

        endpoints = list_audio_endpoints(include_inactive=True)
        return list(endpoints), []
    except Exception as exc:
        return [], [f"CoreAudio endpoint enumeration failed: {exc}"]


def _endpoint_is_known_inactive(
    endpoint: AudioEndpoint,
    windows_endpoints: list[dict[str, Any]],
    *,
    flow: str,
) -> bool:
    if not windows_endpoints:
        return False
    target = normalize_device_name(endpoint.name)
    if not target:
        return False
    matching = [
        item
        for item in windows_endpoints
        if str(item.get("flow", "")) == flow
        and normalize_device_name(item.get("name")) == target
    ]
    return bool(matching) and not any(bool(item.get("active")) for item in matching)


def _deduplicate(endpoints: list[AudioEndpoint]) -> list[AudioEndpoint]:
    selected: list[AudioEndpoint] = []
    for endpoint in sorted(
        endpoints,
        key=lambda item: (item.hostapi_priority, item.index),
    ):
        matches = [
            index
            for index, current in enumerate(selected)
            if device_names_match(endpoint.name, current.name)
        ]
        if len(matches) == 1:
            # The preferred backend is already present because candidates are
            # sorted WASAPI -> DirectSound -> MME -> WDM-KS.
            continue
        selected.append(endpoint)
    return selected


def _raw_default_pair(sounddevice_module: Any) -> tuple[int | None, int | None]:
    try:
        default_devices = sounddevice_module.default.device
        input_index = _safe_int(default_devices[0], -1)
        output_index = _safe_int(default_devices[1], -1)
        return (
            input_index if input_index >= 0 else None,
            output_index if output_index >= 0 else None,
        )
    except Exception:
        return None, None


def _resolve_default_endpoint(
    *,
    flow: str,
    selected: list[AudioEndpoint],
    raw_by_index: dict[int, AudioEndpoint],
    raw_default_index: int | None,
    hostapis: list[Any],
    windows_endpoints: list[dict[str, Any]],
) -> AudioEndpoint | None:
    channel_attr = "max_input_channels" if flow == "capture" else "max_output_channels"

    def usable_raw(index: int | None) -> AudioEndpoint | None:
        if index is None:
            return None
        endpoint = raw_by_index.get(index)
        if endpoint is None or getattr(endpoint, channel_attr) <= 0:
            return None
        return endpoint

    def preferred_for(endpoint: AudioEndpoint | None) -> AudioEndpoint | None:
        if endpoint is None:
            return None
        exact = [
            item
            for item in selected
            if normalize_device_name(item.name) == normalize_device_name(endpoint.name)
        ]
        if len(exact) == 1:
            return exact[0]
        name_matcher = (
            input_device_names_match if flow == "capture" else device_names_match
        )
        matches = [item for item in selected if name_matcher(item.name, endpoint.name)]
        return matches[0] if len(matches) == 1 else None

    def selected_for_name(name: object) -> AudioEndpoint | None:
        matcher = (
            unique_input_device_name_match
            if flow == "capture"
            else unique_device_name_match
        )
        matched_name = matcher(name, (item.name for item in selected))
        if not matched_name:
            return None
        return next(item for item in selected if item.name == matched_name)

    core_defaults = [
        item
        for item in windows_endpoints
        if str(item.get("flow", "")) == flow
        and bool(item.get("active"))
        and bool(item.get("is_default"))
    ]
    role_priority = (
        {"console": 0, "communications": 1, "multimedia": 2}
        if flow == "capture"
        else {"multimedia": 0, "console": 1, "communications": 2}
    )
    core_defaults.sort(
        key=lambda item: min(
            (
                role_priority.get(str(role), 50)
                for role in item.get("default_roles", [])
            ),
            default=50,
        )
    )
    if flow == "capture":
        externally_active: list[AudioEndpoint] = []
        for item in windows_endpoints:
            if (
                str(item.get("flow", "")) != "capture"
                or not bool(item.get("active"))
                or not bool(item.get("has_external_active_session"))
                or _is_mixline_capture_endpoint(item.get("name"))
            ):
                continue
            matched = selected_for_name(item.get("name"))
            if matched is not None and matched not in externally_active:
                externally_active.append(matched)
        if len(externally_active) == 1:
            return externally_active[0]
        if len(externally_active) > 1:
            for item in core_defaults:
                matched = selected_for_name(item.get("name"))
                if matched in externally_active:
                    return matched

    for default_item in core_defaults:
        matched = selected_for_name(default_item.get("name"))
        if matched is not None:
            return matched

    resolved = preferred_for(usable_raw(raw_default_index))
    if resolved is not None:
        return resolved

    default_key = "default_input_device" if flow == "capture" else "default_output_device"
    ranked_hostapis = sorted(
        enumerate(hostapis),
        key=lambda item: (
            _HOST_API_PRIORITY.get(
                str(item[1].get("name", "") or "").strip().casefold(),
                50,
            ),
            item[0],
        ),
    )
    for _hostapi_index, hostapi in ranked_hostapis:
        resolved = preferred_for(usable_raw(_safe_int(hostapi.get(default_key), -1)))
        if resolved is not None:
            return resolved

    return selected[0] if selected else None


def _scan(sounddevice_module: Any) -> DeviceInventorySnapshot:
    started = time.monotonic()
    errors: list[str] = []
    try:
        raw_devices = list(sounddevice_module.query_devices())
    except Exception as exc:
        raise RuntimeError(f"sounddevice device query failed: {exc}") from exc
    if not raw_devices:
        raise RuntimeError("sounddevice returned an empty device table")

    try:
        hostapis = list(sounddevice_module.query_hostapis())
    except Exception as exc:
        hostapis = []
        errors.append(f"sounddevice host API query failed: {exc}")

    windows_endpoints: list[dict[str, Any]] = []
    if sounddevice_module is sd:
        windows_endpoints, windows_errors = _windows_endpoint_diagnostics()
        errors.extend(windows_errors)

    raw_endpoints: list[AudioEndpoint] = []
    filtered: list[dict[str, Any]] = []
    malformed_count = 0
    for index, raw in enumerate(raw_devices):
        try:
            name = str(raw.get("name", "") or "").strip()
            hostapi_index = _safe_int(raw.get("hostapi"), -1)
            hostapi = _hostapi_name(hostapis, hostapi_index)
            endpoint = AudioEndpoint(
                index=index,
                name=name,
                hostapi=hostapi,
                hostapi_index=hostapi_index,
                max_input_channels=max(_safe_int(raw.get("max_input_channels"), 0), 0),
                max_output_channels=max(_safe_int(raw.get("max_output_channels"), 0), 0),
                default_samplerate=max(_safe_float(raw.get("default_samplerate"), 0.0), 0.0),
            )
        except Exception:
            malformed_count += 1
            continue
        if endpoint.max_input_channels <= 0 and endpoint.max_output_channels <= 0:
            malformed_count += 1
            continue
        if _is_pseudo_or_malformed(endpoint.name, endpoint.hostapi):
            filtered.append(
                {"index": endpoint.index, "name": endpoint.name, "reason": "pseudo_or_malformed"}
            )
            continue
        raw_endpoints.append(endpoint)

    input_candidates = [
        item
        for item in raw_endpoints
        if item.max_input_channels > 0
        and not _endpoint_is_known_inactive(item, windows_endpoints, flow="capture")
    ]
    output_candidates = [
        item
        for item in raw_endpoints
        if item.max_output_channels > 0
        and not _endpoint_is_known_inactive(item, windows_endpoints, flow="render")
    ]
    inputs = _deduplicate(input_candidates)
    outputs = _deduplicate(output_candidates)

    raw_by_index = {item.index: item for item in raw_endpoints}
    raw_default_input, raw_default_output = _raw_default_pair(sounddevice_module)
    default_input = _resolve_default_endpoint(
        flow="capture",
        selected=inputs,
        raw_by_index=raw_by_index,
        raw_default_index=raw_default_input,
        hostapis=hostapis,
        windows_endpoints=windows_endpoints,
    )
    default_output = _resolve_default_endpoint(
        flow="render",
        selected=outputs,
        raw_by_index=raw_by_index,
        raw_default_index=raw_default_output,
        hostapis=hostapis,
        windows_endpoints=windows_endpoints,
    )
    default_input_index = default_input.index if default_input is not None else None
    default_output_index = default_output.index if default_output is not None else None
    inputs = [
        replace(item, is_default_input=item.index == default_input_index)
        for item in inputs
    ]
    outputs = [
        replace(item, is_default_output=item.index == default_output_index)
        for item in outputs
    ]
    inputs.sort(key=lambda item: (not item.is_default_input, item.hostapi_priority, item.name.casefold()))
    outputs.sort(key=lambda item: (not item.is_default_output, item.hostapi_priority, item.name.casefold()))

    inactive_windows = [item for item in windows_endpoints if not bool(item.get("active"))]
    diagnostics = {
        "backend": "sounddevice",
        "frozen_runtime": bool(getattr(sys, "frozen", False)),
        "raw_device_count": len(raw_devices),
        "input_count": len(inputs),
        "output_count": len(outputs),
        "filtered_count": len(filtered) + malformed_count,
        "filtered": filtered,
        "malformed_count": malformed_count,
        "default_input_index_raw": raw_default_input,
        "default_output_index_raw": raw_default_output,
        "default_input_index": default_input_index,
        "default_output_index": default_output_index,
        "default_input_name": default_input.name if default_input else None,
        "default_output_name": default_output.name if default_output else None,
        "hostapis": [str(item.get("name", "") or "") for item in hostapis],
        "windows_endpoint_count": len(windows_endpoints),
        "windows_inactive_endpoint_count": len(inactive_windows),
        "windows_inactive_endpoints": inactive_windows,
        "windows_active_capture_sessions": [
            {
                "name": item.get("name"),
                "is_default": bool(item.get("is_default")),
                "default_roles": list(item.get("default_roles", [])),
                "has_external_active_session": bool(
                    item.get("has_external_active_session")
                ),
                "active_session_process_ids": list(
                    item.get("active_session_process_ids", [])
                ),
            }
            for item in windows_endpoints
            if str(item.get("flow", "")) == "capture"
            and bool(item.get("has_active_session"))
        ],
        "errors": errors,
        "scan_duration_ms": round((time.monotonic() - started) * 1000.0, 2),
    }
    return DeviceInventorySnapshot(
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        default_input_index=default_input_index,
        default_output_index=default_output_index,
        scanned_at=time.monotonic(),
        from_cache=False,
        diagnostics=diagnostics,
    )


def get_device_inventory(
    *,
    force_refresh: bool = False,
    allow_cached: bool = True,
    sounddevice_module: Any = None,
) -> DeviceInventorySnapshot:
    global _last_snapshot, _last_cache_token

    module = sounddevice_module or sd
    token = _query_token(module)
    shared_cache = module is sd
    now = time.monotonic()

    if shared_cache and allow_cached and not force_refresh:
        with _cache_lock:
            if (
                _last_snapshot is not None
                and _last_cache_token == token
                and now - _last_snapshot.scanned_at <= _CACHE_MAX_AGE_S
            ):
                return _last_snapshot

    try:
        with _scan_lock:
            snapshot = _scan(module)
    except Exception as exc:
        if shared_cache and allow_cached:
            with _cache_lock:
                cached = _last_snapshot
                cache_token = _last_cache_token
            if (
                cached is not None
                and cache_token == token
                and now - cached.scanned_at <= _EMPTY_SCAN_CACHE_MAX_AGE_S
            ):
                diagnostics = dict(cached.diagnostics)
                diagnostics["cache_reason"] = str(exc)
                diagnostics["cache_age_s"] = round(now - cached.scanned_at, 3)
                diagnostics["errors"] = [*diagnostics.get("errors", []), str(exc)]
                logger.warning(
                    "Audio device scan failed; using last-good inventory age=%.1fs: %s",
                    now - cached.scanned_at,
                    exc,
                )
                return replace(cached, from_cache=True, diagnostics=diagnostics)
        logger.warning("Audio device enumeration failed: %s", exc)
        return DeviceInventorySnapshot(
            inputs=(),
            outputs=(),
            default_input_index=None,
            default_output_index=None,
            scanned_at=now,
            from_cache=False,
            diagnostics={
                "backend": "sounddevice",
                "frozen_runtime": bool(getattr(sys, "frozen", False)),
                "raw_device_count": 0,
                "input_count": 0,
                "output_count": 0,
                "errors": [str(exc)],
            },
        )

    if shared_cache:
        with _cache_lock:
            _last_snapshot = snapshot
            _last_cache_token = token
    return snapshot


def list_input_devices(
    *,
    force_refresh: bool = True,
    sounddevice_module: Any = None,
) -> list[dict[str, Any]]:
    snapshot = get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    )
    return [
        {
            "index": item.index,
            "name": item.name,
            "hostapi": item.hostapi,
            "is_default": item.is_default_input,
        }
        for item in snapshot.inputs
    ]


def list_output_devices(
    *,
    force_refresh: bool = False,
    sounddevice_module: Any = None,
) -> list[dict[str, Any]]:
    snapshot = get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    )
    return [
        {
            "index": item.index,
            "name": item.name,
            "hostapi": item.hostapi,
            "is_default": item.is_default_output,
        }
        for item in snapshot.outputs
    ]


def default_input_device_index(
    *,
    force_refresh: bool = False,
    sounddevice_module: Any = None,
) -> int | None:
    return get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    ).default_input_index


def default_output_device_index(
    *,
    force_refresh: bool = False,
    sounddevice_module: Any = None,
) -> int | None:
    return get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    ).default_output_index


def default_input_device_name(
    *,
    force_refresh: bool = False,
    sounddevice_module: Any = None,
) -> str | None:
    endpoint = get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    ).default_input
    return endpoint.name if endpoint is not None else None


def default_output_device_name(
    *,
    force_refresh: bool = False,
    sounddevice_module: Any = None,
) -> str | None:
    endpoint = get_device_inventory(
        force_refresh=force_refresh,
        sounddevice_module=sounddevice_module,
    ).default_output
    return endpoint.name if endpoint is not None else None


def device_inventory_diagnostics(
    *,
    force_refresh: bool = False,
    include_inactive_endpoints: bool = False,
) -> dict[str, Any]:
    snapshot = get_device_inventory(force_refresh=force_refresh)
    result = dict(snapshot.diagnostics)
    inactive = list(result.get("windows_inactive_endpoints", []))
    if not include_inactive_endpoints:
        result.pop("windows_inactive_endpoints", None)
        result["windows_inactive_endpoint_samples"] = [
            {
                "name": item.get("name"),
                "flow": item.get("flow"),
                "state_name": item.get("state_name"),
            }
            for item in inactive[:8]
        ]
    result["from_cache"] = snapshot.from_cache
    result["inventory_age_s"] = round(max(0.0, time.monotonic() - snapshot.scanned_at), 3)
    return result


def _reset_device_inventory_cache_for_tests() -> None:
    global _last_snapshot, _last_cache_token
    with _cache_lock:
        _last_snapshot = None
        _last_cache_token = None
