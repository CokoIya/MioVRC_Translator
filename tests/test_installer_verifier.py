import hashlib
import os

import pytest

from src.updater import installer_verifier
from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    sign_installer_metadata,
)
from src.updater.installer_verifier import InstallerVerificationError, verify_installer
from src.updater.manifest_signature import public_key_from_seed


SEED = "11" * 32
OTHER_SEED = "22" * 32
KEY_ID = "test-installer-key"
PUBLIC_KEY = public_key_from_seed(SEED)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verification_kwargs(
    data: bytes,
    *,
    digest: str | None = None,
    size: int | None = None,
    seed: str = SEED,
    key_id: str = KEY_ID,
    trusted_public_keys=None,
) -> dict[str, object]:
    expected_digest = digest or _digest(data)
    expected_size = len(data) if size is None else size
    signature = sign_installer_metadata(
        seed,
        sha256=expected_digest,
        size_bytes=expected_size,
    )
    if trusted_public_keys is None:
        trusted_public_keys = ((key_id, public_key_from_seed(seed)),)
    return {
        "expected_sha256": expected_digest,
        "expected_size": expected_size,
        "installer_signature": signature,
        "signature_algorithm": INSTALLER_SIGNATURE_ALGORITHM,
        "signature_key_id": key_id,
        "trusted_public_keys": trusted_public_keys,
    }


def test_verify_installer_requires_nonempty_public_key_allowlist(tmp_path):
    payload = b"installer"
    installer = tmp_path / "setup.exe"
    installer.write_bytes(payload)
    kwargs = _verification_kwargs(payload)
    kwargs["trusted_public_keys"] = ()

    with pytest.raises(InstallerVerificationError, match="No trusted installer public keys"):
        verify_installer(installer, **kwargs)


def test_verify_installer_checks_signature_size_and_hash(tmp_path):
    payload = b"signed installer"
    installer = tmp_path / "setup.exe"
    installer.write_bytes(payload)

    result = verify_installer(installer, **_verification_kwargs(payload))

    assert result.sha256 == _digest(payload)
    assert result.size_bytes == len(payload)
    assert result.signature_key_id == KEY_ID


def test_verify_installer_hashes_authenticated_file_once(tmp_path, monkeypatch):
    payload = b"signed installer"
    installer = tmp_path / "setup.exe"
    installer.write_bytes(payload)
    expected_digest = _digest(payload)
    calls = []

    def hash_file(_path):
        calls.append("sha256")
        return expected_digest

    monkeypatch.setattr(installer_verifier, "_sha256_file", hash_file)

    result = verify_installer(installer, **_verification_kwargs(payload))

    assert result.sha256 == expected_digest
    assert calls == ["sha256"]


def test_verify_installer_rejects_size_hash_untrusted_key_and_bad_signature(tmp_path):
    payload = b"installer"
    installer = tmp_path / "setup.exe"
    installer.write_bytes(payload)

    with pytest.raises(InstallerVerificationError, match="size mismatch"):
        verify_installer(
            installer,
            **_verification_kwargs(payload, size=len(payload) + 1),
        )

    with pytest.raises(InstallerVerificationError, match="SHA256"):
        verify_installer(
            installer,
            **_verification_kwargs(payload, digest="0" * 64),
        )

    untrusted = _verification_kwargs(payload)
    untrusted["trusted_public_keys"] = (("other-key", public_key_from_seed(OTHER_SEED)),)
    with pytest.raises(InstallerVerificationError, match="key id is not trusted"):
        verify_installer(installer, **untrusted)

    invalid = _verification_kwargs(payload)
    invalid["installer_signature"] = "0" * 128
    with pytest.raises(InstallerVerificationError, match="signature is invalid"):
        verify_installer(installer, **invalid)


def test_verify_installer_rejects_hardlinked_path(tmp_path):
    payload = b"installer"
    original = tmp_path / "original.exe"
    installer = tmp_path / "setup.exe"
    original.write_bytes(payload)
    try:
        os.link(original, installer)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable")

    with pytest.raises(InstallerVerificationError, match="hard links"):
        verify_installer(installer, **_verification_kwargs(payload))


def test_verify_installer_rejects_missing_nonregular_and_symlink(tmp_path):
    metadata = _verification_kwargs(b"x")
    with pytest.raises(InstallerVerificationError, match="missing"):
        verify_installer(tmp_path / "missing.exe", **metadata)

    directory = tmp_path / "directory.exe"
    directory.mkdir()
    with pytest.raises(InstallerVerificationError, match="regular file"):
        verify_installer(directory, **metadata)

    target = tmp_path / "target.exe"
    target.write_bytes(b"target")
    link = tmp_path / "link.exe"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(InstallerVerificationError, match="symlink|reparse"):
        verify_installer(link, **_verification_kwargs(b"target"))


def test_verify_installer_rejects_unsupported_algorithm(tmp_path):
    payload = b"installer"
    installer = tmp_path / "setup.exe"
    installer.write_bytes(payload)
    kwargs = _verification_kwargs(payload)
    kwargs["signature_algorithm"] = "rsa"

    with pytest.raises(InstallerVerificationError, match="algorithm"):
        verify_installer(installer, **kwargs)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell is Windows-only")
def test_windows_powershell_executable_resolves_absolute_system_binary():
    powershell = installer_verifier.windows_powershell_executable()

    assert powershell.is_absolute()
    assert powershell.name.casefold() == "powershell.exe"
    assert powershell.parent.name.casefold() == "v1.0"
    assert powershell.is_file()
    assert not powershell.is_symlink()
