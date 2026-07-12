# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

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

from src.updater.installer_signature import (
    InstallerSignatureError,
    parse_installer_signature_metadata,
    verify_installer_metadata_signature,
)
from src.updater.manifest_signature import (
    ManifestSignatureError,
    verify_manifest_signature_with_trusted_keys,
)
from src.utils.secure_http import (
    open_validated_requests_response,
    read_bounded_requests_response,
)
from src.version import (
    APP_VERSION,
    UPDATE_CHECK_URL,
    UPDATE_CHECK_URLS,
    UPDATE_MANIFEST_PUBLIC_KEY,
    UPDATE_MANIFEST_PUBLIC_KEY_ID,
    TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS,
    TRUSTED_INSTALLER_PUBLIC_KEYS,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = (4, 10)
_MAX_RETRIES = 3
_RETRY_DELAYS = (5, 15)  # seconds to wait before 2nd and 3rd attempt
_MANIFEST_CACHE_BUST_PARAM = "_mio_update_check"
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_MANIFEST_REDIRECTS = 5
# Serialise all HTTPS manifest fetches to prevent SSL socket corruption on
# concurrent requests (Python 3.11 + OpenSSL 3.x + Windows: shared urllib3
# pool cannot safely handle simultaneous SSL reads across threads).
_http_lock = threading.Lock()
# Prevent concurrent update-check workers from causing SSL crashes.  Callers
# that arrive while a check is active subscribe to that worker instead of
# being silently dropped.
_update_check_lock = threading.Lock()
_MANIFEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
    "User-Agent": f"MioTranslator/{APP_VERSION} (+https://78hejiu.top)",
}
_TRUSTED_DIRECT_DOWNLOAD_HOSTS = {
    "78hejiu.top",
    "download.78hejiu.top",
}
_TRUSTED_MANIFEST_HOSTS = frozenset(_TRUSTED_DIRECT_DOWNLOAD_HOSTS)
_TRUSTED_GITHUB_OWNER = "cokoiya"
_TRUSTED_GITHUB_REPO = "miovrc_translator"
_TRUSTED_GITHUB_RELEASE_PREFIX = (
    f"/{_TRUSTED_GITHUB_OWNER}/{_TRUSTED_GITHUB_REPO}/releases/download/"
)
_TRUSTED_GITHUB_ASSET_HOSTS = frozenset(
    {
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
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


class _UpdateSourceFailure(RuntimeError):
    """Aggregate source failure with enough information to make retry decisions."""

    def __init__(self, source_errors: dict[str, BaseException], *, retryable: bool):
        self.source_errors = dict(source_errors)
        self.retryable = bool(retryable)
        super().__init__(
            "; ".join(f"{url}: {error}" for url, error in self.source_errors.items())
        )


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    notes: str = ""
    localized_notes: dict[str, str] = field(default_factory=dict)
    installer_name: str = ""
    size_bytes: int | None = None
    sha256: str = ""
    installer_signature: str = ""
    installer_signature_algorithm: str = ""
    installer_signature_key_id: str = ""
    flow: str = "update"


@dataclass(frozen=True, slots=True)
class _UpdateCheckSubscriber:
    on_update_available: Callable[[UpdateInfo], None]
    on_no_update: Callable[[], None] | None
    on_error: Callable[[str], None] | None


@dataclass(frozen=True, slots=True)
class _InstallerFetchSubscriber:
    on_installer_available: Callable[[UpdateInfo], None]
    on_error: Callable[[str], None] | None


_MAX_WORKER_SUBSCRIBERS = 256
_update_check_in_progress = False
_update_check_thread: threading.Thread | None = None
_update_check_subscribers: list[_UpdateCheckSubscriber] = []
_installer_fetch_lock = threading.RLock()
_installer_fetch_thread: threading.Thread | None = None
_installer_fetch_subscribers: list[_InstallerFetchSubscriber] = []


def _strict_https_parts(url: str):
    try:
        parsed = urlparse(str(url or "").strip())
        host = str(parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or bool(parsed.fragment)
    ):
        return None
    return parsed, host


def _trusted_github_asset_host(host: str) -> bool:
    return host in _TRUSTED_GITHUB_ASSET_HOSTS


def _trusted_download_host(
    hostname: str | None,
    *,
    allow_release_asset_redirect: bool = False,
) -> bool:
    host = str(hostname or "").strip().rstrip(".").casefold()
    if not host:
        return False
    if host in _TRUSTED_DIRECT_DOWNLOAD_HOSTS or host == "github.com":
        return True
    return allow_release_asset_redirect and _trusted_github_asset_host(host)


def is_trusted_download_url(
    url: str,
    *,
    allow_release_asset_redirect: bool = False,
) -> bool:
    parsed_parts = _strict_https_parts(url)
    if parsed_parts is None:
        return False
    parsed, host = parsed_parts
    if not _trusted_download_host(
        host,
        allow_release_asset_redirect=allow_release_asset_redirect,
    ):
        return False
    if host == "github.com":
        return parsed.path.casefold().startswith(_TRUSTED_GITHUB_RELEASE_PREFIX)
    return True


def _is_trusted_manifest_url(
    url: str,
    *,
    allow_release_asset_redirect: bool = False,
) -> bool:
    parsed_parts = _strict_https_parts(url)
    if parsed_parts is None:
        return False
    parsed, host = parsed_parts
    path = parsed.path.casefold()
    if host in _TRUSTED_MANIFEST_HOSTS:
        return path.endswith("/installer_manifest.json")
    if host == "github.com":
        release_root = (
            f"/{_TRUSTED_GITHUB_OWNER}/{_TRUSTED_GITHUB_REPO}/releases/"
        )
        return (
            path.startswith(release_root + "latest/download/")
            or path.startswith(release_root + "download/")
        ) and path.endswith("/installer_manifest.json")
    return allow_release_asset_redirect and _trusted_github_asset_host(host)


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
    size_bytes = _parse_size(data.get("size_bytes"))
    if not version or not download_url:
        return None
    try:
        _parse_version(version)
    except ValueError as exc:
        raise RuntimeError("Update manifest contains an invalid version") from exc
    if not sha256:
        raise RuntimeError("Update manifest is missing a valid installer SHA256")
    if size_bytes is None:
        raise RuntimeError("Update manifest is missing a valid installer size")
    if not is_trusted_download_url(download_url):
        raise RuntimeError("Update manifest contains an untrusted installer URL")
    try:
        installer_signature = parse_installer_signature_metadata(data)
    except InstallerSignatureError as exc:
        raise RuntimeError(str(exc)) from exc
    return UpdateInfo(
        version=version,
        download_url=download_url,
        notes=str(data.get("notes") or data.get("release_notes") or "").strip(),
        localized_notes=_parse_localized_notes(data),
        installer_name=str(data.get("installer_name", "")).strip(),
        size_bytes=size_bytes,
        sha256=sha256,
        installer_signature=installer_signature.signature,
        installer_signature_algorithm=installer_signature.algorithm,
        installer_signature_key_id=installer_signature.key_id,
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
        verify_manifest_signature_with_trusted_keys(
            data,
            _trusted_update_manifest_public_keys(),
            required=True,
        )
        metadata = parse_installer_signature_metadata(data)
        verify_installer_metadata_signature(
            sha256=data.get("sha256"),
            size_bytes=data.get("size_bytes"),
            signature=metadata.signature,
            signature_algorithm=metadata.algorithm,
            signature_key_id=metadata.key_id,
            trusted_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
        )
        return True
    except InstallerSignatureError as exc:
        raise ManifestSignatureError(
            f"Installer metadata signature check failed: {exc}"
        ) from exc
    except ManifestSignatureError:
        raise
    except Exception as exc:
        raise ManifestSignatureError(f"Update manifest signature check failed: {exc}") from exc


def _trusted_update_manifest_public_keys() -> tuple[tuple[object, object], ...]:
    """Merge the active key with overlap keys while preserving test/embed APIs."""

    active_id = str(UPDATE_MANIFEST_PUBLIC_KEY_ID or "").strip()
    active_key = str(UPDATE_MANIFEST_PUBLIC_KEY or "").strip()
    merged: list[tuple[object, object]] = []
    if active_id or active_key:
        merged.append((active_id, active_key))
    try:
        configured = tuple(TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS)
    except TypeError:
        configured = (TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS,)
    for item in configured:
        try:
            key_id, _public_key = item
        except (TypeError, ValueError):
            merged.append(item)
            continue
        if active_id and str(key_id or "").strip() == active_id:
            continue
        merged.append(item)
    return tuple(merged)


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


def is_newer_version(candidate: str, current: str) -> bool:
    """Return whether *candidate* is a valid version newer than *current*."""

    return _is_newer(candidate, current)


def _select_newest_update_info(candidates: list[UpdateInfo]) -> UpdateInfo | None:
    selected: UpdateInfo | None = None
    for candidate in candidates:
        if selected is None or _is_newer(candidate.version, selected.version):
            selected = candidate
    return selected


def _is_permanent_update_source_error(error: BaseException) -> bool:
    """Return whether another request cannot change the outcome of this failure."""

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(current, (ManifestSignatureError, InstallerSignatureError)):
            return True
        cause = current.__cause__
        context = current.__context__
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
    return False


def _fetch_update_candidates_from_sources(
    *,
    permanent_source_errors: dict[str, BaseException] | None = None,
) -> tuple[list[UpdateInfo], dict[str, BaseException]]:
    """Fetch all usable sources while skipping deterministic failures already seen."""

    permanent_errors = (
        permanent_source_errors if permanent_source_errors is not None else {}
    )
    candidates: list[UpdateInfo] = []
    transient_errors: dict[str, BaseException] = {}
    for manifest_url in _update_check_urls():
        if manifest_url in permanent_errors:
            continue
        try:
            update_info = _fetch_update_info(manifest_url)
        except Exception as exc:
            if _is_permanent_update_source_error(exc):
                permanent_errors[manifest_url] = exc
                logger.debug(
                    "Update source rejected without retry (%s): %s",
                    manifest_url,
                    exc,
                )
            else:
                transient_errors[manifest_url] = exc
                logger.debug(
                    "Update source temporarily failed (%s): %s",
                    manifest_url,
                    exc,
                )
            continue
        if update_info is not None:
            candidates.append(update_info)
    return candidates, transient_errors


def _source_failure(
    permanent_source_errors: dict[str, BaseException],
    transient_source_errors: dict[str, BaseException],
) -> _UpdateSourceFailure:
    errors = dict(permanent_source_errors)
    errors.update(transient_source_errors)
    return _UpdateSourceFailure(errors, retryable=bool(transient_source_errors))


def _fetch_latest_installer_info_from_sources(
    *,
    permanent_source_errors: dict[str, BaseException] | None = None,
) -> UpdateInfo | None:
    permanent_errors = (
        permanent_source_errors if permanent_source_errors is not None else {}
    )
    candidates, transient_errors = _fetch_update_candidates_from_sources(
        permanent_source_errors=permanent_errors,
    )
    update_info = _select_newest_update_info(candidates)
    if update_info is None and (permanent_errors or transient_errors):
        raise _source_failure(permanent_errors, transient_errors)
    if update_info is not None and _is_newer(APP_VERSION, update_info.version):
        raise RuntimeError(
            "Newest signed installer manifest is older than the installed version"
        )
    return update_info


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
    request_url = _manifest_request_url(manifest_url)
    if not _is_trusted_manifest_url(request_url):
        raise RuntimeError("Update manifest URL is not trusted")

    with _http_lock:
        with open_validated_requests_response(
            request_url,
            url_validator=lambda candidate: _is_trusted_manifest_url(
                candidate,
                allow_release_asset_redirect=True,
            ),
            timeout=_REQUEST_TIMEOUT,
            label="Update manifest",
            headers=_MANIFEST_HEADERS,
            stream=True,
            max_redirects=_MAX_MANIFEST_REDIRECTS,
            request_get=requests.get,
        ) as resp:
            resp.raise_for_status()
            payload = read_bounded_requests_response(
                resp,
                limit=_MAX_MANIFEST_BYTES,
                label="Update manifest",
            )
        data = json.loads(payload.decode("utf-8-sig"))
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
) -> threading.Thread:
    """Silently check for updates in a daemon thread.

    Calls *on_update_available(update_info)* when a newer version is detected.
    Retries up to ``_MAX_RETRIES`` times with back-off
    on network failures.

    Optional callbacks:
    * *on_no_update* - called when the remote version is not newer.
    * *on_error* - called when the check cannot complete safely.
    """
    retries = _MAX_RETRIES if max_retries is None else max(1, int(max_retries))
    delays = tuple(_RETRY_DELAYS if retry_delays is None else retry_delays)
    subscriber = _UpdateCheckSubscriber(
        on_update_available=on_update_available,
        on_no_update=on_no_update,
        on_error=on_error,
    )

    def _worker() -> None:
        global _update_check_in_progress
        global _update_check_thread

        last_error: Exception | None = None
        outcome = "error"
        outcome_value: UpdateInfo | str | None = "unknown error"
        permanent_source_errors: dict[str, BaseException] = {}
        try:
            for attempt in range(1, retries + 1):
                try:
                    logger.info(
                        "Update check attempt %d/%d  (local %s)",
                        attempt, retries, APP_VERSION,
                    )
                    candidates, transient_errors = _fetch_update_candidates_from_sources(
                        permanent_source_errors=permanent_source_errors,
                    )
                    update_info = _select_newest_update_info(candidates)
                    if update_info is None and (
                        permanent_source_errors or transient_errors
                    ):
                        raise _source_failure(
                            permanent_source_errors,
                            transient_errors,
                        )
                    remote_version = update_info.version if update_info else ""

                    if update_info is not None and _is_newer(remote_version, APP_VERSION):
                        logger.info("Update available: %s -> %s", APP_VERSION, remote_version)
                        outcome = "update"
                        outcome_value = update_info
                    else:
                        logger.info(
                            "No update needed (local=%s, remote=%s)",
                            APP_VERSION, remote_version or "<empty>",
                        )
                        outcome = "no_update"
                        outcome_value = None
                    break  # request succeeded; done regardless of version comparison

                except Exception as exc:
                    last_error = exc
                    retryable = not isinstance(exc, _UpdateSourceFailure) or exc.retryable
                    if not retryable:
                        break
                    if attempt < retries:
                        logger.warning(
                            "Update check attempt %d/%d failed; retrying: %s",
                            attempt,
                            retries,
                            exc,
                        )
                        delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 0
                        if delay > 0:
                            time.sleep(delay)
            if outcome == "error":
                outcome_value = str(last_error) if last_error else "unknown error"
                logger.warning("Update check failed: %s", outcome_value)
        except Exception as exc:
            outcome = "error"
            outcome_value = str(exc) or exc.__class__.__name__
            logger.exception("Unexpected update-check worker failure")
        finally:
            with _update_check_lock:
                subscribers = tuple(_update_check_subscribers)
                _update_check_subscribers.clear()
                _update_check_thread = None
                _update_check_in_progress = False

        for current in subscribers:
            callback: Callable[..., None] | None
            callback_args: tuple[object, ...]
            if outcome == "update":
                callback = current.on_update_available
                callback_args = (outcome_value,)
            elif outcome == "no_update":
                callback = current.on_no_update
                callback_args = ()
            else:
                callback = current.on_error
                callback_args = (str(outcome_value or "unknown error"),)
            if callback is None:
                continue
            try:
                callback(*callback_args)
            except Exception:
                logger.exception("Update-check subscriber callback failed")

    thread = threading.Thread(target=_worker, daemon=True, name="mio-update-check")
    global _update_check_in_progress
    global _update_check_thread
    with _update_check_lock:
        if _update_check_in_progress:
            active_thread = _update_check_thread
            if active_thread is None:
                raise RuntimeError("Update checker entered an invalid active state")
            if len(_update_check_subscribers) < _MAX_WORKER_SUBSCRIBERS:
                _update_check_subscribers.append(subscriber)
            else:
                logger.warning(
                    "Update-check subscriber limit reached (%d); dropping callback",
                    _MAX_WORKER_SUBSCRIBERS,
                )
            logger.debug("Update check joined the active worker")
            return active_thread

        _update_check_in_progress = True
        _update_check_thread = thread
        _update_check_subscribers.clear()
        _update_check_subscribers.append(subscriber)
        try:
            # Start while holding the state lock so another caller can never
            # receive a Thread object that has not started yet.
            thread.start()
        except BaseException:
            _update_check_subscribers.clear()
            _update_check_thread = None
            _update_check_in_progress = False
            raise
        return thread


def fetch_latest_installer_info(
    on_installer_available: Callable[[UpdateInfo], None],
    *,
    on_error: Callable[[str], None] | None = None,
    max_retries: int | None = None,
    retry_delays: tuple[float, ...] | list[float] | None = None,
) -> threading.Thread:
    """Fetch a trusted current-or-newer installer for repair or reinstall.

    This is used for repair/reinstall flows where the currently installed app can
    already report the latest version but still be missing bundled runtime files.
    Older signed releases are rejected to prevent repair-triggered downgrades.
    """
    retries = _MAX_RETRIES if max_retries is None else max(1, int(max_retries))
    delays = tuple(_RETRY_DELAYS if retry_delays is None else retry_delays)
    subscriber = _InstallerFetchSubscriber(
        on_installer_available=on_installer_available,
        on_error=on_error,
    )

    def _worker() -> None:
        global _installer_fetch_thread

        last_error: Exception | None = None
        update_info: UpdateInfo | None = None
        permanent_source_errors: dict[str, BaseException] = {}
        try:
            for attempt in range(1, retries + 1):
                try:
                    logger.info("Installer manifest fetch attempt %d/%d", attempt, retries)
                    update_info = _fetch_latest_installer_info_from_sources(
                        permanent_source_errors=permanent_source_errors,
                    )
                    if update_info is None:
                        raise RuntimeError("Installer manifest did not contain a valid download")
                    break
                except Exception as exc:
                    update_info = None
                    last_error = exc
                    retryable = not isinstance(exc, _UpdateSourceFailure) or exc.retryable
                    if not retryable:
                        break
                    if attempt < retries:
                        logger.warning(
                            "Installer manifest fetch attempt %d/%d failed; retrying: %s",
                            attempt,
                            retries,
                            exc,
                        )
                        delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 0
                        if delay > 0:
                            time.sleep(delay)
        except Exception as exc:
            update_info = None
            last_error = exc
            logger.exception("Unexpected installer-manifest worker failure")
        finally:
            with _installer_fetch_lock:
                subscribers = tuple(_installer_fetch_subscribers)
                _installer_fetch_subscribers.clear()
                _installer_fetch_thread = None

        if update_info is not None:
            for current in subscribers:
                try:
                    current.on_installer_available(update_info)
                except Exception:
                    logger.exception("Installer-manifest subscriber callback failed")
            return

        msg = str(last_error) if last_error else "unknown error"
        logger.warning("Installer manifest fetch failed: %s", msg)
        for current in subscribers:
            if current.on_error is None:
                continue
            try:
                current.on_error(msg)
            except Exception:
                logger.exception("Installer-manifest error callback failed")

    global _installer_fetch_thread
    with _installer_fetch_lock:
        if _installer_fetch_thread is not None:
            active_thread = _installer_fetch_thread
            if len(_installer_fetch_subscribers) < _MAX_WORKER_SUBSCRIBERS:
                _installer_fetch_subscribers.append(subscriber)
            else:
                logger.warning(
                    "Installer-manifest subscriber limit reached (%d); dropping callback",
                    _MAX_WORKER_SUBSCRIBERS,
                )
            logger.debug("Installer manifest fetch joined the active worker")
            return active_thread

        thread = threading.Thread(
            target=_worker,
            daemon=True,
            name="mio-installer-manifest-fetch",
        )
        _installer_fetch_subscribers.clear()
        _installer_fetch_subscribers.append(subscriber)
        _installer_fetch_thread = thread
        try:
            thread.start()
        except BaseException:
            _installer_fetch_subscribers.clear()
            _installer_fetch_thread = None
            raise
        return thread
