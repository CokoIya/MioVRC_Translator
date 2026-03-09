"""設定に応じて利用する ASR バックエンドを生成する  """

from src.utils.ui_config import DEFAULT_ASR_ENGINE

AVAILABLE_ASR_ENGINES = ("sensevoice-small",)


def _resolve_device(device: str) -> str:
    """CUDA が使えない場合は安全側で CPU へ戻す  """
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
    """設定から ASR 実装を選択する    不明な値は SenseVoice Small へ寄せる  """
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
