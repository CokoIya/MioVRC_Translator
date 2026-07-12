# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType

try:
    from cryptography.exceptions import InvalidSignature as _InvalidSignature
    from cryptography.hazmat.primitives import serialization as _serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey as _Ed25519PrivateKey,
        Ed25519PublicKey as _Ed25519PublicKey,
    )
except (ImportError, OSError) as exc:  # pragma: no cover - import failure path
    _CRYPTOGRAPHY_IMPORT_ERROR: BaseException | None = exc
    _InvalidSignature = None
    _serialization = None
    _Ed25519PrivateKey = None
    _Ed25519PublicKey = None
else:
    _CRYPTOGRAPHY_IMPORT_ERROR = None


SIGNATURE_FIELD = "signature"
SIGNATURE_ALGORITHM_FIELD = "signature_algorithm"
SIGNATURE_KEY_ID_FIELD = "signature_key_id"
SIGNATURE_ALGORITHM = "ed25519"
_IGNORED_SIGNATURE_FIELDS = {
    SIGNATURE_FIELD,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_KEY_ID_FIELD,
}
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ManifestSignatureError(RuntimeError):
    pass


def _require_cryptography() -> None:
    if (
        _CRYPTOGRAPHY_IMPORT_ERROR is not None
        or _serialization is None
        or _Ed25519PrivateKey is None
        or _Ed25519PublicKey is None
        or _InvalidSignature is None
    ):
        raise ManifestSignatureError(
            "Ed25519 support is unavailable because cryptography could not be imported"
        ) from _CRYPTOGRAPHY_IMPORT_ERROR


def _decode_hex_bytes(value: str, *, expected_len: int, label: str) -> bytes:
    text = str(value or "").strip().lower()
    if text.startswith("ed25519:"):
        text = text.split(":", 1)[1].strip()
    text = text.replace(" ", "")
    if not re.fullmatch(r"[0-9a-f]+", text or ""):
        raise ManifestSignatureError(f"{label} must be hexadecimal")
    data = bytes.fromhex(text)
    if len(data) != expected_len:
        raise ManifestSignatureError(f"{label} must be {expected_len} bytes")
    return data


def _normalize_signature_key_id(value: object) -> str:
    key_id = str(value or "").strip()
    if not _KEY_ID_RE.fullmatch(key_id):
        raise ManifestSignatureError("Update manifest signature key id is invalid")
    return key_id


def normalize_trusted_manifest_public_keys(
    values: Mapping[object, object] | Iterable[tuple[object, object]],
) -> Mapping[str, str]:
    """Return an immutable key-id to Ed25519 public-key allowlist.

    Accepting more than one key permits a safe overlap release before the
    active signing key is rotated. Conflicting duplicate key IDs fail closed.
    """

    items = values.items() if isinstance(values, Mapping) else values
    try:
        iterator = iter(items)
    except TypeError as exc:
        raise ManifestSignatureError(
            "Trusted update manifest public-key configuration is invalid"
        ) from exc

    normalized: dict[str, str] = {}
    for item in iterator:
        try:
            raw_key_id, raw_public_key = item
        except (TypeError, ValueError) as exc:
            raise ManifestSignatureError(
                "Trusted update manifest public-key entries must contain a key id and key"
            ) from exc
        key_id = _normalize_signature_key_id(raw_key_id)
        public_key = _decode_hex_bytes(
            str(raw_public_key or ""),
            expected_len=32,
            label="Ed25519 public key",
        ).hex()
        previous = normalized.get(key_id)
        if previous is not None and previous != public_key:
            raise ManifestSignatureError(
                f"Trusted update manifest key id {key_id!r} is configured more than once"
            )
        normalized[key_id] = public_key

    if not normalized:
        raise ManifestSignatureError(
            "No trusted update manifest public keys are configured; updates are blocked"
        )
    return MappingProxyType(normalized)


def canonical_manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    payload = {
        str(key): value
        for key, value in manifest.items()
        if str(key) not in _IGNORED_SIGNATURE_FIELDS
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def public_key_from_seed(seed_hex: str) -> str:
    _require_cryptography()
    seed = _decode_hex_bytes(seed_hex, expected_len=32, label="Ed25519 seed")
    try:
        private_key = _Ed25519PrivateKey.from_private_bytes(seed)
        public_key = private_key.public_key().public_bytes(
            encoding=_serialization.Encoding.Raw,
            format=_serialization.PublicFormat.Raw,
        )
    except Exception as exc:
        raise ManifestSignatureError(f"Unable to derive Ed25519 public key: {exc}") from exc
    return public_key.hex()


def generate_seed_hex() -> str:
    return os.urandom(32).hex()


def sign_ed25519(seed_hex: str, message: bytes) -> str:
    """Sign an arbitrary byte string with a raw 32-byte Ed25519 seed."""
    _require_cryptography()
    seed = _decode_hex_bytes(seed_hex, expected_len=32, label="Ed25519 seed")
    try:
        private_key = _Ed25519PrivateKey.from_private_bytes(seed)
        signature = private_key.sign(bytes(message))
    except ManifestSignatureError:
        raise
    except Exception as exc:
        raise ManifestSignatureError(f"Unable to create Ed25519 signature: {exc}") from exc
    return signature.hex()


def sign_manifest(manifest: Mapping[str, object], seed_hex: str) -> str:
    return sign_ed25519(seed_hex, canonical_manifest_bytes(manifest))


def verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    _require_cryptography()
    public_key = _decode_hex_bytes(
        public_key_hex,
        expected_len=32,
        label="Ed25519 public key",
    )
    signature = _decode_hex_bytes(
        signature_hex,
        expected_len=64,
        label="Ed25519 signature",
    )
    try:
        verifier = _Ed25519PublicKey.from_public_bytes(public_key)
        verifier.verify(signature, bytes(message))
    except Exception as exc:
        if _InvalidSignature is not None and isinstance(exc, _InvalidSignature):
            return False
        raise ManifestSignatureError(f"Ed25519 verification failed: {exc}") from exc
    return True


def verify_manifest_signature(
    manifest: Mapping[str, object],
    public_key_hex: str,
    *,
    required: bool = False,
    expected_key_id: str = "",
) -> bool:
    public_key = str(public_key_hex or "").strip()
    if not public_key:
        if required:
            raise ManifestSignatureError("Update manifest public key is not configured")
        return False

    signature = str(manifest.get(SIGNATURE_FIELD, "") or "").strip()
    if not signature:
        if required:
            raise ManifestSignatureError("Update manifest signature is missing")
        return False

    expected_key_id = str(expected_key_id or "").strip()
    if expected_key_id:
        key_id = str(manifest.get(SIGNATURE_KEY_ID_FIELD, "") or "").strip()
        if key_id != expected_key_id:
            raise ManifestSignatureError("Update manifest signature key id is not trusted")

    algorithm = str(
        manifest.get(SIGNATURE_ALGORITHM_FIELD, SIGNATURE_ALGORITHM) or ""
    ).strip().lower()
    if algorithm != SIGNATURE_ALGORITHM:
        raise ManifestSignatureError("Update manifest signature algorithm is not supported")

    try:
        verified = verify_ed25519(
            public_key,
            signature,
            canonical_manifest_bytes(manifest),
        )
    except ManifestSignatureError:
        raise
    except Exception as exc:
        raise ManifestSignatureError(
            f"Update manifest signature verification failed: {exc}"
        ) from exc

    if not verified:
        raise ManifestSignatureError("Update manifest signature is invalid")
    return True


def verify_manifest_signature_with_trusted_keys(
    manifest: Mapping[str, object],
    trusted_public_keys: Mapping[object, object]
    | Iterable[tuple[object, object]],
    *,
    required: bool = False,
) -> str | None:
    """Verify a manifest by selecting its public key from a trusted allowlist."""

    signature = str(manifest.get(SIGNATURE_FIELD, "") or "").strip()
    if not signature:
        if required:
            raise ManifestSignatureError("Update manifest signature is missing")
        return None

    key_id = _normalize_signature_key_id(manifest.get(SIGNATURE_KEY_ID_FIELD))
    trusted = normalize_trusted_manifest_public_keys(trusted_public_keys)
    public_key = trusted.get(key_id)
    if public_key is None:
        raise ManifestSignatureError(
            "Update manifest signature key id is not trusted"
        )
    verify_manifest_signature(
        manifest,
        public_key,
        required=True,
        expected_key_id=key_id,
    )
    return key_id
