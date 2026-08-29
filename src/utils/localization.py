from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter

from src.utils.ui_config import DEFAULT_UI_LANGUAGE

logger = logging.getLogger(__name__)

SUPPORTED_UI_LANGUAGES = ("zh-CN", "en", "ja", "ru", "ko")
FALLBACK_UI_LANGUAGE = "en"

# Public compatibility keys retained for older dialogs/plugins even when the
# current first-party UI no longer references them directly.  Integrity tests
# treat every other unreferenced key as a catalog defect.
RESERVED_COMPATIBILITY_UI_KEYS = frozenset(
    {
        "app_language",
        "api_missing_message",
        "api_missing_title",
        "asr_api_key_separate_hint",
        "asr_dictionary",
        "asr_dictionary_hint",
        "asr_engine_notice",
        "creator_banner",
        "dictionary_update",
        "fixed_by_backend",
        "floating_hidden",
        "floating_shown",
        "floating_window_header",
        "game_not_running_message",
        "game_not_running_title",
        "guide_auto_hint",
        "guide_button",
        "guide_next",
        "guide_page",
        "guide_prev",
        "hotkey_registration_failed_message",
        "hotkey_registration_failed_title",
        "listen_requires_api",
        "manual_input_hint",
        "model_download_wait",
        "model_downloading",
        "model_unloaded",
        "output_hint",
        "partial_refresh_interval",
        "realtime_button",
        "realtime_tooltip",
        "recognition_window_length",
        "settings_button",
        "sponsor_hint_line1",
        "sponsor_hint_line2",
        "status_config_issue",
        "status_listening",
        "status_network_issue",
        "status_request_limited",
        "status_restarting",
        "status_runtime_issue",
        "status_stopped",
        "streaming_hint",
        "streaming_params",
        "text_input_send_to_vrc",
        "translate_to",
        "translation_backend",
        "translation_backend_params",
        "translation_init_failed_title",
        "translation_lock_off",
        "translation_lock_on",
        "tts_bert_language_hint",
        "tts_gpu_unavailable_body",
        "tts_playback_failed_message",
        "tts_playback_failed_title",
        "tts_read_button",
        "tts_reading",
        "update_ready_title",
        "vad_silence_label",
        "voice_record_imported_status",
        "window_must_not_be_less_than_interval",
        "runtime_repair_open_release",
    }
)

_LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "cn": "zh-CN",
    "en-us": "en",
    "en-gb": "en",
    "ja-jp": "ja",
    "jp": "ja",
    "ru-ru": "ru",
    "ko-kr": "ko",
    "kr": "ko",
}


@dataclass(frozen=True)
class LocalizationIssue:
    code: str
    key: str
    language: str
    detail: str = ""


def normalize_ui_language(
    language: object,
    *,
    default: str = DEFAULT_UI_LANGUAGE,
) -> str:
    """Normalize a UI locale while preserving the five supported app locales."""

    text = str(language or "").strip().replace("_", "-")
    lowered = text.casefold()
    exact = {item.casefold(): item for item in SUPPORTED_UI_LANGUAGES}
    if lowered in exact:
        return exact[lowered]
    if lowered in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lowered]
    base = lowered.split("-", 1)[0]
    for item in SUPPORTED_UI_LANGUAGES:
        if item.casefold().split("-", 1)[0] == base:
            return item

    normalized_default = str(default or DEFAULT_UI_LANGUAGE).strip()
    return (
        exact.get(normalized_default.casefold())
        or _LANGUAGE_ALIASES.get(normalized_default.casefold())
        or DEFAULT_UI_LANGUAGE
    )


def language_fallback_chain(language: object) -> tuple[str, ...]:
    """Return a deterministic locale fallback chain without duplicates."""

    requested = normalize_ui_language(language)
    chain: list[str] = [requested]
    for candidate in (FALLBACK_UI_LANGUAGE, DEFAULT_UI_LANGUAGE):
        normalized = normalize_ui_language(candidate)
        if normalized not in chain:
            chain.append(normalized)
    return tuple(chain)


def localized_template(
    values: Mapping[str, object],
    language: object,
    *,
    key: str = "",
) -> str:
    """Select a localized template from a locale-to-text mapping."""

    for candidate in language_fallback_chain(language):
        value = values.get(candidate)
        if value is not None and str(value).strip():
            return str(value)
    return str(key or "")


def format_localized(
    template: object,
    *,
    key: str = "",
    values: Mapping[str, object] | None = None,
) -> str:
    """Format translated text without allowing a catalog defect to crash the UI."""

    text = str(template or "")
    if not values:
        return text
    try:
        return text.format(**dict(values))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        logger.error("Invalid localization format (key=%s): %s", key or "<unknown>", exc)
        return text


def translate_language_catalog(
    catalog: Mapping[str, Mapping[str, object]],
    language: object,
    key: str,
    **kwargs: object,
) -> str:
    """Translate from a locale -> key -> text catalog."""

    for candidate in language_fallback_chain(language):
        table = catalog.get(candidate, {})
        value = table.get(key)
        if value is not None and str(value).strip():
            return format_localized(value, key=key, values=kwargs)
    return format_localized(key, key=key, values=kwargs)


def translate_key_catalog(
    catalog: Mapping[str, Mapping[str, object]],
    language: object,
    key: str,
    **kwargs: object,
) -> str:
    """Translate from a key -> locale -> text catalog."""

    values = catalog.get(key)
    if not isinstance(values, Mapping):
        return format_localized(key, key=key, values=kwargs)
    template = localized_template(values, language, key=key)
    return format_localized(template, key=key, values=kwargs)


def placeholder_fields(template: object) -> frozenset[str]:
    """Return named ``str.format`` fields and reject malformed templates."""

    fields: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in Formatter().parse(
        str(template or "")
    ):
        if field_name is not None:
            fields.add(field_name)
    return frozenset(fields)


def validate_language_catalog(
    catalog: Mapping[str, Mapping[str, object]],
    *,
    languages: tuple[str, ...] = SUPPORTED_UI_LANGUAGES,
    reference_language: str = FALLBACK_UI_LANGUAGE,
) -> tuple[LocalizationIssue, ...]:
    """Validate completeness, values, format syntax, and placeholder parity."""

    issues: list[LocalizationIssue] = []
    all_keys = set().union(
        *(set(catalog.get(language, {}).keys()) for language in languages)
    )
    reference = catalog.get(reference_language, {})
    for key in sorted(all_keys):
        reference_fields: frozenset[str] | None = None
        reference_value = reference.get(key)
        if reference_value is not None:
            try:
                reference_fields = placeholder_fields(reference_value)
            except ValueError as exc:
                issues.append(
                    LocalizationIssue("malformed", key, reference_language, str(exc))
                )
        for language in languages:
            table = catalog.get(language, {})
            if key not in table:
                issues.append(LocalizationIssue("missing", key, language))
                continue
            value = table.get(key)
            if value is None or not str(value).strip():
                issues.append(LocalizationIssue("empty", key, language))
                continue
            try:
                fields = placeholder_fields(value)
            except ValueError as exc:
                issues.append(LocalizationIssue("malformed", key, language, str(exc)))
                continue
            if reference_fields is not None and fields != reference_fields:
                issues.append(
                    LocalizationIssue(
                        "placeholder_mismatch",
                        key,
                        language,
                        f"expected={sorted(reference_fields)} actual={sorted(fields)}",
                    )
                )
    return tuple(issues)


def format_locale_number(
    value: int | float,
    language: object,
    *,
    decimals: int = 0,
    grouping: bool = False,
) -> str:
    """Format a UI number without mutating the process-wide C locale."""

    decimals = max(0, min(int(decimals), 6))
    spec = f",.{decimals}f" if grouping else f".{decimals}f"
    rendered = format(float(value), spec)
    if normalize_ui_language(language) == "ru":
        rendered = rendered.replace(",", "\u202f").replace(".", ",")
    return rendered


def format_locale_percent(
    value: int | float,
    language: object,
    *,
    decimals: int = 0,
) -> str:
    return f"{format_locale_number(value, language, decimals=decimals)}%"
