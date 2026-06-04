from __future__ import annotations

from src.asr import hf_model_downloader as downloader


def _clear_mirror_env(monkeypatch):
    for name in ("MIO_HF_MIRROR_BASES", "HF_ENDPOINT", "HF_HUB_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def test_mirror_candidates_allow_custom_mainland_proxy(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setenv(
        "MIO_HF_MIRROR_BASES",
        "https://mirror.example/hf, https://hf-mirror.com/",
    )

    candidates = downloader._mirror_candidates()

    assert candidates[0] == "https://mirror.example/hf"
    assert candidates.count("https://hf-mirror.com") == 1
    assert "https://huggingface.co" in candidates


def test_select_mirror_uses_locale_preferred_base_when_probes_fail(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setattr(downloader, "_probe_mirror_throughput", lambda *args: None)
    monkeypatch.setattr(downloader, "_preferred_mirror_for_locale", lambda: "https://hf-mirror.com")

    selected = downloader._select_mirror("model/repo", "config.json")

    assert selected == "https://hf-mirror.com"


def test_mirror_fallback_order_keeps_selected_then_locale_then_official(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setenv("MIO_HF_MIRROR_BASES", "https://mirror.example")
    monkeypatch.setattr(downloader, "_preferred_mirror_for_locale", lambda: "https://hf-mirror.com")

    order = downloader._mirror_fallback_order("https://mirror.example")

    assert order[:3] == [
        "https://mirror.example",
        "https://hf-mirror.com",
        "https://huggingface.co",
    ]


def test_asr_model_file_lists_use_real_weight_names_not_default_model_bin():
    assert downloader._HF_MODEL_FILES["iic/Whisper-large-v3-turbo"] == [
        "configuration.json",
        "large-v3-turbo.pt",
    ]
    assert "model.bin" not in downloader._HF_MODEL_FILES["iic/SenseVoiceSmall"]
