import base64
import copy
import ctypes
import json
import os
import shutil
import sys
import threading
import time
import logging
from pathlib import Path
from ctypes import wintypes

from src.utils.app_paths import resource_base_dirs, writable_app_dir
from src.utils.ui_language_detection import bootstrap_ui_language
from src.utils.locale_detect import select_default_asr_engine
from src.utils.global_hotkey import (
    DEFAULT_TEXT_INPUT_HOTKEY,
    HotkeyError,
    normalize_hotkey,
)
from src.utils.ui_config import (
    DEFAULT_ASR_ENGINE,
    SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES,
    SUPPORTED_TARGET_LANGUAGE_CODES,
    TRANSLATION_BACKENDS,
    default_backend_for_ui_language,
    get_backend_value,
)
from src.asr.model_registry import (
    ASR_ENGINE_SPECS,
    ASR_ENGINE_FOLLOW_MAIN,
    LISTEN_SELECTABLE_ASR_ENGINES,
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_DEFAULT_REGION,
    QWEN3_ASR_LEGACY_MODEL_IDS,
    QWEN3_ASR_REGION_BASE_URLS,
    get_asr_engine_spec,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)

_SAVE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
_PROTECTED_SECRET_PREFIX = "dpapi:v1:"
_DEFAULT_DENOISE_STRENGTH = 0.0
_DEFAULT_MIC_TAIL_SILENCE_S = 0.65
_DEFAULT_LISTEN_TAIL_SILENCE_S = 0.65
_DEFAULT_VAD_SPEECH_RATIO = 0.6
_DEFAULT_VAD_ACTIVATION_THRESHOLD_S = 0.2
_DEFAULT_OPENAI_MODEL = str(
    TRANSLATION_BACKENDS.get("openai", {}).get("model", "gpt-5.5")
)
_DEFAULT_ANTHROPIC_MODEL = str(
    TRANSLATION_BACKENDS.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")
)
_APP_MODE_VALUES = {"translation", "simultaneous"}
_SIMUL_MODE_DEFAULTS = {
    "tts_backend": "edge",
    "tts_strategy": "queue",
    "vad_silence_ms": 300,
    "show_subtitle": True,
    "subtitle_position": "bottom",
    "aggressive_chunking": False,
    "merge_window_ms": 800,
}
_LEGACY_OPENAI_MODEL_PREFIXES = (
    "gpt-3.5",
    "gpt-4-",
    "gpt-4o",
)
_LEGACY_OPENAI_MODEL_IDS = {
    "gpt-4",
    "gpt-5.4-pro",
}
_LEGACY_ANTHROPIC_MODEL_IDS = {
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
}
_ASR_CONFIG_KEYS = frozenset(
    {
        "auto_fallback",
        "correction",
        "device",
        "engine",
        "engine_source",
        "fallback_engine",
        "gemini_live",
        "language",
        "qwen3_asr",
        "sensevoice",
        "streaming",
        "user_selected_engine",
        "webspeech",
    }
)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.c_void_p),
    ]


def _can_protect_secrets() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(raw: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    blob_in = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(raw: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    blob_in = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _protect_secret(value: object) -> str:
    text = str(value or "")
    if not text or text.startswith(_PROTECTED_SECRET_PREFIX):
        return text
    if not _can_protect_secrets():
        return text
    try:
        sealed = _dpapi_protect(text.encode("utf-8"))
    except Exception as exc:
        logger.error(
            "DPAPI sealing failed; secret will be written without OS-level "
            "protection. Cause: %s",
            exc,
        )
        return text
    return _PROTECTED_SECRET_PREFIX + base64.b64encode(sealed).decode("ascii")


def _unprotect_secret(value: object) -> str:
    text = str(value or "")
    if not text.startswith(_PROTECTED_SECRET_PREFIX):
        return text
    if not _can_protect_secrets():
        # Preserve ciphertext so a round-trip save does not erase the user's
        # stored secret on a host where DPAPI is temporarily unavailable.
        # Downstream consumers must treat the prefixed value as unusable
        # (see is_protected_secret_blob).
        logger.error(
            "Encountered DPAPI-protected secret but DPAPI is unavailable on "
            "this platform; preserving ciphertext but it will not be usable."
        )
        return text
    encoded = text[len(_PROTECTED_SECRET_PREFIX) :]
    try:
        sealed = base64.b64decode(encoded)
        return _dpapi_unprotect(sealed).decode("utf-8")
    except Exception as exc:
        logger.error(
            "DPAPI unsealing failed; preserving ciphertext blob (it will not "
            "be usable until decryption succeeds). Cause: %s",
            exc,
        )
        return text


def is_protected_secret_blob(value: object) -> bool:
    """Return True if *value* is still in DPAPI ciphertext form.

    Callers that consume secrets at runtime (e.g. when constructing an API
    client) should reject these values rather than passing the ciphertext
    downstream as if it were the cleartext secret.
    """
    return isinstance(value, str) and value.startswith(_PROTECTED_SECRET_PREFIX)


def _walk_translation_backend_configs(config: dict):
    trans_cfg = config.get("translation", {}) if isinstance(config, dict) else {}
    if not isinstance(trans_cfg, dict):
        return
    for backend_cfg in trans_cfg.values():
        if isinstance(backend_cfg, dict):
            yield backend_cfg


def _walk_asr_provider_configs(config: dict):
    asr_cfg = config.get("asr", {}) if isinstance(config, dict) else {}
    if not isinstance(asr_cfg, dict):
        return
    for key in ("qwen3_asr", "gemini_live"):
        provider_cfg = asr_cfg.get(key, {})
        if isinstance(provider_cfg, dict):
            yield provider_cfg


def _walk_secret_configs(config: dict):
    yield from _walk_translation_backend_configs(config)
    yield from _walk_asr_provider_configs(config)


def _contains_plaintext_api_key(config: dict | None) -> bool:
    if not isinstance(config, dict):
        return False
    for backend_cfg in _walk_secret_configs(config):
        api_key = str(backend_cfg.get("api_key", "") or "")
        if api_key and not api_key.startswith(_PROTECTED_SECRET_PREFIX):
            return True
    return False


def _unprotect_config_for_runtime(config: dict | None) -> dict | None:
    if not isinstance(config, dict):
        return config
    for backend_cfg in _walk_secret_configs(config):
        if "api_key" in backend_cfg:
            backend_cfg["api_key"] = _unprotect_secret(backend_cfg.get("api_key"))
    return config


def _protect_config_for_storage(config: dict) -> dict:
    payload = copy.deepcopy(config)
    for backend_cfg in _walk_secret_configs(payload):
        if "api_key" in backend_cfg:
            backend_cfg["api_key"] = _protect_secret(backend_cfg.get("api_key"))
    return payload


def _config_path() -> Path:
    return writable_app_dir() / "config.json"


def _example_path() -> Path:
    for base in resource_base_dirs():
        candidate = base / "config.example.json"
        if candidate.exists():
            return candidate
    return resource_base_dirs()[0] / "config.example.json"


def _path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _cleanup_obsolete_runtime_models() -> None:
    runtime_models_dir = writable_app_dir() / "runtime_models"
    try:
        runtime_root = runtime_models_dir.resolve(strict=False)
    except OSError as exc:
        logger.warning("Could not resolve runtime model directory %s: %s", runtime_models_dir, exc)
        return
    if not runtime_models_dir.exists():
        return

    supported_names = {
        spec.model_id.replace("/", "--")
        for spec in ASR_ENGINE_SPECS.values()
        if spec.requires_local_model
    }

    for target in runtime_models_dir.iterdir():
        if target.name in supported_names:
            continue
        if not target.is_dir():
            continue
        has_managed_metadata = (target / ".mio-model.json").is_file()
        has_removed_model_marker = (target / "model.bin").is_file()
        if not has_managed_metadata and not has_removed_model_marker:
            continue
        try:
            resolved_target = target.resolve(strict=False)
        except OSError as exc:
            logger.warning("Could not resolve obsolete model path %s: %s", target, exc)
            continue
        if resolved_target == runtime_root or not _path_is_within(resolved_target, runtime_root):
            logger.warning("Skipping unsafe obsolete model cleanup path: %s", target)
            continue
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            logger.info("Removed obsolete runtime model directory: %s", target)
        except (OSError, IOError) as exc:
            logger.warning("Failed to remove obsolete runtime model %s: %s", target, exc)


def _merge_defaults(defaults, current):
    # 递归合并：用户值优先，example 里有但用户没配的键用默认值补上
    if isinstance(defaults, dict):
        node = current if isinstance(current, dict) else {}
        merged = {key: _merge_defaults(value, node.get(key)) for key, value in defaults.items()}
        for key, value in node.items():
            if key not in merged:
                merged[key] = value
        return merged
    if current is None:
        return defaults
    return current


def _base_language_from_ui_language(language: object) -> str:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized:
        return "en"
    if normalized.startswith("zh"):
        return "zh"
    base = normalized.split("-", 1)[0]
    if base in SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES and base != "auto":
        return base
    return "en"


def _default_translation_language_pair(ui_language: object) -> tuple[str, str]:
    source_language = _base_language_from_ui_language(ui_language)
    target_language = "ja" if source_language == "zh" else "zh"
    return source_language, target_language


def _normalize_translation_source_language(language: object) -> str | None:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized:
        return None
    if normalized.startswith("zh"):
        normalized = "zh"
    else:
        normalized = normalized.split("-", 1)[0]
    if normalized in SUPPORTED_MANUAL_SOURCE_LANGUAGE_CODES:
        return normalized
    return None


def _normalize_translation_target_language(language: object) -> str | None:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        normalized = "zh"
    else:
        normalized = normalized.split("-", 1)[0]
    if normalized in SUPPORTED_TARGET_LANGUAGE_CODES:
        return normalized
    return None


def _load_json_dict(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, IOError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load JSON from %s: %s", path, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error loading JSON from %s: %s", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _backup_invalid_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_name(
            f"{path.stem}.corrupt-{timestamp}-{counter}{path.suffix}"
        )
        counter += 1
    try:
        shutil.copy2(path, backup_path)
    except (OSError, IOError) as exc:
        logger.warning("Failed to backup config file %s: %s", path, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error backing up config file %s: %s", path, exc)
        return None
    return backup_path


def _ensure_vrc_listen_config(config: dict, loaded: dict | None = None) -> bool:
    changed = False
    audio_cfg = config.setdefault("audio", {})
    legacy_cfg = audio_cfg.get("desktop_capture", {})
    if not isinstance(legacy_cfg, dict):
        legacy_cfg = {}
    loaded = loaded if isinstance(loaded, dict) else {}
    loaded_vrc_cfg = loaded.get("vrc_listen", {})
    if not isinstance(loaded_vrc_cfg, dict):
        loaded_vrc_cfg = {}
    vrc_cfg = config.get("vrc_listen", {})
    if not isinstance(vrc_cfg, dict):
        vrc_cfg = {}
        config["vrc_listen"] = vrc_cfg
        changed = True
    elif "vrc_listen" not in config:
        config["vrc_listen"] = vrc_cfg
        changed = True

    defaults = {
        "enabled": False,
        "loopback_device": None,
        "asr_engine": ASR_ENGINE_FOLLOW_MAIN,
        "source_language": "auto",
        "target_language": "zh",
        "segment_duration_s": 2.0,
        "tail_silence_s": _DEFAULT_LISTEN_TAIL_SILENCE_S,
        "self_suppress": False,
        "self_suppress_seconds": 0.65,
        "show_overlay": False,
        "send_to_chatbox": True,
    }

    for key, value in defaults.items():
        if key not in vrc_cfg:
            vrc_cfg[key] = value
            changed = True

    if "vrc_listen" not in loaded:
        legacy_device = str(legacy_cfg.get("output_device", "")).strip()
        if bool(legacy_cfg.get("enabled", False)) and not bool(vrc_cfg.get("enabled", False)):
            vrc_cfg["enabled"] = True
            changed = True
        if legacy_device and not str(vrc_cfg.get("loopback_device") or "").strip():
            vrc_cfg["loopback_device"] = legacy_device
            changed = True

    if vrc_cfg.get("loopback_device") == "":
        vrc_cfg["loopback_device"] = None
        changed = True
    if not str(vrc_cfg.get("source_language", "")).strip():
        vrc_cfg["source_language"] = "auto"
        changed = True
    if not str(vrc_cfg.get("target_language", "")).strip():
        vrc_cfg["target_language"] = "zh"
        changed = True
    listen_engine = str(vrc_cfg.get("asr_engine", "") or "").strip()
    if listen_engine not in LISTEN_SELECTABLE_ASR_ENGINES:
        vrc_cfg["asr_engine"] = ASR_ENGINE_FOLLOW_MAIN
        changed = True
    elif listen_engine not in ASR_ENGINE_SPECS and listen_engine != ASR_ENGINE_FOLLOW_MAIN:
        vrc_cfg["asr_engine"] = ASR_ENGINE_FOLLOW_MAIN
        changed = True
    if "segment_duration_s" not in loaded_vrc_cfg:
        legacy_interval_ms = audio_cfg.get("desktop_chunk_interval_ms")
        legacy_window_s = audio_cfg.get("desktop_chunk_window_s")
        try:
            if legacy_interval_ms is not None:
                migrated_segment_s = float(legacy_interval_ms) / 1000.0
            elif legacy_window_s is not None:
                migrated_segment_s = float(legacy_window_s)
            else:
                migrated_segment_s = 2.0
            if migrated_segment_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            migrated_segment_s = 2.0
        vrc_cfg["segment_duration_s"] = migrated_segment_s
        changed = True
    else:
        try:
            segment_duration_s = float(vrc_cfg.get("segment_duration_s", 2.0))
            if segment_duration_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            vrc_cfg["segment_duration_s"] = 2.0
            changed = True
    if "tail_silence_s" not in loaded_vrc_cfg:
        try:
            migrated_tail_s = float(
                audio_cfg.get(
                    "vad_silence_threshold",
                    _DEFAULT_LISTEN_TAIL_SILENCE_S,
                )
            )
            if migrated_tail_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            migrated_tail_s = _DEFAULT_LISTEN_TAIL_SILENCE_S
        vrc_cfg["tail_silence_s"] = migrated_tail_s
        changed = True
    else:
        try:
            tail_silence_s = float(
                vrc_cfg.get("tail_silence_s", _DEFAULT_LISTEN_TAIL_SILENCE_S)
            )
            if tail_silence_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            vrc_cfg["tail_silence_s"] = _DEFAULT_LISTEN_TAIL_SILENCE_S
            changed = True
    if "self_suppress" not in vrc_cfg:
        vrc_cfg["self_suppress"] = False
        changed = True
    try:
        suppress_seconds = float(vrc_cfg.get("self_suppress_seconds", 0.65))
        if suppress_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        vrc_cfg["self_suppress_seconds"] = 0.65
        changed = True
    if "show_overlay" not in vrc_cfg:
        vrc_cfg["show_overlay"] = False
        changed = True
    if "send_to_chatbox" not in vrc_cfg:
        vrc_cfg["send_to_chatbox"] = True
        changed = True
    return changed


def _ensure_audio_device_config(config: dict, loaded: dict | None = None) -> bool:
    changed = False
    audio_cfg = config.get("audio", {})
    if not isinstance(audio_cfg, dict):
        return False

    loaded = loaded if isinstance(loaded, dict) else {}
    loaded_audio_cfg = loaded.get("audio", {})
    if not isinstance(loaded_audio_cfg, dict):
        loaded_audio_cfg = {}

    input_device = audio_cfg.get("input_device")
    if input_device == "":
        audio_cfg["input_device"] = None
        input_device = None
        changed = True

    configured_input = str(input_device or "").strip() or None
    if configured_input is None and audio_cfg.get("input_device") is not None:
        audio_cfg["input_device"] = None
        changed = True

    mode = str(audio_cfg.get("input_device_mode", "")).strip().lower()
    if mode not in {"auto", "fixed"}:
        if "input_device_mode" in loaded_audio_cfg:
            normalized_mode = "fixed" if configured_input else "auto"
        else:
            normalized_mode = "fixed" if configured_input else "auto"
        audio_cfg["input_device_mode"] = normalized_mode
        changed = True

    if "vad_silence_threshold" not in loaded_audio_cfg:
        if audio_cfg.get("vad_silence_threshold") != _DEFAULT_MIC_TAIL_SILENCE_S:
            audio_cfg["vad_silence_threshold"] = _DEFAULT_MIC_TAIL_SILENCE_S
            changed = True
    else:
        try:
            vad_silence_threshold = float(
                audio_cfg.get("vad_silence_threshold", _DEFAULT_MIC_TAIL_SILENCE_S)
            )
            if vad_silence_threshold <= 0:
                raise ValueError
        except (TypeError, ValueError):
            audio_cfg["vad_silence_threshold"] = _DEFAULT_MIC_TAIL_SILENCE_S
            changed = True

    if "denoise_strength" not in loaded_audio_cfg:
        if audio_cfg.get("denoise_strength") != _DEFAULT_DENOISE_STRENGTH:
            audio_cfg["denoise_strength"] = _DEFAULT_DENOISE_STRENGTH
            changed = True
    else:
        try:
            denoise_strength = float(
                audio_cfg.get("denoise_strength", _DEFAULT_DENOISE_STRENGTH)
            )
            if denoise_strength < 0:
                raise ValueError
        except (TypeError, ValueError):
            audio_cfg["denoise_strength"] = _DEFAULT_DENOISE_STRENGTH
            changed = True

    if "vad_speech_ratio" not in loaded_audio_cfg:
        if audio_cfg.get("vad_speech_ratio") != _DEFAULT_VAD_SPEECH_RATIO:
            audio_cfg["vad_speech_ratio"] = _DEFAULT_VAD_SPEECH_RATIO
            changed = True
    else:
        try:
            vad_speech_ratio = float(
                audio_cfg.get("vad_speech_ratio", _DEFAULT_VAD_SPEECH_RATIO)
            )
            if not 0.0 <= vad_speech_ratio <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            audio_cfg["vad_speech_ratio"] = _DEFAULT_VAD_SPEECH_RATIO
            changed = True

    if "vad_activation_threshold_s" not in loaded_audio_cfg:
        if audio_cfg.get("vad_activation_threshold_s") != _DEFAULT_VAD_ACTIVATION_THRESHOLD_S:
            audio_cfg["vad_activation_threshold_s"] = _DEFAULT_VAD_ACTIVATION_THRESHOLD_S
            changed = True
    else:
        try:
            vad_activation_threshold = float(
                audio_cfg.get(
                    "vad_activation_threshold_s",
                    _DEFAULT_VAD_ACTIVATION_THRESHOLD_S,
                )
            )
            if vad_activation_threshold <= 0:
                raise ValueError
        except (TypeError, ValueError):
            audio_cfg["vad_activation_threshold_s"] = _DEFAULT_VAD_ACTIVATION_THRESHOLD_S
            changed = True
    return changed


def _ensure_text_input_window_config(config: dict) -> bool:
    changed = False
    window_cfg = config.get("text_input_window", {})
    if not isinstance(window_cfg, dict):
        window_cfg = {}
        config["text_input_window"] = window_cfg
        changed = True
    if "hotkey" not in window_cfg:
        window_cfg["hotkey"] = DEFAULT_TEXT_INPUT_HOTKEY
        changed = True
        return changed

    hotkey = str(window_cfg.get("hotkey", "") or "").strip()
    if not hotkey:
        if window_cfg.get("hotkey") != "":
            window_cfg["hotkey"] = ""
            changed = True
        return changed

    try:
        normalized_hotkey = normalize_hotkey(hotkey)
    except HotkeyError:
        normalized_hotkey = DEFAULT_TEXT_INPUT_HOTKEY
    if window_cfg.get("hotkey") != normalized_hotkey:
        window_cfg["hotkey"] = normalized_hotkey
        changed = True
    return changed


def _ensure_translation_config(
    config: dict,
    loaded: dict | None = None,
    *,
    prefer_auto_backend: bool = False,
) -> bool:
    changed = False
    trans_cfg = config.get("translation", {})
    if not isinstance(trans_cfg, dict):
        return False
    loaded = loaded if isinstance(loaded, dict) else {}
    loaded_trans_cfg = loaded.get("translation", {})
    if not isinstance(loaded_trans_cfg, dict):
        loaded_trans_cfg = {}

    ui_cfg = config.get("ui", {})
    ui_language = ui_cfg.get("language") if isinstance(ui_cfg, dict) else None
    default_source_language, default_target_language = _default_translation_language_pair(
        ui_language
    )
    recommended_backend = default_backend_for_ui_language(str(ui_language or ""))
    backend = str(trans_cfg.get("backend", "") or "").strip()
    backend_source = str(trans_cfg.get("backend_source", "") or "").strip().lower()
    language_pair_source = str(
        trans_cfg.get("language_pair_source", "") or ""
    ).strip().lower()
    loaded_had_backend = bool(str(loaded_trans_cfg.get("backend", "") or "").strip())
    loaded_had_backend_source = "backend_source" in loaded_trans_cfg
    loaded_had_source_language = bool(
        str(loaded_trans_cfg.get("source_language", "") or "").strip()
    )
    loaded_target_language = _normalize_translation_target_language(
        loaded_trans_cfg.get("target_language")
    )
    loaded_had_custom_target_language = (
        loaded_target_language is not None and loaded_target_language != "ja"
    )

    if (
        not prefer_auto_backend
        and loaded_had_backend
        and not loaded_had_backend_source
        and trans_cfg.get("backend_source") != "manual"
    ):
        trans_cfg["backend_source"] = "manual"
        backend_source = "manual"
        changed = True

    if backend_source not in {"auto", "manual"}:
        trans_cfg["backend_source"] = "auto" if prefer_auto_backend or not backend else "manual"
        backend_source = str(trans_cfg["backend_source"])
        changed = True

    if prefer_auto_backend or not backend or backend not in TRANSLATION_BACKENDS:
        if trans_cfg.get("backend") != recommended_backend:
            trans_cfg["backend"] = recommended_backend
            changed = True
        if trans_cfg.get("backend_source") != "auto":
            trans_cfg["backend_source"] = "auto"
            changed = True
    elif backend_source == "auto":
        # Auto defaults follow the detected computer/UI language until the user
        # explicitly saves Settings, which marks the backend as manual.
        if backend != recommended_backend:
            trans_cfg["backend"] = recommended_backend
            changed = True

    if language_pair_source not in {"auto", "manual"}:
        language_pair_source = (
            "auto"
            if (
                prefer_auto_backend
                or (
                    not loaded_had_source_language
                    and not loaded_had_custom_target_language
                )
            )
            else "manual"
        )
        trans_cfg["language_pair_source"] = language_pair_source
        changed = True

    current_source_language = _normalize_translation_source_language(
        trans_cfg.get("source_language")
    )
    current_target_language = _normalize_translation_target_language(
        trans_cfg.get("target_language")
    )
    if prefer_auto_backend or language_pair_source == "auto":
        if trans_cfg.get("source_language") != default_source_language:
            trans_cfg["source_language"] = default_source_language
            changed = True
        if trans_cfg.get("target_language") != default_target_language:
            trans_cfg["target_language"] = default_target_language
            changed = True
        if trans_cfg.get("language_pair_source") != "auto":
            trans_cfg["language_pair_source"] = "auto"
            changed = True
    else:
        if current_source_language is None:
            trans_cfg["source_language"] = default_source_language
            changed = True
        elif trans_cfg.get("source_language") != current_source_language:
            trans_cfg["source_language"] = current_source_language
            changed = True
        if current_target_language is None:
            trans_cfg["target_language"] = default_target_language
            changed = True
        elif trans_cfg.get("target_language") != current_target_language:
            trans_cfg["target_language"] = current_target_language
            changed = True

    if "send_to_chatbox" not in trans_cfg:
        trans_cfg["send_to_chatbox"] = True
        changed = True

    for backend_code in TRANSLATION_BACKENDS:
        backend_cfg = trans_cfg.get(backend_code, {})
        if not isinstance(backend_cfg, dict):
            backend_cfg = {}
            trans_cfg[backend_code] = backend_cfg
            changed = True
        for key in ("base_url", "model"):
            default_value = get_backend_value(backend_code, key)
            if default_value and not str(backend_cfg.get(key, "") or "").strip():
                backend_cfg[key] = default_value
                changed = True

    openai_cfg = trans_cfg.get("openai", {})
    if isinstance(openai_cfg, dict):
        model = str(openai_cfg.get("model", "") or "").strip().lower()
        if model in _LEGACY_OPENAI_MODEL_IDS or model.startswith(
            _LEGACY_OPENAI_MODEL_PREFIXES
        ):
            openai_cfg["model"] = _DEFAULT_OPENAI_MODEL
            changed = True

    anthropic_cfg = trans_cfg.get("anthropic", {})
    if isinstance(anthropic_cfg, dict):
        model = str(anthropic_cfg.get("model", "") or "").strip().lower()
        if model in _LEGACY_ANTHROPIC_MODEL_IDS:
            anthropic_cfg["model"] = _DEFAULT_ANTHROPIC_MODEL
            changed = True

    return changed


def _ensure_asr_config(config: dict) -> bool:
    """Validate the ASR section and keep provider secrets separate from translation."""
    changed = False
    asr_cfg = config.get("asr", {})
    if not isinstance(asr_cfg, dict):
        return False

    if "user_selected_engine" not in asr_cfg:
        asr_cfg["user_selected_engine"] = asr_cfg.get("engine_source") == "manual"
        changed = True
    if "auto_fallback" not in asr_cfg:
        asr_cfg["auto_fallback"] = True
        changed = True
    if "fallback_engine" not in asr_cfg:
        asr_cfg["fallback_engine"] = "sensevoice-small"
        changed = True

    engine = str(asr_cfg.get("engine", "")).strip()
    if engine not in ASR_ENGINE_SPECS:
        engine = DEFAULT_ASR_ENGINE
        asr_cfg["engine"] = engine
        changed = True

    for key in tuple(asr_cfg.keys()):
        if key not in _ASR_CONFIG_KEYS:
            del asr_cfg[key]
            changed = True

    # SenseVoice section — always present so the menu and download flow
    # have a stable place to read from.
    sv_spec = get_asr_engine_spec("sensevoice-small")
    sensevoice_cfg = asr_cfg.get("sensevoice")
    if not isinstance(sensevoice_cfg, dict):
        sensevoice_cfg = {}
        asr_cfg["sensevoice"] = sensevoice_cfg
        changed = True
    if str(sensevoice_cfg.get("model_id", "")).strip() != sv_spec.model_id:
        sensevoice_cfg["model_id"] = sv_spec.model_id
        changed = True
    if str(sensevoice_cfg.get("model_revision", "")).strip() != sv_spec.model_revision:
        sensevoice_cfg["model_revision"] = sv_spec.model_revision
        changed = True

    if "ncpu" not in sensevoice_cfg:
        sensevoice_cfg["ncpu"] = None
        changed = True

    webspeech_cfg = asr_cfg.get("webspeech")
    if not isinstance(webspeech_cfg, dict):
        webspeech_cfg = {}
        asr_cfg["webspeech"] = webspeech_cfg
        changed = True
    webspeech_defaults = {
        "language": "ja-JP",
        "continuous": True,
        "interim_results": True,
        "max_alternatives": 1,
        "restart_on_end": True,
        "silence_timeout_ms": 800,
        "final_timeout_seconds": 4.0,
        "partial_timeout_seconds": 0.2,
        "connection_timeout_seconds": 3.0,
        "auto_open_browser": True,
        "bridge_port": 0,
    }
    for key, value in webspeech_defaults.items():
        if key not in webspeech_cfg:
            webspeech_cfg[key] = value
            changed = True

    qwen_cfg = asr_cfg.get("qwen3_asr")
    if not isinstance(qwen_cfg, dict):
        qwen_cfg = {}
        asr_cfg["qwen3_asr"] = qwen_cfg
        changed = True
    qwen_defaults = {
        "api_key": "",
        "region": QWEN3_ASR_DEFAULT_REGION,
        "base_url": "",
        "model": QWEN3_ASR_DEFAULT_MODEL,
        "language": "ja",
        "mode": "vad_chunked",
        "sample_rate": 16000,
        "max_segment_seconds": 6.0,
        "tail_silence_seconds": 0.7,
        "overlap_ms": 300,
        "timeout_seconds": 15,
    }
    for key, value in qwen_defaults.items():
        if key not in qwen_cfg:
            qwen_cfg[key] = value
            changed = True
    qwen_region = normalize_qwen3_asr_region(qwen_cfg.get("region"))
    if qwen_cfg.get("region") != qwen_region:
        qwen_cfg["region"] = qwen_region
        changed = True
    qwen_base_url = str(qwen_cfg.get("base_url", "") or "").strip().rstrip("/")
    auto_base_url = get_qwen3_asr_base_url(qwen_region)
    known_base_urls = frozenset(QWEN3_ASR_REGION_BASE_URLS.values())
    if auto_base_url and (not qwen_base_url or qwen_base_url in known_base_urls):
        if qwen_base_url != auto_base_url:
            qwen_cfg["base_url"] = auto_base_url
            changed = True
    qwen_model = str(qwen_cfg.get("model", "") or "").strip()
    if not qwen_model or qwen_model in QWEN3_ASR_LEGACY_MODEL_IDS:
        if qwen_cfg.get("model") != QWEN3_ASR_DEFAULT_MODEL:
            qwen_cfg["model"] = QWEN3_ASR_DEFAULT_MODEL
            changed = True

    gemini_cfg = asr_cfg.get("gemini_live")
    if not isinstance(gemini_cfg, dict):
        gemini_cfg = {}
        asr_cfg["gemini_live"] = gemini_cfg
        changed = True
    gemini_defaults = {
        "api_key": "",
        "model": "gemini-3.1-flash-live-preview",
        "language": "ja-JP",
        "transcribe_only": True,
        "system_instruction": (
            "You are a speech-to-text engine. Output only the transcription. "
            "Do not translate, summarize, explain, or answer."
        ),
        "timeout_seconds": 20,
        "use_live_api": True,
        "live_silence_duration_ms": 600,
    }
    for key, value in gemini_defaults.items():
        if key not in gemini_cfg:
            gemini_cfg[key] = value
            changed = True

    return changed


def _apply_startup_asr_default(config: dict) -> bool:
    asr_cfg = config.setdefault("asr", {})
    if not isinstance(asr_cfg, dict):
        return False
    if asr_cfg.get("user_selected_engine") or asr_cfg.get("engine_source") == "manual":
        asr_cfg["user_selected_engine"] = True
        return False
    engine = select_default_asr_engine()
    if engine not in ASR_ENGINE_SPECS:
        engine = DEFAULT_ASR_ENGINE
    if asr_cfg.get("engine") == engine and asr_cfg.get("engine_source") == "auto":
        return False
    asr_cfg["engine"] = engine
    asr_cfg["engine_source"] = "auto"
    asr_cfg["user_selected_engine"] = False
    return True


def _apply_initial_asr_default(config: dict) -> bool:
    return _apply_startup_asr_default(config)


def _ensure_tts_config(config: dict, loaded: dict | None = None) -> bool:
    """Ensure TTS configuration exists and is valid."""
    changed = False
    tts_cfg = config.get("tts", {})
    if "tts" not in config or not isinstance(tts_cfg, dict):
        tts_cfg = {}
        config["tts"] = tts_cfg
        changed = True

    legacy_output_device = tts_cfg.get("output_device")
    if "output_to_vrchat" not in tts_cfg:
        tts_cfg["output_to_vrchat"] = (
            legacy_output_device is not None
            and legacy_output_device != -1
            and str(legacy_output_device).strip() != ""
        )
        changed = True

    # Default values
    defaults = {
        "enabled": False,
        "engine": "edge",
        "auto_read": True,
        "monitor_enabled": False,
        "output_device": None,
        "output_device_name": "",
    }

    for key, value in defaults.items():
        if key not in tts_cfg:
            tts_cfg[key] = value
            changed = True

    # Ensure engine configs exist
    engine_defaults = {
        "edge": {
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": 1.0,
            "volume": 0.8,
        },
        "gtts": {
            "voice": "zh-CN",
            "rate": 1.0,
            "volume": 0.8,
        },
        "pyttsx3": {
            "voice": None,
            "rate": 1.0,
            "volume": 1.0,
        },
        "voicevox": {
            "voice": None,
            "rate": 1.0,
            "volume": 0.8,
        },
        "aivis_speech": {
            "voice": None,
            "rate": 1.0,
            "volume": 0.8,
        },
        "style_bert_vits2": {
            "voice": None,
            "rate": 1.0,
            "volume": 0.8,
            "device": "cpu",
            "bert_language": "jp",
        },
    }

    for engine, engine_cfg in engine_defaults.items():
        if engine not in tts_cfg:
            tts_cfg[engine] = engine_cfg
            changed = True
        elif not isinstance(tts_cfg[engine], dict):
            tts_cfg[engine] = engine_cfg
            changed = True
        else:
            for key, value in engine_cfg.items():
                if key not in tts_cfg[engine]:
                    tts_cfg[engine][key] = value
                    changed = True

    return changed


def _ensure_mode_config(config: dict) -> bool:
    """Ensure the high-level app mode configuration exists and is valid."""
    changed = False
    mode = str(config.get("app_mode", "") or "").strip().lower()
    if mode not in _APP_MODE_VALUES:
        mode = "translation"
    if config.get("app_mode") != mode:
        config["app_mode"] = mode
        changed = True

    simul_cfg = config.get("simul_mode", {})
    if "simul_mode" not in config or not isinstance(simul_cfg, dict):
        simul_cfg = {}
        config["simul_mode"] = simul_cfg
        changed = True

    for key, value in _SIMUL_MODE_DEFAULTS.items():
        if key not in simul_cfg:
            simul_cfg[key] = value
            changed = True
    return changed


def load_config() -> dict:
    config_path = _config_path()
    logger.info("Loading configuration from %s", config_path)
    _cleanup_obsolete_runtime_models()
    created_new = False
    recovered_invalid = False
    if not config_path.exists():
        example_path = _example_path()
        if example_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(example_path, config_path)
            created_new = True
        else:
            return {}
    example_path = _example_path()
    defaults = _load_json_dict(example_path) or {}
    loaded = _load_json_dict(config_path)
    had_plaintext_secret = _contains_plaintext_api_key(loaded)
    loaded = _unprotect_config_for_runtime(loaded)
    config_changed = False
    if loaded is None:
        _backup_invalid_config(config_path)
        loaded = {}
        recovered_invalid = True
        config_changed = True
    elif had_plaintext_secret and _can_protect_secrets():
        config_changed = True
    merged = _merge_defaults(defaults, loaded)
    if not isinstance(merged, dict):
        merged = dict(defaults) if isinstance(defaults, dict) else {}
        config_changed = True
    config_changed = _ensure_vrc_listen_config(merged, loaded) or config_changed
    if _ensure_audio_device_config(merged, loaded):
        config_changed = True
    if _ensure_text_input_window_config(merged):
        config_changed = True
    if _apply_startup_asr_default(merged):
        config_changed = True
    if _ensure_asr_config(merged):
        config_changed = True
    if _ensure_tts_config(merged, loaded):
        config_changed = True
    if _ensure_mode_config(merged):
        config_changed = True
    if bootstrap_ui_language(merged, prefer_auto=created_new or recovered_invalid):
        config_changed = True
    if _ensure_translation_config(
        merged,
        loaded,
        prefer_auto_backend=created_new or recovered_invalid,
    ):
        config_changed = True
    if config_changed:
        save_config(merged)
    logger.info(
        "Configuration ready (created_new=%s recovered_invalid=%s changed=%s)",
        created_new,
        recovered_invalid,
        config_changed,
    )
    return merged


def save_config(config: dict) -> None:
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    storage_config = _protect_config_for_storage(config)
    temp_path = config_path.with_name(
        f"{config_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with _SAVE_LOCK:
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(storage_config, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, config_path)
            logger.debug("Configuration saved to %s", config_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass


def get(config: dict, *keys, default=None):
    node = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
