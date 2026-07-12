from __future__ import annotations

import ctypes
import json
import locale
import os
from collections.abc import Mapping

from src.utils.secure_http import open_trusted_https_url, read_bounded_response
from src.utils.ui_config import DEFAULT_UI_LANGUAGE, UI_LANGUAGE_LABELS


_IP_LOOKUP_URL = "https://ipapi.co/json/"
_IP_LOOKUP_HOSTS = frozenset({"ipapi.co"})
_MAX_IP_LOOKUP_BYTES = 64 * 1024
_REQUEST_HEADERS = {
    "User-Agent": "MioTranslator/desktop",
    "Accept": "application/json",
}


def _language_from_tag(value: object) -> str:
    """Map an OS locale/UI-language tag to one of Mio's UI catalogs."""
    tag = str(value or "").strip().replace("_", "-").casefold()
    tag = tag.split(".", 1)[0].split("@", 1)[0]
    primary = tag.split("-", 1)[0]
    if primary == "zh":
        return "zh-CN"
    if primary in {"en", "ja", "ru", "ko"}:
        return primary
    return "en"


def _windows_display_language_tag() -> str | None:
    """Return the current user's Windows display-language locale name."""
    try:
        language_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except (AttributeError, OSError, TypeError, ValueError):
        return None

    locale_name = locale.windows_locale.get(language_id)
    if locale_name:
        return locale_name

    # Keep the five supported primary languages working even when Python's
    # Windows locale table does not contain a newly introduced sublanguage.
    return {
        0x0004: "zh",
        0x0009: "en",
        0x0011: "ja",
        0x0012: "ko",
        0x0019: "ru",
    }.get(language_id & 0x03FF)


def _language_from_country(country_code: str | None) -> str:
    code = str(country_code or "").upper()
    if code in {"CN", "TW", "HK", "MO", "SG"}:
        return "zh-CN"
    if code == "JP":
        return "ja"
    if code == "KR":
        return "ko"
    if code in {"RU", "BY", "KZ"}:
        return "ru"
    return "en"


def _language_from_locale() -> str:
    # Windows' display language can intentionally differ from its regional
    # format. GetUserDefaultUILanguage reflects the former; locale.getlocale()
    # generally reflects the latter and therefore is only a fallback.
    windows_display_language = _windows_display_language_tag()
    if windows_display_language:
        return _language_from_tag(windows_display_language)

    candidates: list[str] = []
    for env_name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)

    try:
        current = locale.getlocale()[0]
    except Exception:
        current = None
    if current:
        candidates.append(current)

    if candidates:
        return _language_from_tag(candidates[0])
    return "en"


def _language_from_ip() -> str | None:
    try:
        with open_trusted_https_url(
            _IP_LOOKUP_URL,
            trusted_hosts=_IP_LOOKUP_HOSTS,
            timeout=1.2,
            label="IP language lookup",
            headers=_REQUEST_HEADERS,
            max_redirects=2,
        ) as response:
            payload = json.loads(
                read_bounded_response(
                    response,
                    limit=_MAX_IP_LOOKUP_BYTES,
                    label="IP language lookup response",
                ).decode("utf-8")
            )
    except Exception:
        return None

    if not isinstance(payload, Mapping):
        return None
    country_code = payload.get("country_code")
    if not country_code:
        return None
    return _language_from_country(str(country_code))


def detect_initial_ui_language(*, allow_ip_lookup: bool = False) -> str:
    if allow_ip_lookup:
        detected = _language_from_ip()
        if detected in UI_LANGUAGE_LABELS:
            return detected
    return _language_from_locale()


def bootstrap_ui_language(config: dict, *, prefer_auto: bool = False) -> bool:
    ui_cfg = config.setdefault("ui", {})
    if not isinstance(ui_cfg, dict):
        return False

    language = ui_cfg.get("language")
    source = str(ui_cfg.get("language_source") or "").lower()
    allow_ip_lookup = bool(ui_cfg.get("allow_ip_language_detection", False))
    changed = False

    if "allow_ip_language_detection" not in ui_cfg:
        ui_cfg["allow_ip_language_detection"] = False
        changed = True

    if source == "manual" and language in UI_LANGUAGE_LABELS:
        return False

    if source not in {"auto", "manual"}:
        if language in UI_LANGUAGE_LABELS and not prefer_auto:
            ui_cfg["language_source"] = "manual"
            return True
        ui_cfg["language_source"] = "auto"
        changed = True

    if ui_cfg.get("language_source") == "manual":
        if language not in UI_LANGUAGE_LABELS:
            ui_cfg["language"] = DEFAULT_UI_LANGUAGE
            changed = True
        return changed

    if language in UI_LANGUAGE_LABELS and not prefer_auto:
        return changed

    detected = detect_initial_ui_language(allow_ip_lookup=allow_ip_lookup)
    if ui_cfg.get("language") != detected:
        ui_cfg["language"] = detected
        changed = True
    if ui_cfg.get("language_source") != "auto":
        ui_cfg["language_source"] = "auto"
        changed = True
    return changed
