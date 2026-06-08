from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Callable

import requests

from src.updater.manifest_signature import ManifestSignatureError, verify_manifest_signature
from src.version import (
    APP_VERSION,
    REQUIRE_UPDATE_MANIFEST_SIGNATURE,
    UPDATE_CHECK_URL,
    UPDATE_CHECK_URLS,
    UPDATE_MANIFEST_PUBLIC_KEY,
    UPDATE_MANIFEST_PUBLIC_KEY_ID,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = (4, 10)
_MAX_RETRIES = 3
_RETRY_DELAYS = (5, 15)  # seconds to wait before 2nd and 3rd attempt
_MANIFEST_CACHE_BUST_PARAM = "_mio_update_check"
# Serialise all HTTPS manifest fetches to prevent SSL socket corruption on
# concurrent requests (Python 3.11 + OpenSSL 3.x + Windows: shared urllib3
# pool cannot safely handle simultaneous SSL reads across threads).
_http_lock = threading.Lock()
# Prevent concurrent update-check workers from causing SSL crashes.
_update_check_in_progress = False
_update_check_lock = threading.Lock()
_MANIFEST_HEADERS = {
    "Accept": "application/json",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
    "User-Agent": f"MioTranslator/{APP_VERSION} (+https://78hejiu.top)",
}
_TRUSTED_DIRECT_DOWNLOAD_HOSTS = {
    "78hejiu.top",
    "download.78hejiu.top",
}
_TRUSTED_GITHUB_OWNER = "cokoiya"
_TRUSTED_GITHUB_REPO = "miovrc_translator"
_TRUSTED_GITHUB_RELEASE_PREFIX = (
    f"/{_TRUSTED_GITHUB_OWNER}/{_TRUSTED_GITHUB_REPO}/releases/download/"
)

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


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    notes: str = ""
    localized_notes: dict[str, str] = field(default_factory=dict)
    installer_name: str = ""
    size_bytes: int | None = None
    sha256: str = ""


def _trusted_download_host(hostname: str | None, *, allow_release_asset_redirect: bool = False) -> bool:
    host = str(hostname or "").strip().lower()
    if not host:
        return False
    if host in _TRUSTED_DIRECT_DOWNLOAD_HOSTS or host == "github.com":
        return True
    if allow_release_asset_redirect:
        return host == "objects.githubusercontent.com" or host.endswith(".githubusercontent.com")
    return False


def is_trusted_download_url(url: str, *, allow_release_asset_redirect: bool = False) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https":
        return False
    if not _trusted_download_host(
        parsed.hostname,
        allow_release_asset_redirect=allow_release_asset_redirect,
    ):
        return False
    host = str(parsed.hostname or "").strip().lower()
    if host == "github.com":
        return parsed.path.lower().startswith(_TRUSTED_GITHUB_RELEASE_PREFIX)
    return True


def _parse_size(value: object) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _parse_sha256(value: object) -> str:
    digest = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        return digest
    return ""


def _parse_localized_notes(data: dict) -> dict[str, str]:
    localized: dict[str, str] = {}
    notes_i18n = data.get("notes_i18n") or data.get("release_notes_i18n")
    if isinstance(notes_i18n, dict):
        for key, value in notes_i18n.items():
            lang = str(key or "").strip().lower().replace("_", "-")
            text = str(value or "").strip()
            if lang and text:
                localized[lang] = text

    for key, value in data.items():
        field_name = str(key or "").strip().lower()
        prefix = ""
        if field_name.startswith("notes_") and field_name != "notes_i18n":
            prefix = "notes_"
        elif field_name.startswith("release_notes_") and field_name != "release_notes_i18n":
            prefix = "release_notes_"
        if not prefix:
            continue
        lang = field_name[len(prefix):].strip().replace("_", "-")
        text = str(value or "").strip()
        if lang and text:
            localized[lang] = text
    return localized


def _parse_update_info(data: dict) -> UpdateInfo | None:
    version = str(data.get("version", "")).strip()
    download_url = str(data.get("url") or data.get("installer_url") or "").strip()
    sha256 = _parse_sha256(data.get("sha256"))
    if not version or not download_url:
        return None
    if not sha256:
        raise RuntimeError("Update manifest is missing a valid installer SHA256")
    if not is_trusted_download_url(download_url):
        raise RuntimeError("Update manifest contains an untrusted installer URL")
    return UpdateInfo(
        version=version,
        download_url=download_url,
        notes=str(data.get("notes") or data.get("release_notes") or "").strip(),
        localized_notes=_parse_localized_notes(data),
        installer_name=str(data.get("installer_name", "")).strip(),
        size_bytes=_parse_size(data.get("size_bytes")),
        sha256=sha256,
    )


def _manifest_request_url(url: str, *, timestamp_ms: int | None = None) -> str:
    parsed = urlparse(str(url or "").strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != _MANIFEST_CACHE_BUST_PARAM
    ]
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    query.append((_MANIFEST_CACHE_BUST_PARAM, str(timestamp_ms)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _verify_update_manifest(data: dict) -> bool:
    try:
        verified = verify_manifest_signature(
            data,
            UPDATE_MANIFEST_PUBLIC_KEY,
            required=REQUIRE_UPDATE_MANIFEST_SIGNATURE,
            expected_key_id=UPDATE_MANIFEST_PUBLIC_KEY_ID,
        )
    except ManifestSignatureError as exc:
        if REQUIRE_UPDATE_MANIFEST_SIGNATURE:
            raise
        logger.warning("Update manifest signature was ignored: %s", exc)
        return False
    except Exception as exc:
        raise ManifestSignatureError(f"Update manifest signature check failed: {exc}") from exc

    if verified:
        return True
    if REQUIRE_UPDATE_MANIFEST_SIGNATURE:
        raise ManifestSignatureError("Update manifest signature is required")
    logger.warning("Update manifest signature is not configured; relying on HTTPS and SHA256 only")
    return False


def _update_check_urls() -> tuple[str, ...]:
    configured = tuple(
        str(url or "").strip()
        for url in UPDATE_CHECK_URLS
        if str(url or "").strip()
    )
    if configured:
        return configured
    return (UPDATE_CHECK_URL,)


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


def _select_newest_update_info(candidates: list[UpdateInfo]) -> UpdateInfo | None:
    selected: UpdateInfo | None = None
    for candidate in candidates:
        if selected is None or _is_newer(candidate.version, selected.version):
            selected = candidate
    return selected


def update_notes_for_language(update_info: UpdateInfo, language: str | None, *, fallback: str = "") -> str:
    lang = str(language or "").strip().lower().replace("_", "-")
    localized = update_info.localized_notes or {}
    for key in (lang, lang.split("-", 1)[0] if lang else "", "en"):
        if key and localized.get(key):
            return localized[key].strip()
    if update_info.notes:
        return update_info.notes.strip()
    for key in ("zh-cn", "zh"):
        if localized.get(key):
            return localized[key].strip()
    return str(fallback or "").strip()


def _fetch_update_info(manifest_url: str) -> UpdateInfo | None:
    with _http_lock:
        resp = requests.get(
            _manifest_request_url(manifest_url),
            headers=_MANIFEST_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = json.loads(resp.content.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise RuntimeError("Update manifest is not a JSON object")
        _verify_update_manifest(data)
        return _parse_update_info(data)


def check_for_update(
    on_update_available: Callable[[UpdateInfo], None],
    *,
    on_no_update: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    max_retries: int | None = None,
    retry_delays: tuple[float, ...] | list[float] | None = None,
) -> threading.Thread | None:
    """Silently check for updates in a daemon thread.

    Calls *on_update_available(update_info)* when a newer version is detected.
    Retries up to ``_MAX_RETRIES`` times with back-off
    on network failures.

    Optional callbacks:
    * *on_no_update* - called when the remote version is not newer.
    * *on_error* - called with an error message after all retries are exhausted.
    """
    global _update_check_in_progress
    with _update_check_lock:
        if _update_check_in_progress:
            logger.debug("Update check skipped: another check is already in progress")
            return None
        _update_check_in_progress = True

    def release_progress() -> None:
        global _update_check_in_progress
        _update_check_in_progress = False

    retries = _MAX_RETRIES if max_retries is None else max(1, int(max_retries))
    delays = tuple(_RETRY_DELAYS if retry_delays is None else retry_delays)
    last_error: Exception | None = None

    def _worker() -> None:
        nonlocal last_error
        try:

            for attempt in range(1, retries + 1):
                try:
                    logger.info(
                        "Update check attempt %d/%d  (local %s)",
                        attempt, retries, APP_VERSION,
                    )
                    candidates: list[UpdateInfo] = []
                    source_errors: list[str] = []
                    for manifest_url in _update_check_urls():
                        try:
                            update_info = _fetch_update_info(manifest_url)
                        except Exception as exc:
                            source_errors.append(f"{manifest_url}: {exc}")
                            logger.warning("Update source failed (%s): %s", manifest_url, exc)
                            continue
                        if update_info is not None:
                            candidates.append(update_info)
                    update_info = _select_newest_update_info(candidates)
                    if update_info is None and source_errors:
                        raise RuntimeError("; ".join(source_errors))
                    remote_version = update_info.version if update_info else ""

                    if update_info is not None and _is_newer(remote_version, APP_VERSION):
                        logger.info("Update available: %s -> %s", APP_VERSION, remote_version)
                        on_update_available(update_info)
                    else:
                        logger.info(
                            "No update needed (local=%s, remote=%s)",
                            APP_VERSION, remote_version or "<empty>",
                        )
                        if on_no_update is not None:
                            on_no_update()
                    return  # request succeeded; done regardless of version comparison

                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Update check attempt %d/%d failed: %s", attempt, retries, exc,
                    )
                    if attempt < retries:
                        delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 0
                        if delay > 0:
                            time.sleep(delay)

            msg = str(last_error) if last_error else "unknown error"
            logger.warning("All %d update check attempts failed: %s", retries, msg)
            if on_error is not None:
                on_error(msg)
        finally:
            release_progress()

    thread = threading.Thread(target=_worker, daemon=True, name="mio-update-check")
    thread.start()
    return thread
