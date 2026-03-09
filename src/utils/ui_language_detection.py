"""Resolve the initial desktop UI language from saved config, IP, or locale."""

from __future__ import annotations

import json
import locale
import os
import urllib.request
from collections.abc import Mapping

from src.utils.ui_config import DEFAULT_UI_LANGUAGE, UI_LANGUAGE_LABELS


_IP_LOOKUP_URL = "https://ipapi.co/json/"
_REQUEST_HEADERS = {
    "User-Agent": "MioTranslator/desktop",
    "Accept": "application/json",
}


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

    normalized = " ".join(candidates).lower()
    if "zh" in normalized:
        return "zh-CN"
    if "ja" in normalized:
        return "ja"
    if "ko" in normalized:
        return "ko"
    if "ru" in normalized:
        return "ru"
    return "en"


def _language_from_ip() -> str | None:
    request = urllib.request.Request(_IP_LOOKUP_URL, headers=_REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    country_code = payload.get("country_code")
    if not country_code:
        return None
    return _language_from_country(str(country_code))


def detect_initial_ui_language() -> str:
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
    changed = False

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

    detected = detect_initial_ui_language()
    if ui_cfg.get("language") != detected:
        ui_cfg["language"] = detected
        changed = True
    if ui_cfg.get("language_source") != "auto":
        ui_cfg["language_source"] = "auto"
        changed = True
    return changed
