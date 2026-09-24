# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from src.asr.model_registry import (
    DEFAULT_ASR_ENGINE,
    get_asr_runtime_spec,
    normalize_asr_engine,
)
from src.asr.fallback_asr import FallbackASR
from src.asr.text_corrections import LayeredASRCorrector


def _resolve_device(device: str) -> str:
    if device != "cuda":
        return device
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _create_sensevoice(config: dict, corrector: LayeredASRCorrector):
    asr_cfg = config.get("asr", {})
    spec = get_asr_runtime_spec(config, "sensevoice-small")
    device = _resolve_device(asr_cfg.get("device", "cpu"))
    from src.asr.sensevoice_asr import SenseVoiceASR
    sensevoice_cfg = asr_cfg.get("sensevoice", {})
    ncpu = sensevoice_cfg.get("ncpu")
    return SenseVoiceASR(
        device=device,
        model_id=spec.model_id,
        model_revision=spec.model_revision,
        corrector=corrector,
        ncpu=ncpu,
    )


def _create_ready_sensevoice_fallback(
    config: dict,
    corrector: LayeredASRCorrector,
):
    """Create a local fallback only when its verified model already exists.

    Provider failures occur on realtime ASR worker threads. Starting a large
    model download there makes Stop unable to quiesce and can hold the pipeline
    for minutes. Explicitly selecting SenseVoice still preserves its managed
    download flow; automatic online-provider fallback now fails fast instead.
    """

    from src.asr.errors import ASRUnsupportedRuntimeError
    from src.asr.model_manager import existing_model_path

    spec = get_asr_runtime_spec(config, "sensevoice-small")
    if existing_model_path(spec) is None:
        raise ASRUnsupportedRuntimeError(
            "SenseVoice fallback is not installed. Download the local ASR model "
            "from Settings before enabling automatic fallback."
        )
    return _create_sensevoice(config, corrector)


def _auto_fallback_enabled(config: dict) -> bool:
    asr_cfg = config.get("asr", {})
    if not isinstance(asr_cfg, dict):
        return True
    return bool(asr_cfg.get("auto_fallback", True))


def _provider_auto_fallback_enabled(
    config: dict,
    provider_key: str,
    *,
    default: bool,
) -> bool:
    if not _auto_fallback_enabled(config):
        return False
    asr_cfg = config.get("asr", {})
    if not isinstance(asr_cfg, dict):
        return bool(default)
    # Provider sections are stored with underscores ("edge_stt"); the
    # hyphenated engine id is accepted for configs written by hand.
    provider_cfg = asr_cfg.get(provider_key)
    if not isinstance(provider_cfg, dict):
        provider_cfg = asr_cfg.get(provider_key.replace("_", "-"))
    if not isinstance(provider_cfg, dict):
        return bool(default)
    return bool(provider_cfg.get("auto_fallback", default))


def create_asr(config: dict, engine: str | None = None):
    asr_cfg = config.get("asr", {})
    engine = normalize_asr_engine(engine or asr_cfg.get("engine", DEFAULT_ASR_ENGINE))
    corrector = LayeredASRCorrector(config)

    if engine == "qwen3-asr":
        from src.asr.qwen3_asr import Qwen3ASRProvider
        primary = Qwen3ASRProvider(config, corrector=corrector)
        if _auto_fallback_enabled(config):
            return FallbackASR(
                primary,
                fallback_factory=lambda: _create_ready_sensevoice_fallback(
                    config,
                    corrector,
                ),
                auto_fallback=True,
            )
        return primary

    if engine == "edge-stt":
        from src.asr.edge_stt_asr import EdgeSTTASRProvider
        primary = EdgeSTTASRProvider(config, corrector=corrector)
        # On by default: a route that drops the Edge socket must not leave
        # the player without recognition. The key used to be read under a
        # hyphenated name nothing ever wrote, so the switch could never turn on.
        if _provider_auto_fallback_enabled(config, "edge_stt", default=True):
            return FallbackASR(
                primary,
                fallback_factory=lambda: _create_ready_sensevoice_fallback(
                    config,
                    corrector,
                ),
                auto_fallback=True,
            )
        return primary

    return _create_sensevoice(config, corrector)
