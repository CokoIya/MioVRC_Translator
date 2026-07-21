from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    sign_installer_metadata,
)
from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    public_key_from_seed,
    sign_manifest,
)
from tools.release import generate_release_checksums as release_checksums


TEST_VERSION = "v9.8.7.6"
TEST_KEY_ID = "test-release-key"
TEST_INSTALLER_KEY_ID = "test-installer-key"
TEST_SEED = "17" * 32
HISTORICAL_INSTALLER_KEY_ID = "historical-installer-key"
HISTORICAL_INSTALLER_SEED = "19" * 32


def _key_catalog_path(installer_manifest: Path) -> Path:
    return installer_manifest.parent / "release_signing_keys.json"


def _write_key_catalog(path: Path) -> None:
    active_public_key = public_key_from_seed(TEST_SEED)
    retired_public_key = public_key_from_seed(HISTORICAL_INSTALLER_SEED)
    payload = {
        "schema_version": 1,
        "active_release": TEST_VERSION,
        "active_manifest_key_id": TEST_KEY_ID,
        "active_installer_key_id": TEST_INSTALLER_KEY_ID,
        "keys": [
            {
                "generation": 1,
                "status": "retired-untrusted",
                "manifest_key_id": "historical-manifest-key",
                "installer_key_id": HISTORICAL_INSTALLER_KEY_ID,
                "public_key_hex": retired_public_key,
                "public_key_sha256": hashlib.sha256(
                    bytes.fromhex(retired_public_key)
                ).hexdigest(),
                "trusted_by_active_runtime": False,
            },
            {
                "generation": 2,
                "status": "active",
                "manifest_key_id": TEST_KEY_ID,
                "installer_key_id": TEST_INSTALLER_KEY_ID,
                "public_key_hex": active_public_key,
                "public_key_sha256": hashlib.sha256(
                    bytes.fromhex(active_public_key)
                ).hexdigest(),
                "trusted_by_active_runtime": True,
            },
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_signed_manifest(
    path: Path,
    *,
    installer: Path,
    installer_bytes: bytes,
    seed: str,
    key_id: str,
) -> None:
    manifest: dict[str, object] = {
        "version": TEST_VERSION,
        "installer_name": installer.name,
        "size_bytes": len(installer_bytes),
        "sha256": hashlib.sha256(installer_bytes).hexdigest(),
        INSTALLER_SIGNATURE_ALGORITHM_FIELD: INSTALLER_SIGNATURE_ALGORITHM,
        INSTALLER_SIGNATURE_KEY_ID_FIELD: TEST_INSTALLER_KEY_ID,
        INSTALLER_SIGNATURE_FIELD: sign_installer_metadata(
            seed,
            sha256=hashlib.sha256(installer_bytes).hexdigest(),
            size_bytes=len(installer_bytes),
        ),
        SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
        SIGNATURE_KEY_ID_FIELD: key_id,
    }
    manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _release_inputs(tmp_path: Path):
    root = tmp_path / "release"
    dist = root / "dist"
    dist.mkdir(parents=True)
    installer = dist / f"MioTranslator-Setup-{TEST_VERSION}.exe"
    installer_bytes = b"final installer payload\x00\x01"
    installer.write_bytes(installer_bytes)
    update_manifest = root / "mio_update.json"
    installer_manifest = root / "docs" / "installer_manifest.json"
    for manifest in (update_manifest, installer_manifest):
        _write_signed_manifest(
            manifest,
            installer=installer,
            installer_bytes=installer_bytes,
            seed=TEST_SEED,
            key_id=TEST_KEY_ID,
        )
    _write_key_catalog(_key_catalog_path(installer_manifest))
    return root, dist, installer, update_manifest, installer_manifest


def _generate(tmp_path: Path):
    root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    result = release_checksums.generate_release_checksum_artifacts(
        installer,
        update_manifest,
        installer_manifest,
        _key_catalog_path(installer_manifest),
        TEST_SEED,
        dist_dir=dist,
        version=TEST_VERSION,
        expected_public_key=public_key,
        key_id=TEST_KEY_ID,
        installer_key_id=TEST_INSTALLER_KEY_ID,
        trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
    )
    return (
        root,
        dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        result,
    )


def test_generates_deterministic_versioned_checksums_and_verified_signature(tmp_path):
    (
        _root,
        _dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        result,
    ) = _generate(tmp_path)

    assert result.checksum_path.name == (f"MioTranslator-{TEST_VERSION}-SHA256SUMS.txt")
    assert result.signature_path.name == (
        f"MioTranslator-{TEST_VERSION}-SHA256SUMS.txt.sig.json"
    )
    checksum_bytes = result.checksum_path.read_bytes()
    lines = checksum_bytes.decode("ascii").splitlines()
    assert lines == sorted(lines, key=lambda line: line.split(" *", 1)[1])
    assert len(lines) == 4
    assert (
        f"{hashlib.sha256(installer.read_bytes()).hexdigest()} *{installer.name}"
        in lines
    )
    key_catalog = _key_catalog_path(installer_manifest)
    assert (
        f"{hashlib.sha256(key_catalog.read_bytes()).hexdigest()} *{key_catalog.name}"
        in lines
    )

    metadata = json.loads(result.signature_path.read_text(encoding="ascii"))
    assert metadata["signature_context"] == (
        release_checksums.CHECKSUM_SIGNATURE_CONTEXT
    )
    assert metadata["signature_key_id"] == TEST_KEY_ID
    assert metadata["public_key"] == public_key
    assert metadata["signed_file_sha256"] == hashlib.sha256(checksum_bytes).hexdigest()
    assert release_checksums.verify_release_checksum_artifacts(
        installer,
        update_manifest,
        installer_manifest,
        _key_catalog_path(installer_manifest),
        result.checksum_path,
        result.signature_path,
        version=TEST_VERSION,
        expected_public_key=public_key,
        key_id=TEST_KEY_ID,
        installer_key_id=TEST_INSTALLER_KEY_ID,
        trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
    )


def test_repeated_generation_is_byte_for_byte_deterministic(tmp_path):
    (
        _root,
        dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        first,
    ) = _generate(tmp_path)
    first_checksum = first.checksum_path.read_bytes()
    first_signature = first.signature_path.read_bytes()

    second = release_checksums.generate_release_checksum_artifacts(
        installer,
        update_manifest,
        installer_manifest,
        _key_catalog_path(installer_manifest),
        TEST_SEED,
        dist_dir=dist,
        version=TEST_VERSION,
        expected_public_key=public_key,
        key_id=TEST_KEY_ID,
        installer_key_id=TEST_INSTALLER_KEY_ID,
        trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
    )

    assert second.checksum_path.read_bytes() == first_checksum
    assert second.signature_path.read_bytes() == first_signature
    assert not list(dist.glob(".*.tmp"))
    assert not list(dist.glob(".*.bak"))


def test_wrong_seed_is_rejected_before_outputs_are_created(tmp_path):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="does not match the active public verification key",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            "18" * 32,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )

    assert not list(dist.glob("*SHA256SUMS*"))


@pytest.mark.parametrize(
    ("mismatch", "error_match"),
    (
        ("release", "active release does not match"),
        ("manifest_id", "active manifest key id does not match"),
        ("installer_id", "active installer key id does not match"),
        ("public_hash", "public-key SHA-256 is invalid"),
        ("retired_trusted", "must be retired and untrusted"),
    ),
)
def test_key_catalog_must_match_active_source_trust(
    tmp_path,
    mismatch,
    error_match,
):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    key_catalog = _key_catalog_path(installer_manifest)
    catalog = json.loads(key_catalog.read_text(encoding="utf-8"))
    if mismatch == "release":
        catalog["active_release"] = "v0.0.0"
    elif mismatch == "manifest_id":
        catalog["active_manifest_key_id"] = "unexpected-manifest-key"
    elif mismatch == "installer_id":
        catalog["active_installer_key_id"] = "unexpected-installer-key"
    elif mismatch == "public_hash":
        catalog["keys"][1]["public_key_sha256"] = "00" * 32
    else:
        catalog["keys"][0]["trusted_by_active_runtime"] = True
    key_catalog.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(release_checksums.ReleaseChecksumError, match=error_match):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            key_catalog,
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )

    assert not list(dist.glob("*SHA256SUMS*"))


def test_tampered_installer_metadata_signature_is_rejected(tmp_path):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    manifest = json.loads(update_manifest.read_text(encoding="utf-8"))
    manifest[INSTALLER_SIGNATURE_FIELD] = "00" * 64
    manifest[SIGNATURE_FIELD] = sign_manifest(manifest, TEST_SEED)
    update_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="Update manifest installer metadata signature is invalid",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )

    assert not list(dist.glob("*SHA256SUMS*"))


def test_valid_historical_installer_signature_is_rejected_for_active_release(tmp_path):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    historical_public_key = public_key_from_seed(HISTORICAL_INSTALLER_SEED)
    installer_bytes = installer.read_bytes()
    installer_digest = hashlib.sha256(installer_bytes).hexdigest()
    historical_signature = sign_installer_metadata(
        HISTORICAL_INSTALLER_SEED,
        sha256=installer_digest,
        size_bytes=len(installer_bytes),
    )
    for manifest_path in (update_manifest, installer_manifest):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[INSTALLER_SIGNATURE_KEY_ID_FIELD] = HISTORICAL_INSTALLER_KEY_ID
        manifest[INSTALLER_SIGNATURE_FIELD] = historical_signature
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, TEST_SEED)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="does not use the active key id",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=(
                (TEST_INSTALLER_KEY_ID, public_key),
                (HISTORICAL_INSTALLER_KEY_ID, historical_public_key),
            ),
        )

    assert not list(dist.glob("*SHA256SUMS*"))


@pytest.mark.parametrize(
    "tamper_target",
    ["checksum", "signature", "installer", "catalog"],
)
def test_verification_rejects_tampering(tmp_path, tamper_target):
    (
        _root,
        _dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        result,
    ) = _generate(tmp_path)
    if tamper_target == "checksum":
        result.checksum_path.write_bytes(result.checksum_path.read_bytes() + b"x")
    elif tamper_target == "signature":
        metadata = json.loads(result.signature_path.read_text(encoding="ascii"))
        metadata["signature"] = "00" * 64
        result.signature_path.write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
            encoding="ascii",
        )
    elif tamper_target == "installer":
        installer.write_bytes(installer.read_bytes() + b"tampered")
    else:
        key_catalog = _key_catalog_path(installer_manifest)
        key_catalog.write_bytes(key_catalog.read_bytes() + b" ")

    with pytest.raises(release_checksums.ReleaseChecksumError):
        release_checksums.verify_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            result.checksum_path,
            result.signature_path,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )


def test_existing_symlink_output_is_rejected(tmp_path):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not replace", encoding="utf-8")
    checksum_path = dist / f"MioTranslator-{TEST_VERSION}-SHA256SUMS.txt"
    try:
        checksum_path.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="regular non-link file",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )

    assert outside.read_text(encoding="utf-8") == "do not replace"


def test_non_regular_output_is_rejected(tmp_path):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    checksum_path = dist / f"MioTranslator-{TEST_VERSION}-SHA256SUMS.txt"
    checksum_path.mkdir()

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="regular non-link file",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )


def test_failed_staged_self_verification_preserves_existing_outputs(
    tmp_path,
    monkeypatch,
):
    _root, dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    public_key = public_key_from_seed(TEST_SEED)
    checksum_path = dist / f"MioTranslator-{TEST_VERSION}-SHA256SUMS.txt"
    signature_path = dist / f"{checksum_path.name}.sig.json"
    old_checksum = b"previous checksum artifact\n"
    old_signature = b"previous signature artifact\n"
    checksum_path.write_bytes(old_checksum)
    signature_path.write_bytes(old_signature)
    monkeypatch.setattr(release_checksums, "verify_ed25519", lambda *_args: False)

    with pytest.raises(
        release_checksums.ReleaseChecksumError,
        match="Detached signature verification failed",
    ):
        release_checksums.generate_release_checksum_artifacts(
            installer,
            update_manifest,
            installer_manifest,
            _key_catalog_path(installer_manifest),
            TEST_SEED,
            dist_dir=dist,
            version=TEST_VERSION,
            expected_public_key=public_key,
            key_id=TEST_KEY_ID,
            installer_key_id=TEST_INSTALLER_KEY_ID,
            trusted_installer_public_keys=((TEST_INSTALLER_KEY_ID, public_key),),
        )

    assert checksum_path.read_bytes() == old_checksum
    assert signature_path.read_bytes() == old_signature
    assert not list(dist.glob(".*.tmp"))
    assert not list(dist.glob(".*.bak"))


def test_reparse_attribute_is_treated_as_unsafe():
    fake_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o644,
        st_file_attributes=release_checksums._FILE_ATTRIBUTE_REPARSE_POINT,
    )
    assert release_checksums._is_link_or_reparse(fake_stat)


def test_cli_requires_seed_without_echoing_secret(tmp_path, monkeypatch, capsys):
    _root, _dist, installer, update_manifest, installer_manifest = _release_inputs(
        tmp_path
    )
    monkeypatch.delenv("MIO_RELEASE_SIGNING_SEED", raising=False)
    monkeypatch.delenv("MIO_MANIFEST_SEED", raising=False)

    assert (
        release_checksums.main(
            [
                str(installer),
                str(update_manifest),
                str(installer_manifest),
                str(_key_catalog_path(installer_manifest)),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "MIO_RELEASE_SIGNING_SEED" in captured.err
    assert TEST_SEED not in captured.out + captured.err


def _configure_cli_test_trust(monkeypatch, public_key: str) -> None:
    monkeypatch.setattr(
        release_checksums,
        "UPDATE_MANIFEST_PUBLIC_KEY",
        public_key,
    )
    monkeypatch.setattr(
        release_checksums,
        "UPDATE_MANIFEST_PUBLIC_KEY_ID",
        TEST_KEY_ID,
    )
    monkeypatch.setattr(
        release_checksums,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        ((TEST_INSTALLER_KEY_ID, public_key),),
    )
    monkeypatch.setattr(
        release_checksums,
        "INSTALLER_SIGNATURE_KEY_ID",
        TEST_INSTALLER_KEY_ID,
    )


def test_verify_only_cli_uses_default_paths_without_seed(
    tmp_path,
    monkeypatch,
    capsys,
):
    (
        _root,
        _dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        result,
    ) = _generate(tmp_path)
    _configure_cli_test_trust(monkeypatch, public_key)
    monkeypatch.delenv("MIO_RELEASE_SIGNING_SEED", raising=False)
    monkeypatch.delenv("MIO_MANIFEST_SEED", raising=False)

    assert (
        release_checksums.main(
            [
                "--verify-only",
                "--version",
                TEST_VERSION,
                str(installer),
                str(update_manifest),
                str(installer_manifest),
                str(_key_catalog_path(installer_manifest)),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert str(result.checksum_path) in captured.out
    assert str(result.signature_path) in captured.out
    assert TEST_SEED not in captured.out + captured.err


def test_verify_only_cli_reports_tampering_without_seed(
    tmp_path,
    monkeypatch,
    capsys,
):
    (
        _root,
        _dist,
        installer,
        update_manifest,
        installer_manifest,
        public_key,
        result,
    ) = _generate(tmp_path)
    _configure_cli_test_trust(monkeypatch, public_key)
    monkeypatch.delenv("MIO_RELEASE_SIGNING_SEED", raising=False)
    monkeypatch.delenv("MIO_MANIFEST_SEED", raising=False)
    result.checksum_path.write_bytes(result.checksum_path.read_bytes() + b"tampered")

    assert (
        release_checksums.main(
            [
                "--verify-only",
                "--version",
                TEST_VERSION,
                str(installer),
                str(update_manifest),
                str(installer_manifest),
                str(_key_catalog_path(installer_manifest)),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "MIO_RELEASE_SIGNING_SEED" not in captured.err
    assert TEST_SEED not in captured.out + captured.err
