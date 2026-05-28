"""Tests for custom Style-Bert-VITS2 voice import and playback helpers."""
from __future__ import annotations

import builtins
import json
import importlib
import sys
from types import SimpleNamespace
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
                "data": {
                    "spk2id": {"Alice": 0},
                    "style2id": {"Neutral": 0, "Happy": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    (model_dir / "style_vectors.npy").write_bytes(b"style")
    (model_dir / "voice.safetensors").write_bytes(b"weights")
    return model_dir


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
    assert voices[0].language == "en"


def test_style_bert_synthesis_uses_catalog_voice_language(tmp_path, monkeypatch):
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
    assert ensured_languages == ["en"]
    assert infer_calls[0]["language"] == "EN"


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


def test_style_bert_engine_requires_shared_runtime_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")
    _write_style_model(model_store.style_bert_models_dir(), "sample_voice")

    engine = StyleBertVits2TTS()
    engine._tts_model_cls = object()

    monkeypatch.setattr(engine_store, "style_bert_bert_assets_ready", lambda _lang: False)
    assert engine.is_available() is False

    monkeypatch.setattr(engine_store, "style_bert_bert_assets_ready", lambda _lang: True)
    assert engine.is_available() is True


def test_transformers_export_probe_reports_missing_style_bert_symbols():
    module = SimpleNamespace(AutoTokenizer=object())

    missing = engine_store._missing_transformers_bert_exports(module)

    assert "AutoModelForMaskedLM" in missing
    assert "AutoTokenizer" not in missing


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
    assert g2p_en.G2p()("hello") == ["HH", "AH0", "L", "OW1"]
