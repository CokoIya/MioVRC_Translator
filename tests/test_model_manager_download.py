from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import types

import pytest

from src.asr import model_manager
from src.asr.model_registry import (
    ASRRuntimeSpec,
    SENSEVOICE_DEFAULT_MODEL,
    SENSEVOICE_DEFAULT_REVISION,
)


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
        label="Whisper Small",
        config_key="whisper",
        model_id="iic/speech_whisper-small_asr_english",
        model_revision="master",
        required_files=("small.en.pb",),
    )


def _configure_generic_download(monkeypatch, tmp_path, snapshot_download):
    _install_fake_modelscope(monkeypatch, snapshot_download)
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda _model_id, _revision: {
            "resolved_revision": "master",
            "total_size": 123,
        },
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")


def _write_generic_model(path: pathlib.Path, *, weights: bytes = b"weights") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "configuration.json").write_text("{}", encoding="utf-8")
    (path / "small.en.pb").write_bytes(weights)


def _create_directory_symlink(link: pathlib.Path, destination: pathlib.Path) -> None:
    try:
        link.symlink_to(destination, target_is_directory=True)
        return
    except (NotImplementedError, OSError) as symlink_error:
        if sys.platform == "win32":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(destination)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return
        pytest.skip(f"Directory links are unavailable: {symlink_error}")


def test_download_model_to_retries_transient_modelscope_failure(monkeypatch, tmp_path):
    attempts: list[int] = []
    staging_paths: list[pathlib.Path] = []
    events: list[dict[str, object]] = []
    spec = _whisper_spec()
    target = tmp_path / "downloaded"

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
        assert allow_file_pattern == [
            "configuration.json",
            "config.yaml",
            "small.en.pb",
        ]
        assert max_workers == 1
        attempts.append(1)
        staging = pathlib.Path(local_dir)
        staging_paths.append(staging)
        assert staging.parent == target.parent
        assert staging != target
        assert staging.name.startswith(".mio-staging-")
        if len(attempts) == 1:
            raise ConnectionError("connection reset")
        (staging / "configuration.json").write_text("{}", encoding="utf-8")
        (staging / "small.en.pb").write_bytes(b"weights")
        return str(staging)

    _install_fake_modelscope(monkeypatch, fake_snapshot_download)
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda _model_id, _revision: {"resolved_revision": "master", "total_size": 123},
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")

    result = model_manager.download_model_to(
        spec,
        target,
        progress_callback=lambda event: events.append(event),
    )

    assert result == target
    assert len(attempts) == 2
    assert staging_paths[0] == staging_paths[1]
    assert not staging_paths[0].exists()
    metadata = json.loads((target / ".mio-model.json").read_text(encoding="utf-8"))
    assert metadata["resolved_revision"] == "master"
    retry = next(event for event in events if event["stage"] == "download_retry")
    assert retry["attempt"] == 2
    assert retry["max_attempts"] == model_manager._DOWNLOAD_ATTEMPTS


def test_download_model_to_preserves_existing_target_until_staged_install(
    monkeypatch, tmp_path
):
    spec = _whisper_spec()
    target = tmp_path / "downloaded"
    target.mkdir()
    partial_file = target / "small.en.pb.incomplete"
    partial_file.write_bytes(b"partial")
    observed_existing_target: list[bool] = []

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
        staging = pathlib.Path(local_dir)
        assert staging != target
        observed_existing_target.append(partial_file.read_bytes() == b"partial")
        assert not (staging / partial_file.name).exists()
        (staging / "configuration.json").write_text("{}", encoding="utf-8")
        (staging / "small.en.pb").write_bytes(b"weights")
        return str(staging)

    _install_fake_modelscope(monkeypatch, fake_snapshot_download)
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda _model_id, _revision: {"resolved_revision": "master", "total_size": 123},
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")

    result = model_manager.download_model_to(spec, target)

    assert result == target
    assert observed_existing_target == [True]
    assert not partial_file.exists()
    assert (target / "small.en.pb").read_bytes() == b"weights"


def test_existing_valid_target_survives_failed_generic_download(monkeypatch, tmp_path):
    spec = _whisper_spec()
    target = tmp_path / "downloaded"
    _write_generic_model(target, weights=b"original weights")
    marker = target / "keep.txt"
    marker.write_text("original installation", encoding="utf-8")
    staging_paths: list[pathlib.Path] = []

    def fake_snapshot_download(_model_id, **kwargs):
        staging = pathlib.Path(kwargs["local_dir"])
        staging_paths.append(staging)
        (staging / "partial.tmp").write_bytes(b"partial")
        raise ConnectionError("offline")

    _configure_generic_download(monkeypatch, tmp_path, fake_snapshot_download)
    monkeypatch.setattr(model_manager, "_DOWNLOAD_ATTEMPTS", 1)

    with pytest.raises(RuntimeError, match="download failed after 1 attempts"):
        model_manager.download_model_to(spec, target, force=True)

    assert (target / "small.en.pb").read_bytes() == b"original weights"
    assert marker.read_text(encoding="utf-8") == "original installation"
    assert staging_paths and not staging_paths[0].exists()
    assert not list(tmp_path.glob(".mio-staging-*"))


def test_failed_staging_verification_is_cleaned(monkeypatch, tmp_path):
    spec = _whisper_spec()
    target = tmp_path / "downloaded"
    staging_paths: list[pathlib.Path] = []

    def fake_snapshot_download(_model_id, **kwargs):
        staging = pathlib.Path(kwargs["local_dir"])
        staging_paths.append(staging)
        (staging / "configuration.json").write_text("{}", encoding="utf-8")
        return str(staging)

    _configure_generic_download(monkeypatch, tmp_path, fake_snapshot_download)

    with pytest.raises(RuntimeError, match="staging directory is incomplete"):
        model_manager.download_model_to(spec, target)

    assert not target.exists()
    assert staging_paths and not staging_paths[0].exists()
    assert not list(tmp_path.glob(".mio-staging-*"))


def test_failed_atomic_replacement_restores_original_target(monkeypatch, tmp_path):
    spec = _whisper_spec()
    target = tmp_path / "downloaded"
    target.mkdir()
    marker = target / "original.txt"
    marker.write_text("preserve me", encoding="utf-8")

    def fake_snapshot_download(_model_id, **kwargs):
        staging = pathlib.Path(kwargs["local_dir"])
        _write_generic_model(staging, weights=b"new weights")
        return str(staging)

    _configure_generic_download(monkeypatch, tmp_path, fake_snapshot_download)
    real_rename = model_manager.os.rename
    failed = False

    def fail_staging_install(source, destination):
        nonlocal failed
        source_path = pathlib.Path(source)
        destination_path = pathlib.Path(destination)
        if (
            not failed
            and source_path.name.startswith(".mio-staging-")
            and destination_path == target
        ):
            failed = True
            raise OSError("simulated atomic install failure")
        return real_rename(source, destination)

    monkeypatch.setattr(model_manager.os, "rename", fail_staging_install)

    with pytest.raises(RuntimeError, match="original installation was restored"):
        model_manager.download_model_to(spec, target)

    assert failed
    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert not (target / "small.en.pb").exists()
    assert not list(tmp_path.glob(".mio-staging-*"))
    assert not list(tmp_path.glob(".mio-backup-*"))


def test_target_symlink_is_rejected_without_touching_destination(tmp_path):
    spec = _whisper_spec()
    destination = tmp_path / "outside"
    _write_generic_model(destination, weights=b"outside weights")
    marker = destination / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    target = tmp_path / "downloaded"
    _create_directory_symlink(target, destination)

    with pytest.raises(RuntimeError, match="Unsafe ASR model target directory"):
        model_manager.download_model_to(spec, target, force=True)

    assert marker.read_text(encoding="utf-8") == "untouched"
    assert (destination / "small.en.pb").read_bytes() == b"outside weights"
    assert target.is_symlink() or model_manager._is_reparse_point(target.lstat())


def test_ancestor_symlink_is_rejected_without_creating_destination(tmp_path):
    spec = _whisper_spec()
    destination = tmp_path / "outside"
    destination.mkdir()
    linked_parent = tmp_path / "linked"
    _create_directory_symlink(linked_parent, destination)

    with pytest.raises(RuntimeError, match="Unsafe ASR model directory"):
        model_manager.download_model_to(spec, linked_parent / "downloaded")

    assert not (destination / "downloaded").exists()


def test_non_directory_target_is_rejected(tmp_path):
    target = tmp_path / "downloaded"
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unsafe ASR model target directory"):
        model_manager.download_model_to(_whisper_spec(), target)

    assert target.read_text(encoding="utf-8") == "not a directory"


def test_modelscope_cache_symlink_is_rejected(monkeypatch, tmp_path):
    destination = tmp_path / "outside-cache"
    destination.mkdir()
    marker = destination / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    cache_link = tmp_path / "cache"
    _create_directory_symlink(cache_link, destination)

    _install_fake_modelscope(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("download must not start with an unsafe cache")
        ),
    )
    monkeypatch.setattr(model_manager, "cache_dir", lambda: cache_link)

    with pytest.raises(RuntimeError, match="Unsafe ASR model directory"):
        model_manager.download_model_to(_whisper_spec(), tmp_path / "downloaded")

    assert marker.read_text(encoding="utf-8") == "untouched"


def test_cache_dir_prefers_generic_modelscope_env(monkeypatch, tmp_path):
    legacy_cache = tmp_path / "legacy"
    generic_cache = tmp_path / "generic"
    monkeypatch.setenv("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR", str(legacy_cache))
    monkeypatch.setenv("MIO_TRANSLATOR_MODELSCOPE_CACHE_DIR", str(generic_cache))

    assert model_manager.cache_dir() == generic_cache



def _pinned_sensevoice_spec(contents: dict[str, bytes]) -> ASRRuntimeSpec:
    return ASRRuntimeSpec(
        engine="sensevoice-small",
        label="SenseVoice Small",
        config_key="sensevoice",
        model_id=SENSEVOICE_DEFAULT_MODEL,
        model_revision=SENSEVOICE_DEFAULT_REVISION,
        bundled_dir_names=("sensevoice-small",),
        required_files=tuple(contents),
        required_file_sha256=tuple(
            (name, hashlib.sha256(content).hexdigest())
            for name, content in contents.items()
        ),
    )


def _sensevoice_test_contents() -> dict[str, bytes]:
    return {
        "am.mvn": b"cmvn",
        "chn_jpn_yue_eng_ko_spectok.bpe.model": b"tokenizer",
        "config.yaml": b"model: SenseVoiceSmall\n",
        "configuration.json": json.dumps(
            {"model": {"type": "funasr"}, "file_path_metas": {"config": "config.yaml"}}
        ).encode(),
        "model.pt": b"weights",
    }


def test_pinned_sensevoice_update_check_does_not_query_mutable_network_state(
    monkeypatch,
):
    spec = _pinned_sensevoice_spec(_sensevoice_test_contents())
    monkeypatch.setattr(
        model_manager,
        "get_local_model_status",
        lambda _spec: {
            "installed": True,
            "local_revision": "legacy-local-metadata",
        },
    )
    monkeypatch.setattr(
        model_manager,
        "_fetch_remote_snapshot_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pinned revisions must not query mutable network state")
        ),
    )

    status = model_manager.check_model_update(spec)

    assert status["remote_revision"] == SENSEVOICE_DEFAULT_REVISION
    assert status["local_revision_known"] is True
    assert status["update_available"] is False


def test_pinned_sensevoice_downloads_each_hashed_file_without_snapshot(
    monkeypatch, tmp_path
):
    contents = _sensevoice_test_contents()
    spec = _pinned_sensevoice_spec(contents)
    source_dir = tmp_path / "modelscope-files"
    source_dir.mkdir()
    calls: list[tuple[str, str, str]] = []

    def fake_model_file_download(model_id, filename, *, revision, cache_dir):
        calls.append((model_id, filename, revision))
        assert cache_dir == str(tmp_path / "cache")
        source = source_dir / filename
        source.write_bytes(contents[filename])
        return str(source)

    file_mod = types.ModuleType("modelscope.hub.file_download")
    file_mod.model_file_download = fake_model_file_download
    monkeypatch.setitem(sys.modules, "modelscope.hub.file_download", file_mod)
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(
        model_manager,
        "_fetch_required_snapshot_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot metadata must not be queried")
        ),
    )
    monkeypatch.setattr(
        model_manager,
        "_download_verified_http_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("HTTPS fallback should not be needed")
        ),
    )

    target = model_manager.download_model_to(spec, tmp_path / "downloaded")

    assert [filename for _, filename, _ in calls] == list(contents)
    assert all(model_id == SENSEVOICE_DEFAULT_MODEL for model_id, _, _ in calls)
    assert all(revision == SENSEVOICE_DEFAULT_REVISION for _, _, revision in calls)
    assert not list(target.glob("*.part"))
    assert model_manager.verify_model_integrity(target, spec)


def test_pinned_download_hash_mismatch_never_replaces_existing_file(
    monkeypatch, tmp_path
):
    contents = _sensevoice_test_contents()
    spec = _pinned_sensevoice_spec(contents)
    target = tmp_path / "downloaded"
    target.mkdir()
    old_bytes = b"existing unverified file"
    (target / "am.mvn").write_bytes(old_bytes)
    bad_source = tmp_path / "bad-am.mvn"
    bad_source.write_bytes(b"attacker bytes")

    file_mod = types.ModuleType("modelscope.hub.file_download")
    file_mod.model_file_download = lambda *_args, **_kwargs: str(bad_source)
    monkeypatch.setitem(sys.modules, "modelscope.hub.file_download", file_mod)
    monkeypatch.setattr(model_manager, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(model_manager, "_DOWNLOAD_ATTEMPTS", 1)
    monkeypatch.setattr(
        model_manager,
        "_download_verified_http_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    import pytest

    with pytest.raises(RuntimeError, match="am.mvn"):
        model_manager.download_model_to(spec, target)

    assert (target / "am.mvn").read_bytes() == old_bytes
    assert not (target / "am.mvn.part").exists()


def test_https_fallback_uses_pinned_revision_and_verifies_before_replace(
    monkeypatch, tmp_path
):
    content = b"verified content"
    spec = _pinned_sensevoice_spec({"model.pt": content})
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {}
        url = "https://www.modelscope.cn/model.pt"

        def close(self):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, *, chunk_size):
            captured["chunk_size"] = chunk_size
            yield content[:5]
            yield content[5:]

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        model_manager,
        "_is_safe_model_download_url",
        lambda url: str(url).startswith("https://"),
    )
    root = tmp_path / "target"
    root.mkdir()
    tracker = model_manager._AggregateDownloadProgress(None, None)
    model_manager._download_verified_http_file(
        spec,
        "model.pt",
        target_path=root / "model.pt",
        root=root,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        tracker=tracker,
    )

    assert SENSEVOICE_DEFAULT_REVISION in str(captured["url"])
    assert str(captured["url"]).endswith("/model.pt")
    assert captured["kwargs"]["allow_redirects"] is False
    assert (root / "model.pt").read_bytes() == content
    assert not (root / "model.pt.part").exists()
