"""ASR factory — only whisper-base and whisper-small are supported."""

from src.asr.whisper_asr import ALLOWED_SIZES


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
    """Create a WhisperASR instance from config. Only 'whisper-base' and 'whisper-small' are valid."""
    asr_cfg = config.get("asr", {})
    engine = asr_cfg.get("engine", "whisper-base")
    device = _resolve_device(asr_cfg.get("device", "cpu"))

    # Strip the "whisper-" prefix if present, then validate
    if engine.startswith("whisper-"):
        size = engine.split("-", 1)[1]
    else:
        size = "base"

    if size not in ALLOWED_SIZES:
        size = "base"

    from src.asr.whisper_asr import WhisperASR
    return WhisperASR(model_size=size, device=device)
