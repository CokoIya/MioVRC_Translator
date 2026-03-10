from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Mio RealTime Translator"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_base_dirs() -> list[Path]:
    if not getattr(sys, "frozen", False):
        return [project_root()]

    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass)
        if meipass_path not in dirs:
            dirs.append(meipass_path)
    return dirs


def writable_app_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return project_root()

    override = os.environ.get("MIO_TRANSLATOR_HOME")
    if override:
        return Path(override).expanduser()

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / APP_DIR_NAME

    return Path.home() / ".local" / "share" / APP_DIR_NAME
