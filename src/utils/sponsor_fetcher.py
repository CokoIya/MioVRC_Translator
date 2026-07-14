"""Remote sponsor list fetcher with local cache.

Fetches sponsors.json from GitHub first, then falls back to the mirror.
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

GITHUB_SPONSORS_URL = (
    "https://raw.githubusercontent.com/"
    "CokoIya/MioVRC_Translator/main/docs/sponsors.json"
)
MIRROR_SPONSORS_URL = "https://miovrc.com/sponsors.json"
SPONSOR_SOURCE_URLS = (
    GITHUB_SPONSORS_URL,
    MIRROR_SPONSORS_URL,
)
_TIMEOUT_S = 8
_MAX_CACHE_BYTES = 4 * 1024 * 1024
_MAX_REMOTE_BYTES = 4 * 1024 * 1024
_CACHE_FILENAME = "sponsors_cache.json"
_MAX_SPONSORS = 500
_MAX_SPONSOR_NAME_CHARS = 128
_MAX_TIP_LANGUAGES = 16
_MAX_TIP_CHARS = 1024
_MAX_UPDATED_CHARS = 64
_TRUSTED_SOURCE_HOSTS = frozenset(
    filter(
        None,
        (urllib.parse.urlsplit(url).hostname for url in SPONSOR_SOURCE_URLS),
    )
)

_EMPTY: dict = {
    "version": 1,
    "updated": "",
    "tip": {
        "zh": "赞助请在备注中留下您的称呼或 VRC ID，感谢您的支持！",
        "ja": "ご支援の際は備考にお名前または VRC ID をご記入ください。",
        "en": "Please include your name or VRC ID in the donation note. Thank you!",
        "ko": "후원 시 비고란에 닉네임 또는 VRC ID를 남겨 주세요. 감사합니다!",
        "ru": "При донате укажите ваш ник или VRC ID в примечании. Спасибо!",
    },
    "sponsors": [],
}


SponsorCallback = Callable[[dict], None]


@dataclass(frozen=True, slots=True)
class _SponsorSubscriber:
    callback: SponsorCallback
    fallback_on_error: bool
    fallback_payload: dict


_MAX_FETCH_SUBSCRIBERS = 256
_fetch_lock = threading.RLock()
_fetch_thread: threading.Thread | None = None
_fetch_subscribers: list[_SponsorSubscriber] = []


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
        return _validate_sponsors_payload(data)
    except Exception:
        logger.debug("Failed to load %s cache", "sponsors", exc_info=True)
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
        logger.debug("Failed to save sponsors cache", exc_info=True)


def _sponsors_request_url(url: str, now: float | None = None) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_", str(int((time.time() if now is None else now) * 1000))))
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query))
    )


def _validate_sponsors_payload(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("sponsors.json is not a JSON object")
    sponsors = data.get("sponsors", [])
    if not isinstance(sponsors, list):
        raise ValueError("sponsors.json field 'sponsors' must be a list")
    tip = data.get("tip", {})
    if tip is not None and not isinstance(tip, dict):
        raise ValueError("sponsors.json field 'tip' must be an object")

    clean_tip: dict[str, str] = {}
    for raw_language, raw_text in (tip or {}).items():
        language = str(raw_language or "").strip().lower().replace("_", "-")
        text = " ".join(str(raw_text or "").split()).strip()
        if (
            not language
            or len(language) > 24
            or not language.isprintable()
            or not text
            or not text.isprintable()
        ):
            continue
        clean_tip[language] = text[:_MAX_TIP_CHARS]
        if len(clean_tip) >= _MAX_TIP_LANGUAGES:
            break

    clean_sponsors: list[dict[str, str]] = []
    for item in sponsors:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split()).strip()
        if not name or not name.isprintable():
            continue
        clean_sponsors.append({"name": name[:_MAX_SPONSOR_NAME_CHARS]})
        if len(clean_sponsors) >= _MAX_SPONSORS:
            break

    updated = " ".join(str(data.get("updated") or "").split()).strip()
    if not updated.isprintable():
        updated = ""
    version = data.get("version", 1)
    if not isinstance(version, (int, str)) or isinstance(version, bool):
        version = 1
    return {
        "version": version,
        "updated": updated[:_MAX_UPDATED_CHARS],
        "tip": clean_tip,
        "sponsors": clean_sponsors,
    }


def _fetch_remote_from_url(url: str) -> dict:
    with open_trusted_https_url(
        _sponsors_request_url(url),
        trusted_hosts=_TRUSTED_SOURCE_HOSTS,
        timeout=_TIMEOUT_S,
        label="sponsors",
    ) as resp:
        charset = resp.headers.get_content_charset("utf-8")
        payload = read_bounded_response(
            resp,
            limit=_MAX_REMOTE_BYTES,
            label="sponsors response",
        )
        raw = payload.decode(charset)
    return _validate_sponsors_payload(json.loads(raw))


def _fetch_remote() -> tuple[dict, str]:
    errors: list[str] = []
    for source_url in SPONSOR_SOURCE_URLS:
        try:
            return _fetch_remote_from_url(source_url), source_url
        except Exception as exc:
            errors.append(f"{source_url}: {exc}")
    raise RuntimeError("; ".join(errors) or "no sponsor sources available")


def get_sponsors(
    on_result: SponsorCallback,
    *,
    force_refresh: bool = False,
) -> None:
    """Return sponsors data via callback, fetching in background.

    Default mode serves cached data (or the empty template) immediately, then
    refreshes in the background.

    Force-refresh mode prefers a fresh remote result first; on failure, it falls
    back to the cached payload (or the empty template).
    """
    cached = _load_cache()
    fallback_payload = cached if cached is not None else _EMPTY

    if not force_refresh:
        on_result(fallback_payload)
        if cached is not None:
            logger.info(
                "Sponsors list served from cache (version=%s count=%s)",
                cached.get("version", ""),
                len(cached.get("sponsors", []) if isinstance(cached.get("sponsors"), list) else []),
            )

    subscriber = _SponsorSubscriber(
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
                "Sponsors list refreshed from remote (source=%s version=%s count=%s)",
                source_url,
                fresh.get("version", ""),
                len(fresh.get("sponsors", []) if isinstance(fresh.get("sponsors"), list) else []),
            )
        except Exception as exc:
            logger.warning("Failed to fetch sponsors from remote: %s", exc)
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
                logger.exception("Sponsors result callback failed")

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
                name="sponsor-fetch",
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
        "Sponsors refresh subscriber limit reached (%d); dropping remote callback",
        _MAX_FETCH_SUBSCRIBERS,
    )
    if overflow and force_refresh:
        on_result(fallback_payload)
