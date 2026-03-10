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
    if bootstrap_ui_language(merged, prefer_auto=created_new):
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
