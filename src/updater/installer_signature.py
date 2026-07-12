# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from src.updater.manifest_signature import (
    ManifestSignatureError,
    sign_ed25519,
    verify_ed25519,
)


INSTALLER_SIGNATURE_FIELD = "installer_signature"
INSTALLER_SIGNATURE_ALGORITHM_FIELD = "installer_signature_algorithm"
INSTALLER_SIGNATURE_KEY_ID_FIELD = "installer_signature_key_id"
INSTALLER_SIGNATURE_ALGORITHM = "ed25519"

_SIGNATURE_CONTEXT = b"Mio RealTime Translator installer metadata v1\x00"
_HEX_32_RE = re.compile(r"[0-9a-f]{64}")
_HEX_64_RE = re.compile(r"[0-9a-f]{128}")
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_SIGNED_SIZE = (1 << 63) - 1


class InstallerSignatureError(RuntimeError):
    """Raised when installer metadata is not authenticated by a trusted key."""


@dataclass(frozen=True, slots=True)
class InstallerSignatureMetadata:
    algorithm: str
    key_id: str
    signature: str


def normalize_installer_sha256(value: object) -> str:
    digest = str(value or "").strip().lower()
    if not _HEX_32_RE.fullmatch(digest):
        raise InstallerSignatureError("A valid installer SHA256 digest is required")
    return digest


def normalize_installer_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise InstallerSignatureError("Installer size metadata is invalid") from exc
    if size <= 0 or size > _MAX_SIGNED_SIZE:
        raise InstallerSignatureError("Installer size metadata is invalid")
    return size


def normalize_signature_key_id(value: object) -> str:
    key_id = str(value or "").strip()
    if not _KEY_ID_RE.fullmatch(key_id):
        raise InstallerSignatureError("Installer signature key id is invalid")
    return key_id


def normalize_installer_signature(value: object) -> str:
    signature = str(value or "").strip().lower()
    if signature.startswith("ed25519:"):
        signature = signature.split(":", 1)[1].strip()
    signature = signature.replace(" ", "")
    if not _HEX_64_RE.fullmatch(signature):
        raise InstallerSignatureError("Installer Ed25519 signature is invalid")
    return signature


def normalize_installer_signature_algorithm(value: object) -> str:
    algorithm = str(value or "").strip().lower()
    if algorithm != INSTALLER_SIGNATURE_ALGORITHM:
        raise InstallerSignatureError(
            "Installer signature algorithm is not supported"
        )
    return algorithm


def _normalize_public_key(value: object) -> str:
    public_key = str(value or "").strip().lower()
    if public_key.startswith("ed25519:"):
        public_key = public_key.split(":", 1)[1].strip()
    public_key = public_key.replace(" ", "")
    if not _HEX_32_RE.fullmatch(public_key):
        raise InstallerSignatureError("Trusted installer public key is invalid")
    return public_key


def normalize_trusted_public_keys(
    values: Mapping[object, object] | Iterable[tuple[object, object]],
) -> Mapping[str, str]:
    """Normalize an immutable key-id to Ed25519 public-key allowlist."""

    items = values.items() if isinstance(values, Mapping) else values
    normalized: dict[str, str] = {}
    try:
        iterator = iter(items)
    except TypeError as exc:
        raise InstallerSignatureError(
            "Trusted installer public-key configuration is invalid"
        ) from exc

    for item in iterator:
        try:
            raw_key_id, raw_public_key = item
        except (TypeError, ValueError) as exc:
            raise InstallerSignatureError(
                "Trusted installer public-key entries must contain a key id and key"
            ) from exc
        key_id = normalize_signature_key_id(raw_key_id)
        public_key = _normalize_public_key(raw_public_key)
        previous = normalized.get(key_id)
        if previous is not None and previous != public_key:
            raise InstallerSignatureError(
                f"Trusted installer key id {key_id!r} is configured more than once"
            )
        normalized[key_id] = public_key

    if not normalized:
        raise InstallerSignatureError(
            "No trusted installer public keys are configured; updates are blocked"
        )
    return MappingProxyType(normalized)


def installer_signature_message(*, sha256: object, size_bytes: object) -> bytes:
    """Build the unambiguous, domain-separated bytes signed for an installer."""

    digest = normalize_installer_sha256(sha256)
    size = normalize_installer_size(size_bytes)
    return _SIGNATURE_CONTEXT + size.to_bytes(8, "big") + bytes.fromhex(digest)


def sign_installer_metadata(
    seed_hex: str,
    *,
    sha256: object,
    size_bytes: object,
) -> str:
    try:
        return sign_ed25519(
            seed_hex,
            installer_signature_message(sha256=sha256, size_bytes=size_bytes),
        )
    except ManifestSignatureError as exc:
        raise InstallerSignatureError(
            f"Unable to sign installer metadata: {exc}"
        ) from exc


def parse_installer_signature_metadata(
    manifest: Mapping[str, object],
) -> InstallerSignatureMetadata:
    return InstallerSignatureMetadata(
        algorithm=normalize_installer_signature_algorithm(
            manifest.get(INSTALLER_SIGNATURE_ALGORITHM_FIELD)
        ),
        key_id=normalize_signature_key_id(
            manifest.get(INSTALLER_SIGNATURE_KEY_ID_FIELD)
        ),
        signature=normalize_installer_signature(
            manifest.get(INSTALLER_SIGNATURE_FIELD)
        ),
    )


def verify_installer_metadata_signature(
    *,
    sha256: object,
    size_bytes: object,
    signature: object,
    signature_algorithm: object,
    signature_key_id: object,
    trusted_public_keys: Mapping[object, object]
    | Iterable[tuple[object, object]],
) -> str:
    normalize_installer_signature_algorithm(signature_algorithm)
    key_id = normalize_signature_key_id(signature_key_id)
    normalized_signature = normalize_installer_signature(signature)
    trusted = normalize_trusted_public_keys(trusted_public_keys)
    public_key = trusted.get(key_id)
    if public_key is None:
        raise InstallerSignatureError(
            "Installer signature key id is not trusted"
        )

    message = installer_signature_message(sha256=sha256, size_bytes=size_bytes)
    try:
        verified = verify_ed25519(public_key, normalized_signature, message)
    except ManifestSignatureError as exc:
        raise InstallerSignatureError(
            f"Installer Ed25519 verification failed: {exc}"
        ) from exc
    if not verified:
        raise InstallerSignatureError("Installer Ed25519 signature is invalid")
    return key_id
