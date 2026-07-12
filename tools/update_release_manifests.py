"""Generate and atomically publish signed release manifests.

Usage:
    python tools/update_release_manifests.py [installer_exe_path]

Required environment variable:
    MIO_RELEASE_SIGNING_SEED (or legacy MIO_MANIFEST_SEED) - a 64-hex-character
    Ed25519 seed matching the public keys configured in version.py.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    ManifestSignatureError,
    public_key_from_seed,
    sign_manifest,
    verify_manifest_signature,
)
from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    InstallerSignatureError,
    normalize_trusted_public_keys,
    parse_installer_signature_metadata,
    sign_installer_metadata,
    verify_installer_metadata_signature,
)
from src.version import (
    APP_VERSION,
    INSTALLER_SIGNATURE_KEY_ID,
    TRUSTED_INSTALLER_PUBLIC_KEYS,
    UPDATE_MANIFEST_PUBLIC_KEY,
    UPDATE_MANIFEST_PUBLIC_KEY_ID,
)


RELEASE_TIMEZONE = dt.timezone(dt.timedelta(hours=9), name="JST")
_HOMEPAGE_URL = "https://78hejiu.top"
_DEFAULT_MODEL_RUNTIME_DIR = (
    r"%LOCALAPPDATA%\Mio RealTime Translator\runtime_models\iic--SenseVoiceSmall"
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_CHUNK_SIZE = 1024 * 1024
_NOTES_FIELDS = ("notes", "notes_i18n")

MANI_KEYS_ORDERED = (
    "version",
    "installer_name",
    "url",
    "installer_url",
    "size_bytes",
    "sha256",
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    "notes",
    "notes_i18n",
    "homepage_url",
    "published_at",
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    SIGNATURE_FIELD,
)

INSTALLER_MANI_KEYS_ORDERED = (
    "version",
    "installer_name",
    "installer_url",
    "size_bytes",
    "sha256",
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    "notes",
    "notes_i18n",
    "model_runtime_dir",
    "app_exe",
    "homepage_url",
    "published_at",
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    SIGNATURE_FIELD,
)


class ManifestGenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseManifestResult:
    update_path: Path
    installer_manifest_path: Path
    update_manifest: dict[str, object]
    installer_manifest: dict[str, object]
    size_bytes: int
    sha256: str
    installer_signature: str
    installer_signature_key_id: str
    published_at: str


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _regular_file_snapshot(path: Path, *, label: str) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise ManifestGenerationError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise ManifestGenerationError(f"Unable to inspect {label}: {exc}") from exc
    if _is_link_or_reparse(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise ManifestGenerationError(f"{label} must be a regular non-link file: {path}")
    return file_stat


def _same_file_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    before_identity = (getattr(before, "st_dev", 0), getattr(before, "st_ino", 0))
    after_identity = (getattr(after, "st_dev", 0), getattr(after, "st_ino", 0))
    identity_known = all(before_identity + after_identity)
    return (
        (not identity_known or before_identity == after_identity)
        and before.st_size == after.st_size
        and getattr(before, "st_mtime_ns", None)
        == getattr(after, "st_mtime_ns", None)
    )


def _sha256_file(path: Path) -> tuple[int, str]:
    before = _regular_file_snapshot(path, label="Installer")
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
                hasher.update(chunk)
    except OSError as exc:
        raise ManifestGenerationError(f"Unable to read installer: {exc}") from exc
    after = _regular_file_snapshot(path, label="Installer")
    if not _same_file_snapshot(before, after):
        raise ManifestGenerationError("Installer changed while it was being hashed")
    if after.st_size <= 0:
        raise ManifestGenerationError("Installer must not be empty")
    return int(after.st_size), hasher.hexdigest()


def _ordered_json(data: Mapping[str, object], keys: Sequence[str]) -> str:
    ordered = {key: data[key] for key in keys if key in data}
    ordered.update({key: value for key, value in data.items() if key not in ordered})
    return json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"


def _load_existing_manifest(path: Path) -> dict[str, object]:
    if not os.path.lexists(path):
        return {}
    _regular_file_snapshot(path, label="Existing release manifest")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestGenerationError(
            f"Existing release manifest is unreadable or invalid: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ManifestGenerationError(
            f"Existing release manifest must be a JSON object: {path}"
        )
    return payload


def _preserved_notes(existing: Mapping[str, object]) -> dict[str, object]:
    return {
        field: copy.deepcopy(existing[field])
        for field in _NOTES_FIELDS
        if field in existing
    }


def _published_at(now: dt.datetime | None) -> str:
    current = now if now is not None else dt.datetime.now(RELEASE_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ManifestGenerationError("Release timestamp must be timezone-aware")
    return current.astimezone(RELEASE_TIMEZONE).isoformat(timespec="seconds")


def _normalize_version(version: str) -> str:
    normalized = str(version or "").strip()
    if not normalized:
        raise ManifestGenerationError("Release version is empty")
    if not normalized.lower().startswith("v"):
        normalized = f"v{normalized}"
    return normalized


def _build_manifests(
    *,
    version: str,
    installer_name: str,
    size_bytes: int,
    sha256: str,
    installer_signature: str,
    installer_signature_key_id: str,
    published_at: str,
    seed_hex: str,
    key_id: str,
    existing_update: Mapping[str, object],
    existing_installer: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    github_url = (
        "https://github.com/CokoIya/MioVRC_Translator/releases/download"
        f"/{version}/{installer_name}"
    )

    update_manifest: dict[str, object] = {
        "version": version,
        "installer_name": installer_name,
        "url": github_url,
        "installer_url": github_url,
        "size_bytes": size_bytes,
        "sha256": sha256,
        INSTALLER_SIGNATURE_ALGORITHM_FIELD: INSTALLER_SIGNATURE_ALGORITHM,
        INSTALLER_SIGNATURE_KEY_ID_FIELD: installer_signature_key_id,
        INSTALLER_SIGNATURE_FIELD: installer_signature,
        **_preserved_notes(existing_update),
        "homepage_url": _HOMEPAGE_URL,
        "published_at": published_at,
        SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
        SIGNATURE_KEY_ID_FIELD: key_id,
    }
    update_manifest[SIGNATURE_FIELD] = sign_manifest(update_manifest, seed_hex)

    installer_manifest: dict[str, object] = {
        "version": version,
        "installer_name": installer_name,
        "installer_url": github_url,
        "size_bytes": size_bytes,
        "sha256": sha256,
        INSTALLER_SIGNATURE_ALGORITHM_FIELD: INSTALLER_SIGNATURE_ALGORITHM,
        INSTALLER_SIGNATURE_KEY_ID_FIELD: installer_signature_key_id,
        INSTALLER_SIGNATURE_FIELD: installer_signature,
        **_preserved_notes(existing_installer),
        "model_runtime_dir": existing_installer.get(
            "model_runtime_dir",
            _DEFAULT_MODEL_RUNTIME_DIR,
        ),
        "app_exe": "MioTranslator.exe",
        "homepage_url": _HOMEPAGE_URL,
        "published_at": published_at,
        SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
        SIGNATURE_KEY_ID_FIELD: key_id,
    }
    installer_manifest[SIGNATURE_FIELD] = sign_manifest(installer_manifest, seed_hex)
    return update_manifest, installer_manifest


def _write_temp_bytes(target: Path, payload: bytes, *, suffix: str) -> Path:
    parent = target.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise ManifestGenerationError(
            f"Release manifest directory is unavailable: {parent}"
        ) from exc
    if _is_link_or_reparse(parent_stat) or not stat.S_ISDIR(parent_stat.st_mode):
        raise ManifestGenerationError(
            f"Release manifest directory is unsafe: {parent}"
        )

    fd, temp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.",
        suffix=suffix,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return temp_path


def _stage_manifest(
    target: Path,
    manifest: Mapping[str, object],
    ordered_keys: Sequence[str],
    *,
    public_key: str,
    key_id: str,
    trusted_installer_public_keys,
) -> Path:
    temp_path = _write_temp_bytes(
        target,
        _ordered_json(manifest, ordered_keys).encode("utf-8"),
        suffix=".tmp",
    )
    try:
        staged = json.loads(temp_path.read_text(encoding="utf-8"))
        if staged != manifest:
            raise ManifestGenerationError(
                f"Staged release manifest changed during serialization: {target}"
            )
        verify_manifest_signature(
            staged,
            public_key,
            required=True,
            expected_key_id=key_id,
        )
        installer_metadata = parse_installer_signature_metadata(staged)
        verify_installer_metadata_signature(
            sha256=staged.get("sha256"),
            size_bytes=staged.get("size_bytes"),
            signature=installer_metadata.signature,
            signature_algorithm=installer_metadata.algorithm,
            signature_key_id=installer_metadata.key_id,
            trusted_public_keys=trusted_installer_public_keys,
        )
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return temp_path


def _fsync_directories(paths: Sequence[Path]) -> None:
    if os.name == "nt":
        return
    for directory in dict.fromkeys(path.parent for path in paths):
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace_with_transient_retry(source: Path, destination: Path) -> None:
    """Retry only Windows sharing/access races, never semantic failures."""

    retry_delays = (0.02, 0.05, 0.1, 0.2)
    for attempt in range(len(retry_delays) + 1):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            transient_windows_error = os.name == "nt" and getattr(
                exc,
                "winerror",
                None,
            ) in {5, 32, 33}
            if not transient_windows_error or attempt >= len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])


def _replace_staged_manifests(staged: Sequence[tuple[Path, Path]]) -> None:
    backups: dict[Path, Path | None] = {}
    replaced: list[Path] = []
    try:
        for target, _temp_path in staged:
            if os.path.lexists(target):
                _regular_file_snapshot(target, label="Existing release manifest")
                backups[target] = _write_temp_bytes(
                    target,
                    target.read_bytes(),
                    suffix=".bak",
                )
            else:
                backups[target] = None

        try:
            for target, temp_path in staged:
                _replace_with_transient_retry(temp_path, target)
                replaced.append(target)
            _fsync_directories([target for target, _temp_path in staged])
        except Exception as replace_error:
            rollback_errors: list[str] = []
            for target in reversed(replaced):
                backup = backups.get(target)
                try:
                    if backup is None:
                        target.unlink(missing_ok=True)
                    else:
                        _replace_with_transient_retry(backup, target)
                        backups[target] = None
                except OSError as rollback_error:
                    rollback_errors.append(f"{target}: {rollback_error}")
            if rollback_errors:
                raise ManifestGenerationError(
                    "Release manifest replacement failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from replace_error
            raise
    finally:
        for _target, temp_path in staged:
            try:
                temp_path.unlink()
            except OSError:
                pass
        for backup in backups.values():
            if backup is None:
                continue
            try:
                backup.unlink()
            except OSError:
                pass


def update_release_manifests(
    installer_path: Path,
    seed_hex: str,
    *,
    root: Path = ROOT,
    version: str = APP_VERSION,
    now: dt.datetime | None = None,
    expected_public_key: str = UPDATE_MANIFEST_PUBLIC_KEY,
    key_id: str = UPDATE_MANIFEST_PUBLIC_KEY_ID,
    installer_key_id: str = INSTALLER_SIGNATURE_KEY_ID,
    trusted_installer_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
) -> ReleaseManifestResult:
    release_version = _normalize_version(version)
    public_key = public_key_from_seed(seed_hex)
    if public_key != str(expected_public_key or "").strip().lower():
        raise ManifestGenerationError(
            "Release signing seed does not match UPDATE_MANIFEST_PUBLIC_KEY"
        )
    normalized_installer_keys = normalize_trusted_public_keys(
        trusted_installer_public_keys
    )
    trusted_installer_key = normalized_installer_keys.get(installer_key_id)
    if trusted_installer_key is None:
        raise ManifestGenerationError(
            "INSTALLER_SIGNATURE_KEY_ID is not present in "
            "TRUSTED_INSTALLER_PUBLIC_KEYS"
        )
    if public_key != trusted_installer_key:
        raise ManifestGenerationError(
            "Release signing seed does not match the configured installer public key"
        )

    installer_path = Path(installer_path)
    size_bytes, sha256 = _sha256_file(installer_path)
    installer_signature = sign_installer_metadata(
        seed_hex,
        sha256=sha256,
        size_bytes=size_bytes,
    )
    published_at = _published_at(now)
    installer_name = f"MioTranslator-Setup-{release_version}.exe"

    update_path = Path(root) / "mio_update.json"
    installer_manifest_path = Path(root) / "docs" / "installer_manifest.json"
    existing_update = _load_existing_manifest(update_path)
    existing_installer = _load_existing_manifest(installer_manifest_path)
    update_manifest, installer_manifest = _build_manifests(
        version=release_version,
        installer_name=installer_name,
        size_bytes=size_bytes,
        sha256=sha256,
        installer_signature=installer_signature,
        installer_signature_key_id=installer_key_id,
        published_at=published_at,
        seed_hex=seed_hex,
        key_id=key_id,
        existing_update=existing_update,
        existing_installer=existing_installer,
    )

    staged: list[tuple[Path, Path]] = []
    try:
        staged.append(
            (
                update_path,
                _stage_manifest(
                    update_path,
                    update_manifest,
                    MANI_KEYS_ORDERED,
                    public_key=public_key,
                    key_id=key_id,
                    trusted_installer_public_keys=normalized_installer_keys,
                ),
            )
        )
        staged.append(
            (
                installer_manifest_path,
                _stage_manifest(
                    installer_manifest_path,
                    installer_manifest,
                    INSTALLER_MANI_KEYS_ORDERED,
                    public_key=public_key,
                    key_id=key_id,
                    trusted_installer_public_keys=normalized_installer_keys,
                ),
            )
        )
        _replace_staged_manifests(staged)
    finally:
        for _target, temp_path in staged:
            try:
                temp_path.unlink()
            except OSError:
                pass

    return ReleaseManifestResult(
        update_path=update_path,
        installer_manifest_path=installer_manifest_path,
        update_manifest=update_manifest,
        installer_manifest=installer_manifest,
        size_bytes=size_bytes,
        sha256=sha256,
        installer_signature=installer_signature,
        installer_signature_key_id=installer_key_id,
        published_at=published_at,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
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

    installer_path = (
        Path(arguments[0])
        if arguments
        else ROOT / "dist" / f"MioTranslator-Setup-v{APP_VERSION}.exe"
    )
    try:
        result = update_release_manifests(installer_path, seed_hex)
    except (
        ManifestGenerationError,
        ManifestSignatureError,
        InstallerSignatureError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Installer : {installer_path}")
    print(f"Size      : {result.size_bytes:,} bytes")
    print(f"SHA-256   : {result.sha256}")
    print(f"Key ID    : {result.installer_signature_key_id}")
    print(f"Written   : {result.update_path}")
    print(f"Written   : {result.installer_manifest_path}")
    print("Done. Both staged signatures were verified before atomic replacement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
