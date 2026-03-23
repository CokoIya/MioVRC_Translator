import json
import shutil
from pathlib import Path

from src.utils.app_paths import resource_base_dirs, writable_app_dir
from src.utils.ui_language_detection import bootstrap_ui_language


def _config_path() -> Path:
    return writable_app_dir() / "config.json"


def _example_path() -> Path:
    for base in resource_base_dirs():
        candidate = base / "config.example.json"
        if candidate.exists():
            return candidate
    return resource_base_dirs()[0] / "config.example.json"


def _merge_defaults(defaults, current):
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
        "target_language": "zh",
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
    if not str(vrc_cfg.get("target_language", "")).strip():
        vrc_cfg["target_language"] = "zh"
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
