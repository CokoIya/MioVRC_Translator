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
import urllib.request
from pathlib import Path
from typing import Callable

from src.utils.app_paths import writable_app_dir

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
_CACHE_FILENAME = "catalog_cache.json"

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


def _cache_path() -> Path:
    return writable_app_dir() / _CACHE_FILENAME


def _load_cache() -> dict | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        path = _cache_path()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
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
    with urllib.request.urlopen(_catalog_request_url(url), timeout=_TIMEOUT_S) as resp:
        charset = resp.headers.get_content_charset("utf-8")
        raw = resp.read().decode(charset)
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

    def _run() -> None:
        try:
            fresh, source_url = _fetch_remote()
            _save_cache(fresh)
            on_result(fresh)
            logger.info(
                "Catalog refreshed from remote (source=%s version=%s updated=%s)",
                source_url,
                fresh.get("version", ""),
                fresh.get("updated", ""),
            )
        except Exception as exc:
            logger.warning("Failed to fetch catalog from remote: %s", exc)
            if force_refresh:
                on_result(fallback_payload)

    threading.Thread(target=_run, daemon=True, name="catalog-fetch").start()
