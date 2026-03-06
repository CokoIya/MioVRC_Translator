"""Configuration file helpers."""

import json
import shutil
import sys
from pathlib import Path


def _runtime_base_dirs() -> list[Path]:
    """Return possible runtime roots (source mode + frozen mode)."""
    if not getattr(sys, "frozen", False):
        return [Path(__file__).resolve().parents[2]]

    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass)
        if meipass_path not in dirs:
            dirs.append(meipass_path)
    return dirs


def _config_path() -> Path:
    # Keep config beside the EXE in frozen mode for easy user editing.
    return _runtime_base_dirs()[0] / "config.json"


def _example_path() -> Path:
    for base in _runtime_base_dirs():
        candidate = base / "config.example.json"
        if candidate.exists():
            return candidate
    return _runtime_base_dirs()[0] / "config.example.json"


def load_config() -> dict:
    """Load config.json. If missing, copy from config.example.json."""
    config_path = _config_path()
    if not config_path.exists():
        example_path = _example_path()
        if example_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(example_path, config_path)
        else:
            return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Save config to config.json."""
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get(config: dict, *keys, default=None):
    """Safely fetch a nested key from dict."""
    node = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
