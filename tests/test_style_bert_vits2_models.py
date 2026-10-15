"""Tests for custom Style-Bert-VITS2 voice import and playback helpers."""
from __future__ import annotations

import builtins
import json
import importlib
import os
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from src.tts import style_bert_vits2_engine as engine_store
from src.tts import style_bert_vits2_models as model_store
from src.tts.style_bert_vits2_engine import StyleBertVits2TTS


def _write_style_model(root: Path, name: str = "sample_voice") -> Path:
    model_dir = root / name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text(
        json.dumps(
            {
                # A regular (multilingual) SBV2 2.x model; JP-Extra models end
                # in "-JP-Extra" and a missing version means JP-Extra upstream.
                "version": "2.5.0",
                "data": {
                    "spk2id": {"Alice": 0},
                    "style2id": {"Neutral": 0, "Happy": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    (model_dir / "style_vectors.npy").write_bytes(b"style")
    (model_dir / "voice.safetensors").write_bytes(b"weights")
    return model_dir


def test_sbv2_probe_does_not_throttle_process_wide_torch_threads(monkeypatch):
    setter_calls: list[tuple[str, int]] = []
    fake_torch = SimpleNamespace(
        get_num_threads=lambda: 16,
        get_num_interop_threads=lambda: 8,
        set_num_threads=lambda value: setter_calls.append(("threads", value)),
        set_num_interop_threads=lambda value: setter_calls.append(("interop", value)),
    )
    monkeypatch.setattr(engine_store, "torch", fake_torch)
    monkeypatch.setattr(engine_store, "_SBV2_CPU_RUNTIME_CONFIGURED", False)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        monkeypatch.delenv(name, raising=False)

    engine_store._configure_style_bert_cpu_runtime()

    assert setter_calls == []
    assert all(
        name not in os.environ
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
    )


def test_style_bert_close_releases_cached_models_and_cuda_cache(monkeypatch):
    events: list[str] = []
    engine = StyleBertVits2TTS.__new__(StyleBertVits2TTS)
    engine._closed = False
    engine._model_cache = {"voice": object()}
    engine._patch_applied = False

    monkeypatch.setattr(engine_store.gc, "collect", lambda: events.append("gc"))
    monkeypatch.setattr(
        engine_store,
        "clear_torch_cuda_cache",
        lambda: events.append("cuda-cache-clear"),
    )

    engine.close()
    engine.close()

    assert engine._model_cache == {}
    assert events == ["gc", "cuda-cache-clear"]
    assert engine.is_available() is False


def test_import_style_model_folder_and_list_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    source_dir = _write_style_model(tmp_path / "source")

    imported = model_store.import_style_bert_model_path(source_dir)
    listed = model_store.list_imported_style_bert_models()

    assert len(imported) == 1
    assert imported[0].name == "sample_voice"
    assert imported[0].speakers == ("Alice",)
    assert imported[0].styles == ("Neutral", "Happy")
    assert len(listed) == 1
    assert listed[0].directory.parent == model_store.style_bert_models_dir()


def test_import_model_root_accepts_multiple_children(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    source_root = tmp_path / "bundle"
    _write_style_model(source_root, "voice_a")
    _write_style_model(source_root, "voice_b")

    imported = model_store.import_style_bert_model_path(source_root)

    assert [item.name for item in imported] == ["voice_a", "voice_b"]


@pytest.mark.parametrize("suffix", (".pth", ".pt"))
def test_import_rejects_pickle_backed_style_bert_weights(tmp_path, suffix):
    model_dir = _write_style_model(tmp_path / "source")
    (model_dir / "voice.safetensors").unlink()
    (model_dir / f"voice{suffix}").write_bytes(b"pickle")

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Convert the model to .safetensors",
    ):
        model_store.inspect_style_bert_model_dir(model_dir)


def test_newer_pickle_weight_cannot_override_safe_style_bert_weight(tmp_path):
    model_dir = _write_style_model(tmp_path / "source")
    unsafe = model_dir / "newer.pth"
    unsafe.write_bytes(b"pickle")
    newer = (model_dir / "voice.safetensors").stat().st_mtime_ns + 10_000_000
    os.utime(unsafe, ns=(newer, newer))

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Unsafe pickle-backed model weights",
    ):
        model_store.inspect_style_bert_model_dir(model_dir)


def test_import_rejects_unsupported_onnx_style_bert_weight(tmp_path):
    model_dir = _write_style_model(tmp_path / "source")
    (model_dir / "voice.safetensors").unlink()
    (model_dir / "voice.onnx").write_bytes(b"onnx")

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Unsupported Style-Bert-VITS2",
    ):
        model_store.inspect_style_bert_model_dir(model_dir)


def test_style_bert_engine_lists_and_synthesizes_imported_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(
        StyleBertVits2TTS,
        "_ensure_bert_runtime",
        lambda self, language=None: None,
    )
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "sample_voice")

    infer_calls: list[dict[str, object]] = []

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.spk2id = {"Alice": 7}
            self.style2id = {"Neutral": 0, "Happy": 1}

        def infer(self, **kwargs):
            infer_calls.append(kwargs)
            return 24000, np.array([0, 1200, -1200], dtype=np.int16)

    engine = StyleBertVits2TTS(bert_language="en")
    engine._tts_model_cls = FakeModel

    voices = engine.get_available_voices()
    audio = engine.synthesize(
        "hello",
        model_store.style_bert_voice_id("sample_voice", "Alice", "Happy"),
        rate=2.0,
        volume=0.5,
    )

    assert [voice.id for voice in voices] == [
        "sample_voice :: Alice :: Neutral",
        "sample_voice :: Alice :: Happy",
    ]
    assert [voice.language for voice in voices] == ["en", "en"]
    assert [voice.locale for voice in voices] == ["en-US", "en-US"]
    assert audio.startswith(b"RIFF")
    assert infer_calls == [
        {
            "text": "hello",
            "language": "EN",
            "speaker_id": 7,
            "style": "Happy",
            "length": 0.5,
            "sdp_ratio": 0.0,
        }
    ]


def test_hololive_catalog_labels_matching_imported_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "SBV2_HoloLow")

    imported = managed_root / "SBV2_HoloLow"
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["data"]["spk2id"] = {"MoriCalliope": 0}
    config["data"]["style2id"] = {"Neutral": 0}
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")

    voices = StyleBertVits2TTS().get_available_voices()

    assert voices[0].id == "SBV2_HoloLow :: MoriCalliope :: Neutral"
    assert voices[0].name == "Mori Calliope / Neutral"
    # A catalog voice is multilingual: it is listed in the language the
    # player chose, not locked to the talent's own language (1.5.0 locked it
    # and catalog voices could no longer speak Chinese).
    assert voices[0].language == "ja"
    chinese = StyleBertVits2TTS(bert_language="zh").get_available_voices()
    assert chinese[0].language == "zh"
    assert chinese[0].locale == "zh-CN"


def test_light_style_bert_voice_listing_does_not_load_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "sample_voice")
    monkeypatch.setattr(
        engine_store,
        "_load_runtime_tts_model_cls",
        lambda: (_ for _ in ()).throw(AssertionError("runtime should not load")),
    )

    voices = engine_store.list_style_bert_vits2_voices("en")

    assert [voice.id for voice in voices] == [
        "sample_voice :: Alice :: Neutral",
        "sample_voice :: Alice :: Happy",
    ]
    assert [voice.language for voice in voices] == ["en", "en"]


def test_style_bert_synthesis_uses_configured_bert_language(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "SBV2_HoloLow")

    imported = managed_root / "SBV2_HoloLow"
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["data"]["spk2id"] = {"MoriCalliope": 0}
    config["data"]["style2id"] = {"Neutral": 0}
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")

    ensured_languages: list[str | None] = []
    infer_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        StyleBertVits2TTS,
        "_ensure_bert_runtime",
        lambda self, language=None: ensured_languages.append(language),
    )

    class FakeModel:
        def __init__(self, **_kwargs):
            self.spk2id = {"MoriCalliope": 3}
            self.style2id = {"Neutral": 0}

        def infer(self, **kwargs):
            infer_calls.append(kwargs)
            return 24000, np.array([0, 600, -600], dtype=np.int16)

    engine = StyleBertVits2TTS(bert_language="jp")
    engine._tts_model_cls = FakeModel

    audio = engine.synthesize(
        "hello",
        model_store.style_bert_voice_id("SBV2_HoloLow", "MoriCalliope", "Neutral"),
    )

    assert audio.startswith(b"RIFF")
    # Mori Calliope is an English catalog voice: the global "jp" setting must
    # not push English text through the Japanese g2p.
    assert ensured_languages == ["en"]
    assert infer_calls[0]["language"] == "EN"
    assert infer_calls[0]["sdp_ratio"] == 0.0


def test_style_bert_unknown_model_follows_configured_bert_language(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "sample_voice")

    ensured_languages: list[str | None] = []
    infer_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        StyleBertVits2TTS,
        "_ensure_bert_runtime",
        lambda self, language=None: ensured_languages.append(language),
    )

    class FakeModel:
        def __init__(self, **_kwargs):
            self.spk2id = {"Speaker": 0}
            self.style2id = {"Neutral": 0}

        def infer(self, **kwargs):
            infer_calls.append(kwargs)
            return 24000, np.array([0, 600, -600], dtype=np.int16)

    engine = StyleBertVits2TTS(bert_language="zh")
    engine._tts_model_cls = FakeModel

    engine.synthesize(
        "你好",
        model_store.style_bert_voice_id("sample_voice", "Speaker", "Neutral"),
    )

    assert ensured_languages == ["zh"]
    assert infer_calls[0]["language"] == "ZH"


def test_style_bert_jp_extra_model_always_speaks_japanese(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "jp_extra_voice")
    imported = managed_root / "jp_extra_voice"
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["version"] = "2.5.0-JP-Extra"
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")

    info = model_store.inspect_style_bert_model_dir(imported)
    assert info.jp_extra is True
    # Upstream raises "trained with JP-Extra, but the language is not JP" for
    # anything else, so the engine must ignore an English BERT setting here.
    assert model_store.resolve_style_bert_voice_language(info, "Speaker", "en") == "jp"
    assert (
        model_store.style_bert_voice_language(
            model_store.style_bert_voice_id("jp_extra_voice", "Speaker", "Neutral"),
            "en",
        )
        == "jp"
    )
    assert StyleBertVits2TTS(bert_language="en").get_available_voices()[0].language == "ja"


def test_imported_chinese_pack_with_training_flag_still_speaks_chinese(
    tmp_path, monkeypatch
):
    # Player voice packs often keep data.use_jp_extra = true (the training
    # default) while the version says a regular multilingual model. Upstream
    # only looks at the version suffix, so the pack must keep Chinese.
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "chinese_pack")
    imported = managed_root / "chinese_pack"
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["version"] = "2.4.1"
    config["data"]["use_jp_extra"] = True
    config["data"]["spk2id"] = {"Xiaoyu": 0}
    config["data"]["style2id"] = {"Neutral": 0}
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")
    voice_id = model_store.style_bert_voice_id("chinese_pack", "Xiaoyu", "Neutral")

    assert model_store.inspect_style_bert_model_dir(imported).jp_extra is False
    assert StyleBertVits2TTS(bert_language="zh").get_available_voices()[0].language == "zh"
    ensured, calls = _synthesize_languages(
        monkeypatch,
        StyleBertVits2TTS(bert_language="zh"),
        voice_id,
        "大家好，我是小雨。",
        ready={"zh"},
    )
    assert ensured == ["zh"]
    assert calls[0]["language"] == "ZH"


@pytest.mark.parametrize(
    ("version", "jp_extra"),
    [
        ("2.5.0-JP-Extra", True),
        ("2.0-JP-Extra", True),
        ("2.5.0", False),
        ("2.1", False),
        ("1.3", False),
        (None, True),  # missing: upstream HyperParameters defaults to 2.0-JP-Extra
    ],
)
def test_jp_extra_detection_matches_upstream(tmp_path, version, jp_extra):
    model_dir = _write_style_model(tmp_path, "voice")
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    config["data"]["use_jp_extra"] = not jp_extra  # never decides it
    if version is None:
        config.pop("version")
    else:
        config["version"] = version
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    assert model_store.inspect_style_bert_model_dir(model_dir).jp_extra is jp_extra


def test_style_bert_cuda_prepares_acoustic_model_as_fp32(monkeypatch):
    monkeypatch.setattr(engine_store, "style_bert_cuda_available", lambda: True)

    class FakeNet:
        def __init__(self):
            self.float_calls = 0

        def float(self):
            self.float_calls += 1
            return self

    class FakeModel:
        def __init__(self):
            self.net = FakeNet()
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            setattr(self, "_TTSModel__net_g", self.net)

    engine = StyleBertVits2TTS(device="cuda")
    model = FakeModel()

    engine._prepare_voice_model_for_device(model)

    assert model.load_calls == 1
    assert model.net.float_calls == 1


def test_style_bert_language_mapping_helpers():
    assert engine_store.normalize_style_bert_bert_language("ja") == "jp"
    assert engine_store.normalize_style_bert_bert_language("english") == "en"
    assert engine_store.normalize_style_bert_bert_language("en_US") == "en"
    assert engine_store.normalize_style_bert_bert_language("zh-CN") == "zh"
    assert engine_store.normalize_style_bert_bert_language("中文") == "zh"
    assert (
        engine_store.style_bert_bert_model_id("zh")
        == "hfl/chinese-roberta-wwm-ext-large"
    )


def test_style_bert_english_dependency_probe_reports_missing_sentencepiece(monkeypatch):
    def fake_import_module(name):
        if name == "sentencepiece":
            raise ModuleNotFoundError("No module named 'sentencepiece'")
        return SimpleNamespace()

    monkeypatch.setattr(engine_store.importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError) as excinfo:
        engine_store._ensure_style_bert_language_runtime_dependencies("english")

    message = str(excinfo.value)
    assert "English BERT runtime dependency is missing" in message
    assert "sentencepiece" in message


def test_style_bert_chinese_dependency_probe_reports_missing_segmenter(monkeypatch):
    def fake_import_module(name):
        if name == "jieba.posseg":
            raise ModuleNotFoundError("No module named 'jieba.posseg'")
        return SimpleNamespace()

    monkeypatch.setattr(engine_store.importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError) as excinfo:
        engine_store._ensure_style_bert_language_runtime_dependencies("zh-CN")

    message = str(excinfo.value)
    assert "Chinese BERT runtime dependency is missing" in message
    assert "jieba POS tokenizer" in message


def test_style_bert_english_text_processing_preflight(monkeypatch):
    calls = []
    fake_languages = SimpleNamespace(EN="EN", ZH="ZH")
    fake_constants = SimpleNamespace(Languages=fake_languages)
    fake_nlp = SimpleNamespace(clean_text=lambda text, language: calls.append((text, language)))

    engine_store._SBV2_LANGUAGE_PREFLIGHTED.discard("en")
    monkeypatch.setitem(sys.modules, "style_bert_vits2.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "style_bert_vits2.nlp", fake_nlp)

    engine_store._preflight_style_bert_text_processing("en")
    engine_store._preflight_style_bert_text_processing("en")

    assert calls == [("Hello, nice to meet you in 2026.", "EN")]
    assert "en" in engine_store._SBV2_LANGUAGE_PREFLIGHTED


def test_style_bert_chinese_text_processing_preflight(monkeypatch):
    calls = []
    fake_languages = SimpleNamespace(EN="EN", ZH="ZH")
    fake_constants = SimpleNamespace(Languages=fake_languages)
    fake_nlp = SimpleNamespace(clean_text=lambda text, language: calls.append((text, language)))

    engine_store._SBV2_LANGUAGE_PREFLIGHTED.discard("zh")
    monkeypatch.setitem(sys.modules, "style_bert_vits2.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "style_bert_vits2.nlp", fake_nlp)

    engine_store._preflight_style_bert_text_processing("zh")
    engine_store._preflight_style_bert_text_processing("zh")

    assert calls == [("你好，Mio 2026。", "ZH")]
    assert "zh" in engine_store._SBV2_LANGUAGE_PREFLIGHTED


def test_style_bert_japanese_dependency_probe_requires_packaged_openjtalk_dict(
    tmp_path, monkeypatch
):
    fake_dict_dir = tmp_path / "missing-dic"
    fake_pyopenjtalk = SimpleNamespace(
        OPEN_JTALK_DICT_DIR=str(fake_dict_dir).encode("utf-8")
    )

    monkeypatch.setattr(engine_store.sys, "frozen", True, raising=False)
    monkeypatch.setitem(sys.modules, "pyopenjtalk", fake_pyopenjtalk)

    with pytest.raises(RuntimeError) as excinfo:
        engine_store._ensure_style_bert_language_runtime_dependencies("jp")

    message = str(excinfo.value)
    assert "Open JTalk dictionary is missing" in message
    assert str(fake_dict_dir) in message


def test_style_bert_japanese_dependency_probe_accepts_packaged_openjtalk_dict(
    tmp_path, monkeypatch
):
    fake_dict_dir = tmp_path / "open_jtalk_dic_utf_8-1.11"
    fake_dict_dir.mkdir()
    for name in engine_store._OPEN_JTALK_REQUIRED_DICT_FILES:
        (fake_dict_dir / name).write_bytes(b"ok")
    fake_pyopenjtalk = SimpleNamespace(
        OPEN_JTALK_DICT_DIR=str(fake_dict_dir).encode("utf-8")
    )

    monkeypatch.setattr(engine_store.sys, "frozen", True, raising=False)
    monkeypatch.setitem(sys.modules, "pyopenjtalk", fake_pyopenjtalk)

    engine_store._ensure_style_bert_language_runtime_dependencies("jp")


def test_style_bert_engine_reports_missing_bert_runtime_at_synthesis(tmp_path, monkeypatch):
    """A missing BERT download must surface as a specific error, not as "unavailable".

    An unavailable engine is silently dropped by the manager; the explicit
    error reaches the player with a hint to download the model.
    """
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    _write_style_model(model_store.style_bert_models_dir(), "sample_voice")

    engine = StyleBertVits2TTS()
    engine._tts_model_cls = object()

    monkeypatch.setattr(engine_store, "style_bert_bert_assets_ready", lambda _lang: False)
    assert engine.is_available() is True
    with pytest.raises(RuntimeError) as excinfo:
        engine.synthesize(
            "hello",
            model_store.style_bert_voice_id("sample_voice", "Speaker", "Neutral"),
        )
    assert "BERT runtime model" in str(excinfo.value)
    assert "is not downloaded" in str(excinfo.value)

    monkeypatch.setattr(engine_store, "style_bert_bert_assets_ready", lambda _lang: True)
    assert engine.is_available() is True
    engine.close()
    assert engine.is_available() is False


def test_transformers_export_probe_reports_missing_style_bert_symbols():
    module = SimpleNamespace(AutoTokenizer=object())

    missing = engine_store._missing_transformers_bert_exports(module)

    assert "AutoModelForMaskedLM" in missing
    assert "AutoTokenizer" not in missing


def test_transformers_export_recovery_installs_missing_symbol(monkeypatch):
    fake_transformers = SimpleNamespace(
        AutoModelForMaskedLM=object(),
        DebertaV2Model=object(),
        DebertaV2Tokenizer=object(),
        PreTrainedModel=object(),
        PreTrainedTokenizer=object(),
        PreTrainedTokenizerFast=object(),
    )

    class AutoTokenizer:
        pass

    def fake_import_module(name):
        if name == "transformers.models.auto.tokenization_auto":
            return SimpleNamespace(AutoTokenizer=AutoTokenizer)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(engine_store.importlib, "import_module", fake_import_module)

    engine_store._install_transformers_bert_exports(fake_transformers)

    assert fake_transformers.AutoTokenizer is AutoTokenizer
    assert engine_store._missing_transformers_bert_exports(fake_transformers) == []


def test_style_bert_engine_module_imports_without_scipy(monkeypatch):
    for module_name in list(sys.modules):
        if module_name == "scipy" or module_name.startswith("scipy."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setitem(sys.modules, "scipy", None)

    importlib.reload(engine_store)


def test_style_bert_runtime_imports_without_numba(monkeypatch):
    pytest.importorskip("style_bert_vits2")
    for module_name in list(sys.modules):
        if module_name == "numba" or module_name.startswith("numba."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
        if module_name == "style_bert_vits2" or module_name.startswith("style_bert_vits2."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "numba" or name.startswith("numba."):
            raise ModuleNotFoundError("No module named 'numba'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setattr(engine_store, "_RUNTIME_LOAD_ATTEMPTED", False)
    monkeypatch.setattr(engine_store, "_RUNTIME_TTS_MODEL_CLS", None)
    monkeypatch.setattr(engine_store, "_RUNTIME_IMPORT_ERROR", "")

    assert engine_store.style_bert_runtime_available() is True
    assert engine_store.style_bert_runtime_error() == ""


def test_style_bert_replaces_numba_backed_monotonic_alignment(monkeypatch):
    module_name = "style_bert_vits2.models.monotonic_alignment"

    class _NumbaLikeCallable:
        py_func = object()

        def __call__(self, *_args, **_kwargs):
            raise OSError("could not get source code")

    fake_module = SimpleNamespace(maximum_path=_NumbaLikeCallable())
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    engine_store._install_monotonic_alignment_fallback()

    assert fake_module.maximum_path is engine_store._maximum_path_fallback
    assert fake_module._MIO_FALLBACK is True


def test_style_bert_disables_pyopenjtalk_worker_in_frozen_runtime(monkeypatch):
    class FakeWorkerClient:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    first_client = FakeWorkerClient()
    fake_worker_module = SimpleNamespace(
        WORKER_CLIENT=first_client,
        initialize_worker=lambda *_args, **_kwargs: None,
        terminate_worker=lambda: None,
    )

    monkeypatch.setattr(engine_store.sys, "frozen", True, raising=False)
    monkeypatch.setattr(engine_store, "_SBV2_PYOPENJTALK_WORKER_PATCHED", False)
    monkeypatch.setitem(
        sys.modules,
        "style_bert_vits2.nlp.japanese.pyopenjtalk_worker",
        fake_worker_module,
    )

    engine_store._disable_packaged_pyopenjtalk_worker()

    assert first_client.closed is True
    assert fake_worker_module.WORKER_CLIENT is None

    second_client = FakeWorkerClient()
    fake_worker_module.WORKER_CLIENT = second_client
    fake_worker_module.initialize_worker()

    assert second_client.closed is True
    assert fake_worker_module.WORKER_CLIENT is None


def test_style_bert_disables_typeguard_in_frozen_runtime(monkeypatch):
    def broken_typechecked(target=None, **_kwargs):
        raise OSError("could not get source code")

    fake_typeguard = SimpleNamespace(typechecked=broken_typechecked)
    fake_decorators = SimpleNamespace(typechecked=broken_typechecked)
    def wrapped():
        return "ok"

    monkeypatch.setattr(engine_store.sys, "frozen", True, raising=False)
    monkeypatch.setattr(engine_store, "_SBV2_TYPEGUARD_PATCHED", False)
    monkeypatch.setitem(sys.modules, "typeguard", fake_typeguard)
    monkeypatch.setitem(sys.modules, "typeguard._decorators", fake_decorators)

    engine_store._install_packaged_typeguard_noop_patch()

    assert fake_typeguard.typechecked(wrapped) is wrapped
    assert fake_decorators.typechecked()(wrapped) is wrapped


def test_style_bert_installs_offline_g2p_fallback_when_nltk_is_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "g2p_en", raising=False)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "nltk" or name.startswith("nltk."):
            raise ModuleNotFoundError("No module named 'nltk'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    engine_store._install_g2p_en_offline_fallback()
    import g2p_en

    assert getattr(g2p_en, "_MIO_FALLBACK", False) is True
    assert g2p_en.__spec__ is importlib.util.find_spec("g2p_en")
    assert g2p_en.G2p()("hello") == ["HH", "AH0", "L", "OW1"]


def test_style_bert_english_g2p_never_imports_nltk(monkeypatch):
    """The shim must stay NLTK-free: the frozen build ships a tagger NLTK 3.9+ cannot use."""
    monkeypatch.delitem(sys.modules, "g2p_en", raising=False)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "nltk" or name.startswith("nltk.") or name == "g2p_en.g2p":
            raise AssertionError(f"unexpected import of {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    engine_store._install_g2p_en_offline_fallback()
    import g2p_en

    g2p = g2p_en.G2p()
    # Out-of-vocabulary words go through g2p_en's GRU (when its weights are
    # installed) or the letter rules; both yield ARPAbet phones SBV2 accepts.
    phones = [phone for phone in g2p("skibidi") if phone != " "]
    assert phones
    assert all(phone.rstrip("012") in engine_store._G2P_EN_PHONEMES or phone in engine_store._G2P_EN_PHONEMES for phone in phones)
    if engine_store._load_neural_english_g2p() is not None:
        assert engine_store._load_neural_english_g2p().predict("skibidi") == phones


def test_style_bert_replaces_an_imported_real_g2p_en(monkeypatch):
    """A real g2p_en that was imported first is swapped out, including SBV2's bound instance."""
    fake_module = ModuleType("g2p_en")
    fake_module.__spec__ = None
    fake_module.G2p = object
    monkeypatch.setitem(sys.modules, "g2p_en", fake_module)
    fake_sbv2_g2p = ModuleType("style_bert_vits2.nlp.english.g2p")
    fake_sbv2_g2p._g2p = object()
    monkeypatch.setitem(sys.modules, "style_bert_vits2.nlp.english.g2p", fake_sbv2_g2p)

    engine_store._install_g2p_en_offline_fallback()

    installed = sys.modules["g2p_en"]
    assert installed is not fake_module
    assert getattr(installed, "_MIO_FALLBACK", False) is True
    assert installed.__spec__ is not None
    assert isinstance(fake_sbv2_g2p._g2p, engine_store._OfflineG2p)



def _managed_install_fixture(tmp_path: Path):
    root = tmp_path / "managed"
    root.mkdir()
    target = _write_style_model(root, "sample_voice")
    (target / "old.marker").write_text("old", encoding="utf-8")
    staging = _write_style_model(root, ".incoming.staging")
    (staging / "new.marker").write_text("new", encoding="utf-8")
    _, staging_snapshot = model_store._scan_model_tree(staging)
    return root, target, staging, staging_snapshot


def test_staged_publication_keeps_existing_target_recoverable_until_validated(
    tmp_path,
    monkeypatch,
):
    root, target, staging, staging_snapshot = _managed_install_fixture(tmp_path)
    original_inspect = model_store.inspect_style_bert_model_dir
    observed_backups: list[Path] = []

    def inspect_published(path):
        backups = [
            candidate
            for candidate in root.iterdir()
            if candidate.name.startswith(".sample_voice.")
            and candidate.name.endswith(".backup")
        ]
        assert len(backups) == 1
        assert (backups[0] / "old.marker").read_text(encoding="utf-8") == "old"
        assert (Path(path) / "new.marker").read_text(encoding="utf-8") == "new"
        observed_backups.extend(backups)
        return original_inspect(path)

    monkeypatch.setattr(model_store, "inspect_style_bert_model_dir", inspect_published)

    installed = model_store._install_staged_tree(
        staging,
        staging_snapshot,
        target,
        root,
    )

    assert installed.name == "sample_voice"
    assert observed_backups
    assert (target / "new.marker").read_text(encoding="utf-8") == "new"
    assert not (target / "old.marker").exists()
    assert not any(path.exists() for path in observed_backups)


def test_post_install_validation_failure_restores_previous_target(tmp_path, monkeypatch):
    root, target, staging, staging_snapshot = _managed_install_fixture(tmp_path)

    def fail_validation(_path):
        raise model_store.StyleBertVits2ModelError("forced post-install failure")

    monkeypatch.setattr(model_store, "inspect_style_bert_model_dir", fail_validation)

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="forced post-install failure",
    ):
        model_store._install_staged_tree(
            staging,
            staging_snapshot,
            target,
            root,
        )

    assert (target / "old.marker").read_text(encoding="utf-8") == "old"
    assert not (target / "new.marker").exists()
    assert not any(path.name.endswith(".backup") for path in root.iterdir())


def test_staging_tampering_is_rejected_before_existing_target_moves(tmp_path):
    root, target, staging, staging_snapshot = _managed_install_fixture(tmp_path)
    (staging / "voice.safetensors").write_bytes(b"tampered weights")

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Staged model changed before atomic publication",
    ):
        model_store._install_staged_tree(
            staging,
            staging_snapshot,
            target,
            root,
        )

    assert (target / "old.marker").read_text(encoding="utf-8") == "old"
    assert staging.exists()
    assert not any(path.name.endswith(".backup") for path in root.iterdir())


def test_tampered_backup_is_not_republished_during_rollback(tmp_path, monkeypatch):
    root, target, staging, staging_snapshot = _managed_install_fixture(tmp_path)
    tampered_backups: list[Path] = []

    def tamper_backup_then_fail(_path):
        backup = next(path for path in root.iterdir() if path.name.endswith(".backup"))
        (backup / "old.marker").write_text("tampered", encoding="utf-8")
        tampered_backups.append(backup)
        raise model_store.StyleBertVits2ModelError("forced validation failure")

    monkeypatch.setattr(
        model_store,
        "inspect_style_bert_model_dir",
        tamper_backup_then_fail,
    )

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="could not be restored safely",
    ):
        model_store._install_staged_tree(
            staging,
            staging_snapshot,
            target,
            root,
        )

    assert not target.exists()
    assert len(tampered_backups) == 1
    assert tampered_backups[0].exists()
    assert (tampered_backups[0] / "old.marker").read_text(encoding="utf-8") == "tampered"


def test_tree_snapshot_remains_equal_after_root_directory_rename(tmp_path):
    original = tmp_path / "original"
    nested = original / "nested"
    nested.mkdir(parents=True)
    (nested / "payload.bin").write_bytes(b"payload")
    _, before = model_store._scan_model_tree(original)
    renamed = tmp_path / "renamed"

    os.rename(original, renamed)
    _, after = model_store._scan_model_tree(renamed)

    assert after == before


def _write_file_at_tree_depth(root: Path, depth: int) -> Path:
    root.mkdir()
    parent = root
    for _index in range(1, depth):
        parent = parent / "d"
        parent.mkdir()
    payload = parent / "payload.bin"
    payload.write_bytes(b"payload")
    return payload


def test_model_tree_accepts_file_at_maximum_depth(tmp_path):
    root = tmp_path / "depth-16"
    payload = _write_file_at_tree_depth(root, model_store._MAX_MODEL_DEPTH)

    _, snapshot = model_store._scan_model_tree(root)

    assert any(entry.relative_path == payload.relative_to(root).as_posix() for entry in snapshot.entries)


def test_model_tree_rejects_file_beyond_maximum_depth(tmp_path):
    root = tmp_path / "depth-17"
    _write_file_at_tree_depth(root, model_store._MAX_MODEL_DEPTH + 1)

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="maximum depth",
    ):
        model_store._scan_model_tree(root)


def test_remove_validated_tree_wraps_nested_directory_identity_failure(
    tmp_path,
    monkeypatch,
):
    managed = tmp_path / "managed"
    tree = managed / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    _, expected_snapshot = model_store._scan_model_tree(tree)
    original_scan = model_store._scan_model_tree
    replaced: list[Path] = []

    def scan_then_replace(path):
        result = original_scan(path)
        if Path(path) == tree and not replaced:
            nested.rmdir()
            nested.mkdir()
            replaced.append(nested)
        return result

    monkeypatch.setattr(model_store, "_scan_model_tree", scan_then_replace)

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Could not remove managed model directory",
    ):
        model_store._remove_validated_tree(tree, managed, expected_snapshot)

    assert nested.exists()


def test_remove_validated_tree_wraps_root_identity_failure(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    tree = managed / "tree"
    tree.mkdir(parents=True)
    _, expected_snapshot = model_store._scan_model_tree(tree)
    original_scan = model_store._scan_model_tree
    quarantine = managed / "quarantine"
    replaced = False

    def scan_then_replace(path):
        nonlocal replaced
        result = original_scan(path)
        if Path(path) == tree and not replaced:
            os.rename(tree, quarantine)
            tree.mkdir()
            replaced = True
        return result

    monkeypatch.setattr(model_store, "_scan_model_tree", scan_then_replace)

    with pytest.raises(
        model_store.StyleBertVits2ModelError,
        match="Could not remove managed model tree",
    ):
        model_store._remove_validated_tree(tree, managed, expected_snapshot)

    assert tree.exists()
    assert quarantine.exists()


def _catalog_voice(
    tmp_path, monkeypatch, model: str = "SBV2_HoloLow", speaker: str = "MoriCalliope"
) -> str:
    """A Hololive catalog voice (EN Mori Calliope or JP Tokino Sora)."""
    assert model_store.style_bert_preset_language(model, speaker) in {"EN", "JP"}
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, model)
    imported = managed_root / model
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["data"]["spk2id"] = {speaker: 0}
    config["data"]["style2id"] = {"Neutral": 0}
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return model_store.style_bert_voice_id(model, speaker, "Neutral")


def _synthesize_languages(monkeypatch, engine, voice_id, text, ready=()):
    ensured: list[str | None] = []
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        StyleBertVits2TTS,
        "_ensure_bert_runtime",
        lambda self, language=None: ensured.append(language),
    )
    monkeypatch.setattr(
        engine_store, "style_bert_bert_assets_ready", lambda language: language in ready
    )
    speaker = model_store.parse_style_bert_voice_id(voice_id)[1]

    class FakeModel:
        def __init__(self, **_kwargs):
            self.spk2id = {speaker: 0}
            self.style2id = {"Neutral": 0}

        def infer(self, **kwargs):
            calls.append(kwargs)
            return 24000, np.array([0, 600, -600], dtype=np.int16)

    engine._tts_model_cls = FakeModel
    engine.synthesize(text, voice_id)
    return ensured, calls


@pytest.mark.parametrize(
    ("model", "speaker"),
    [("SBV2_HoloLow", "MoriCalliope"), ("SBV2_HoloJPTest2", "TokinoSora")],
)
def test_catalog_voice_speaks_chinese_when_the_player_chose_chinese(
    tmp_path, monkeypatch, model, speaker
):
    voice_id = _catalog_voice(tmp_path, monkeypatch, model, speaker)

    ensured, calls = _synthesize_languages(
        monkeypatch,
        StyleBertVits2TTS(bert_language="zh"),
        voice_id,
        "你好，今天也请多关照。",
        ready={"zh"},
    )

    assert ensured == ["zh"]
    assert calls[0]["language"] == "ZH"
    assert model_store.style_bert_voice_language(voice_id, "zh") == "zh"


def test_kana_text_is_read_as_japanese_whatever_the_setting(tmp_path, monkeypatch):
    voice_id = _catalog_voice(tmp_path, monkeypatch, "SBV2_HoloJPTest2", "TokinoSora")

    ensured, calls = _synthesize_languages(
        monkeypatch,
        StyleBertVits2TTS(bert_language="zh"),
        voice_id,
        "こんにちは、よろしくね。",
        ready={"zh", "jp"},
    )

    assert ensured == ["jp"]
    assert calls[0]["language"] == "JP"


def test_missing_bert_for_the_detected_language_falls_back_to_the_setting(
    tmp_path, monkeypatch
):
    voice_id = _catalog_voice(tmp_path, monkeypatch)

    ensured, calls = _synthesize_languages(
        monkeypatch,
        StyleBertVits2TTS(bert_language="zh"),
        voice_id,
        "see you tomorrow",
        ready={"zh"},
    )

    assert ensured == ["zh"]
    assert calls[0]["language"] == "ZH"


def test_jp_extra_model_still_speaks_japanese_for_chinese_text(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    managed_root = model_store.style_bert_models_dir()
    _write_style_model(managed_root, "jp_extra_voice")
    imported = managed_root / "jp_extra_voice"
    config = json.loads((imported / "config.json").read_text(encoding="utf-8"))
    config["version"] = "2.5.0-JP-Extra"
    config["data"]["spk2id"] = {"Speaker": 0}
    config["data"]["style2id"] = {"Neutral": 0}
    (imported / "config.json").write_text(json.dumps(config), encoding="utf-8")

    ensured, calls = _synthesize_languages(
        monkeypatch,
        StyleBertVits2TTS(bert_language="zh"),
        model_store.style_bert_voice_id("jp_extra_voice", "Speaker", "Neutral"),
        "你好",
        ready={"zh", "jp"},
    )

    assert ensured == ["jp"]
    assert calls[0]["language"] == "JP"


@pytest.mark.parametrize(
    ("text", "setting", "expected"),
    [
        ("你好", "zh", "zh"),
        ("你好", "jp", "jp"),
        ("你好", "en", "zh"),
        ("了解しました", "zh", "jp"),
        ("カタカナ", "en", "jp"),
        ("hello there", "zh", "en"),
        ("Hello 你好", "zh", "zh"),
        ("2026!", "zh", "zh"),
        ("", "jp", "jp"),
    ],
)
def test_style_bert_text_language(text, setting, expected):
    assert model_store.style_bert_text_language(text, setting) == expected
