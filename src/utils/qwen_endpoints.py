from __future__ import annotations

import re
import urllib.parse

from src.utils.secure_http import validate_api_base_url

QWEN_TOKYO_COMPATIBLE_MODE_PATH = "/compatible-mode/v1"
QWEN_TOKYO_DASHSCOPE_PATH = "/api/v1"

_QWEN_SHARED_API_HOSTS = frozenset(
    {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
    }
)
_QWEN_TOKYO_WORKSPACE_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"\.ap-northeast-1\.maas\.aliyuncs\.com$",
    re.IGNORECASE,
)


def _normalized_hostname(value: object) -> str:
    text = str(value or "").strip()
    if "://" in text:
        text = urllib.parse.urlsplit(text).hostname or ""
    return text.rstrip(".").casefold()


def is_qwen_tokyo_workspace_host(hostname: object) -> bool:
    host = _normalized_hostname(hostname)
    return bool(_QWEN_TOKYO_WORKSPACE_HOST_RE.fullmatch(host))


def is_qwen_translation_api_host(
    hostname: object,
    *,
    provider_id: object = "",
) -> bool:
    """Identify official Qwen translation hosts without trusting lookalikes."""

    host = _normalized_hostname(hostname)
    if host in _QWEN_SHARED_API_HOSTS:
        return True
    return (
        str(provider_id or "").strip().casefold() == "qianwen"
        and is_qwen_tokyo_workspace_host(host)
    )


def is_qwen_tokyo_workspace_base_url(
    url: object,
    *,
    endpoint_path: str,
) -> bool:
    candidate = str(url or "").strip()
    if not candidate:
        return False
    try:
        normalized = validate_api_base_url(candidate, label="Qwen Tokyo workspace API")
        parsed = urllib.parse.urlsplit(normalized)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and port in (None, 443)
        and is_qwen_tokyo_workspace_host(parsed.hostname)
        and parsed.path.rstrip("/") == endpoint_path.rstrip("/")
    )


def require_qwen_tokyo_workspace_base_url(
    url: object,
    *,
    endpoint_path: str,
    label: str,
) -> str:
    candidate = str(url or "").strip()
    if not is_qwen_tokyo_workspace_base_url(
        candidate,
        endpoint_path=endpoint_path,
    ):
        raise ValueError(
            f"{label} must be an HTTPS Tokyo workspace endpoint ending in "
            f"{endpoint_path}"
        )
    return validate_api_base_url(candidate, label=label)
