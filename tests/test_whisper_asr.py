import numpy as np

from src.asr.factory import create_asr
from src.asr.whisper_asr import WhisperASR


def test_factory_creates_whisper_asr_provider():
    config = {
        "asr": {
            "engine": "whisper-large-v3-turbo",
            "device": "cpu",
            "whisper": {
                "model_id": "iic/speech_whisper-small_asr_english",
                "model_revision": "master",
                "ncpu": 2,
            },
        }
    }

    provider = create_asr(config)

    assert isinstance(provider, WhisperASR)
    assert provider.provider_id == "whisper-large-v3-turbo"
    assert provider.model_id == "iic/speech_whisper-small_asr_english"
    assert provider.ncpu == 2
    assert provider.supports_partial is False


def test_whisper_asr_clean_text_removes_tags_and_spacing():
    provider = WhisperASR()

    text = provider._clean_text(
        [
            {
                "text": "<|ja|> \u3053 \u3093 \u306b \u3061 \u306f \u3001 VRChat \u884c \u3053 \u3046"
            }
        ],
        language="ja",
    )

    assert text == "\u3053\u3093\u306b\u3061\u306f\u3001VRChat \u884c\u3053\u3046"


def test_whisper_asr_empty_audio_returns_empty_without_loading():
    provider = WhisperASR()
    provider._model = object()

    assert provider.transcribe(np.array([], dtype=np.float32), language="en") == ""
