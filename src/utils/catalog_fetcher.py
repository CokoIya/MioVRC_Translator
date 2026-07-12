"""Remote catalog fetcher with local cache.

Fetches catalog.json from GitHub first, then falls back to the mirror.
Falls back to a cached copy when offline.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.utils.app_paths import (
    atomic_write_text,
    read_secure_text,
    secure_file_path,
    writable_app_dir,
)
from src.utils.secure_http import open_trusted_https_url, read_bounded_response

logger = logging.getLogger(__name__)

GITHUB_CATALOG_URL = (
    "https://raw.githubusercontent.com/"
    "CokoIya/MioVRC_Translator/main/docs/catalog.json"
)
MIRROR_CATALOG_URL = "https://78hejiu.top/catalog.json"
CATALOG_SOURCE_URLS = (
    GITHUB_CATALOG_URL,
    MIRROR_CATALOG_URL,
)
_TIMEOUT_S = 8
_MAX_CACHE_BYTES = 4 * 1024 * 1024
_MAX_REMOTE_BYTES = 4 * 1024 * 1024
_CACHE_FILENAME = "catalog_cache.json"
_TRUSTED_SOURCE_HOSTS = frozenset(
    filter(
        None,
        (urllib.parse.urlsplit(url).hostname for url in CATALOG_SOURCE_URLS),
    )
)

_EMPTY_CATALOG: dict = {
    "version": 1,
    "updated": "",
    "translation_backends": {},
    "translation_model_presets": {},
    "translation_model_profiles": {},
    "translation_backend_region_base_urls": {},
    "translation_backend_region_aliases": {},
    "translation_backend_default_regions": {},
}


CatalogCallback = Callable[[dict], None]


@dataclass(frozen=True, slots=True)
class _CatalogSubscriber:
    callback: CatalogCallback
    fallback_on_error: bool
    fallback_payload: dict


_MAX_FETCH_SUBSCRIBERS = 256
_fetch_lock = threading.RLock()
_fetch_thread: threading.Thread | None = None
_fetch_subscribers: list[_CatalogSubscriber] = []


def _cache_path() -> Path:
    return secure_file_path(writable_app_dir() / _CACHE_FILENAME)


def _load_cache() -> dict | None:
    try:
        path = _cache_path()
        if not path.exists():
            return None
        data = json.loads(
            read_secure_text(path, encoding="utf-8", max_bytes=_MAX_CACHE_BYTES)
        )
        return _validate_catalog_payload(data)
    except Exception:
        logger.debug("Failed to load %s cache", "catalog", exc_info=True)
        return None


def _save_cache(data: dict) -> None:
    try:
        path = _cache_path()
        atomic_write_text(
            path,
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Failed to save catalog cache", exc_info=True)


def _catalog_request_url(url: str, now: float | None = None) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_", str(int((time.time() if now is None else now) * 1000))))
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query))
    )


def _validate_catalog_payload(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("catalog.json is not a JSON object")
    backends = data.get("translation_backends", {})
    if not isinstance(backends, dict):
        raise ValueError("catalog.json field 'translation_backends' must be an object")
    return data


def _fetch_remote_from_url(url: str) -> dict:
    with open_trusted_https_url(
        _catalog_request_url(url),
        trusted_hosts=_TRUSTED_SOURCE_HOSTS,
        timeout=_TIMEOUT_S,
        label="catalog",
    ) as resp:
        charset = resp.headers.get_content_charset("utf-8")
        payload = read_bounded_response(
            resp,
            limit=_MAX_REMOTE_BYTES,
            label="catalog response",
        )
        raw = payload.decode(charset)
    return _validate_catalog_payload(json.loads(raw))


def _fetch_remote() -> tuple[dict, str]:
    errors: list[str] = []
    for source_url in CATALOG_SOURCE_URLS:
        try:
            return _fetch_remote_from_url(source_url), source_url
        except Exception as exc:
            errors.append(f"{source_url}: {exc}")
    raise RuntimeError("; ".join(errors) or "no catalog sources available")


def get_catalog(
    on_result: CatalogCallback,
    *,
    force_refresh: bool = False,
) -> None:
    """Return catalog data via callback, fetching in background.

    Default mode serves cached data (or the empty template) immediately, then
    refreshes in the background.

    Force-refresh mode prefers a fresh remote result first; on failure, it falls
    back to the cached payload (or the empty template).
    """
    cached = _load_cache()
    fallback_payload = cached if cached is not None else _EMPTY_CATALOG

    if not force_refresh:
        on_result(fallback_payload)
        if cached is not None:
            logger.info(
                "Catalog served from cache (version=%s updated=%s)",
                cached.get("version", ""),
                cached.get("updated", ""),
            )

    subscriber = _CatalogSubscriber(
        callback=on_result,
        fallback_on_error=bool(force_refresh),
        fallback_payload=fallback_payload,
    )

    def _run() -> None:
        global _fetch_thread

        fresh: dict | None = None
        source_url = ""
        try:
            fresh, source_url = _fetch_remote()
            _save_cache(fresh)
            logger.info(
                "Catalog refreshed from remote (source=%s version=%s updated=%s)",
                source_url,
                fresh.get("version", ""),
                fresh.get("updated", ""),
            )
        except Exception as exc:
            logger.warning("Failed to fetch catalog from remote: %s", exc)
        finally:
            with _fetch_lock:
                subscribers = tuple(_fetch_subscribers)
                _fetch_subscribers.clear()
                _fetch_thread = None

        for current in subscribers:
            payload = fresh if fresh is not None else (
                current.fallback_payload if current.fallback_on_error else None
            )
            if payload is None:
                continue
            try:
                current.callback(payload)
            except Exception:
                logger.exception("Catalog result callback failed")

    global _fetch_thread
    overflow = False
    with _fetch_lock:
        if len(_fetch_subscribers) >= _MAX_FETCH_SUBSCRIBERS:
            overflow = True
        else:
            _fetch_subscribers.append(subscriber)
            if _fetch_thread is not None:
                return
            active_thread = threading.Thread(
                target=_run,
                daemon=True,
                name="catalog-fetch",
            )
            _fetch_thread = active_thread
            try:
                # Starting under the re-entrant state lock keeps an inline test
                # worker and a real worker equally race-free.
                active_thread.start()
            except BaseException:
                _fetch_subscribers.clear()
                _fetch_thread = None
                raise
            return

    logger.warning(
        "Catalog refresh subscriber limit reached (%d); dropping remote callback",
        _MAX_FETCH_SUBSCRIBERS,
    )
    if overflow and force_refresh:
        on_result(fallback_payload)
