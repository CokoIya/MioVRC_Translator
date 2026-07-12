# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from src.updater.installer_signature import (
    InstallerSignatureError,
    normalize_installer_sha256,
    normalize_installer_size,
    verify_installer_metadata_signature,
)
from src.utils.windows_executables import (
    WindowsExecutableResolutionError,
    trusted_windows_powershell_executable,
)

_CHUNK_SIZE = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class InstallerVerificationError(RuntimeError):
    """Raised when an update installer cannot be authenticated exactly."""


@dataclass(frozen=True, slots=True)
class InstallerVerificationResult:
    path: Path
    size_bytes: int
    sha256: str
    signature_key_id: str


def _is_safe_regular_file_stat(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    link_count = int(getattr(file_stat, "st_nlink", 1) or 1)
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not stat.S_ISLNK(file_stat.st_mode)
        and not (attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
        and link_count == 1
    )


def _regular_file_stat(path: Path) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise InstallerVerificationError("Update installer is missing") from exc
    except OSError as exc:
        raise InstallerVerificationError(
            f"Unable to inspect update installer: {exc}"
        ) from exc

    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(file_stat.st_mode) or (
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise InstallerVerificationError(
            "Update installer must not be a symlink or reparse point"
        )
    if not stat.S_ISREG(file_stat.st_mode):
        raise InstallerVerificationError("Update installer is not a regular file")
    if int(getattr(file_stat, "st_nlink", 1) or 1) != 1:
        raise InstallerVerificationError(
            "Update installer must not have multiple hard links"
        )
    return file_stat


def _same_file_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    identity_before = (
        getattr(before, "st_dev", None),
        getattr(before, "st_ino", None),
    )
    identity_after = (
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
    )
    identity_known = all(
        value not in (None, 0) for value in identity_before + identity_after
    )
    return (not identity_known or identity_before == identity_after) and (
        before.st_size == after.st_size
        and getattr(before, "st_mtime_ns", None)
        == getattr(after, "st_mtime_ns", None)
        and getattr(before, "st_ctime_ns", None)
        == getattr(after, "st_ctime_ns", None)
        and getattr(before, "st_birthtime_ns", None)
        == getattr(after, "st_birthtime_ns", None)
        and int(getattr(before, "st_nlink", 1) or 1)
        == int(getattr(after, "st_nlink", 1) or 1)
        and int(getattr(before, "st_file_attributes", 0) or 0)
        == int(getattr(after, "st_file_attributes", 0) or 0)
    )


def _sha256_file(path: Path) -> str:
    before = _regular_file_stat(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InstallerVerificationError(
            f"Unable to read update installer: {exc}"
        ) from exc

    try:
        opened = os.fstat(descriptor)
        on_disk = _regular_file_stat(path)
        if (
            not _is_safe_regular_file_stat(opened)
            or not _same_file_snapshot(before, opened)
            or not _same_file_snapshot(opened, on_disk)
        ):
            raise InstallerVerificationError(
                "Installer changed while it was being opened for verification"
            )

        hasher = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)

        final_handle_stat = os.fstat(descriptor)
        final_disk_stat = _regular_file_stat(path)
        if (
            not _same_file_snapshot(opened, final_handle_stat)
            or not _same_file_snapshot(final_handle_stat, final_disk_stat)
        ):
            raise InstallerVerificationError(
                "Installer changed while its SHA256 digest was being calculated"
            )
        return hasher.hexdigest()
    except InstallerVerificationError:
        raise
    except OSError as exc:
        raise InstallerVerificationError(
            f"Unable to read update installer: {exc}"
        ) from exc
    finally:
        os.close(descriptor)


def windows_powershell_executable() -> Path:
    """Resolve the trusted inbox Windows PowerShell binary without PATH search."""

    try:
        return trusted_windows_powershell_executable()
    except WindowsExecutableResolutionError as exc:
        raise InstallerVerificationError(
            f"Trusted Windows PowerShell is unavailable: {exc}"
        ) from exc


def verify_installer(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str,
    expected_size: int,
    installer_signature: str,
    signature_algorithm: str,
    signature_key_id: str,
    trusted_public_keys: Mapping[object, object]
    | Iterable[tuple[object, object]],
) -> InstallerVerificationResult:
    """Authenticate metadata, file identity, size, and digest without PKI."""

    try:
        expected_digest = normalize_installer_sha256(expected_sha256)
        normalized_size = normalize_installer_size(expected_size)
        verified_key_id = verify_installer_metadata_signature(
            sha256=expected_digest,
            size_bytes=normalized_size,
            signature=installer_signature,
            signature_algorithm=signature_algorithm,
            signature_key_id=signature_key_id,
            trusted_public_keys=trusted_public_keys,
        )
    except InstallerSignatureError as exc:
        raise InstallerVerificationError(str(exc)) from exc

    installer_path = Path(path)
    before = _regular_file_stat(installer_path)
    if before.st_size != normalized_size:
        raise InstallerVerificationError(
            f"Installer size mismatch (expected {normalized_size}, got {before.st_size})"
        )

    actual_digest = _sha256_file(installer_path)
    if actual_digest != expected_digest:
        raise InstallerVerificationError("Installer SHA256 verification failed")

    after_hash = _regular_file_stat(installer_path)
    if not _same_file_snapshot(before, after_hash):
        raise InstallerVerificationError(
            "Installer changed while it was being verified"
        )

    return InstallerVerificationResult(
        path=installer_path,
        size_bytes=int(after_hash.st_size),
        sha256=actual_digest,
        signature_key_id=verified_key_id,
    )
