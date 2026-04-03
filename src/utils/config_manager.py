import json
import shutil
from pathlib import Path

from src.utils.app_paths import resource_base_dirs, writable_app_dir
from src.utils.ui_language_detection import bootstrap_ui_language
from src.utils.ui_config import DEFAULT_ASR_ENGINE


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
    return changed


def _ensure_translation_config(config: dict) -> bool:
    changed = False
    trans_cfg = config.get("translation", {})
    if not isinstance(trans_cfg, dict):
        return False

    openai_cfg = trans_cfg.get("openai", {})
    if not isinstance(openai_cfg, dict):
        return False

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
    created_new = False
    if not config_path.exists():
        example_path = _example_path()
        if example_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(example_path, config_path)
            created_new = True
        else:
            return {}
    defaults = {}
    example_path = _example_path()
    if example_path.exists():
        with example_path.open("r", encoding="utf-8") as f:
            defaults = json.load(f)

    with config_path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    merged = _merge_defaults(defaults, loaded)
    config_changed = _ensure_vrc_listen_config(merged, loaded)
    if _ensure_translation_config(merged):
        config_changed = True
    if _ensure_asr_config(merged):
        config_changed = True
    if bootstrap_ui_language(merged, prefer_auto=created_new) or config_changed:
        save_config(merged)
    return merged


def save_config(config: dict) -> None:
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get(config: dict, *keys, default=None):
    node = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
