from __future__ import annotations

import io
import os
import struct
import sys
import types
import wave
from collections import OrderedDict
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest

import src.tts.xtts_engine as xtts_engine
from src.tts.factory import create_tts_engine
from src.tts.wav_utils import decode_wav_bytes
from src.tts.xtts_engine import (
    XTTS_SUPPORTED_LANGUAGES,
    XTTSRuntimeStatus,
    XTTSAudioDecoderUnavailableError,
    XTTSTS,
    first_usable_xtts_reference_audio_path,
    first_xtts_reference_audio_path,
    list_xtts_reference_voices,
    normalize_xtts_language_code,
    normalize_xtts_reference_audio_file,
    repair_xtts_reference_audio_file,
    validate_xtts_reference_audio_file,
    xtts_language_from_target_language,
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


def test_xtts_runtime_status_requires_sklearn_component():
    status = XTTSRuntimeStatus(("sklearn",), None)

    assert status.ready is False
    assert status.missing_component_names == ("scikit-learn runtime",)


def test_xtts_recovers_missing_transformers_gpt2_exports(monkeypatch):
    class GenerationMixin:
        pass

    class GenerationConfig:
        pass

    class LogitsProcessorList:
        pass

    class PreTrainedModel:
        pass

    class StoppingCriteriaList:
        pass

    class GPT2Config:
        pass

    class GPT2Model:
        pass

    class GPT2PreTrainedModel:
        pass

    fake_transformers = types.SimpleNamespace(__version__="test")
    modules = {
        "transformers": fake_transformers,
        "transformers.utils.import_utils": types.SimpleNamespace(
            is_torchvision_available=lambda: False
        ),
        "transformers.generation": types.SimpleNamespace(GenerationMixin=GenerationMixin),
        "transformers.generation.configuration_utils": types.SimpleNamespace(
            GenerationConfig=GenerationConfig
        ),
        "transformers.generation.logits_process": types.SimpleNamespace(
            LogitsProcessorList=LogitsProcessorList
        ),
        "transformers.generation.stopping_criteria": types.SimpleNamespace(
            StoppingCriteriaList=StoppingCriteriaList
        ),
        "transformers.modeling_utils": types.SimpleNamespace(PreTrainedModel=PreTrainedModel),
        "transformers.models.gpt2.configuration_gpt2": types.SimpleNamespace(GPT2Config=GPT2Config),
        "transformers.models.gpt2.modeling_gpt2": types.SimpleNamespace(
            GPT2Model=GPT2Model,
            GPT2PreTrainedModel=GPT2PreTrainedModel
        ),
    }

    monkeypatch.setattr(xtts_engine.importlib, "import_module", lambda name: modules[name])

    xtts_engine._ensure_transformers_xtts_exports()

    assert fake_transformers.GenerationMixin is GenerationMixin
    assert fake_transformers.GenerationConfig is GenerationConfig
    assert fake_transformers.LogitsProcessorList is LogitsProcessorList
    assert fake_transformers.PreTrainedModel is PreTrainedModel
    assert fake_transformers.StoppingCriteriaList is StoppingCriteriaList
    assert fake_transformers.GPT2Config is GPT2Config
    assert fake_transformers.GPT2Model is GPT2Model
    assert fake_transformers.GPT2PreTrainedModel is GPT2PreTrainedModel


def test_xtts_disables_broken_optional_torchvision(monkeypatch, caplog):
    fake_import_utils = types.SimpleNamespace(
        _torchvision_available=True,
        _torchvision_version="broken",
        is_torchvision_available=lambda: True,
    )

    def fake_import_module(name: str):
        if name == "transformers.utils.import_utils":
            return fake_import_utils
        if name == "torchvision.transforms":
            raise RuntimeError("operator torchvision::nms does not exist")
        raise AssertionError(name)

    monkeypatch.setattr(xtts_engine.importlib, "import_module", fake_import_module)

    with caplog.at_level("WARNING"):
        xtts_engine._disable_broken_transformers_optional_vision()

    assert fake_import_utils._torchvision_available is False
    assert fake_import_utils._torchvision_version == "unavailable"
    assert "Disabling broken optional torchvision integration" in caplog.text


def test_xtts_restores_removed_transformers_isin_helper(monkeypatch):
    fake_utils = types.SimpleNamespace()
    fake_torch = types.SimpleNamespace(
        isin=lambda elements, test_elements: (elements, test_elements)
    )

    monkeypatch.setattr(
        xtts_engine.importlib,
        "import_module",
        lambda name: fake_utils
        if name == "transformers.pytorch_utils"
        else (_ for _ in ()).throw(AssertionError(name)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    xtts_engine._install_transformers_pytorch_utils_compat()

    assert fake_utils.isin_mps_friendly("elements", "tests") == (
        "elements",
        "tests",
    )


def test_xtts_packaging_selftest_imports_frozen_sensitive_modules(monkeypatch):
    imported: list[str] = []
    monkeypatch.setattr(
        xtts_engine,
        "_ensure_transformers_xtts_exports",
        lambda: imported.append("transformers-exports"),
    )

    def fake_import_module(name: str):
        imported.append(name)
        return types.SimpleNamespace()

    monkeypatch.setattr(xtts_engine.importlib, "import_module", fake_import_module)

    ok, message = xtts_engine.xtts_packaging_selftest()

    assert ok is True
    assert "available" in message
    assert imported == [
        "transformers-exports",
        "TTS.tts.layers.xtts.gpt_inference",
        "TTS.tts.layers.xtts.gpt",
        "TTS.tts.models.xtts",
    ]


def test_xtts_installs_matplotlib_stub_when_plotting_backend_is_absent(monkeypatch):
    for module_name in ("matplotlib", "matplotlib.pyplot", "matplotlib.colors"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(xtts_engine, "_module_available", lambda name: False if name == "matplotlib" else True)

    xtts_engine._install_matplotlib_runtime_stub()

    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    matplotlib.use("Agg")
    fig, ax = plt.subplots()
    ax.imshow([[1.0]], norm=LogNorm())
    fig.colorbar(object(), ax=ax)
    plt.close()


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


def test_xtts_reference_import_reports_missing_audio_decoder(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp3"
    source.write_bytes(b"not a wav or mp3 stream")
    output = tmp_path / "reference.wav"

    monkeypatch.setitem(sys.modules, "av", None)

    with pytest.raises(XTTSAudioDecoderUnavailableError, match="MP3.*reference audio"):
        normalize_xtts_reference_audio_file(source, output)


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
    assert engine._detect_language("Hola, señor") == "es"
    assert engine._detect_language("Bonjour, ça va") == "fr"
    assert engine._detect_language("hello") == "en"


def test_xtts_supported_languages_include_full_xtts_v2_set(monkeypatch):
    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: False)
    engine = XTTSTS()

    assert tuple(engine.get_supported_languages()[1:]) == XTTS_SUPPORTED_LANGUAGES
    assert "hi" in engine.get_supported_languages()


def test_xtts_language_normalization_maps_app_target_codes():
    assert normalize_xtts_language_code("zh") == "zh-cn"
    assert normalize_xtts_language_code("jp") == "ja"
    assert normalize_xtts_language_code("English") == "en"
    assert xtts_language_from_target_language("es") == "es"
    assert xtts_language_from_target_language("pt") == "pt"
    assert xtts_language_from_target_language("th") == "auto"


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
        }
    ]


def test_xtts_prewarm_loads_model_and_caches_conditioning(monkeypatch):
    engine = XTTSTS(device="cpu")
    calls = []
    monkeypatch.setattr(engine, "_can_synthesize", lambda: True)
    monkeypatch.setattr(
        engine,
        "_resolve_reference_audio",
        lambda voice: calls.append(("voice", voice)) or "voice.wav",
    )
    monkeypatch.setattr(
        engine,
        "_ensure_model_loaded",
        lambda: calls.append(("load", "")),
    )
    monkeypatch.setattr(
        engine,
        "_get_conditioning_latents",
        lambda path: calls.append(("conditioning", path)) or (object(), object()),
    )

    engine.prewarm("sample")

    assert calls == [
        ("voice", "sample"),
        ("load", ""),
        ("conditioning", "voice.wav"),
    ]


def test_xtts_engine_enables_gpu_when_cuda_is_available(monkeypatch):
    captured: list[dict[str, object]] = []

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(
        "src.tts.xtts_engine.inspect_torch_cuda_runtime",
        lambda _index=0: xtts_engine.TorchCudaRuntimeStatus(
            torch_importable=True,
            torch_version="2.8.0+cu128",
            cuda_build="12.8",
            cuda_available=True,
            device_count=1,
            device_name="NVIDIA RTX",
            capability=(8, 9),
            bf16_supported=True,
        ),
    )
    monkeypatch.setattr(
        XTTSTS,
        "_configure_loaded_model_runtime",
        lambda self: None,
    )
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
    assert engine._torch_device == "cuda:0"
    assert engine._runtime_precision == "float16"
    assert "gpu" not in captured[0]


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
    assert "gpu" not in captured[0]


def test_xtts_cuda_initialization_failure_reloads_on_cpu(monkeypatch):
    captured: list[bool] = []

    class FakeCoquiTTS:
        def __init__(self, **kwargs):
            use_gpu = len(captured) == 0
            captured.append(use_gpu)
            if use_gpu:
                raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(
        "src.tts.xtts_engine.inspect_torch_cuda_runtime",
        lambda _index=0: xtts_engine.TorchCudaRuntimeStatus(
            torch_importable=True,
            torch_version="2.8.0+cu128",
            cuda_build="12.8",
            cuda_available=True,
            device_count=1,
            device_name="NVIDIA RTX",
            capability=(8, 9),
            bf16_supported=True,
        ),
    )
    monkeypatch.setattr("src.tts.xtts_engine.clear_torch_cuda_cache", lambda: None)
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

    assert captured == [True, False]
    assert engine._requested_device == "cuda"
    assert engine._runtime_device == "cpu"
    assert engine._runtime_precision == "float32"
    assert "out of memory" in engine._cuda_fallback_reason.lower()


def test_xtts_conditioning_tensors_are_moved_to_selected_cuda_device(monkeypatch):
    moved: list[tuple[str, bool]] = []

    class FakeTensor:
        def to(self, device, non_blocking=False):
            moved.append((str(device), bool(non_blocking)))
            return self

    class FakeCoreModel:
        def get_conditioning_latents(self, **_kwargs):
            return FakeTensor(), FakeTensor()

        def inference(self, **_kwargs):
            return {"wav": np.zeros(10, dtype=np.float32)}

    engine = XTTSTS.__new__(XTTSTS)
    engine._model = types.SimpleNamespace(
        synthesizer=types.SimpleNamespace(tts_model=FakeCoreModel())
    )
    engine._conditioning_cache = OrderedDict()
    engine._conditioning_cache_size = 1
    engine._runtime_device = "cuda"
    engine._torch_device = "cuda:1"
    engine._runtime_precision = "float32"
    monkeypatch.setattr(
        engine,
        "_torch_execution_context",
        lambda **_kwargs: nullcontext(),
    )

    engine._get_conditioning_latents("voice.wav")

    assert moved == [("cuda:1", True), ("cuda:1", True)]


def test_xtts_mixed_precision_failure_retries_in_float32(monkeypatch):
    calls = 0

    class FakeCoreModel:
        def inference(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("bfloat16 operator is not implemented")
            return {"wav": np.zeros(10, dtype=np.float32)}

    engine = XTTSTS.__new__(XTTSTS)
    engine._runtime_device = "cuda"
    engine._runtime_precision = "bfloat16"
    engine._precision_fallback_reason = ""
    engine._inference_kwargs = {}
    monkeypatch.setattr(
        engine,
        "_torch_execution_context",
        lambda **_kwargs: nullcontext(),
    )

    samples = engine._run_xtts_core_inference(
        core_model=FakeCoreModel(),
        text="hello",
        language="en",
        gpt_cond_latent=object(),
        speaker_embedding=object(),
        speed=1.0,
    )

    assert calls == 2
    assert samples.shape == (10,)
    assert engine._runtime_precision == "float32"
    assert "not implemented" in engine._precision_fallback_reason


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


def test_xtts_coqui_api_fallback_disables_spacy_sentence_splitting(monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(_tone_wav_bytes(duration_s=3.0, sample_rate=24000))
    calls: list[dict[str, object]] = []

    class FakeModel:
        def tts_to_file(self, **kwargs):
            if kwargs.get("split_sentences") is not False:
                raise RuntimeError("enable_text_splitting=True requires Spacy")
            calls.append(kwargs)
            output_path = kwargs["file_path"]
            with open(output_path, "wb") as wav_file:
                wav_file.write(_tone_wav_bytes(duration_s=0.1, sample_rate=24000))

    monkeypatch.setattr("src.tts.xtts_engine.is_xtts_runtime_available", lambda **_kwargs: True)
    engine = XTTSTS.__new__(XTTSTS)
    engine._model = FakeModel()
    engine._reference_audio_path = str(reference)
    engine._language = "ja"
    engine._supported_languages = ["ja"]
    engine._optimized_inference = False
    engine._enable_text_splitting = True
    engine._inference_kwargs = {}

    text = (
        "これはXTTSの音声合成テストです。"
        "短いノイズではなく自然な人の声として聞こえるか確認してください。"
        "長い文章でも内蔵分割で処理できる必要があります。"
    )
    audio = engine.synthesize(text, "custom", rate=1.0, volume=1.0)

    assert audio.startswith(b"RIFF")
    assert len(calls) >= 2
    assert all(call["split_sentences"] is False for call in calls)
    assert all(len(str(call["text"])) <= 70 for call in calls)



def test_xtts_oversized_reference_is_rejected_without_pyav(monkeypatch, tmp_path):
    source = tmp_path / "oversized.wav"
    source.write_bytes(b"12345")
    output = tmp_path / "normalized.wav"
    monkeypatch.setattr(xtts_engine, "XTTS_REFERENCE_MAX_FILE_BYTES", 4)
    monkeypatch.setattr(
        xtts_engine,
        "_read_audio_with_av",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized files must not reach PyAV")
        ),
    )

    with pytest.raises(ValueError, match="exceeds the 256 MiB safety limit"):
        normalize_xtts_reference_audio_file(source, output)

    assert not output.exists()


def test_xtts_hardlinked_reference_is_rejected_without_pyav(monkeypatch, tmp_path):
    original = tmp_path / "original.wav"
    original.write_bytes(b"not-a-wave")
    source = tmp_path / "hardlinked.wav"
    try:
        os.link(original, source)
    except (OSError, NotImplementedError):
        pytest.skip("hard-link creation is unavailable")
    monkeypatch.setattr(
        xtts_engine,
        "_read_audio_with_av",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("linked files must not reach PyAV")
        ),
    )

    with pytest.raises(RuntimeError, match="unsafe writable file"):
        normalize_xtts_reference_audio_file(source, tmp_path / "normalized.wav")


def test_xtts_symlinked_reference_is_rejected_without_pyav(monkeypatch, tmp_path):
    original = tmp_path / "original.wav"
    original.write_bytes(b"not-a-wave")
    source = tmp_path / "symlinked.wav"
    try:
        os.symlink(original, source)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic-link creation is unavailable")
    monkeypatch.setattr(
        xtts_engine,
        "_read_audio_with_av",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("linked files must not reach PyAV")
        ),
    )

    with pytest.raises(RuntimeError, match="unsafe writable file"):
        normalize_xtts_reference_audio_file(source, tmp_path / "normalized.wav")


def test_xtts_pyav_decoder_receives_open_file_object(monkeypatch, tmp_path):
    source = tmp_path / "reference.mp3"
    source.write_bytes(b"fake encoded audio")
    opened: dict[str, object] = {}

    class FakeFrame:
        @staticmethod
        def to_ndarray():
            return np.array([[1000, -1000]], dtype=np.int16)

    class FakeContainer:
        streams = [types.SimpleNamespace(type="audio")]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def decode(_stream):
            yield FakeFrame()

    class FakeAudioResampler:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def resample(frame):
            return [] if frame is None else frame

    fake_av = types.ModuleType("av")
    fake_av_audio = types.ModuleType("av.audio")
    fake_av_resampler = types.ModuleType("av.audio.resampler")
    fake_av_resampler.AudioResampler = FakeAudioResampler

    def fake_open(value):
        opened["value"] = value
        opened["was_open"] = not value.closed
        opened["was_file_object"] = hasattr(value, "read") and not isinstance(
            value,
            (str, bytes, os.PathLike),
        )
        return FakeContainer()

    fake_av.open = fake_open
    monkeypatch.setitem(sys.modules, "av", fake_av)
    monkeypatch.setitem(sys.modules, "av.audio", fake_av_audio)
    monkeypatch.setitem(sys.modules, "av.audio.resampler", fake_av_resampler)

    audio, sample_rate = xtts_engine._read_audio_with_av(source, 24000)

    assert sample_rate == 24000
    assert audio.tolist() == pytest.approx([1000 / 32768.0, -1000 / 32768.0])
    assert opened["was_open"] is True
    assert opened["was_file_object"] is True
    assert opened["value"].closed is True


def test_xtts_synthesis_rejects_replaced_output_path(monkeypatch, tmp_path):
    generated_paths: list[Path] = []

    class ReplacingModel:
        @staticmethod
        def tts_to_file(*, file_path, **_kwargs):
            path = Path(file_path)
            generated_paths.append(path)
            path.unlink()
            path.write_bytes(_tone_wav_bytes(duration_s=1.0, sample_rate=24000))

    monkeypatch.setattr(xtts_engine, "app_temp_dir", lambda: tmp_path)
    engine = XTTSTS.__new__(XTTSTS)
    engine._model = ReplacingModel()
    engine._inference_kwargs = {}
    engine._xtts_core_model = lambda: None
    engine._xtts_text_chunks = lambda _text, _language, _model: ["hello"]

    with pytest.raises(RuntimeError, match="output path was replaced"):
        engine._synthesize_with_coqui_api_no_spacy(
            text="hello",
            ref_audio=str(tmp_path / "reference.wav"),
            language="en",
            speed=1.0,
            volume=1.0,
        )

    assert len(generated_paths) == 1
    assert generated_paths[0].exists()
    assert generated_paths[0].read_bytes().startswith(b"RIFF")
    generated_paths[0].unlink()


def test_xtts_private_temp_cleanup_does_not_delete_replacement(tmp_path):
    temp_path, original_stat = xtts_engine._allocate_private_temp_file(
        tmp_path,
        prefix=".reference.",
        suffix=".wav",
    )
    temp_path.unlink()
    temp_path.write_bytes(b"replacement")

    xtts_engine._cleanup_private_temp_file(
        temp_path,
        original_stat,
        purpose="test",
    )

    assert temp_path.read_bytes() == b"replacement"
