from __future__ import annotations

import sys
import types

from src.asr import model_manager
from src.asr.model_registry import ASRRuntimeSpec


def _install_fake_modelscope(monkeypatch, snapshot_download):
    class FakeProgressCallback:
        pass

    modelscope = types.ModuleType("modelscope")
    hub = types.ModuleType("modelscope.hub")
    callback_mod = types.ModuleType("modelscope.hub.callback")
    callback_mod.ProgressCallback = FakeProgressCallback
    snapshot_mod = types.ModuleType("modelscope.hub.snapshot_download")
    snapshot_mod.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "modelscope", modelscope)
    monkeypatch.setitem(sys.modules, "modelscope.hub", hub)
    monkeypatch.setitem(sys.modules, "modelscope.hub.callback", callback_mod)
    monkeypatch.setitem(sys.modules, "modelscope.hub.snapshot_download", snapshot_mod)


def _whisper_spec() -> ASRRuntimeSpec:
    return ASRRuntimeSpec(
        engine="whisper-large-v3-turbo",
        label="Whisper Large V3 Turbo",
        config_key="whisper",
        model_id="iic/Whisper-large-v3-turbo",
        model_revision="master",
        required_files=("large-v3-turbo.pt",),
    )


def test_download_model_to_retries_transient_modelscope_failure(monkeypatch, tmp_path):
    attempts: list[int] = []
    events: list[dict[str, object]] = []
    spec = _whisper_spec()

    def fake_snapshot_download(
        model_id,
        *,
        revision,
        cache_dir,
        local_dir,
        allow_file_pattern,
        enable_file_lock,
        max_workers,
        progress_callbacks,
    ):
        del model_id, revision, cache_dir, enable_file_lock, progress_callbacks
        assert allow_file_pattern == ["configuration.json", "config.yaml", "large-v3-turbo.pt"]
        assert max_workers == 1
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("connection reset")
        target = tmp_path / "downloaded"
        assert str(target) == local_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / "configuration.json").write_text("{}", encoding="utf-8")
        (target / "large-v3-turbo.pt").write_bytes(b"weights")
        return str(target)

    _install_fake_modelscope(monkeypatch, fake_snapshot_download)
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda _model_id, _revision: {"resolved_revision": "master", "total_size": 123},
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")

    result = model_manager.download_model_to(
        spec,
        tmp_path / "downloaded",
        progress_callback=lambda event: events.append(event),
    )

    assert result == tmp_path / "downloaded"
    assert len(attempts) == 2
    assert any(event["stage"] == "download_retry" for event in events)


def test_download_model_to_keeps_incomplete_target_for_resume(monkeypatch, tmp_path):
    spec = _whisper_spec()
    target = tmp_path / "downloaded"
    target.mkdir()
    partial_file = target / "large-v3-turbo.pt.incomplete"
    partial_file.write_bytes(b"partial")

    def fake_snapshot_download(
        model_id,
        *,
        revision,
        cache_dir,
        local_dir,
        allow_file_pattern,
        enable_file_lock,
        max_workers,
        progress_callbacks,
    ):
        del (
            model_id,
            revision,
            cache_dir,
            allow_file_pattern,
            enable_file_lock,
            max_workers,
            progress_callbacks,
        )
        assert str(target) == local_dir
        assert partial_file.exists()
        (target / "configuration.json").write_text("{}", encoding="utf-8")
        (target / "large-v3-turbo.pt").write_bytes(b"weights")
        return str(target)

    _install_fake_modelscope(monkeypatch, fake_snapshot_download)
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda _model_id, _revision: {"resolved_revision": "master", "total_size": 123},
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")

    result = model_manager.download_model_to(spec, target)

    assert result == target
    assert partial_file.exists()


def test_cache_dir_prefers_generic_modelscope_env(monkeypatch, tmp_path):
    legacy_cache = tmp_path / "legacy"
    generic_cache = tmp_path / "generic"
    monkeypatch.setenv("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR", str(legacy_cache))
    monkeypatch.setenv("MIO_TRANSLATOR_MODELSCOPE_CACHE_DIR", str(generic_cache))

    assert model_manager.cache_dir() == generic_cache
