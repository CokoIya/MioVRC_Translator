"""Catalog loader — merges remote catalog with builtin defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.ui_config import (
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

_OPENAI_MODEL_PREFIXES = ("gpt-", "o", "chatgpt-")
_OPENAI_ALLOWED_LABELS = {"gpt", "openai", "chatgpt", "chatgpt / openai", "openai / gpt"}


def _is_openai_model_id(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text.startswith(_OPENAI_MODEL_PREFIXES)


def _dedupe_presets(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _sanitize_openai_backend(backends: dict[str, dict[str, Any]]) -> None:
    backend = backends.get("openai")
    if not isinstance(backend, dict):
        backends["openai"] = dict(TRANSLATION_BACKENDS["openai"])
        return

    builtin = TRANSLATION_BACKENDS["openai"]
    label = str(backend.get("label", "") or "").strip().lower()
    if label and label not in _OPENAI_ALLOWED_LABELS:
        backend["label"] = builtin["label"]
    if not _is_openai_model_id(backend.get("model")):
        backend["model"] = builtin["model"]
    if str(backend.get("base_url", "") or "").strip().rstrip("/") == "https://api.anthropic.com":
        backend["base_url"] = builtin["base_url"]


def _sanitize_openai_presets(presets: dict[str, tuple[str, ...]]) -> None:
    builtin = tuple(TRANSLATION_MODEL_PRESETS["openai"])
    filtered = tuple(value for value in _dedupe_presets(presets.get("openai", ())) if _is_openai_model_id(value))
    presets["openai"] = filtered or builtin


def _sanitize_openai_profiles(
    profiles: dict[str, dict[str, dict[str, str]]],
    presets: dict[str, tuple[str, ...]],
) -> None:
    builtin = TRANSLATION_MODEL_PROFILES["openai"]
    remote_profiles = profiles.get("openai", {})
    cleaned: dict[str, dict[str, str]] = {}
    if isinstance(remote_profiles, dict):
        for model, profile in remote_profiles.items():
            if _is_openai_model_id(model) and isinstance(profile, dict):
                cleaned[str(model)] = dict(profile)
    for model in presets.get("openai", ()):
        if model in builtin and model not in cleaned:
            cleaned[model] = dict(builtin[model])
    for model, profile in builtin.items():
        cleaned.setdefault(model, dict(profile))
    profiles["openai"] = cleaned


def load_catalog_from_data(data: dict | None) -> TranslationCatalog:
    """Build a catalog from parsed JSON, falling back to builtin values for missing fields."""
    if not isinstance(data, dict):
        return BUILTIN_CATALOG

    backends = data.get("translation_backends")
    backends = dict(backends) if isinstance(backends, dict) else dict(TRANSLATION_BACKENDS)
    for k, v in TRANSLATION_BACKENDS.items():
        if k not in backends:
            backends[k] = dict(v)
        elif isinstance(backends[k], dict):
            merged_backend = dict(v)
            merged_backend.update(backends[k])
            backends[k] = merged_backend
        else:
            backends[k] = dict(v)
    _sanitize_openai_backend(backends)

    presets_raw = data.get("translation_model_presets")
    presets: dict[str, tuple[str, ...]] = {}
    if isinstance(presets_raw, dict):
        for k, v in presets_raw.items():
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
    _sanitize_openai_presets(presets)

    profiles_raw = data.get("translation_model_profiles")
    profiles: dict[str, dict[str, dict[str, str]]] = {}
    if isinstance(profiles_raw, dict):
        for bk, bv in profiles_raw.items():
            if not isinstance(bv, dict):
                continue
            profiles[bk] = {}
            for mk, mv in bv.items():
                if isinstance(mv, dict):
                    profiles[bk][mk] = {str(kk): str(vv) for kk, vv in mv.items()}
    # Fill missing profiles from builtin
    for bk, bv in TRANSLATION_MODEL_PROFILES.items():
        if bk not in profiles:
            profiles[bk] = dict(bv)
        else:
            for mk, mv in bv.items():
                if mk not in profiles[bk]:
                    profiles[bk][mk] = dict(mv)
    _sanitize_openai_profiles(profiles, presets)

    region_base_urls_raw = data.get("translation_backend_region_base_urls")
    region_base_urls: dict[str, dict[str, str]] = {}
    if isinstance(region_base_urls_raw, dict):
        for k, v in region_base_urls_raw.items():
            if isinstance(v, dict):
                region_base_urls[k] = {str(kk): str(vv) for kk, vv in v.items()}
    for k, v in TRANSLATION_BACKEND_REGION_BASE_URLS.items():
        if k not in region_base_urls:
            region_base_urls[k] = dict(v)

    region_aliases_raw = data.get("translation_backend_region_aliases")
    region_aliases: dict[str, dict[str, str]] = {}
    if isinstance(region_aliases_raw, dict):
        for k, v in region_aliases_raw.items():
            if isinstance(v, dict):
                region_aliases[k] = {str(kk): str(vv) for kk, vv in v.items()}
    for k, v in TRANSLATION_BACKEND_REGION_ALIASES.items():
        if k not in region_aliases:
            region_aliases[k] = dict(v)

    default_regions_raw = data.get("translation_backend_default_regions")
    default_regions: dict[str, str] = {}
    if isinstance(default_regions_raw, dict):
        for k, v in default_regions_raw.items():
            default_regions[k] = str(v) if v else ""
    for k, v in TRANSLATION_BACKEND_DEFAULT_REGIONS.items():
        if k not in default_regions:
            default_regions[k] = v

    return TranslationCatalog(
        translation_backends=backends,
        translation_model_presets=presets,
        translation_model_profiles=profiles,
        translation_backend_region_base_urls=region_base_urls,
        translation_backend_region_aliases=region_aliases,
        translation_backend_default_regions=default_regions,
    )
