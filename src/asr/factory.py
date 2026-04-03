from src.asr.model_registry import (
    DEFAULT_ASR_ENGINE,
    get_asr_runtime_spec,
    normalize_asr_engine,
)
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


def create_asr(config: dict):
    asr_cfg = config.get("asr", {})
    engine = normalize_asr_engine(asr_cfg.get("engine", DEFAULT_ASR_ENGINE))
    device = _resolve_device(asr_cfg.get("device", "cpu"))

    spec = get_asr_runtime_spec(config, engine)
    corrector = LayeredASRCorrector(config)
    from src.asr.sensevoice_asr import SenseVoiceASR

    return SenseVoiceASR(
        device=device,
        model_id=spec.model_id,
        model_revision=spec.model_revision,
        corrector=corrector,
    )
