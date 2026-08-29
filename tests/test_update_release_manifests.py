from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.updater.manifest_signature import (
    ManifestSignatureError,
    public_key_from_seed,
    verify_manifest_signature,
)
from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    verify_installer_metadata_signature,
)
from tools import update_release_manifests as release_tool


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def _release_inputs(tmp_path: Path):
    root = tmp_path / "release-root"
    update_path = root / "mio_update.json"
    installer_manifest_path = root / "docs" / "installer_manifest.json"
    update_raw = _write_json(
        update_path,
        {
            "version": "v1.0.0",
            "notes": "Human-authored update notes",
            "notes_i18n": {"en": "English update notes", "ja": "Japanese notes"},
            "published_at": "2025-01-01T00:00:00+09:00",
        },
    )
    installer_raw = _write_json(
        installer_manifest_path,
        {
            "version": "v1.0.0",
            "notes": "Human-authored installer notes",
            "notes_i18n": {"en": "English installer notes"},
            "model_runtime_dir": r"%LOCALAPPDATA%\Custom\SenseVoice",
            "published_at": "2025-01-01T00:00:00+09:00",
        },
    )
    installer = root / "dist" / "setup.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"signed installer bytes")
    return root, installer, update_path, installer_manifest_path, update_raw, installer_raw


def test_release_manifests_preserve_notes_use_jst_and_verify_before_publish(tmp_path):
    root, installer, update_path, installer_manifest_path, _update_raw, _installer_raw = (
        _release_inputs(tmp_path)
    )
    seed = "11" * 32
    public_key = public_key_from_seed(seed)
    now = dt.datetime(2026, 7, 10, 1, 2, 3, tzinfo=dt.timezone.utc)

    result = release_tool.update_release_manifests(
        installer,
        seed,
        root=root,
        version="v9.8.7",
        now=now,
        expected_public_key=public_key,
        key_id="test-release-key",
        installer_key_id="test-installer-key",
        trusted_installer_public_keys=(("test-installer-key", public_key),),
    )

    update_manifest = json.loads(update_path.read_text(encoding="utf-8"))
    installer_manifest = json.loads(
        installer_manifest_path.read_text(encoding="utf-8")
    )
    assert update_manifest["notes"] == "Human-authored update notes"
    assert update_manifest["notes_i18n"]["ja"] == "Japanese notes"
    assert installer_manifest["notes"] == "Human-authored installer notes"
    assert installer_manifest["model_runtime_dir"] == r"%LOCALAPPDATA%\Custom\SenseVoice"
    assert update_manifest["published_at"] == "2026-07-10T10:02:03+09:00"
    assert installer_manifest["published_at"] == "2026-07-10T10:02:03+09:00"
    assert result.sha256 == hashlib.sha256(b"signed installer bytes").hexdigest()
    assert result.size_bytes == len(b"signed installer bytes")
    assert result.installer_signature_key_id == "test-installer-key"

    assert verify_manifest_signature(
        update_manifest,
        public_key,
        required=True,
        expected_key_id="test-release-key",
    )
    for manifest in (update_manifest, installer_manifest):
        assert verify_installer_metadata_signature(
            sha256=manifest["sha256"],
            size_bytes=manifest["size_bytes"],
            signature=manifest[INSTALLER_SIGNATURE_FIELD],
            signature_algorithm=manifest[INSTALLER_SIGNATURE_ALGORITHM_FIELD],
            signature_key_id=manifest[INSTALLER_SIGNATURE_KEY_ID_FIELD],
            trusted_public_keys=(("test-installer-key", public_key),),
        ) == "test-installer-key"
    assert verify_manifest_signature(
        installer_manifest,
        public_key,
        required=True,
        expected_key_id="test-release-key",
    )

    update_text = update_path.read_text(encoding="utf-8")
    assert update_text.index('"sha256"') < update_text.index('"notes"')
    assert update_text.index('"notes"') < update_text.index('"notes_i18n"')
    assert not list(root.rglob("*.tmp"))
    assert not list(root.rglob("*.bak"))


def test_signature_verification_failure_leaves_both_manifests_unchanged(
    tmp_path,
    monkeypatch,
):
    root, installer, update_path, installer_manifest_path, update_raw, installer_raw = (
        _release_inputs(tmp_path)
    )
    seed = "22" * 32
    public_key = public_key_from_seed(seed)
    calls = 0

    def verify_staged(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ManifestSignatureError("staged signature rejected")
        return True

    monkeypatch.setattr(release_tool, "verify_manifest_signature", verify_staged)

    with pytest.raises(ManifestSignatureError, match="staged signature rejected"):
        release_tool.update_release_manifests(
            installer,
            seed,
            root=root,
            version="v2.0.0",
            expected_public_key=public_key,
            key_id="test-release-key",
            installer_key_id="test-installer-key",
            trusted_installer_public_keys=(("test-installer-key", public_key),),
        )

    assert calls == 2
    assert update_path.read_bytes() == update_raw
    assert installer_manifest_path.read_bytes() == installer_raw
    assert not list(root.rglob("*.tmp"))
    assert not list(root.rglob("*.bak"))


def test_second_atomic_replace_failure_rolls_back_first_manifest(tmp_path, monkeypatch):
    root, installer, update_path, installer_manifest_path, update_raw, installer_raw = (
        _release_inputs(tmp_path)
    )
    seed = "33" * 32
    public_key = public_key_from_seed(seed)
    real_replace = os.replace

    def fail_installer_manifest_replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path == installer_manifest_path and source_path.suffix == ".tmp":
            raise OSError("simulated second replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(release_tool.os, "replace", fail_installer_manifest_replace)

    with pytest.raises(OSError, match="simulated second replace failure"):
        release_tool.update_release_manifests(
            installer,
            seed,
            root=root,
            version="v2.0.0",
            expected_public_key=public_key,
            key_id="test-release-key",
            installer_key_id="test-installer-key",
            trusted_installer_public_keys=(("test-installer-key", public_key),),
        )

    assert update_path.read_bytes() == update_raw
    assert installer_manifest_path.read_bytes() == installer_raw
    assert not list(root.rglob("*.tmp"))
    assert not list(root.rglob("*.bak"))


def test_windows_transient_replace_error_is_retried(monkeypatch, tmp_path):
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    real_replace = os.replace
    attempts = 0
    sleeps: list[float] = []

    def flaky_replace(current_source, current_destination):
        # release_tool.os is the real os module, so this patch is process-wide.
        # Background threads left running by other tests also write files
        # atomically, and counting their replaces made this test flaky.
        if os.fspath(current_source) != os.fspath(source):
            return real_replace(current_source, current_destination)
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = OSError("transient sharing violation")
            error.winerror = 5
            raise error
        return real_replace(current_source, current_destination)

    monkeypatch.setattr(release_tool.os, "replace", flaky_replace)
    monkeypatch.setattr(release_tool.time, "sleep", sleeps.append)

    release_tool._replace_with_transient_retry(source, destination)

    assert attempts == 3
    assert sleeps == [0.02, 0.05]
    assert destination.read_bytes() == b"new"


def test_release_seed_must_match_installer_public_key_configuration(tmp_path):
    root, installer, update_path, installer_manifest_path, update_raw, installer_raw = (
        _release_inputs(tmp_path)
    )
    seed = "44" * 32
    public_key = public_key_from_seed(seed)

    with pytest.raises(
        release_tool.ManifestGenerationError,
        match="configured installer public key",
    ):
        release_tool.update_release_manifests(
            installer,
            seed,
            root=root,
            version="v2.0.0",
            expected_public_key=public_key,
            key_id="test-release-key",
            installer_key_id="test-installer-key",
            trusted_installer_public_keys=(
                ("test-installer-key", public_key_from_seed("45" * 32)),
            ),
        )

    assert update_path.read_bytes() == update_raw
    assert installer_manifest_path.read_bytes() == installer_raw
