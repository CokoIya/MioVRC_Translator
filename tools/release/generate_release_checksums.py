"""Generate deterministic checksums and a detached Ed25519 release signature.

The signing seed is accepted only through ``MIO_RELEASE_SIGNING_SEED`` (or the
legacy ``MIO_MANIFEST_SEED`` environment variable).  It is never written to
disk or included in command output.

Usage::

    python tools/release/generate_release_checksums.py \
        dist/MioTranslator-Setup-v1.3.9.exe \
        mio_update.json \
        docs/installer_manifest.json \
        docs/release_signing_keys.json
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.updater.manifest_signature import (  # noqa: E402
    SIGNATURE_ALGORITHM,
    ManifestSignatureError,
    normalize_trusted_manifest_public_keys,
    public_key_from_seed,
    sign_ed25519,
    verify_ed25519,
    verify_manifest_signature,
)
from src.updater.installer_signature import (  # noqa: E402
    InstallerSignatureError,
    InstallerSignatureMetadata,
    normalize_signature_key_id,
    normalize_trusted_public_keys,
    parse_installer_signature_metadata,
    verify_installer_metadata_signature,
)
from src.version import (  # noqa: E402
    APP_VERSION,
    INSTALLER_SIGNATURE_KEY_ID,
    TRUSTED_INSTALLER_PUBLIC_KEYS,
    UPDATE_MANIFEST_PUBLIC_KEY,
    UPDATE_MANIFEST_PUBLIC_KEY_ID,
)


CHECKSUM_SIGNATURE_CONTEXT = "MioVRC_Translator/release-checksums/v1"
CHECKSUM_SCHEMA_VERSION = 1
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_CHUNK_SIZE = 1024 * 1024
_VERSION_RE = re.compile(r"v[0-9]+(?:\.[0-9]+){2,3}")
_SAFE_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_CHECKSUM_LINE_RE = re.compile(r"([0-9a-f]{64}) \*([A-Za-z0-9][A-Za-z0-9._-]{0,254})")
_METADATA_FIELDS = (
    "schema_version",
    "release_version",
    "signed_file",
    "signed_file_size",
    "signed_file_sha256",
    "signature_algorithm",
    "signature_context",
    "signature_key_id",
    "public_key",
    "signature",
)


class ReleaseChecksumError(RuntimeError):
    """Raised when release checksum generation or verification fails."""


@dataclass(frozen=True, slots=True)
class ReleaseChecksumResult:
    checksum_path: Path
    signature_path: Path
    checksum_sha256: str
    signature_key_id: str
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _HashedFile:
    path: Path
    name: str
    size: int
    sha256: str
    snapshot: os.stat_result


_TrustedPublicKeys = Mapping[object, object] | Iterable[tuple[object, object]]


def _reject_nonstandard_json_constant(value: str) -> object:
    raise ValueError(f"Non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _absolute_path(path: os.PathLike[str] | str) -> Path:
    """Return a lexical absolute path without following links or reparses."""

    return Path(os.path.abspath(os.fspath(path)))


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _assert_safe_ancestors(path: Path, *, label: str) -> None:
    for ancestor in reversed(path.parents):
        try:
            ancestor_stat = ancestor.lstat()
        except OSError as exc:
            raise ReleaseChecksumError(
                f"Unable to inspect {label} parent directory: {ancestor}"
            ) from exc
        if _is_link_or_reparse(ancestor_stat) or not stat.S_ISDIR(
            ancestor_stat.st_mode
        ):
            raise ReleaseChecksumError(
                f"{label} parent directory is unsafe: {ancestor}"
            )


def _regular_file_snapshot(path: Path, *, label: str) -> os.stat_result:
    _assert_safe_ancestors(path, label=label)
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseChecksumError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise ReleaseChecksumError(f"Unable to inspect {label}: {path}") from exc
    if _is_link_or_reparse(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise ReleaseChecksumError(f"{label} must be a regular non-link file: {path}")
    return file_stat


def _safe_directory(path: Path, *, label: str) -> os.stat_result:
    _assert_safe_ancestors(path, label=label)
    try:
        directory_stat = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseChecksumError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise ReleaseChecksumError(f"Unable to inspect {label}: {path}") from exc
    if _is_link_or_reparse(directory_stat) or not stat.S_ISDIR(directory_stat.st_mode):
        raise ReleaseChecksumError(f"{label} is unsafe: {path}")
    return directory_stat


def _same_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    before_identity = (getattr(before, "st_dev", 0), getattr(before, "st_ino", 0))
    after_identity = (getattr(after, "st_dev", 0), getattr(after, "st_ino", 0))
    identity_known = all(before_identity + after_identity)
    return (
        (not identity_known or before_identity == after_identity)
        and before.st_size == after.st_size
        and getattr(before, "st_mtime_ns", None) == getattr(after, "st_mtime_ns", None)
        and getattr(before, "st_ctime_ns", None) == getattr(after, "st_ctime_ns", None)
    )


def _open_regular_readonly(path: Path, *, label: str) -> tuple[int, os.stat_result]:
    before = _regular_file_snapshot(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseChecksumError(f"Unable to open {label}: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
            raise ReleaseChecksumError(
                f"{label} must be a regular non-link file: {path}"
            )
        if not _same_snapshot(before, opened):
            raise ReleaseChecksumError(f"{label} changed while it was opened: {path}")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, opened


def _hash_regular_file(
    path: Path, *, label: str, require_nonempty: bool
) -> _HashedFile:
    descriptor, opened = _open_regular_readonly(path, label=label)
    hasher = hashlib.sha256()
    try:
        while True:
            chunk = os.read(descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
        after_read = os.fstat(descriptor)
    except OSError as exc:
        raise ReleaseChecksumError(f"Unable to read {label}: {path}") from exc
    finally:
        os.close(descriptor)
    current = _regular_file_snapshot(path, label=label)
    if not _same_snapshot(opened, after_read) or not _same_snapshot(opened, current):
        raise ReleaseChecksumError(f"{label} changed while it was hashed: {path}")
    if require_nonempty and current.st_size <= 0:
        raise ReleaseChecksumError(f"{label} must not be empty: {path}")
    return _HashedFile(
        path=path,
        name=path.name,
        size=int(current.st_size),
        sha256=hasher.hexdigest(),
        snapshot=current,
    )


def _read_regular_bytes(path: Path, *, label: str) -> tuple[bytes, os.stat_result]:
    descriptor, opened = _open_regular_readonly(path, label=label)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after_read = os.fstat(descriptor)
    except OSError as exc:
        raise ReleaseChecksumError(f"Unable to read {label}: {path}") from exc
    finally:
        os.close(descriptor)
    current = _regular_file_snapshot(path, label=label)
    if not _same_snapshot(opened, after_read) or not _same_snapshot(opened, current):
        raise ReleaseChecksumError(f"{label} changed while it was read: {path}")
    return b"".join(chunks), current


def _normalize_release_version(version: str) -> str:
    normalized = str(version or "").strip()
    if not normalized.lower().startswith("v"):
        normalized = f"v{normalized}"
    if not _VERSION_RE.fullmatch(normalized):
        raise ReleaseChecksumError("Release version is invalid")
    return normalized


def _normalize_signing_identity(public_key: str, key_id: str) -> tuple[str, str]:
    try:
        trusted = normalize_trusted_manifest_public_keys(((key_id, public_key),))
    except ManifestSignatureError as exc:
        raise ReleaseChecksumError(
            f"Release signing identity is invalid: {exc}"
        ) from exc
    normalized_key_id, normalized_public_key = next(iter(trusted.items()))
    return normalized_key_id, normalized_public_key


def _safe_filename(name: str, *, label: str) -> str:
    value = str(name or "")
    if not _SAFE_FILENAME_RE.fullmatch(value):
        raise ReleaseChecksumError(f"{label} filename is unsafe")
    return value


def _parse_signed_manifest(
    raw: bytes,
    *,
    label: str,
    public_key: str,
    key_id: str,
) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseChecksumError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ReleaseChecksumError(f"{label} must be a JSON object")
    try:
        verify_manifest_signature(
            payload,
            public_key,
            required=True,
            expected_key_id=key_id,
        )
    except ManifestSignatureError as exc:
        raise ReleaseChecksumError(f"{label} signature is invalid: {exc}") from exc
    return payload


def _parse_release_key_catalog(
    raw: bytes,
    *,
    release_version: str,
    manifest_key_id: str,
    installer_key_id: str,
    public_key: str,
) -> dict[str, object]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseChecksumError(
            "Release signing key catalog is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ReleaseChecksumError("Release signing key catalog must be a JSON object")
    if payload.get("schema_version") != 1 or isinstance(
        payload.get("schema_version"), bool
    ):
        raise ReleaseChecksumError(
            "Release signing key catalog schema version is invalid"
        )
    if payload.get("active_release") != release_version:
        raise ReleaseChecksumError(
            "Release signing key catalog active release does not match"
        )
    if payload.get("active_manifest_key_id") != manifest_key_id:
        raise ReleaseChecksumError(
            "Release signing key catalog active manifest key id does not match"
        )
    if payload.get("active_installer_key_id") != installer_key_id:
        raise ReleaseChecksumError(
            "Release signing key catalog active installer key id does not match"
        )

    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise ReleaseChecksumError(
            "Release signing key catalog must contain key entries"
        )
    active_entries: list[dict[str, object]] = []
    trusted_entries: list[dict[str, object]] = []
    for index, raw_entry in enumerate(raw_keys):
        if not isinstance(raw_entry, dict):
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} is invalid"
            )
        entry = raw_entry
        status = entry.get("status")
        trusted = entry.get("trusted_by_active_runtime")
        if not isinstance(status, str) or not status:
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} status is invalid"
            )
        if not isinstance(trusted, bool):
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} trust marker is invalid"
            )

        entry_public_key = str(entry.get("public_key_hex", "") or "").strip()
        entry_manifest_key_id = str(entry.get("manifest_key_id", "") or "").strip()
        entry_installer_key_id = str(entry.get("installer_key_id", "") or "").strip()
        try:
            normalized_manifest_entry = normalize_trusted_manifest_public_keys(
                ((entry_manifest_key_id, entry_public_key),)
            )
            normalized_installer_entry_id = normalize_signature_key_id(
                entry_installer_key_id
            )
        except (InstallerSignatureError, ManifestSignatureError) as exc:
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} identity is invalid: {exc}"
            ) from exc
        normalized_entry_public_key = normalized_manifest_entry[entry_manifest_key_id]
        raw_key_sha256 = hashlib.sha256(
            bytes.fromhex(normalized_entry_public_key)
        ).hexdigest()
        if entry.get("public_key_sha256") != raw_key_sha256:
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} public-key SHA-256 is invalid"
            )

        if status == "active":
            active_entries.append(entry)
        elif not status.startswith("retired") or trusted:
            raise ReleaseChecksumError(
                f"Release signing key catalog entry {index} must be retired and untrusted"
            )
        if trusted:
            trusted_entries.append(entry)

        if status == "active" and (
            entry_manifest_key_id != manifest_key_id
            or normalized_installer_entry_id != installer_key_id
            or normalized_entry_public_key != public_key
            or entry.get("public_key_sha256")
            != hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
        ):
            raise ReleaseChecksumError(
                "Release signing key catalog active entry does not match source trust"
            )

    if (
        len(active_entries) != 1
        or len(trusted_entries) != 1
        or active_entries[0] is not trusted_entries[0]
    ):
        raise ReleaseChecksumError(
            "Release signing key catalog must contain exactly one active trusted entry"
        )
    return payload


def _validate_manifest_installer_metadata(
    manifest: Mapping[str, object],
    *,
    label: str,
    release_version: str,
    installer: _HashedFile,
) -> None:
    if manifest.get("version") != release_version:
        raise ReleaseChecksumError(f"{label} release version does not match")
    if manifest.get("installer_name") != installer.name:
        raise ReleaseChecksumError(f"{label} installer filename does not match")
    size_bytes = manifest.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise ReleaseChecksumError(f"{label} installer size is invalid")
    if size_bytes != installer.size:
        raise ReleaseChecksumError(f"{label} installer size does not match")
    sha256 = str(manifest.get("sha256", "") or "").strip().lower()
    if sha256 != installer.sha256:
        raise ReleaseChecksumError(f"{label} installer SHA-256 does not match")


def _verify_manifest_installer_signature(
    manifest: Mapping[str, object],
    *,
    label: str,
    installer: _HashedFile,
    installer_key_id: str,
    trusted_installer_public_keys: _TrustedPublicKeys,
) -> InstallerSignatureMetadata:
    try:
        metadata = parse_installer_signature_metadata(manifest)
        if metadata.key_id != installer_key_id:
            raise InstallerSignatureError(
                "Installer signature does not use the active key id"
            )
        verified_key_id = verify_installer_metadata_signature(
            sha256=installer.sha256,
            size_bytes=installer.size,
            signature=metadata.signature,
            signature_algorithm=metadata.algorithm,
            signature_key_id=metadata.key_id,
            trusted_public_keys=trusted_installer_public_keys,
        )
    except InstallerSignatureError as exc:
        raise ReleaseChecksumError(
            f"{label} installer metadata signature is invalid: {exc}"
        ) from exc
    if verified_key_id != metadata.key_id:
        raise ReleaseChecksumError(
            f"{label} installer metadata signature key does not match"
        )
    return metadata


def _signature_message(
    checksum_bytes: bytes,
    *,
    release_version: str,
    checksum_name: str,
) -> bytes:
    fields = (
        release_version.encode("ascii"),
        checksum_name.encode("ascii"),
        bytes(checksum_bytes),
    )
    encoded_fields = b"".join(
        len(field).to_bytes(8, byteorder="big") + field for field in fields
    )
    return CHECKSUM_SIGNATURE_CONTEXT.encode("ascii") + b"\x00" + encoded_fields


def _checksum_bytes(entries: Mapping[str, str]) -> bytes:
    lines: list[str] = []
    for raw_name, raw_digest in sorted(entries.items()):
        name = _safe_filename(raw_name, label="Release input")
        digest = str(raw_digest or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseChecksumError(f"SHA-256 digest is invalid for {name}")
        lines.append(f"{digest} *{name}\n")
    if not lines:
        raise ReleaseChecksumError("No release inputs were provided")
    return "".join(lines).encode("ascii")


def _parse_checksum_bytes(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii")
    except UnicodeError as exc:
        raise ReleaseChecksumError("Checksum file must be ASCII") from exc
    if not text or not text.endswith("\n") or "\r" in text:
        raise ReleaseChecksumError("Checksum file formatting is invalid")
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = _CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ReleaseChecksumError("Checksum file contains an invalid line")
        digest, name = match.groups()
        if name in entries:
            raise ReleaseChecksumError(
                f"Checksum file contains duplicate entry: {name}"
            )
        entries[name] = digest
    if payload != _checksum_bytes(entries):
        raise ReleaseChecksumError("Checksum entries are not in deterministic order")
    return entries


def _metadata_bytes(metadata: Mapping[str, object]) -> bytes:
    ordered = {field: metadata[field] for field in _METADATA_FIELDS}
    return (json.dumps(ordered, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def _parse_signature_metadata(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseChecksumError(
            "Detached signature metadata is invalid JSON"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != set(_METADATA_FIELDS):
        raise ReleaseChecksumError("Detached signature metadata fields are invalid")
    if raw != _metadata_bytes(payload):
        raise ReleaseChecksumError(
            "Detached signature metadata serialization is not deterministic"
        )
    return payload


def _verify_detached_signature(
    checksum_payload: bytes,
    signature_payload: bytes,
    *,
    release_version: str,
    checksum_name: str,
    public_key: str,
    key_id: str,
) -> dict[str, object]:
    metadata = _parse_signature_metadata(signature_payload)
    expected_values: dict[str, object] = {
        "schema_version": CHECKSUM_SCHEMA_VERSION,
        "release_version": release_version,
        "signed_file": checksum_name,
        "signed_file_size": len(checksum_payload),
        "signed_file_sha256": hashlib.sha256(checksum_payload).hexdigest(),
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_context": CHECKSUM_SIGNATURE_CONTEXT,
        "signature_key_id": key_id,
        "public_key": public_key,
    }
    for field, expected in expected_values.items():
        if metadata.get(field) != expected:
            raise ReleaseChecksumError(
                f"Detached signature metadata field does not match: {field}"
            )
    signature = str(metadata.get("signature", "") or "").strip().lower()
    try:
        verified = verify_ed25519(
            public_key,
            signature,
            _signature_message(
                checksum_payload,
                release_version=release_version,
                checksum_name=checksum_name,
            ),
        )
    except ManifestSignatureError as exc:
        raise ReleaseChecksumError(f"Detached signature is invalid: {exc}") from exc
    if not verified:
        raise ReleaseChecksumError("Detached signature verification failed")
    return metadata


def _expected_release_inputs(
    installer_path: Path,
    update_manifest_path: Path,
    installer_manifest_path: Path,
    key_catalog_path: Path,
    *,
    release_version: str,
    public_key: str,
    key_id: str,
    installer_key_id: str,
    trusted_installer_public_keys: _TrustedPublicKeys,
) -> tuple[dict[str, str], tuple[_HashedFile, ...]]:
    expected_installer_name = f"MioTranslator-Setup-{release_version}.exe"
    if installer_path.name != expected_installer_name:
        raise ReleaseChecksumError(
            f"Installer filename must be {expected_installer_name}"
        )
    if update_manifest_path.name != "mio_update.json":
        raise ReleaseChecksumError("Update manifest filename must be mio_update.json")
    if installer_manifest_path.name != "installer_manifest.json":
        raise ReleaseChecksumError(
            "Installer manifest filename must be installer_manifest.json"
        )
    if key_catalog_path.name != "release_signing_keys.json":
        raise ReleaseChecksumError(
            "Release signing key catalog filename must be release_signing_keys.json"
        )
    input_paths = (
        installer_path,
        update_manifest_path,
        installer_manifest_path,
        key_catalog_path,
    )
    if len(set(input_paths)) != len(input_paths):
        raise ReleaseChecksumError("Release input paths must be distinct")
    if len({path.name for path in input_paths}) != len(input_paths):
        raise ReleaseChecksumError("Release input filenames must be distinct")

    installer = _hash_regular_file(
        installer_path,
        label="Installer",
        require_nonempty=True,
    )
    update_raw, update_snapshot = _read_regular_bytes(
        update_manifest_path,
        label="Update manifest",
    )
    installer_manifest_raw, installer_manifest_snapshot = _read_regular_bytes(
        installer_manifest_path,
        label="Installer manifest",
    )
    key_catalog_raw, key_catalog_snapshot = _read_regular_bytes(
        key_catalog_path,
        label="Release signing key catalog",
    )
    update_manifest = _parse_signed_manifest(
        update_raw,
        label="Update manifest",
        public_key=public_key,
        key_id=key_id,
    )
    installer_manifest = _parse_signed_manifest(
        installer_manifest_raw,
        label="Installer manifest",
        public_key=public_key,
        key_id=key_id,
    )
    _validate_manifest_installer_metadata(
        update_manifest,
        label="Update manifest",
        release_version=release_version,
        installer=installer,
    )
    _validate_manifest_installer_metadata(
        installer_manifest,
        label="Installer manifest",
        release_version=release_version,
        installer=installer,
    )
    try:
        normalized_installer_key_id = normalize_signature_key_id(installer_key_id)
        normalized_installer_keys = normalize_trusted_public_keys(
            trusted_installer_public_keys
        )
    except InstallerSignatureError as exc:
        raise ReleaseChecksumError(
            f"Trusted installer signing identity is invalid: {exc}"
        ) from exc
    if normalized_installer_key_id not in normalized_installer_keys:
        raise ReleaseChecksumError("Active installer signature key id is not trusted")
    if normalized_installer_keys[normalized_installer_key_id] != public_key:
        raise ReleaseChecksumError(
            "Active installer and manifest public verification keys do not match"
        )
    _parse_release_key_catalog(
        key_catalog_raw,
        release_version=release_version,
        manifest_key_id=key_id,
        installer_key_id=normalized_installer_key_id,
        public_key=public_key,
    )
    update_installer_signature = _verify_manifest_installer_signature(
        update_manifest,
        label="Update manifest",
        installer=installer,
        installer_key_id=normalized_installer_key_id,
        trusted_installer_public_keys=normalized_installer_keys,
    )
    installer_manifest_signature = _verify_manifest_installer_signature(
        installer_manifest,
        label="Installer manifest",
        installer=installer,
        installer_key_id=normalized_installer_key_id,
        trusted_installer_public_keys=normalized_installer_keys,
    )
    if update_installer_signature != installer_manifest_signature:
        raise ReleaseChecksumError(
            "Signed installer metadata does not match between release manifests"
        )

    update_file = _HashedFile(
        path=update_manifest_path,
        name=update_manifest_path.name,
        size=len(update_raw),
        sha256=hashlib.sha256(update_raw).hexdigest(),
        snapshot=update_snapshot,
    )
    installer_manifest_file = _HashedFile(
        path=installer_manifest_path,
        name=installer_manifest_path.name,
        size=len(installer_manifest_raw),
        sha256=hashlib.sha256(installer_manifest_raw).hexdigest(),
        snapshot=installer_manifest_snapshot,
    )
    key_catalog_file = _HashedFile(
        path=key_catalog_path,
        name=key_catalog_path.name,
        size=len(key_catalog_raw),
        sha256=hashlib.sha256(key_catalog_raw).hexdigest(),
        snapshot=key_catalog_snapshot,
    )
    hashed_files = (
        installer,
        update_file,
        installer_manifest_file,
        key_catalog_file,
    )
    return ({item.name: item.sha256 for item in hashed_files}, hashed_files)


def _assert_inputs_unchanged(files: Sequence[_HashedFile]) -> None:
    for item in files:
        current = _regular_file_snapshot(item.path, label=f"Release input {item.name}")
        if not _same_snapshot(item.snapshot, current):
            raise ReleaseChecksumError(
                f"Release input changed before artifact publication: {item.path}"
            )


def _assert_safe_output_target(path: Path) -> None:
    _safe_directory(path.parent, label="Release artifact directory")
    if os.path.lexists(path):
        _regular_file_snapshot(path, label="Existing release artifact")


def _write_staged_bytes(target: Path, payload: bytes, *, suffix: str) -> Path:
    _assert_safe_output_target(target)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=suffix,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary_path, 0o644)
        staged_payload, _snapshot = _read_regular_bytes(
            temporary_path,
            label="Staged release artifact",
        )
        if staged_payload != payload:
            raise ReleaseChecksumError(
                f"Staged release artifact changed while it was written: {target}"
            )
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return temporary_path


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_staged_artifacts(staged: Sequence[tuple[Path, Path]]) -> None:
    """Publish sequential replacements with rollback protection for the pair."""

    backups: dict[Path, Path | None] = {}
    replaced: list[Path] = []
    try:
        for target, _temporary_path in staged:
            _assert_safe_output_target(target)
            if os.path.lexists(target):
                existing, _snapshot = _read_regular_bytes(
                    target,
                    label="Existing release artifact",
                )
                backups[target] = _write_staged_bytes(
                    target,
                    existing,
                    suffix=".bak",
                )
            else:
                backups[target] = None

        try:
            for target, temporary_path in staged:
                _assert_safe_output_target(target)
                _regular_file_snapshot(
                    temporary_path,
                    label="Staged release artifact",
                )
                os.replace(temporary_path, target)
                replaced.append(target)
            _fsync_directory(staged[0][0].parent)
        except Exception as replacement_error:
            rollback_errors: list[str] = []
            for target in reversed(replaced):
                backup = backups.get(target)
                try:
                    if backup is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backup, target)
                        backups[target] = None
                except OSError as rollback_error:
                    rollback_errors.append(f"{target}: {rollback_error}")
            if rollback_errors:
                raise ReleaseChecksumError(
                    "Release artifact replacement failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from replacement_error
            raise
    finally:
        for _target, temporary_path in staged:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        for backup in backups.values():
            if backup is None:
                continue
            try:
                backup.unlink()
            except OSError:
                pass


def verify_release_checksum_artifacts(
    installer_path: os.PathLike[str] | str,
    update_manifest_path: os.PathLike[str] | str,
    installer_manifest_path: os.PathLike[str] | str,
    key_catalog_path: os.PathLike[str] | str,
    checksum_path: os.PathLike[str] | str,
    signature_path: os.PathLike[str] | str,
    *,
    version: str = APP_VERSION,
    expected_public_key: str = UPDATE_MANIFEST_PUBLIC_KEY,
    key_id: str = UPDATE_MANIFEST_PUBLIC_KEY_ID,
    installer_key_id: str = INSTALLER_SIGNATURE_KEY_ID,
    trusted_installer_public_keys: _TrustedPublicKeys = TRUSTED_INSTALLER_PUBLIC_KEYS,
) -> bool:
    """Verify inputs, deterministic checksums, metadata, and detached signature."""

    release_version = _normalize_release_version(version)
    normalized_key_id, public_key = _normalize_signing_identity(
        expected_public_key,
        key_id,
    )
    installer = _absolute_path(installer_path)
    update_manifest = _absolute_path(update_manifest_path)
    installer_manifest = _absolute_path(installer_manifest_path)
    key_catalog = _absolute_path(key_catalog_path)
    checksum = _absolute_path(checksum_path)
    signature = _absolute_path(signature_path)
    expected_entries, hashed_files = _expected_release_inputs(
        installer,
        update_manifest,
        installer_manifest,
        key_catalog,
        release_version=release_version,
        public_key=public_key,
        key_id=normalized_key_id,
        installer_key_id=installer_key_id,
        trusted_installer_public_keys=trusted_installer_public_keys,
    )
    checksum_payload, _checksum_snapshot = _read_regular_bytes(
        checksum,
        label="Release checksum file",
    )
    signature_payload, _signature_snapshot = _read_regular_bytes(
        signature,
        label="Detached signature metadata",
    )
    actual_entries = _parse_checksum_bytes(checksum_payload)
    if actual_entries != expected_entries:
        raise ReleaseChecksumError("Release checksum entries do not match the inputs")
    expected_checksum_name = f"MioTranslator-{release_version}-SHA256SUMS.txt"
    if checksum.name != expected_checksum_name:
        raise ReleaseChecksumError(
            f"Release checksum filename must be {expected_checksum_name}"
        )
    expected_signature_name = f"{expected_checksum_name}.sig.json"
    if signature.name != expected_signature_name:
        raise ReleaseChecksumError(
            f"Detached signature filename must be {expected_signature_name}"
        )
    _verify_detached_signature(
        checksum_payload,
        signature_payload,
        release_version=release_version,
        checksum_name=checksum.name,
        public_key=public_key,
        key_id=normalized_key_id,
    )
    _assert_inputs_unchanged(hashed_files)
    return True


def generate_release_checksum_artifacts(
    installer_path: os.PathLike[str] | str,
    update_manifest_path: os.PathLike[str] | str,
    installer_manifest_path: os.PathLike[str] | str,
    key_catalog_path: os.PathLike[str] | str,
    seed_hex: str,
    *,
    dist_dir: os.PathLike[str] | str | None = None,
    version: str = APP_VERSION,
    expected_public_key: str = UPDATE_MANIFEST_PUBLIC_KEY,
    key_id: str = UPDATE_MANIFEST_PUBLIC_KEY_ID,
    installer_key_id: str = INSTALLER_SIGNATURE_KEY_ID,
    trusted_installer_public_keys: _TrustedPublicKeys = TRUSTED_INSTALLER_PUBLIC_KEYS,
) -> ReleaseChecksumResult:
    """Publish signed release checksum artifacts with rollback protection."""

    release_version = _normalize_release_version(version)
    normalized_key_id, public_key = _normalize_signing_identity(
        expected_public_key,
        key_id,
    )
    try:
        derived_public_key = public_key_from_seed(seed_hex)
    except ManifestSignatureError as exc:
        raise ReleaseChecksumError(f"Release signing seed is invalid: {exc}") from exc
    if derived_public_key != public_key:
        raise ReleaseChecksumError(
            "Release signing seed does not match the active public verification key"
        )
    try:
        normalized_installer_keys = normalize_trusted_public_keys(
            trusted_installer_public_keys
        )
    except InstallerSignatureError as exc:
        raise ReleaseChecksumError(
            f"Trusted installer signing identity is invalid: {exc}"
        ) from exc

    installer = _absolute_path(installer_path)
    update_manifest = _absolute_path(update_manifest_path)
    installer_manifest = _absolute_path(installer_manifest_path)
    key_catalog = _absolute_path(key_catalog_path)
    output_directory = _absolute_path(
        installer.parent if dist_dir is None else dist_dir
    )
    _safe_directory(output_directory, label="Release artifact directory")
    if installer.parent != output_directory:
        raise ReleaseChecksumError("Final installer must be a direct child of dist")

    checksum_name = f"MioTranslator-{release_version}-SHA256SUMS.txt"
    signature_name = f"{checksum_name}.sig.json"
    checksum_path = output_directory / checksum_name
    signature_path = output_directory / signature_name
    if checksum_path in {
        installer,
        update_manifest,
        installer_manifest,
        key_catalog,
    } or signature_path in {
        installer,
        update_manifest,
        installer_manifest,
        key_catalog,
    }:
        raise ReleaseChecksumError("Release output path overlaps an input")
    _assert_safe_output_target(checksum_path)
    _assert_safe_output_target(signature_path)

    entries, hashed_files = _expected_release_inputs(
        installer,
        update_manifest,
        installer_manifest,
        key_catalog,
        release_version=release_version,
        public_key=public_key,
        key_id=normalized_key_id,
        installer_key_id=installer_key_id,
        trusted_installer_public_keys=normalized_installer_keys,
    )
    checksum_payload = _checksum_bytes(entries)
    try:
        signature_hex = sign_ed25519(
            seed_hex,
            _signature_message(
                checksum_payload,
                release_version=release_version,
                checksum_name=checksum_name,
            ),
        )
    except ManifestSignatureError as exc:
        raise ReleaseChecksumError(f"Unable to sign release checksums: {exc}") from exc
    metadata: dict[str, object] = {
        "schema_version": CHECKSUM_SCHEMA_VERSION,
        "release_version": release_version,
        "signed_file": checksum_name,
        "signed_file_size": len(checksum_payload),
        "signed_file_sha256": hashlib.sha256(checksum_payload).hexdigest(),
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_context": CHECKSUM_SIGNATURE_CONTEXT,
        "signature_key_id": normalized_key_id,
        "public_key": public_key,
        "signature": signature_hex,
    }
    signature_payload = _metadata_bytes(metadata)

    staged: list[tuple[Path, Path]] = []
    try:
        staged.append(
            (
                checksum_path,
                _write_staged_bytes(checksum_path, checksum_payload, suffix=".tmp"),
            )
        )
        staged.append(
            (
                signature_path,
                _write_staged_bytes(signature_path, signature_payload, suffix=".tmp"),
            )
        )
        staged_checksum, _checksum_snapshot = _read_regular_bytes(
            staged[0][1],
            label="Staged release checksum file",
        )
        staged_signature, _signature_snapshot = _read_regular_bytes(
            staged[1][1],
            label="Staged detached signature metadata",
        )
        if _parse_checksum_bytes(staged_checksum) != entries:
            raise ReleaseChecksumError(
                "Staged release checksum entries do not match the inputs"
            )
        _verify_detached_signature(
            staged_checksum,
            staged_signature,
            release_version=release_version,
            checksum_name=checksum_name,
            public_key=public_key,
            key_id=normalized_key_id,
        )
        _assert_inputs_unchanged(hashed_files)
        _replace_staged_artifacts(staged)
    finally:
        for _target, temporary_path in staged:
            try:
                temporary_path.unlink()
            except OSError:
                pass

    verify_release_checksum_artifacts(
        installer,
        update_manifest,
        installer_manifest,
        key_catalog,
        checksum_path,
        signature_path,
        version=release_version,
        expected_public_key=public_key,
        key_id=normalized_key_id,
        installer_key_id=installer_key_id,
        trusted_installer_public_keys=normalized_installer_keys,
    )
    return ReleaseChecksumResult(
        checksum_path=checksum_path,
        signature_path=signature_path,
        checksum_sha256=metadata["signed_file_sha256"],
        signature_key_id=normalized_key_id,
        entries=tuple(sorted(entries.items())),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or verify deterministic release checksums and detached "
            "Ed25519 signature metadata."
        )
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify existing artifacts without requiring a signing seed",
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="release version used for artifact names (default: current app version)",
    )
    parser.add_argument(
        "--checksum-path",
        type=Path,
        help="checksum artifact to verify (default: versioned file beside installer)",
    )
    parser.add_argument(
        "--signature-path",
        type=Path,
        help="signature metadata to verify (default: versioned file beside installer)",
    )
    parser.add_argument("installer", type=Path, help="final release installer")
    parser.add_argument(
        "update_manifest",
        type=Path,
        help="already-signed mio_update.json",
    )
    parser.add_argument(
        "installer_manifest",
        type=Path,
        help="already-signed installer_manifest.json",
    )
    parser.add_argument(
        "key_catalog",
        type=Path,
        help="public release_signing_keys.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        release_version = _normalize_release_version(arguments.version)
    except ReleaseChecksumError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    checksum_name = f"MioTranslator-{release_version}-SHA256SUMS.txt"
    checksum_path = (
        arguments.checksum_path or arguments.installer.parent / checksum_name
    )
    signature_path = (
        arguments.signature_path
        or arguments.installer.parent / f"{checksum_name}.sig.json"
    )
    if arguments.verify_only:
        try:
            verify_release_checksum_artifacts(
                arguments.installer,
                arguments.update_manifest,
                arguments.installer_manifest,
                arguments.key_catalog,
                checksum_path,
                signature_path,
                version=release_version,
                expected_public_key=UPDATE_MANIFEST_PUBLIC_KEY,
                key_id=UPDATE_MANIFEST_PUBLIC_KEY_ID,
                installer_key_id=INSTALLER_SIGNATURE_KEY_ID,
                trusted_installer_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
            )
        except (
            InstallerSignatureError,
            ManifestSignatureError,
            OSError,
            ReleaseChecksumError,
            ValueError,
        ) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Verified  : {checksum_path}")
        print(f"Verified  : {signature_path}")
        print("Done. Release checksum and detached signature verification passed.")
        return 0

    if arguments.checksum_path is not None or arguments.signature_path is not None:
        print(
            "ERROR: --checksum-path and --signature-path require --verify-only.",
            file=sys.stderr,
        )
        return 1
    seed_hex = (
        os.environ.get("MIO_RELEASE_SIGNING_SEED", "").strip()
        or os.environ.get("MIO_MANIFEST_SEED", "").strip()
    )
    if not seed_hex:
        print(
            "ERROR: MIO_RELEASE_SIGNING_SEED environment variable is not set.",
            file=sys.stderr,
        )
        return 1
    try:
        result = generate_release_checksum_artifacts(
            arguments.installer,
            arguments.update_manifest,
            arguments.installer_manifest,
            arguments.key_catalog,
            seed_hex,
            version=release_version,
            expected_public_key=UPDATE_MANIFEST_PUBLIC_KEY,
            key_id=UPDATE_MANIFEST_PUBLIC_KEY_ID,
            installer_key_id=INSTALLER_SIGNATURE_KEY_ID,
            trusted_installer_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
        )
    except (
        InstallerSignatureError,
        ManifestSignatureError,
        OSError,
        ReleaseChecksumError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Written   : {result.checksum_path}")
    print(f"SHA-256   : {result.checksum_sha256}")
    print(f"Key ID    : {result.signature_key_id}")
    print(f"Written   : {result.signature_path}")
    print("Done. Checksums and detached signature self-verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
