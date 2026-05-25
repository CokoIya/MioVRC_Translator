from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping

SIGNATURE_FIELD = "signature"
SIGNATURE_ALGORITHM_FIELD = "signature_algorithm"
SIGNATURE_KEY_ID_FIELD = "signature_key_id"
SIGNATURE_ALGORITHM = "ed25519"
_IGNORED_SIGNATURE_FIELDS = {
    SIGNATURE_FIELD,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_KEY_ID_FIELD,
}

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


class ManifestSignatureError(RuntimeError):
    pass


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x & 1:
        x = _P - x
    return x


_BY = (4 * pow(5, _P - 2, _P)) % _P
_B = (_xrecover(_BY), _BY)
_IDENTITY = (0, 1)


def _point_add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    common = _D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * pow(1 + common, _P - 2, _P)
    y3 = (y1 * y2 + x1 * x2) * pow(1 - common, _P - 2, _P)
    return x3 % _P, y3 % _P


def _point_mul(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _is_on_curve(point: tuple[int, int]) -> bool:
    x, y = point
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _P == 0


def _decode_point(raw: bytes) -> tuple[int, int]:
    if len(raw) != 32:
        raise ManifestSignatureError("Ed25519 point must be 32 bytes")
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if (x & 1) != (raw[31] >> 7):
        x = _P - x
    point = (x, y)
    if not _is_on_curve(point):
        raise ManifestSignatureError("Ed25519 point is not on curve")
    return point


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    value = y | ((x & 1) << 255)
    return value.to_bytes(32, "little")


def _clamp_scalar(raw: bytes) -> int:
    data = bytearray(raw[:32])
    data[0] &= 248
    data[31] &= 63
    data[31] |= 64
    return int.from_bytes(data, "little")


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
    seed = _decode_hex_bytes(seed_hex, expected_len=32, label="Ed25519 seed")
    digest = hashlib.sha512(seed).digest()
    scalar = _clamp_scalar(digest)
    return _encode_point(_point_mul(_B, scalar)).hex()


def generate_seed_hex() -> str:
    return os.urandom(32).hex()


def sign_manifest(manifest: Mapping[str, object], seed_hex: str) -> str:
    seed = _decode_hex_bytes(seed_hex, expected_len=32, label="Ed25519 seed")
    public_key = bytes.fromhex(public_key_from_seed(seed.hex()))
    digest = hashlib.sha512(seed).digest()
    scalar = _clamp_scalar(digest)
    prefix = digest[32:]
    message = canonical_manifest_bytes(manifest)
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    encoded_r = _encode_point(_point_mul(_B, r))
    k = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _L
    s = (r + k * scalar) % _L
    return (encoded_r + s.to_bytes(32, "little")).hex()


def verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
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
    encoded_r = signature[:32]
    encoded_s = signature[32:]
    s = int.from_bytes(encoded_s, "little")
    if s >= _L:
        return False
    public_point = _decode_point(public_key)
    r_point = _decode_point(encoded_r)
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public_key + message).digest(),
        "little",
    ) % _L
    left = _point_mul(_B, s)
    right = _point_add(r_point, _point_mul(public_point, challenge))
    return left == right


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

    algorithm = str(manifest.get(SIGNATURE_ALGORITHM_FIELD, SIGNATURE_ALGORITHM) or "").strip().lower()
    if algorithm != SIGNATURE_ALGORITHM:
        raise ManifestSignatureError("Update manifest signature algorithm is not supported")

    try:
        verified = verify_ed25519(public_key, signature, canonical_manifest_bytes(manifest))
    except ManifestSignatureError:
        raise
    except Exception as exc:
        raise ManifestSignatureError(f"Update manifest signature verification failed: {exc}") from exc

    if not verified:
        raise ManifestSignatureError("Update manifest signature is invalid")
    return True
