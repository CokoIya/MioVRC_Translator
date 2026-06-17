# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import os
import sys
from pathlib import Path

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
    override = os.environ.get("MIO_TRANSLATOR_HOME")
    if override:
        return Path(override).expanduser()
    return resource_base_dirs()[0]


def app_temp_dir() -> Path:
    path = writable_app_dir() / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backgrounds_dir() -> Path:
    path = writable_app_dir() / "backgrounds"
    path.mkdir(parents=True, exist_ok=True)
    return path
