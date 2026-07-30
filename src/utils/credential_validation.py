from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from src.asr.model_registry import (
    ASR_ENGINE_FOLLOW_MAIN,
    normalize_asr_engine,
)
from src.tts.api_tts_config import TTS_API_ENGINE_IDS
from src.translators.asr_rewriter import asr_rewrite_enabled
from src.utils.i18n import tr
from src.utils.ui_config import (
    DEFAULT_ASR_ENGINE,
    backend_api_key_is_required,
    get_backend_label,
    normalize_backend,
    normalize_output_format,
)


def should_bypass_environment_proxies(url: object) -> bool:
    """Load provider networking only when credential validation needs it."""

    from src.utils.provider_network import (
        should_bypass_environment_proxies as _should_bypass,
    )

    return _should_bypass(url)


@dataclass(frozen=True)
class MissingCredential:
    """Describe one required credential without carrying its secret value."""

    scope: str
    provider_id: str
    provider_label: str
    credential_id: str
    credential_label: str
    settings_page: str = "api_config"
    focus_target: str = ""


_DEFAULT_SCOPES = ("translation", "asr", "tts")
_ASR_CREDENTIALS = {
    "qwen3-asr": ("qwen3_asr", "Qwen3-ASR", "qwen_api_key"),
    "gemini-live": ("gemini_live", "Gemini Live", "gemini_api_key"),
}
_TTS_PROVIDER_LABELS = {
    "mimo_tts": "MiMo TTS",
    "qwen_tts": "Qwen TTS",
}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _has_credential(value: object) -> bool:
    # A protected DPAPI blob is deliberately considered configured here. The
    # runtime secret loader remains responsible for rejecting an unusable blob.
    return bool(str(value or "").strip())


def _credential_label(ui_language: str | None, provider_label: str) -> str:
    return tr(
        ui_language,
        "api_credential_label",
        provider=provider_label,
    )


def _translation_requirement(
    config: Mapping[str, object],
    *,
    ui_language: str | None,
    active_only: bool,
) -> MissingCredential | None:
    trans_cfg = _mapping(config.get("translation"))
    if not trans_cfg:
        return None
    if active_only:
        output_needs_translation = (
            normalize_output_format(trans_cfg.get("output_format")) != "original_only"
        )
        rewrite_needs_translation = asr_rewrite_enabled(
            trans_cfg.get("asr_rewrite_style")
        )
        listen_needs_translation = bool(
            _mapping(config.get("vrc_listen")).get("enabled", False)
        )
        if not (
            output_needs_translation
            or rewrite_needs_translation
            or listen_needs_translation
        ):
            return None

    backend = normalize_backend(trans_cfg.get("backend"))
    backend_cfg = _mapping(trans_cfg.get(backend))
    if not backend_api_key_is_required(backend) or (
        backend in {"local_ai", "openai_compatible", "grok_compatible"}
        and should_bypass_environment_proxies(backend_cfg.get("base_url"))
    ):
        return None
    if _has_credential(backend_cfg.get("api_key")):
        return None

    provider_label = get_backend_label(backend, ui_language)
    return MissingCredential(
        scope="translation",
        provider_id=backend,
        provider_label=provider_label,
        credential_id=f"translation.{backend}.api_key",
        credential_label=_credential_label(ui_language, provider_label),
        focus_target="backend_api_key",
    )


def _asr_requirement(
    asr_cfg: Mapping[str, object],
    engine: object,
    *,
    ui_language: str | None,
) -> MissingCredential | None:
    normalized_engine = normalize_asr_engine(str(engine or ""))
    credential_spec = _ASR_CREDENTIALS.get(normalized_engine)
    if credential_spec is None:
        return None
    config_key, provider_label, focus_target = credential_spec
    provider_cfg = _mapping(asr_cfg.get(config_key))
    if normalized_engine == "qwen3-asr" and should_bypass_environment_proxies(
        provider_cfg.get("base_url")
    ):
        return None
    if _has_credential(provider_cfg.get("api_key")):
        return None
    return MissingCredential(
        scope="asr",
        provider_id=normalized_engine,
        provider_label=provider_label,
        credential_id=f"asr.{config_key}.api_key",
        credential_label=_credential_label(ui_language, provider_label),
        focus_target=focus_target,
    )


def _asr_requirements(
    config: Mapping[str, object],
    *,
    ui_language: str | None,
    active_only: bool,
) -> tuple[MissingCredential, ...]:
    asr_cfg = _mapping(config.get("asr"))
    main_engine = normalize_asr_engine(
        str(asr_cfg.get("engine", DEFAULT_ASR_ENGINE) or DEFAULT_ASR_ENGINE)
    )
    candidates: list[MissingCredential] = []
    main_missing = _asr_requirement(
        asr_cfg,
        main_engine,
        ui_language=ui_language,
    )
    if main_missing is not None:
        candidates.append(main_missing)

    listen_cfg = _mapping(config.get("vrc_listen"))
    if not active_only or bool(listen_cfg.get("enabled", False)):
        listen_engine = str(
            listen_cfg.get("asr_engine", ASR_ENGINE_FOLLOW_MAIN)
            or ASR_ENGINE_FOLLOW_MAIN
        ).strip()
        if listen_engine == ASR_ENGINE_FOLLOW_MAIN:
            listen_engine = main_engine
        listen_missing = _asr_requirement(
            asr_cfg,
            listen_engine,
            ui_language=ui_language,
        )
        if listen_missing is not None:
            candidates.append(listen_missing)

    unique: list[MissingCredential] = []
    seen: set[str] = set()
    for requirement in candidates:
        if requirement.credential_id in seen:
            continue
        seen.add(requirement.credential_id)
        unique.append(requirement)
    return tuple(unique)


def _tts_requirement(
    config: Mapping[str, object],
    *,
    ui_language: str | None,
    active_only: bool,
) -> MissingCredential | None:
    tts_cfg = _mapping(config.get("tts"))
    if not tts_cfg:
        return None
    if active_only and not bool(tts_cfg.get("enabled", False)):
        return None
    engine = str(tts_cfg.get("engine", "edge") or "edge").strip().lower()
    if engine not in TTS_API_ENGINE_IDS:
        return None
    engine_cfg = _mapping(tts_cfg.get(engine))
    if should_bypass_environment_proxies(engine_cfg.get("base_url")):
        return None
    if _has_credential(engine_cfg.get("api_key")):
        return None

    provider_label = _TTS_PROVIDER_LABELS.get(engine, engine)
    return MissingCredential(
        scope="tts",
        provider_id=engine,
        provider_label=provider_label,
        credential_id=f"tts.{engine}.api_key",
        credential_label=_credential_label(ui_language, provider_label),
        focus_target="tts_api_key",
    )


def missing_required_credentials(
    config: Mapping[str, object] | None,
    *,
    scopes: Collection[str] = _DEFAULT_SCOPES,
    ui_language: str | None = None,
    active_only: bool = True,
) -> tuple[MissingCredential, ...]:
    """Return all missing API credentials relevant to the requested scopes."""

    if not isinstance(config, Mapping):
        return ()
    if isinstance(scopes, str):
        scopes = (scopes,)
    normalized_scopes = tuple(
        dict.fromkeys(str(scope or "").strip().lower() for scope in scopes)
    )
    missing: list[MissingCredential] = []
    for scope in normalized_scopes:
        if scope == "translation":
            requirement = _translation_requirement(
                config,
                ui_language=ui_language,
                active_only=active_only,
            )
            if requirement is not None:
                missing.append(requirement)
        elif scope == "asr":
            missing.extend(
                _asr_requirements(
                    config,
                    ui_language=ui_language,
                    active_only=active_only,
                )
            )
        elif scope == "tts":
            requirement = _tts_requirement(
                config,
                ui_language=ui_language,
                active_only=active_only,
            )
            if requirement is not None:
                missing.append(requirement)
    return tuple(missing)


def first_missing_required_credential(
    config: Mapping[str, object] | None,
    *,
    scopes: Collection[str] = _DEFAULT_SCOPES,
    ui_language: str | None = None,
    active_only: bool = True,
) -> MissingCredential | None:
    """Return the first missing credential in caller-specified scope order."""

    missing = missing_required_credentials(
        config,
        scopes=scopes,
        ui_language=ui_language,
        active_only=active_only,
    )
    return missing[0] if missing else None
