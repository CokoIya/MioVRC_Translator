"""Tests for Hololive Style-Bert-VITS2 download metadata helpers."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.tts import style_bert_vits2_downloader as downloader
from src.tts import style_bert_vits2_models as model_store


def test_hololive_bundle_metadata_matches_catalog():
    bundle = model_store.hololive_model_bundle("SBV2_HoloLow")

    assert bundle is not None
    assert bundle.files == (
        "SBV2_HoloLow.safetensors",
        "config.json",
        "style_vectors.npy",
    )


def test_hololive_bundle_complete_checks_managed_model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")

    target = model_store.style_bert_models_dir() / "SBV2_HoloLow"
    target.mkdir(parents=True, exist_ok=True)
    (target / "SBV2_HoloLow.safetensors").write_bytes(b"weights")
    (target / "config.json").write_text(
        json.dumps({"data": {"spk2id": {"MoriCalliope": 0}, "style2id": {"Neutral": 0}}}),
        encoding="utf-8",
    )
    np.save(target / "style_vectors.npy", np.zeros((1, 1), dtype=np.float32))

    assert downloader.hololive_bundle_is_complete("SBV2_HoloLow") is True
    assert downloader.hololive_bundle_is_complete("missing-pack") is False


def test_hololive_bundle_complete_rejects_partial_or_invalid_files(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "writable_app_dir", lambda: tmp_path / "app")

    target = model_store.style_bert_models_dir() / "SBV2_HoloLow"
    target.mkdir(parents=True, exist_ok=True)
    (target / "SBV2_HoloLow.safetensors").write_bytes(b"weights")
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "style_vectors.npy").write_bytes(b"not-numpy")

    assert downloader.hololive_bundle_is_complete("SBV2_HoloLow") is False


def test_hololive_downloader_prefers_mainland_mirror_when_probes_fail(monkeypatch):
    for name in ("MIO_HF_MIRROR_BASES", "HF_ENDPOINT", "HF_HUB_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(downloader, "_probe_bundle_throughput", lambda *args: None)
    monkeypatch.setattr(downloader, "_preferred_base_for_locale", lambda: "https://hf-mirror.com")

    selected = downloader._select_base("SBV2_HoloLow", "config.json")

    assert selected == "https://hf-mirror.com"
