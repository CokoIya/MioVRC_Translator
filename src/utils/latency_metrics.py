from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SUM_DURATION_KEYS = frozenset(
    {
        "context_lookup_s",
        "prompt_build_s",
        "provider_s",
        "pool_wait_s",
        "dns_s",
        "tcp_s",
        "tls_s",
        "response_headers_s",
        "provider_processing_s",
        "full_response_s",
        "parse_s",
        "postprocess_s",
        "total_s",
    }
)
_SUM_COUNT_KEYS = frozenset(
    {"context_turns", "prompt_chars", "http_request_count"}
)


def translation_metrics_snapshot(translator: Any) -> dict[str, object]:
    """Return one credential-free provider timing snapshot when supported."""

    getter = getattr(translator, "translation_metrics", None)
    if not callable(getter):
        return {}
    try:
        metrics = getter()
    except Exception:
        return {}
    return dict(metrics) if isinstance(metrics, Mapping) else {}


def merge_translation_metrics(
    aggregate: dict[str, object],
    metrics: Mapping[str, object],
) -> None:
    """Merge one sequential provider call into request-level diagnostics."""

    elapsed_before_call = _nonnegative_float(aggregate.get("total_s")) or 0.0
    nested_provider_calls = _nonnegative_int(metrics.get("provider_calls"))
    if nested_provider_calls is None:
        provider_s = _nonnegative_float(metrics.get("provider_s"))
        http_request_count = _nonnegative_int(metrics.get("http_request_count"))
        no_provider_work = bool(metrics.get("cache_hit", False)) or (
            provider_s == 0.0 and not http_request_count
        )
        nested_provider_calls = 0 if no_provider_work else 1
    aggregate["provider_calls"] = int(
        aggregate.get("provider_calls", 0) or 0
    ) + nested_provider_calls
    for key in _SUM_DURATION_KEYS:
        value = _nonnegative_float(metrics.get(key))
        if value is None:
            continue
        aggregate[key] = float(aggregate.get(key, 0.0) or 0.0) + value
    for key in _SUM_COUNT_KEYS:
        value = _nonnegative_int(metrics.get(key))
        if value is None:
            continue
        aggregate[key] = int(aggregate.get(key, 0) or 0) + value

    first_token_s = _nonnegative_float(metrics.get("first_token_s"))
    if first_token_s is not None and aggregate.get("first_token_s") is None:
        # Translators report TTFT relative to one provider call. When an
        # earlier target or fallback attempt returned no token, include its
        # elapsed time so this milestone stays relative to the logical request.
        aggregate["first_token_s"] = elapsed_before_call + first_token_s

    nested_reuse_observations = _nonnegative_int(
        metrics.get("connection_reuse_observations")
    )
    nested_reused_calls = _nonnegative_int(metrics.get("connection_reused_calls"))
    if nested_reuse_observations is not None:
        aggregate["connection_reuse_observations"] = int(
            aggregate.get("connection_reuse_observations", 0) or 0
        ) + nested_reuse_observations
        aggregate["connection_reused_calls"] = int(
            aggregate.get("connection_reused_calls", 0) or 0
        ) + int(nested_reused_calls or 0)
        aggregate["connection_reused"] = bool(
            aggregate.get("connection_reused", False)
        ) or bool(metrics.get("connection_reused", False))
    elif metrics.get("connection_reused") is not None:
        reused = bool(metrics.get("connection_reused"))
        aggregate["connection_reuse_observations"] = int(
            aggregate.get("connection_reuse_observations", 0) or 0
        ) + 1
        aggregate["connection_reused_calls"] = int(
            aggregate.get("connection_reused_calls", 0) or 0
        ) + int(reused)
        aggregate["connection_reused"] = bool(
            aggregate.get("connection_reused", False)
        ) or reused

    nested_cache_observations = _nonnegative_int(metrics.get("cache_observations"))
    nested_cache_hits = _nonnegative_int(metrics.get("cache_hits"))
    if nested_cache_observations is not None:
        aggregate["cache_observations"] = int(
            aggregate.get("cache_observations", 0) or 0
        ) + nested_cache_observations
        aggregate["cache_hits"] = int(
            aggregate.get("cache_hits", 0) or 0
        ) + int(nested_cache_hits or 0)
        aggregate["cache_hit"] = bool(
            aggregate.get("cache_hit", False)
        ) or bool(metrics.get("cache_hit", False))
    elif "cache_hit" in metrics:
        cache_hit = bool(metrics.get("cache_hit"))
        aggregate["cache_observations"] = int(
            aggregate.get("cache_observations", 0) or 0
        ) + 1
        aggregate["cache_hits"] = int(aggregate.get("cache_hits", 0) or 0) + int(
            cache_hit
        )
        aggregate["cache_hit"] = bool(aggregate.get("cache_hit", False)) or cache_hit

    if bool(metrics.get("wall_timeout_triggered", False)):
        aggregate["wall_timeout_triggered"] = True
    wall_timeout_s = _nonnegative_float(metrics.get("wall_timeout_s"))
    if wall_timeout_s is not None:
        aggregate["wall_timeout_s"] = max(
            float(aggregate.get("wall_timeout_s", 0.0) or 0.0),
            wall_timeout_s,
        )


def _nonnegative_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0.0 else None


def _nonnegative_int(value: object) -> int | None:
    parsed = _nonnegative_float(value)
    return int(parsed) if parsed is not None else None
