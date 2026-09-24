from __future__ import annotations

import logging

import pytest

from src.asr import sensevoice_asr


@pytest.mark.parametrize(
    ("provider_module", "provider_type"),
    (
        pytest.param(
            sensevoice_asr,
            sensevoice_asr.SenseVoiceASR,
            id="sensevoice",
        ),
    ),
)
def test_existing_local_model_is_resolved_once_and_load_stages_are_timed(
    monkeypatch,
    tmp_path,
    caplog,
    provider_module,
    provider_type,
):
    resolved_specs: list[object] = []
    constructed: dict[str, object] = {}

    def fake_auto_model(**kwargs):
        constructed.update(kwargs)
        return object()

    runtime_symbols = (
        (fake_auto_model, lambda text: text)
        if provider_module is sensevoice_asr
        else fake_auto_model
    )
    monkeypatch.setattr(
        provider_module,
        "_load_runtime_symbols",
        lambda: runtime_symbols,
    )

    def resolve_existing(spec):
        resolved_specs.append(spec)
        return tmp_path

    monkeypatch.setattr(provider_module, "existing_model_path", resolve_existing)
    monkeypatch.setattr(
        provider_module,
        "download_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an existing model must not be downloaded")
        ),
    )

    with caplog.at_level(logging.INFO, logger=provider_module.__name__):
        provider_type().load()

    assert len(resolved_specs) == 1
    assert constructed["model"] == str(tmp_path)
    messages = [record.getMessage() for record in caplog.records]
    for stage in (
        "runtime_import",
        "model_resolution_integrity",
        "model_construction",
    ):
        assert any(
            f"stage={stage}" in message and "elapsed_ms=" in message
            for message in messages
        )


@pytest.mark.parametrize(
    ("provider_module", "provider_type"),
    (
        pytest.param(
            sensevoice_asr,
            sensevoice_asr.SenseVoiceASR,
            id="sensevoice",
        ),
    ),
)
def test_missing_local_model_downloads_before_single_post_download_resolution(
    monkeypatch,
    tmp_path,
    provider_module,
    provider_type,
):
    events: list[str] = []
    resolutions = iter((None, tmp_path))

    def fake_auto_model(**_kwargs):
        events.append("construct")
        return object()

    runtime_symbols = (
        (fake_auto_model, lambda text: text)
        if provider_module is sensevoice_asr
        else fake_auto_model
    )
    monkeypatch.setattr(
        provider_module,
        "_load_runtime_symbols",
        lambda: runtime_symbols,
    )

    def resolve_model(_spec):
        events.append("resolve")
        return next(resolutions)

    def download_missing(_spec, **kwargs):
        assert kwargs["known_missing"] is True
        events.append("download")
        return tmp_path

    monkeypatch.setattr(provider_module, "existing_model_path", resolve_model)
    monkeypatch.setattr(provider_module, "download_model", download_missing)

    provider_type().load()

    assert events == ["resolve", "download", "construct"]
