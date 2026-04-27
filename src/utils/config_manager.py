import json
import os
import shutil
import threading
import time
import logging
from pathlib import Path

from src.utils.app_paths import resource_base_dirs, writable_app_dir
from src.utils.ui_language_detection import bootstrap_ui_language
from src.utils.ui_config import (
    DEFAULT_ASR_ENGINE,
    TRANSLATION_BACKENDS,
    default_backend_for_ui_language,
    get_backend_value,
)

_SAVE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _config_path() -> Path:
    return writable_app_dir() / "config.json"


def _example_path() -> Path:
    for base in resource_base_dirs():
        candidate = base / "config.example.json"
        if candidate.exists():
            return candidate
    return resource_base_dirs()[0] / "config.example.json"


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


def _load_json_dict(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except Exception:
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
    except Exception:
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
        "source_language": "auto",
        "target_language": "zh",
        "segment_duration_s": 2.0,
        "tail_silence_s": 1.2,
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
            migrated_tail_s = float(audio_cfg.get("vad_silence_threshold", 1.2))
            if migrated_tail_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            migrated_tail_s = 1.2
        vrc_cfg["tail_silence_s"] = migrated_tail_s
        changed = True
    else:
        try:
            tail_silence_s = float(vrc_cfg.get("tail_silence_s", 1.2))
            if tail_silence_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            vrc_cfg["tail_silence_s"] = 1.2
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
    recommended_backend = default_backend_for_ui_language(str(ui_language or ""))
    backend = str(trans_cfg.get("backend", "") or "").strip()
    backend_source = str(trans_cfg.get("backend_source", "") or "").strip().lower()
    loaded_had_backend = bool(str(loaded_trans_cfg.get("backend", "") or "").strip())
    loaded_had_backend_source = "backend_source" in loaded_trans_cfg

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
    if not isinstance(openai_cfg, dict):
        return changed

    model = str(openai_cfg.get("model", "") or "").strip().lower()
    if model.startswith("gpt-4"):
        openai_cfg["model"] = "gpt-5.4-mini"
        changed = True

    return changed


def _ensure_asr_config(config: dict) -> bool:
    changed = False
    asr_cfg = config.get("asr", {})
    if not isinstance(asr_cfg, dict):
        return False

    engine = str(asr_cfg.get("engine", DEFAULT_ASR_ENGINE)).strip()
    if engine != DEFAULT_ASR_ENGINE:
        asr_cfg["engine"] = DEFAULT_ASR_ENGINE
        changed = True

    sensevoice_cfg = asr_cfg.get("sensevoice", {})
    if not isinstance(sensevoice_cfg, dict):
        sensevoice_cfg = {}
        asr_cfg["sensevoice"] = sensevoice_cfg
        changed = True
    if not str(sensevoice_cfg.get("model_id", "")).strip():
        sensevoice_cfg["model_id"] = "iic/SenseVoiceSmall"
        changed = True
    if not str(sensevoice_cfg.get("model_revision", "")).strip():
        sensevoice_cfg["model_revision"] = "master"
        changed = True

    return changed


def load_config() -> dict:
    config_path = _config_path()
    logger.info("Loading configuration from %s", config_path)
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
    config_changed = False
    if loaded is None:
        _backup_invalid_config(config_path)
        loaded = {}
        recovered_invalid = True
        config_changed = True
    merged = _merge_defaults(defaults, loaded)
    if not isinstance(merged, dict):
        merged = dict(defaults) if isinstance(defaults, dict) else {}
        config_changed = True
    config_changed = _ensure_vrc_listen_config(merged, loaded) or config_changed
    if _ensure_audio_device_config(merged, loaded):
        config_changed = True
    if _ensure_asr_config(merged):
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
    temp_path = config_path.with_name(
        f"{config_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with _SAVE_LOCK:
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
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
