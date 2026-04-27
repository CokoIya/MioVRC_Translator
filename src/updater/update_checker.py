from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

import requests

from src.version import APP_VERSION, UPDATE_CHECK_URL

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 8
_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15]  # seconds to wait before 2nd and 3rd attempt

_PRERELEASE_RANK = {
    "dev": 0,
    "a": 1,
    "alpha": 1,
    "b": 2,
    "beta": 2,
    "pre": 3,
    "preview": 3,
    "rc": 4,
}


def _parse_version(v: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    text = str(v or "").strip().lstrip("vV")
    if not text:
        raise ValueError("empty version")

    match = re.match(
        r"^(?P<release>\d+(?:\.\d+)*)(?:[-_.]?(?P<label>[a-zA-Z]+)(?P<suffix>.*))?$",
        text,
    )
    if not match:
        raise ValueError(f"invalid version: {v}")

    release = tuple(int(part) for part in match.group("release").split("."))
    label = str(match.group("label") or "").strip().lower()
    suffix = str(match.group("suffix") or "")

    if not label:
        return release, (1,)

    rank = _PRERELEASE_RANK.get(label, 0)
    suffix_numbers = tuple(int(x) for x in re.findall(r"\d+", suffix))
    return release, (0, rank, *suffix_numbers)


def _version_tuple(v: str) -> tuple[int, ...]:
    release, prerelease = _parse_version(v)
    return release + prerelease


def _compare_release(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    max_len = max(len(left), len(right))
    left_padded = left + (0,) * (max_len - len(left))
    right_padded = right + (0,) * (max_len - len(right))
    if left_padded > right_padded:
        return 1
    if left_padded < right_padded:
        return -1
    return 0


def _compare_version_parts(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    max_len = max(len(left), len(right))
    left_padded = left + (0,) * (max_len - len(left))
    right_padded = right + (0,) * (max_len - len(right))
    if left_padded > right_padded:
        return 1
    if left_padded < right_padded:
        return -1
    return 0


def _is_newer(remote: str, local: str) -> bool:
    try:
        remote_release, remote_prerelease = _parse_version(remote)
        local_release, local_prerelease = _parse_version(local)
        release_cmp = _compare_release(remote_release, local_release)
        if release_cmp != 0:
            return release_cmp > 0
        return _compare_version_parts(remote_prerelease, local_prerelease) > 0
    except Exception:
        return False


def check_for_update(
    on_update_available: Callable[[str, str, str], None],
    *,
    on_no_update: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Silently check for updates in a daemon thread.

    Calls *on_update_available(version, download_url, notes)* when a newer
    version is detected.  Retries up to ``_MAX_RETRIES`` times with back-off
    on network failures.

    Optional callbacks:
    * *on_no_update* – called when the remote version is not newer.
    * *on_error* – called with an error message after all retries are exhausted.
    """

    def _worker() -> None:
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                logger.info(
                    "Update check attempt %d/%d  (local %s)",
                    attempt, _MAX_RETRIES, APP_VERSION,
                )
                resp = requests.get(UPDATE_CHECK_URL, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                data: dict = resp.json()
                remote_version = str(data.get("version", "")).strip()
                download_url = str(data.get("url") or data.get("installer_url") or "").strip()
                notes = str(data.get("notes", "")).strip()

                if remote_version and download_url and _is_newer(remote_version, APP_VERSION):
                    logger.info("Update available: %s -> %s", APP_VERSION, remote_version)
                    on_update_available(remote_version, download_url, notes)
                else:
                    logger.info(
                        "No update needed (local=%s, remote=%s)",
                        APP_VERSION, remote_version or "<empty>",
                    )
                    if on_no_update is not None:
                        on_no_update()
                return  # request succeeded — done regardless of version comparison

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Update check attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAYS[attempt - 1])

        msg = str(last_error) if last_error else "unknown error"
        logger.warning("All %d update check attempts failed: %s", _MAX_RETRIES, msg)
        if on_error is not None:
            on_error(msg)

    threading.Thread(target=_worker, daemon=True).start()
