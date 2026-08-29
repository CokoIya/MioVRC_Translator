from __future__ import annotations

import pytest

from src.asr.errors import ASRUnsupportedRuntimeError
from src.asr.factory import _create_ready_sensevoice_fallback
from src.asr.text_corrections import LayeredASRCorrector






def test_automatic_local_fallback_never_downloads_missing_model(monkeypatch):
    config = {"asr": {"engine": "qwen3-asr"}}
    monkeypatch.setattr(
        "src.asr.model_manager.existing_model_path",
        lambda _spec: None,
    )
    monkeypatch.setattr(
        "src.asr.factory._create_sensevoice",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing fallback model must not be constructed")
        ),
    )

    with pytest.raises(ASRUnsupportedRuntimeError, match="not installed"):
        _create_ready_sensevoice_fallback(
            config,
            LayeredASRCorrector(config),
        )
