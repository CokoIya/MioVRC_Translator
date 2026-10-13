"""Catalog loader — merges remote catalog with builtin defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any

from src.utils.model_retirement import (  # noqa: F401 - re-exported
    is_retired_translation_model,
    retired_catalog_model_ids,
)
from src.utils.ui_config import (
    ANTHROPIC_MODEL_ID_PREFIXES,
    DEEPSEEK_PRO_MODEL_PATTERN,
    RELAY_BACKEND,
    TRANSLATION_BACKENDS,
    TRANSLATION_MODEL_PRESETS,
    TRANSLATION_MODEL_PROFILES,
    TRANSLATION_BACKEND_REGION_BASE_URLS,
    TRANSLATION_BACKEND_REGION_ALIASES,
    TRANSLATION_BACKEND_DEFAULT_REGIONS,
)


@dataclass(frozen=True)
class TranslationCatalog:
    translation_backends: dict[str, dict[str, Any]] = field(
        default_factory=lambda: dict(TRANSLATION_BACKENDS)
    )
    translation_model_presets: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(TRANSLATION_MODEL_PRESETS)
    )
    translation_model_profiles: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=lambda: dict(TRANSLATION_MODEL_PROFILES)
    )
    translation_backend_region_base_urls: dict[str, dict[str, str]] = field(
        default_factory=lambda: dict(TRANSLATION_BACKEND_REGION_BASE_URLS)
    )
    translation_backend_region_aliases: dict[str, dict[str, str]] = field(
        default_factory=lambda: dict(TRANSLATION_BACKEND_REGION_ALIASES)
    )
    translation_backend_default_regions: dict[str, str] = field(
        default_factory=lambda: dict(TRANSLATION_BACKEND_DEFAULT_REGIONS)
    )


BUILTIN_CATALOG = TranslationCatalog()

# Remote catalog section read by this release. The top-level
# "translation_backends" / "translation_model_presets" /
# "translation_model_profiles" sections are kept in catalog.json for already
# installed older releases, whose backend set differs (separate GPT/Grok/
# Claude-compatible relays) and whose values went stale; reading them here
# would put retired defaults back. Without this section the builtin tables
# apply unchanged.
CATALOG_TRANSLATION_SECTION = "translation_v2"

_OPENAI_MODEL_PREFIXES = ("gpt-", "o", "chatgpt-")
_OPENAI_ALLOWED_LABELS = {"gpt", "openai", "chatgpt", "chatgpt / openai", "openai / gpt"}
_REMOTE_PROTECTED_BACKEND_FIELDS = frozenset(
    {
        "base_url",
        "base_url_input",
        "api_key_required",
        "api_key_input",
        "custom_headers_input",
        "extra_body",
        "prefer_max_completion_tokens",
        "streaming",
        "streaming_input",
    }
)
_REMOTE_MUTABLE_BACKEND_FIELDS = frozenset(
    {
        "api_key_hint",
        "label",
        "max_output_tokens",
        "max_retries",
        "model",
        "model_hint",
        "model_input",
        "timeout_s",
    }
)
_PROFILE_FIELDS = frozenset({"fit", "note", "quality", "speed"})
_MAX_BACKEND_TEXT_CHARS = 512
_MAX_MODEL_ID_CHARS = 256
_MAX_PRESETS_PER_BACKEND = 128
_MAX_PROFILES_PER_BACKEND = 128
_MAX_PROFILE_VALUE_CHARS = 64
# DeepSeek is offered as flash only; a remote catalog cannot recommend a
# DeepSeek pro model on any backend (relays and aggregators included).
_DEEPSEEK_PRO_MODEL_RE = re.compile(DEEPSEEK_PRO_MODEL_PATTERN, re.IGNORECASE)


def _clean_single_line(value: object, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > max_chars or not text.isprintable():
        return ""
    return text


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        normalized = int(default)
    return max(minimum, min(normalized, maximum))


def _bounded_float(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        normalized = float(default)
    if not math.isfinite(normalized):
        normalized = float(default)
    return max(minimum, min(normalized, maximum))


def _sanitize_remote_backend(
    remote_backend: dict[str, object],
    builtin: dict[str, Any],
) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key in _REMOTE_MUTABLE_BACKEND_FIELDS:
        if key not in remote_backend:
            continue
        value = remote_backend[key]
        if key == "timeout_s":
            cleaned[key] = _bounded_float(
                value,
                default=float(builtin.get(key, 15.0)),
                minimum=1.0,
                maximum=120.0,
            )
        elif key == "max_output_tokens":
            cleaned[key] = _bounded_int(
                value,
                default=int(builtin.get(key, 256)),
                minimum=16,
                maximum=4096,
            )
        elif key == "max_retries":
            cleaned[key] = _bounded_int(
                value,
                default=int(builtin.get(key, 0)),
                minimum=0,
                maximum=5,
            )
        elif key == "model_input":
            normalized = str(value or "").strip().lower()
            if normalized in {"editable", "entry", "select"}:
                cleaned[key] = normalized
        else:
            max_chars = _MAX_MODEL_ID_CHARS if key == "model" else _MAX_BACKEND_TEXT_CHARS
            normalized = _clean_single_line(value, max_chars=max_chars)
            if normalized:
                cleaned[key] = normalized
    return cleaned


def _is_openai_model_id(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text.startswith(_OPENAI_MODEL_PREFIXES)


def _is_retired_model(backend_id: str, value: object) -> bool:
    """Whether a remote catalog must not offer ``value`` for ``backend_id``.

    Ids that are retired (the shared tables in ``model_retirement``) or that
    the app deliberately does not offer (DeepSeek pro models).
    """

    text = str(value or "").strip().lower()
    if _DEEPSEEK_PRO_MODEL_RE.search(text):
        return True
    return is_retired_translation_model(backend_id, text)


def _is_allowed_openai_model_id(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not _is_openai_model_id(text):
        return False
    return not _is_retired_model("openai", text)


def _is_allowed_anthropic_model_id(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith(ANTHROPIC_MODEL_ID_PREFIXES)


def _dedupe_presets(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_single_line(value, max_chars=_MAX_MODEL_ID_CHARS)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= _MAX_PRESETS_PER_BACKEND:
            break
    return tuple(result)


def _sanitize_openai_backend(backends: dict[str, dict[str, Any]]) -> None:
    for backend_id in ("openai", RELAY_BACKEND):
        backend = backends.get(backend_id)
        builtin = TRANSLATION_BACKENDS[backend_id]
        if not isinstance(backend, dict):
            backends[backend_id] = dict(builtin)
            continue
        if backend_id == "openai":
            label = str(backend.get("label", "") or "").strip().lower()
            if label and label not in _OPENAI_ALLOWED_LABELS:
                backend["label"] = builtin["label"]
            if not _is_allowed_openai_model_id(backend.get("model")):
                backend["model"] = builtin["model"]
        else:
            # The relay entry is defined by the app: its name, hints and the
            # editable model field must not be replaced by an older catalog
            # that still describes the separate GPT-compatible backend.
            for field_name in ("label", "model", "model_input", "api_key_hint", "model_hint"):
                if field_name in builtin:
                    backend[field_name] = builtin[field_name]
        if str(backend.get("base_url", "") or "").strip().rstrip("/") == "https://api.anthropic.com":
            backend["base_url"] = builtin["base_url"]


def _sanitize_anthropic_backends(backends: dict[str, dict[str, Any]]) -> None:
    for backend_id in ("anthropic",):
        backend = backends.get(backend_id)
        builtin = TRANSLATION_BACKENDS[backend_id]
        if not isinstance(backend, dict):
            backends[backend_id] = dict(builtin)
            continue
        if not _is_allowed_anthropic_model_id(backend.get("model")):
            backend["model"] = builtin["model"]


def _sanitize_retired_backend_models(backends: dict[str, dict[str, Any]]) -> None:
    for backend_id, backend in backends.items():
        if not isinstance(backend, dict) or backend_id == RELAY_BACKEND:
            continue
        if _is_retired_model(backend_id, backend.get("model", "")):
            backend["model"] = TRANSLATION_BACKENDS[backend_id]["model"]


def _sanitize_openai_presets(presets: dict[str, tuple[str, ...]]) -> None:
    # The relay list is read from the relay itself, never from the catalog.
    presets[RELAY_BACKEND] = tuple(TRANSLATION_MODEL_PRESETS[RELAY_BACKEND])
    for backend_id in ("openai",):
        builtin = tuple(TRANSLATION_MODEL_PRESETS[backend_id])
        filtered = tuple(
            value
            for value in _dedupe_presets(presets.get(backend_id, ()))
            if _is_allowed_openai_model_id(value)
        )
        presets[backend_id] = filtered or builtin


def _sanitize_anthropic_presets(presets: dict[str, tuple[str, ...]]) -> None:
    for backend_id in ("anthropic",):
        builtin = tuple(TRANSLATION_MODEL_PRESETS[backend_id])
        filtered = tuple(
            value
            for value in _dedupe_presets(presets.get(backend_id, ()))
            if _is_allowed_anthropic_model_id(value)
        )
        presets[backend_id] = filtered or builtin


def _sanitize_openai_profiles(
    profiles: dict[str, dict[str, dict[str, str]]],
    presets: dict[str, tuple[str, ...]],
) -> None:
    builtin = TRANSLATION_MODEL_PROFILES["openai"]
    remote_profiles = profiles.get("openai", {})
    cleaned: dict[str, dict[str, str]] = {}
    if isinstance(remote_profiles, dict):
        for model, profile in remote_profiles.items():
            if _is_allowed_openai_model_id(model) and isinstance(profile, dict):
                cleaned[str(model)] = dict(profile)
    for model in presets.get("openai", ()):
        if model in builtin and model not in cleaned:
            cleaned[model] = dict(builtin[model])
    for model, profile in builtin.items():
        cleaned.setdefault(model, dict(profile))
    profiles["openai"] = cleaned
    profiles[RELAY_BACKEND] = {}


def _sanitize_anthropic_profiles(
    profiles: dict[str, dict[str, dict[str, str]]],
    presets: dict[str, tuple[str, ...]],
) -> None:
    builtin = TRANSLATION_MODEL_PROFILES["anthropic"]
    remote_profiles = profiles.get("anthropic", {})
    cleaned: dict[str, dict[str, str]] = {}
    allowed_models = set(presets.get("anthropic", ()))
    if isinstance(remote_profiles, dict):
        for model, profile in remote_profiles.items():
            if (
                model in allowed_models
                and _is_allowed_anthropic_model_id(model)
                and isinstance(profile, dict)
            ):
                cleaned[str(model)] = dict(profile)
    for model in allowed_models:
        if model in builtin and model not in cleaned:
            cleaned[model] = dict(builtin[model])
    profiles["anthropic"] = cleaned


def load_catalog_from_data(data: dict | None) -> TranslationCatalog:
    """Build a catalog from parsed JSON, falling back to builtin values for missing fields."""
    if not isinstance(data, dict):
        return BUILTIN_CATALOG

    section = data.get(CATALOG_TRANSLATION_SECTION)
    if not isinstance(section, dict) or not section:
        # Only the older releases' top-level sections (or nothing usable):
        # this release keeps its builtin tables.
        return BUILTIN_CATALOG
    remote_backends = section.get("backends")
    remote_backends = remote_backends if isinstance(remote_backends, dict) else {}
    backends: dict[str, dict[str, Any]] = {}
    for backend_id, builtin in TRANSLATION_BACKENDS.items():
        merged_backend = dict(builtin)
        remote_backend = remote_backends.get(backend_id)
        if isinstance(remote_backend, dict):
            merged_backend.update(_sanitize_remote_backend(remote_backend, builtin))
        for field_name in _REMOTE_PROTECTED_BACKEND_FIELDS:
            if field_name in builtin:
                merged_backend[field_name] = builtin[field_name]
            else:
                merged_backend.pop(field_name, None)
        backends[backend_id] = merged_backend
    _sanitize_openai_backend(backends)
    _sanitize_anthropic_backends(backends)
    _sanitize_retired_backend_models(backends)

    presets_raw = section.get("model_presets")
    presets: dict[str, tuple[str, ...]] = {}
    if isinstance(presets_raw, dict):
        for k, v in presets_raw.items():
            if k not in TRANSLATION_BACKENDS:
                continue
            if isinstance(v, (list, tuple)):
                presets[k] = _dedupe_presets(tuple(str(x) for x in v))
            elif isinstance(v, str):
                presets[k] = (v,)
    # Fill missing backends from builtin
    for k, v in TRANSLATION_MODEL_PRESETS.items():
        if k not in presets:
            presets[k] = v
        else:
            presets[k] = _dedupe_presets(presets[k])
        filtered = tuple(model for model in presets[k] if not _is_retired_model(k, model))
        presets[k] = filtered or tuple(v)
    _sanitize_openai_presets(presets)
    _sanitize_anthropic_presets(presets)

    profiles_raw = section.get("model_profiles")
    profiles: dict[str, dict[str, dict[str, str]]] = {}
    if isinstance(profiles_raw, dict):
        for bk, bv in profiles_raw.items():
            if bk not in TRANSLATION_BACKENDS or not isinstance(bv, dict):
                continue
            profiles[bk] = {}
            for mk, mv in bv.items():
                model_id = _clean_single_line(mk, max_chars=_MAX_MODEL_ID_CHARS)
                if not model_id or not isinstance(mv, dict):
                    continue
                profile = {
                    str(key): cleaned
                    for key, value in mv.items()
                    if str(key) in _PROFILE_FIELDS
                    and (
                        cleaned := _clean_single_line(
                            value,
                            max_chars=_MAX_PROFILE_VALUE_CHARS,
                        )
                    )
                }
                if profile:
                    profiles[bk][model_id] = profile
                if len(profiles[bk]) >= _MAX_PROFILES_PER_BACKEND:
                    break
    # Fill missing profiles from builtin
    for bk, bv in TRANSLATION_MODEL_PROFILES.items():
        if bk not in profiles:
            profiles[bk] = dict(bv)
        else:
            for mk, mv in bv.items():
                if mk not in profiles[bk]:
                    profiles[bk][mk] = dict(mv)
    for backend_id in TRANSLATION_BACKENDS:
        if backend_id == RELAY_BACKEND:
            profiles[backend_id] = {}
            continue
        if backend_id not in profiles:
            # No builtin or remote profiles (Local AI): add no empty entry.
            continue
        allowed_models = set(presets.get(backend_id, ()))
        profiles[backend_id] = {
            model_id: profile
            for model_id, profile in profiles.get(backend_id, {}).items()
            if model_id in allowed_models
        }
    _sanitize_openai_profiles(profiles, presets)
    _sanitize_anthropic_profiles(profiles, presets)

    # The catalog is downloaded without a release signature. Endpoint maps and
    # their selectors therefore remain entirely builtin so remote metadata can
    # never redirect API keys or translation content to another host.
    region_base_urls = {
        backend_id: dict(urls)
        for backend_id, urls in TRANSLATION_BACKEND_REGION_BASE_URLS.items()
    }
    region_aliases = {
        backend_id: dict(aliases)
        for backend_id, aliases in TRANSLATION_BACKEND_REGION_ALIASES.items()
    }
    default_regions = dict(TRANSLATION_BACKEND_DEFAULT_REGIONS)

    return TranslationCatalog(
        translation_backends=backends,
        translation_model_presets=presets,
        translation_model_profiles=profiles,
        translation_backend_region_base_urls=region_base_urls,
        translation_backend_region_aliases=region_aliases,
        translation_backend_default_regions=default_regions,
    )
