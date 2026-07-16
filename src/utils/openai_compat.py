from __future__ import annotations

import json
import re
from collections.abc import Mapping


_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
    }
)
_PROTECTED_SECRET_PREFIX = "dpapi:v1:"
_MAX_CUSTOM_HEADERS = 32
_MAX_HEADER_NAME_CHARS = 128
_MAX_HEADER_VALUE_CHARS = 4096


def normalize_openai_custom_headers(value: object) -> dict[str, str]:
    """Return validated OpenAI-compatible request headers.

    A JSON object is accepted for Settings/UI callers. Runtime callers may
    provide a mapping directly. Header values are never logged by this helper.
    """

    if value is None or value == "":
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Custom request headers must be a JSON object") from exc
    if not isinstance(value, Mapping):
        raise ValueError("Custom request headers must be a JSON object")
    if len(value) > _MAX_CUSTOM_HEADERS:
        raise ValueError(
            f"Custom request headers cannot exceed {_MAX_CUSTOM_HEADERS} entries"
        )

    normalized: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        folded = name.casefold()
        if (
            not name
            or len(name) > _MAX_HEADER_NAME_CHARS
            or _HEADER_NAME_RE.fullmatch(name) is None
        ):
            raise ValueError("A custom request header name is invalid")
        if folded in _FORBIDDEN_REQUEST_HEADERS:
            raise ValueError(f"Custom request header {name!r} is not allowed")
        if folded in seen:
            raise ValueError(f"Custom request header {name!r} is duplicated")

        if raw_value is None or isinstance(raw_value, (dict, list, tuple, set)):
            raise ValueError(f"Custom request header {name!r} must have a text value")
        header_value = str(raw_value)
        if header_value.startswith(_PROTECTED_SECRET_PREFIX):
            raise ValueError(
                f"Custom request header {name!r} could not be decrypted; re-enter it in Settings"
            )
        if (
            not header_value
            or len(header_value) > _MAX_HEADER_VALUE_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in header_value)
        ):
            raise ValueError(f"Custom request header {name!r} has an invalid value")

        seen.add(folded)
        normalized[name] = header_value
    return normalized


def openai_custom_headers_json(value: object) -> str:
    """Serialize validated headers for a masked Settings input field."""

    headers = normalize_openai_custom_headers(value)
    if not headers:
        return ""
    return json.dumps(headers, ensure_ascii=False, separators=(",", ":"))
