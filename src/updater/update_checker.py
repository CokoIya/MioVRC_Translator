from __future__ import annotations

import re
import threading
from typing import Callable

import requests

from src.version import APP_VERSION, UPDATE_CHECK_URL

_REQUEST_TIMEOUT = 8


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _is_newer(remote: str, local: str) -> bool:
    try:
        return _version_tuple(remote) > _version_tuple(local)
    except Exception:
        return False


def check_for_update(
    on_update_available: Callable[[str, str, str], None],
) -> None:
    """Silently check for updates in a daemon thread.

    Calls on_update_available(version, download_url, notes) on the calling
    thread's tkinter event loop via the provided callback — the callback must
    schedule the UI call with widget.after(0, ...) itself.
    """

    def _worker() -> None:
        try:
            resp = requests.get(UPDATE_CHECK_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data: dict = resp.json()
            remote_version = str(data.get("version", "")).strip()
            download_url = str(data.get("url", "")).strip()
            notes = str(data.get("notes", "")).strip()
            if remote_version and download_url and _is_newer(remote_version, APP_VERSION):
                on_update_available(remote_version, download_url, notes)
        except Exception:
            pass  # silent failure — network issues must never affect the app

    threading.Thread(target=_worker, daemon=True).start()
