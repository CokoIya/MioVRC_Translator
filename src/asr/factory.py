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


def _create_whisper(config: dict, corrector: LayeredASRCorrector):
    asr_cfg = config.get("asr", {})
    spec = get_asr_runtime_spec(config, "whisper-large-v3-turbo")
    device = _resolve_device(asr_cfg.get("device", "cpu"))
    from src.asr.whisper_asr import WhisperASR
    whisper_cfg = asr_cfg.get("whisper", {})
    ncpu = whisper_cfg.get("ncpu") if isinstance(whisper_cfg, dict) else None
    return WhisperASR(
        device=device,
        model_id=spec.model_id,
        model_revision=spec.model_revision,
        corrector=corrector,
        ncpu=ncpu,
    )


def _auto_fallback_enabled(config: dict) -> bool:
    asr_cfg = config.get("asr", {})
    if not isinstance(asr_cfg, dict):
        return True
    return bool(asr_cfg.get("auto_fallback", True))


def create_asr(config: dict, engine: str | None = None):
    asr_cfg = config.get("asr", {})
    engine = normalize_asr_engine(engine or asr_cfg.get("engine", DEFAULT_ASR_ENGINE))
    spec = get_asr_runtime_spec(config, engine)
    corrector = LayeredASRCorrector(config)

    if engine == "qwen3-asr":
        from src.asr.qwen3_asr import Qwen3ASRProvider
        primary = Qwen3ASRProvider(config, corrector=corrector)
        if _auto_fallback_enabled(config):
            return FallbackASR(
                primary,
                fallback_factory=lambda: _create_sensevoice(config, corrector),
                auto_fallback=True,
            )
        return primary

    if engine == "gemini-live":
        from src.asr.gemini_live_asr import GeminiLiveASRProvider
        primary = GeminiLiveASRProvider(config, corrector=corrector)
        if _auto_fallback_enabled(config):
            return FallbackASR(
                primary,
                fallback_factory=lambda: _create_sensevoice(config, corrector),
                auto_fallback=True,
            )
        return primary

    if engine == "webspeech":
        from src.asr.webspeech_asr import WebSpeechASRProvider
        primary = WebSpeechASRProvider(config, corrector=corrector)
        if _auto_fallback_enabled(config):
            return FallbackASR(
                primary,
                fallback_factory=lambda: _create_sensevoice(config, corrector),
                auto_fallback=True,
            )
        return primary

    if engine == "whisper-large-v3-turbo":
        return _create_whisper(config, corrector)

    return _create_sensevoice(config, corrector)
