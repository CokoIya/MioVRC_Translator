"""ASR factory with safe device fallback."""


def _resolve_device(device: str) -> str:
    """Resolve configured device and gracefully fallback when CUDA is unavailable."""
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
    """Create ASR instance from config['asr']['engine']."""
    asr_cfg = config.get("asr", {})
    engine = asr_cfg.get("engine", "whisper-small")
    device = _resolve_device(asr_cfg.get("device", "cpu"))

    if engine.startswith("whisper-"):
        size = engine.split("-", 1)[1]
        from src.asr.whisper_asr import WhisperASR
        return WhisperASR(model_size=size, device=device)

    from src.asr.sense_voice import SenseVoiceASR
    return SenseVoiceASR(
        model_id=asr_cfg.get("model"),
        device=device,
    )
