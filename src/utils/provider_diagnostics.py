from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from urllib.parse import urlsplit


_SAFE_PROVIDER_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_KNOWN_PROVIDER_CODES = frozenset(
    {
        "authentication_error",
        "data_inspection_failed",
        "endpoint_not_found",
        "internal_error",
        "internalerror",
        "invalid_api_key",
        "invalid_request_error",
        "invalidparameter",
        "model_not_found",
        "provider_failure",
        "rate_limit",
        "rate_limit_exceeded",
        "service_unavailable",
        "unsupported_model",
    }
)
_STATUS_IN_MESSAGE_RE = re.compile(
    r"\b(?:http|status(?:_code)?|error\s+code)\D{0,12}([1-5][0-9]{2})\b",
    re.IGNORECASE,
)


def provider_endpoint_diagnostics(base_url: object) -> tuple[str, str]:
    """Return a path-free origin and opaque identifier for provider logs."""

    candidate = str(base_url or "").strip()
    endpoint_id = hashlib.sha256(candidate.encode("utf-8", errors="replace")).hexdigest()[:16]
    try:
        parsed = urlsplit(candidate)
        scheme = str(parsed.scheme or "").casefold()
        hostname = str(parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return "invalid-endpoint", endpoint_id
    if not scheme or not hostname:
        return "invalid-endpoint", endpoint_id
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    origin = f"{scheme}://{display_host}"
    if port is not None:
        origin = f"{origin}:{port}"
    return origin, endpoint_id


def safe_exception_summary(error: object) -> str:
    """Build a bounded provider-error summary without returning raw prose."""

    if error is None:
        return "type=UnknownError"
    error_type = type(error).__name__ or "Exception"
    fields = [f"type={error_type[:80]}"]

    status_code = _exception_status_code(error)
    if status_code is not None:
        fields.append(f"status={status_code}")

    provider_code = _exception_provider_code(error)
    if provider_code:
        fields.append(f"code={provider_code}")
    return " ".join(fields)


def _exception_status_code(error: object) -> int | None:
    candidates = [getattr(error, "status_code", None), getattr(error, "status", None)]
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        candidates.extend((response.get("status_code"), response.get("status")))
    elif response is not None:
        candidates.extend(
            (getattr(response, "status_code", None), getattr(response, "status", None))
        )
    for value in candidates:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= parsed <= 599:
            return parsed
    try:
        match = _STATUS_IN_MESSAGE_RE.search(str(error))
    except Exception:
        match = None
    return int(match.group(1)) if match is not None else None


def _exception_provider_code(error: object) -> str:
    candidates: list[object] = [getattr(error, "code", None)]
    body = getattr(error, "body", None)
    if isinstance(body, Mapping):
        nested = body.get("error")
        if isinstance(nested, Mapping):
            candidates.append(nested.get("code"))
        candidates.append(body.get("code"))
    for value in candidates:
        text = str(value or "").strip()
        if (
            _SAFE_PROVIDER_CODE_RE.fullmatch(text)
            and text.casefold() in _KNOWN_PROVIDER_CODES
        ):
            return text
    return ""
