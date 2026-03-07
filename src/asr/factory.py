"""ASR インスタンスの生成を担当する。  対応モデルは Whisper Base と Small のみ。"""

from src.asr.whisper_asr import ALLOWED_SIZES


def _resolve_device(device: str) -> str:
    """設定されたデバイスを解決する。  CUDA が使えない場合は安全に CPU へ戻す。"""
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
    """設定から `WhisperASR` を生成する。  有効値は `whisper-base` と `whisper-small` のみ。"""
    asr_cfg = config.get("asr", {})
    engine = asr_cfg.get("engine", "whisper-base")
    device = _resolve_device(asr_cfg.get("device", "cpu"))

    # `whisper-` 接頭辞があれば外してから検証する。
    if engine.startswith("whisper-"):
        size = engine.split("-", 1)[1]
    else:
        size = "base"

    if size not in ALLOWED_SIZES:
        size = "base"

    from src.asr.whisper_asr import WhisperASR
    return WhisperASR(model_size=size, device=device)
