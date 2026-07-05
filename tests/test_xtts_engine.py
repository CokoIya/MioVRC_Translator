from __future__ import annotations

import io
import struct
import types
import wave
from collections import OrderedDict

import numpy as np
import pytest

from src.tts.factory import create_tts_engine
from src.tts.wav_utils import decode_wav_bytes
from src.tts.xtts_engine import (
    XTTS_SUPPORTED_LANGUAGES,
    XTTSTS,
    first_usable_xtts_reference_audio_path,
    first_xtts_reference_audio_path,
    list_xtts_reference_voices,
    normalize_xtts_reference_audio_file,
    repair_xtts_reference_audio_file,
    validate_xtts_reference_audio_file,
)


def _wav_bytes(duration_s: float = 1.0) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * int(24000 * duration_s))
    return output.getvalue()


def _tone_wav_bytes(
    *,
    duration_s: float = 3.0,
    sample_rate: int = 16000,
    channels: int = 1,
    amplitude: float = 0.25,
) -> bytes:
    samples = np.sin(np.linspace(0.0, np.pi * 2.0 * 220.0 * duration_s, int(sample_rate * duration_s), endpoint=False))
    samples = (samples * amplitude * 32767.0).astype("<i2")
    if channels > 1:
        samples = np.repeat(samples[:, None], channels, axis=1).reshape(-1)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def _raw_wav_bytes(
    *,
    format_tag: int,
    bits_per_sample: int,
    frames: bytes,
    channels: int = 1,
    sample_rate: int = 24000,
) -> bytes:
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        format_tag,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def test_xtts_reference_voice_listing_does_not_require_tts_runtime(tmp_path):
    ref_dir = tmp_path / "reference_audio"
    ref_dir.mkdir()
    (ref_dir / "My Voice.wav").write_bytes(_wav_bytes())

    voices = list_xtts_reference_voices(ref_dir)

    assert [(voice.id, voice.name, voice.locale) for voice in voices] == [
        ("custom", "Custom Cloned Voice", "multi"),
        ("My Voice", "Cloned: My Voice", "multi"),
    ]


def test_first_xtts_reference_audio_path_returns_first_saved_wav(tmp_path):
    ref_dir = tmp_path / "reference_audio"
    ref_dir.mkdir()
    (ref_dir / "b.wav").write_bytes(_wav_bytes())
    (ref_dir / "a.wav").write_bytes(_wav_bytes())

    assert first_xtts_reference_audio_path(ref_dir) == ref_dir / "a.wav"


def test_xtts_reference_audio_is_normalized_to_mono_pcm_wav(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_tone_wav_bytes(channels=2))

    normalize_xtts_reference_audio_file(source, output)

    with wave.open(str(output), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24000
    usable, reason, stats = validate_xtts_reference_audio_file(output)
    assert usable is True
    assert reason == ""
    assert stats is not None
    assert stats.peak > 0.1


def test_xtts_engine_accepts_common_audio_reference_import(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp3"
    source.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    ref_dir = tmp_path / "reference_audio"
    monkeypatch.setattr("src.tts.xtts_engine.xtts_reference_audio_dir", lambda: ref_dir)

    engine = XTTSTS.__new__(XTTSTS)

    assert engine.set_reference_audio(str(source)) is True
    assert engine._reference_audio_path == str(ref_dir / "reference.wav")
    usable, reason, _stats = validate_xtts_reference_audio_file(engine._reference_audio_path)
    assert usable is True
    assert reason == ""


def test_xtts_reference_audio_rejects_silence_instead_of_saving_bad_voice(tmp_path):
    source = tmp_path / "silent.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_wav_bytes(duration_s=3.0))

    with pytest.raises(RuntimeError, match="too quiet|mostly silent"):
        normalize_xtts_reference_audio_file(source, output)

    assert not output.exists()


def test_xtts_reference_audio_normalization_recovers_quiet_voice_signal(tmp_path):
    source = tmp_path / "quiet.wav"
    output = tmp_path / "voice.wav"
    source.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000, amplitude=0.0005))

    usable_before, reason_before, _stats_before = validate_xtts_reference_audio_file(source)
    assert usable_before is False
    assert "too quiet" in reason_before

    normalize_xtts_reference_audio_file(source, output)

    usable, reason, stats = validate_xtts_reference_audio_file(output)
    assert usable is True
    assert reason == ""
    assert stats is not None
    assert stats.peak >= 0.015
    assert stats.active_duration_seconds >= 1.0


def test_xtts_reference_audio_repair_normalizes_existing_quiet_file(tmp_path):
    reference = tmp_path / "quiet_saved.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000, amplitude=0.0005))

    repaired, reason, stats = repair_xtts_reference_audio_file(reference)

    assert repaired is True
    assert reason == ""
    assert stats is not None
    usable, reason_after, _stats_after = validate_xtts_reference_audio_file(reference)
    assert usable is True
    assert reason_after == ""


def test_first_usable_xtts_reference_audio_path_skips_silent_files(tmp_path):
    ref_dir = tmp_path / "reference_audio"
    ref_dir.mkdir()
    (ref_dir / "a.wav").write_bytes(_wav_bytes(duration_s=3.0))
    (ref_dir / "b.wav").write_bytes(_tone_wav_bytes(sample_rate=24000))

    assert first_xtts_reference_audio_path(ref_dir) == ref_dir / "a.wav"
    assert first_usable_xtts_reference_audio_path(ref_dir) == ref_dir / "b.wav"


def test_xtts_engine_reports_unavailable_without_runtime(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: False)

    engine = XTTSTS()

    assert engine.is_available() is False
    assert engine.get_available_voices()[0].locale == "multi"
    with pytest.raises(RuntimeError, match="XTTS-v2 engine not available"):
        engine.synthesize("hello", "custom")


def test_xtts_factory_rejects_unavailable_runtime(monkeypatch):
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: False)

    assert create_tts_engine("xtts") is None


def test_xtts_factory_passes_configured_device_and_language(monkeypatch):
    captured: list[dict[str, str]] = []

    class FakeXTTS:
        def __init__(self, *, device: str, model_name: str, language: str, **kwargs):
            captured.append(
                {
                    "device": device,
                    "model_name": model_name,
                    "language": language,
                    "lazy_load": kwargs.get("lazy_load"),
                    "optimized_inference": kwargs.get("optimized_inference"),
                }
            )
            self._device = device

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("src.tts.factory.XTTSTS", FakeXTTS)

    engine = create_tts_engine(
        "xtts",
        device="cpu",
        config={
            "device": "cuda",
            "language": "ja",
            "model_name": "local-model",
            "lazy_load": "false",
            "optimized_inference": "false",
        },
    )

    assert engine is not None
    assert captured == [
        {
            "device": "cuda",
            "model_name": "local-model",
            "language": "ja",
            "lazy_load": False,
            "optimized_inference": False,
        }
    ]


def test_xtts_language_detection_uses_real_unicode_ranges(monkeypatch):
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: False)
    engine = XTTSTS()

    assert engine._detect_language("\u3053\u3093\u306b\u3061\u306f") == "ja"
    assert engine._detect_language("\u4f60\u597d") == "zh-cn"
    assert engine._detect_language("\uc548\ub155") == "ko"
    assert engine._detect_language("\u0928\u092e\u0938\u094d\u0924\u0947") == "hi"
    assert engine._detect_language("hello") == "en"


def test_xtts_supported_languages_include_full_xtts_v2_set(monkeypatch):
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: False)
    engine = XTTSTS()

    assert tuple(engine.get_supported_languages()[1:]) == XTTS_SUPPORTED_LANGUAGES
    assert "hi" in engine.get_supported_languages()


def test_xtts_volume_adjustment_handles_32_bit_wav_without_corrupting_audio():
    engine = XTTSTS.__new__(XTTSTS)
    source = _raw_wav_bytes(
        format_tag=1,
        bits_per_sample=32,
        frames=struct.pack("<iii", -2147483648, 0, 2147483647),
    )

    adjusted = engine._apply_volume(source, 0.5)

    with wave.open(io.BytesIO(adjusted), "rb") as wav:
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24000
    audio, sample_rate = decode_wav_bytes(adjusted)
    assert sample_rate == 24000
    np.testing.assert_allclose(audio, [-0.5, 0.0, 0.5], rtol=0, atol=1 / 32768)


def test_xtts_engine_passes_rate_to_coqui_speed(monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    captured: dict[str, object] = {}

    class FakeCoquiModel:
        def tts_to_file(self, **kwargs):
            captured.update(kwargs)
            with open(kwargs["file_path"], "wb") as wav_file:
                wav_file.write(_tone_wav_bytes(duration_s=0.5, sample_rate=24000))

    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    engine = XTTSTS.__new__(XTTSTS)
    engine._model = FakeCoquiModel()
    engine._reference_audio_path = str(reference)
    engine._language = "en"
    engine._supported_languages = ["en"]

    audio = engine.synthesize("hello", "custom", rate=1.35, volume=1.0)

    assert audio.startswith(b"RIFF")
    assert captured["speed"] == pytest.approx(1.35)
    assert captured["speaker_wav"] == str(reference)


def test_xtts_engine_uses_managed_local_model_kwargs(monkeypatch):
    captured: list[dict[str, object]] = []

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    monkeypatch.setattr("src.tts.xtts_engine._load_xtts_api", lambda: FakeCoquiTTS)
    monkeypatch.setattr(
        "src.tts.xtts_engine.xtts_coqui_model_kwargs",
        lambda: {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
        },
    )

    engine = XTTSTS(device="cpu", lazy_load=False)

    assert engine.is_available() is True
    assert captured == [
        {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
            "gpu": False,
        }
    ]


def test_xtts_engine_lazy_loads_model_on_first_synthesis(monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    captured: list[dict[str, object]] = []

    class FakeCoquiModel:
        def tts_to_file(self, **kwargs):
            with open(kwargs["file_path"], "wb") as wav_file:
                wav_file.write(_tone_wav_bytes(duration_s=0.5, sample_rate=24000))

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.synthesizer = None

        def tts_to_file(self, **kwargs):
            return FakeCoquiModel().tts_to_file(**kwargs)

    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    monkeypatch.setattr("src.tts.xtts_engine._load_xtts_api", lambda: FakeCoquiTTS)
    monkeypatch.setattr(
        "src.tts.xtts_engine.xtts_coqui_model_kwargs",
        lambda: {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
        },
    )

    engine = XTTSTS(device="cpu")
    engine._reference_audio_path = str(reference)

    assert engine.is_available() is True
    assert captured == []

    engine.synthesize("hello", "custom")

    assert captured == [
        {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
            "gpu": False,
        }
    ]


def test_xtts_engine_enables_gpu_when_cuda_is_available(monkeypatch):
    captured: list[dict[str, object]] = []

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True)
    )
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    monkeypatch.setattr("src.tts.xtts_engine._load_xtts_api", lambda: FakeCoquiTTS)
    monkeypatch.setattr(
        "src.tts.xtts_engine.xtts_coqui_model_kwargs",
        lambda: {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
        },
    )

    engine = XTTSTS(device="cuda", lazy_load=False)

    assert engine.is_available() is True
    assert engine._requested_device == "cuda"
    assert engine._runtime_device == "cuda"
    assert engine._device == "cuda"
    assert captured[0]["gpu"] is True


def test_xtts_engine_falls_back_to_cpu_when_cuda_is_unavailable(monkeypatch):
    captured: list[dict[str, object]] = []

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False)
    )
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    monkeypatch.setattr("src.tts.xtts_engine._load_xtts_api", lambda: FakeCoquiTTS)
    monkeypatch.setattr(
        "src.tts.xtts_engine.xtts_coqui_model_kwargs",
        lambda: {
            "model_path": "local-xtts",
            "config_path": "local-xtts/config.json",
            "progress_bar": False,
        },
    )

    engine = XTTSTS(device="cuda", lazy_load=False)

    assert engine.is_available() is True
    assert engine._requested_device == "cuda"
    assert engine._runtime_device == "cpu"
    assert engine._device == "cpu"
    assert captured[0]["gpu"] is False


def test_xtts_engine_does_not_auto_download_default_model(monkeypatch):
    class FakeCoquiTTS:
        def __init__(self, **_kwargs):
            raise AssertionError("Default XTTS should not fall back to Coqui auto-download")

    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    monkeypatch.setattr("src.tts.xtts_engine._load_xtts_api", lambda: FakeCoquiTTS)
    monkeypatch.setattr("src.tts.xtts_engine.xtts_coqui_model_kwargs", lambda: None)

    engine = XTTSTS(device="cpu")

    assert engine.is_available() is False


def test_xtts_optimized_inference_reuses_conditioning_latents(monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    conditioning_calls: list[str] = []
    inference_calls: list[dict[str, object]] = []

    class FakeCoreModel:
        config = types.SimpleNamespace(
            audio=types.SimpleNamespace(output_sample_rate=24000)
        )

        def get_conditioning_latents(self, **kwargs):
            conditioning_calls.append(str(kwargs["audio_path"]))
            return "gpt-latent", "speaker-embedding"

        def inference(self, **kwargs):
            inference_calls.append(kwargs)
            return {"wav": np.zeros(2400, dtype=np.float32)}

    fake_model = types.SimpleNamespace(
        synthesizer=types.SimpleNamespace(tts_model=FakeCoreModel())
    )
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    engine = XTTSTS.__new__(XTTSTS)
    engine._model = fake_model
    engine._reference_audio_path = str(reference)
    engine._language = "en"
    engine._supported_languages = ["en"]
    engine._optimized_inference = True
    engine._enable_text_splitting = True
    engine._conditioning_cache_size = 4
    engine._conditioning_cache = OrderedDict()
    engine._inference_kwargs = {}

    first = engine.synthesize("hello", "custom", rate=1.1, volume=1.0)
    second = engine.synthesize("hello again", "custom", rate=0.9, volume=1.0)

    assert first.startswith(b"RIFF")
    assert second.startswith(b"RIFF")
    assert conditioning_calls == [str(reference)]
    assert inference_calls[0]["gpt_cond_latent"] == "gpt-latent"
    assert inference_calls[0]["speaker_embedding"] == "speaker-embedding"
    assert inference_calls[0]["speed"] == pytest.approx(1.1)
    assert inference_calls[0]["enable_text_splitting"] is False
    assert inference_calls[1]["speed"] == pytest.approx(0.9)


def test_xtts_optimized_inference_splits_long_text_without_spacy(monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    inference_calls: list[dict[str, object]] = []

    class FakeCoreModel:
        tokenizer = types.SimpleNamespace(char_limits={"ja": 71})
        config = types.SimpleNamespace(
            audio=types.SimpleNamespace(output_sample_rate=24000)
        )

        def get_conditioning_latents(self, **_kwargs):
            return "gpt-latent", "speaker-embedding"

        def inference(self, **kwargs):
            if kwargs.get("enable_text_splitting") is True:
                raise RuntimeError("enable_text_splitting=True requires Spacy")
            inference_calls.append(kwargs)
            return {"wav": np.ones(240, dtype=np.float32) * 0.1}

    fake_model = types.SimpleNamespace(
        synthesizer=types.SimpleNamespace(tts_model=FakeCoreModel())
    )
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    engine = XTTSTS.__new__(XTTSTS)
    engine._model = fake_model
    engine._reference_audio_path = str(reference)
    engine._language = "ja"
    engine._supported_languages = ["ja"]
    engine._optimized_inference = True
    engine._enable_text_splitting = True
    engine._conditioning_cache_size = 4
    engine._conditioning_cache = OrderedDict()
    engine._inference_kwargs = {}

    text = (
        "これはXTTSの音声合成テストです。"
        "短いノイズではなく自然な人の声として聞こえるか確認してください。"
        "長い文章でも内蔵分割で処理できる必要があります。"
    )
    audio = engine.synthesize(text, "custom", rate=1.0, volume=1.0)

    assert audio.startswith(b"RIFF")
    assert len(inference_calls) >= 2
    assert all(call["enable_text_splitting"] is False for call in inference_calls)
    assert all(len(str(call["text"])) <= 70 for call in inference_calls)
