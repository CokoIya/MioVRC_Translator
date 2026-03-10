from src.utils.ui_config import DEFAULT_ASR_ENGINE

AVAILABLE_ASR_ENGINES = ("sensevoice-small",)


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
    engine = asr_cfg.get("engine", DEFAULT_ASR_ENGINE)
    device = _resolve_device(asr_cfg.get("device", "cpu"))
    if engine not in AVAILABLE_ASR_ENGINES:
        engine = DEFAULT_ASR_ENGINE

    from src.asr.sensevoice_asr import SenseVoiceASR

    sensevoice_cfg = asr_cfg.get("sensevoice", {})
    return SenseVoiceASR(
        device=device,
        model_id=sensevoice_cfg.get("model_id", "iic/SenseVoiceSmall"),
        model_revision=sensevoice_cfg.get("model_revision", "master"),
    )
