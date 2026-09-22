# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Let SteamVR start Mio.

The opposite of Mio starting SteamVR, which it must never do: a player who
lives in the headset can ask SteamVR to bring Mio up with it, the way OVR
Overlay Translator's "start with SteamVR" works. SteamVR keeps a list of
overlay applications it launches; an application manifest describes Mio to
it, and the auto-launch flag is set on that entry. Both can only be written
while SteamVR is running, so the preference is applied whenever Mio attaches
to the runtime.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_KEY = "mio.translator.overlay"
MANIFEST_NAME = "mio.translator.vrmanifest"


def manifest_path() -> Path:
    from src.utils.app_paths import writable_app_dir

    return Path(writable_app_dir()) / "steamvr" / MANIFEST_NAME


def launch_command() -> tuple[str, str]:
    """(binary, arguments) SteamVR should run: the frozen exe, or the interpreter with main.py."""

    if getattr(sys, "frozen", False):
        return (str(sys.executable), "")
    here = os.path.dirname(os.path.abspath(__file__))
    main_script = os.path.abspath(os.path.join(here, "..", "..", "main.py"))
    return (str(sys.executable), f'"{main_script}"')


def manifest_document() -> dict:
    binary, arguments = launch_command()
    return {
        "source": "builtin",
        "applications": [
            {
                "app_key": APP_KEY,
                "launch_type": "binary",
                "binary_path_windows": binary,
                "arguments": arguments,
                "is_dashboard_overlay": True,
                "strings": {
                    "en_us": {
                        "name": "Mio Translator",
                        "description": "Real-time translation for VRChat: subtitles, screenshot translation and a wrist panel in the headset.",
                    },
                    "zh_cn": {
                        "name": "Mio 翻译器",
                        "description": "VRChat 实时翻译：头显字幕、截图翻译和手腕面板。",
                    },
                    "ja_jp": {
                        "name": "Mio 翻訳",
                        "description": "VRChat のリアルタイム翻訳：ヘッドセット字幕、スクリーンショット翻訳、手首パネル。",
                    },
                },
            }
        ],
    }


def write_manifest() -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest_document(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def apply(openvr: Any, enabled: bool) -> tuple[bool, str]:
    """Register or withdraw the auto-launch entry. Needs an initialised runtime.

    Returns (ok, reason). Registering also tells SteamVR that this very
    process *is* that application, so its bindings and its entry line up
    whether SteamVR launched it or the player did.
    """

    try:
        apps = openvr.VRApplications()
    except Exception as exc:
        return False, f"applications_unavailable:{type(exc).__name__}"
    try:
        if enabled:
            path = write_manifest()
            apps.addApplicationManifest(str(path), False)
            try:
                apps.identifyApplication(os.getpid(), APP_KEY)
            except Exception:
                logger.debug("identifyApplication failed", exc_info=True)
            apps.setApplicationAutoLaunch(APP_KEY, True)
            logger.info("SteamVR will launch Mio with it (manifest at %s)", path)
        else:
            if bool(apps.isApplicationInstalled(APP_KEY)):
                apps.setApplicationAutoLaunch(APP_KEY, False)
                logger.info("SteamVR will no longer launch Mio with it")
    except Exception as exc:
        logger.info("Could not update SteamVR auto-launch: %s", exc)
        return False, f"apply_failed:{type(exc).__name__}"
    return True, ""


def is_enabled(openvr: Any) -> bool | None:
    """What SteamVR currently has on record; None when it cannot be asked."""

    try:
        apps = openvr.VRApplications()
        if not bool(apps.isApplicationInstalled(APP_KEY)):
            return False
        return bool(apps.getApplicationAutoLaunch(APP_KEY))
    except Exception:
        return None
