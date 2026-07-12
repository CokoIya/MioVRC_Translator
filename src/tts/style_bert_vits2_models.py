"""Utilities for plugin-managed Style-Bert-VITS2 model folders."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.utils.app_paths import (
    atomic_copy_secure_file,
    atomic_write_text,
    read_secure_text,
    require_existing_real_directory,
    require_real_directory,
    resource_base_dirs,
    secure_file_path,
    secure_unlink,
    writable_app_dir,
)

logger = logging.getLogger(__name__)

MODEL_FILE_SUFFIXES = {".safetensors"}
_PICKLE_MODEL_FILE_SUFFIXES = {".pth", ".pt"}
_UNSUPPORTED_MODEL_FILE_SUFFIXES = {".onnx"}
VOICE_ID_SEPARATOR = " :: "

_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_MODEL_FILE_BYTES = 8 * 1024 * 1024 * 1024
_MAX_MODEL_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MODEL_ENTRIES = 512
_MAX_MODEL_DEPTH = 16
_MAX_PRESET_CATALOG_BYTES = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_IMPORT_LOCK = threading.RLock()


class StyleBertVits2ModelError(RuntimeError):
    """Raised when a custom Style-Bert-VITS2 model folder is invalid."""


@dataclass(frozen=True)
class StyleBertVits2ModelInfo:
    """Validated metadata for one imported Style-Bert-VITS2 model."""

    name: str
    directory: Path
    config_path: Path
    style_vectors_path: Path
    model_path: Path
    speakers: tuple[str, ...]
    styles: tuple[str, ...]


@dataclass(frozen=True)
class StyleBertVits2Preset:
    """Friendly catalog entry for a known custom voice layout."""

    key: str
    title: str
    model_path: str
    speaker_id: str
    language: str


@dataclass(frozen=True)
class StyleBertVits2ModelBundle:
    """Download metadata for one shared Hololive model bundle."""

    model_path: str
    files: tuple[str, ...]
    revision: str
    file_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _TreeEntry:
    relative_path: str
    kind: str
    size: int
    mode_type: int
    device: int
    inode: int
    links: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _TreeSnapshot:
    entries: tuple[_TreeEntry, ...]
    file_count: int
    total_bytes: int


def style_bert_models_dir() -> Path:
    """Return the plugin-managed directory for imported custom voices."""
    return require_real_directory(
        writable_app_dir() / "tts_models" / "style_bert_vits2"
    )


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _private_regular_file(file_stat: os.stat_result) -> bool:
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not _is_link_or_reparse(file_stat)
        and int(getattr(file_stat, "st_nlink", 1) or 1) == 1
    )


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    if stat.S_IFMT(first.st_mode) != stat.S_IFMT(second.st_mode):
        return False
    first_id = (
        int(getattr(first, "st_dev", 0)),
        int(getattr(first, "st_ino", 0)),
    )
    second_id = (
        int(getattr(second, "st_dev", 0)),
        int(getattr(second, "st_ino", 0)),
    )
    if first_id[1] and second_id[1]:
        return first_id == second_id
    return _is_link_or_reparse(first) == _is_link_or_reparse(second)


def _same_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _same_identity(first, second)
        and int(first.st_size) == int(second.st_size)
        and int(getattr(first, "st_nlink", 1) or 1)
        == int(getattr(second, "st_nlink", 1) or 1)
        and int(getattr(first, "st_mtime_ns", 0))
        == int(getattr(second, "st_mtime_ns", 0))
        and int(getattr(first, "st_ctime_ns", 0))
        == int(getattr(second, "st_ctime_ns", 0))
    )


def _same_directory_identity(first: os.stat_result, second: os.stat_result) -> bool:
    """Compare directory identity without unstable Windows timestamps.

    NTFS directory timestamps can be committed lazily.  A ``DirEntry.stat``
    snapshot may therefore differ from an immediate ``lstat`` even though the
    directory object was not replaced.  Replacement protection only needs the
    object identity here; every child is validated and captured separately.
    """

    return (
        stat.S_ISDIR(first.st_mode)
        and stat.S_ISDIR(second.st_mode)
        and not _is_link_or_reparse(first)
        and not _is_link_or_reparse(second)
        and _same_identity(first, second)
    )


def _relative_text(path: Path) -> str:
    return path.as_posix()


def _snapshot_entry(
    relative_path: Path,
    kind: str,
    file_stat: os.stat_result,
) -> _TreeEntry:
    return _TreeEntry(
        relative_path=_relative_text(relative_path),
        kind=kind,
        size=int(file_stat.st_size) if kind == "file" else 0,
        mode_type=stat.S_IFMT(file_stat.st_mode),
        device=int(getattr(file_stat, "st_dev", 0)),
        inode=int(getattr(file_stat, "st_ino", 0)),
        links=int(getattr(file_stat, "st_nlink", 1) or 1),
        modified_ns=int(getattr(file_stat, "st_mtime_ns", 0)),
        changed_ns=int(getattr(file_stat, "st_ctime_ns", 0)),
    )


def _scan_model_tree(model_dir: Path) -> tuple[Path, _TreeSnapshot]:
    try:
        root = require_existing_real_directory(Path(model_dir).expanduser())
    except (OSError, RuntimeError, ValueError) as exc:
        raise StyleBertVits2ModelError(
            "The selected path is not a safe folder."
        ) from exc

    entries: list[_TreeEntry] = []
    file_count = 0
    total_bytes = 0

    def _walk(directory: Path, relative_parent: Path, depth: int) -> None:
        nonlocal file_count, total_bytes
        try:
            with os.scandir(directory) as iterator:
                children = sorted(list(iterator), key=lambda item: item.name.lower())
        except OSError as exc:
            raise StyleBertVits2ModelError(
                f"Model folder could not be scanned: {directory.name}"
            ) from exc

        for child in children:
            relative = relative_parent / child.name
            if depth > _MAX_MODEL_DEPTH:
                raise StyleBertVits2ModelError(
                    f"Model folder exceeds the maximum depth of {_MAX_MODEL_DEPTH}."
                )
            if len(entries) >= _MAX_MODEL_ENTRIES:
                raise StyleBertVits2ModelError(
                    f"Model folder contains more than {_MAX_MODEL_ENTRIES} entries."
                )
            try:
                first_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise StyleBertVits2ModelError(
                    f"Model entry could not be inspected: {relative}"
                ) from exc
            if _is_link_or_reparse(first_stat):
                raise StyleBertVits2ModelError(
                    f"Linked model entries are not allowed: {relative}"
                )

            child_path = directory / child.name
            if stat.S_ISDIR(first_stat.st_mode):
                try:
                    validated_child = require_existing_real_directory(child_path)
                    current_stat = os.lstat(validated_child)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise StyleBertVits2ModelError(
                        f"Unsafe model directory entry: {relative}"
                    ) from exc
                if not _same_directory_identity(first_stat, current_stat):
                    raise StyleBertVits2ModelError(
                        f"Model directory changed while being inspected: {relative}"
                    )
                entries.append(_snapshot_entry(relative, "directory", current_stat))
                _walk(validated_child, relative, depth + 1)
                continue

            if not _private_regular_file(first_stat):
                raise StyleBertVits2ModelError(
                    f"Only private regular files are allowed: {relative}"
                )
            try:
                validated_file = secure_file_path(child_path, must_exist=True)
                current_stat = os.lstat(validated_file)
            except (OSError, RuntimeError, ValueError) as exc:
                raise StyleBertVits2ModelError(
                    f"Unsafe model file: {relative}"
                ) from exc
            if not _same_snapshot(first_stat, current_stat):
                raise StyleBertVits2ModelError(
                    f"Model file changed while being inspected: {relative}"
                )

            size = int(current_stat.st_size)
            if size > _MAX_MODEL_FILE_BYTES:
                raise StyleBertVits2ModelError(
                    f"Model file exceeds the 8 GiB limit: {relative}"
                )
            if relative == Path("config.json") and size > _MAX_CONFIG_BYTES:
                raise StyleBertVits2ModelError("config.json exceeds the 4 MiB limit.")
            file_count += 1
            total_bytes += size
            if total_bytes > _MAX_MODEL_TOTAL_BYTES:
                raise StyleBertVits2ModelError(
                    "Model folder exceeds the 16 GiB total size limit."
                )
            entries.append(_snapshot_entry(relative, "file", current_stat))

    _walk(root, Path(), 1)
    entries.sort(key=lambda item: item.relative_path)
    return root, _TreeSnapshot(tuple(entries), file_count, total_bytes)


def _entry_map(snapshot: _TreeSnapshot) -> dict[str, _TreeEntry]:
    return {entry.relative_path: entry for entry in snapshot.entries}


def _inspect_from_snapshot(
    directory: Path,
    snapshot: _TreeSnapshot,
) -> StyleBertVits2ModelInfo:
    entries = _entry_map(snapshot)
    config_entry = entries.get("config.json")
    vectors_entry = entries.get("style_vectors.npy")
    if config_entry is None or config_entry.kind != "file":
        raise StyleBertVits2ModelError("Missing config.json.")
    if vectors_entry is None or vectors_entry.kind != "file":
        raise StyleBertVits2ModelError("Missing style_vectors.npy.")

    top_level_files = [
        entry
        for entry in snapshot.entries
        if entry.kind == "file"
        and "/" not in entry.relative_path
        and not Path(entry.relative_path).name.startswith(".")
    ]
    pickle_weights = [
        entry
        for entry in top_level_files
        if Path(entry.relative_path).suffix.lower() in _PICKLE_MODEL_FILE_SUFFIXES
    ]
    if pickle_weights:
        names = ", ".join(entry.relative_path for entry in pickle_weights)
        raise StyleBertVits2ModelError(
            "Unsafe pickle-backed model weights are not accepted "
            f"({names}). Convert the model to .safetensors before importing it."
        )
    unsupported_weights = [
        entry
        for entry in top_level_files
        if Path(entry.relative_path).suffix.lower() in _UNSUPPORTED_MODEL_FILE_SUFFIXES
    ]
    if unsupported_weights:
        names = ", ".join(entry.relative_path for entry in unsupported_weights)
        raise StyleBertVits2ModelError(
            f"Unsupported Style-Bert-VITS2 model weights ({names}); "
            "import a .safetensors model instead."
        )
    model_entries = [
        entry
        for entry in top_level_files
        if Path(entry.relative_path).suffix.lower() in MODEL_FILE_SUFFIXES
    ]
    model_entries.sort(
        key=lambda entry: (entry.modified_ns, entry.relative_path.lower()),
        reverse=True,
    )
    if not model_entries:
        raise StyleBertVits2ModelError(
            "Missing safe model weights (.safetensors)."
        )

    config_path = secure_file_path(directory / "config.json", must_exist=True)
    style_vectors_path = secure_file_path(
        directory / "style_vectors.npy",
        must_exist=True,
    )
    model_path = secure_file_path(
        directory / model_entries[0].relative_path,
        must_exist=True,
    )
    try:
        raw_config = json.loads(
            read_secure_text(
                config_path,
                encoding="utf-8",
                max_bytes=_MAX_CONFIG_BYTES,
            )
        )
    except Exception as exc:
        raise StyleBertVits2ModelError(
            f"config.json could not be read: {exc}"
        ) from exc

    data = raw_config.get("data") if isinstance(raw_config, dict) else None
    if not isinstance(data, dict):
        raise StyleBertVits2ModelError(
            "config.json does not contain a valid data block."
        )

    speakers = _dict_keys_as_tuple(data.get("spk2id"))
    styles = _dict_keys_as_tuple(data.get("style2id"))
    if not speakers:
        raise StyleBertVits2ModelError("No speakers were found in config.json.")
    if not styles:
        raise StyleBertVits2ModelError("No styles were found in config.json.")

    return StyleBertVits2ModelInfo(
        name=directory.name,
        directory=directory,
        config_path=config_path,
        style_vectors_path=style_vectors_path,
        model_path=model_path,
        speakers=speakers,
        styles=styles,
    )


def inspect_style_bert_model_dir(model_dir: Path) -> StyleBertVits2ModelInfo:
    """Validate and summarize one Style-Bert-VITS2 model directory."""
    directory, snapshot = _scan_model_tree(model_dir)
    return _inspect_from_snapshot(directory, snapshot)


def _safe_direct_child_directories(root: Path) -> list[Path]:
    try:
        directory = require_existing_real_directory(root)
        with os.scandir(directory) as iterator:
            children = sorted(list(iterator), key=lambda item: item.name.lower())
    except (OSError, RuntimeError, ValueError) as exc:
        raise StyleBertVits2ModelError(
            "The selected model root could not be scanned."
        ) from exc

    result: list[Path] = []
    for child in children:
        try:
            child_stat = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise StyleBertVits2ModelError(
                f"Model root entry could not be inspected: {child.name}"
            ) from exc
        if _is_link_or_reparse(child_stat):
            raise StyleBertVits2ModelError(
                f"Linked model root entries are not allowed: {child.name}"
            )
        child_path = directory / child.name
        if stat.S_ISDIR(child_stat.st_mode):
            try:
                result.append(require_existing_real_directory(child_path))
            except (OSError, RuntimeError, ValueError) as exc:
                raise StyleBertVits2ModelError(
                    f"Unsafe model root directory: {child.name}"
                ) from exc
        elif not _private_regular_file(child_stat):
            raise StyleBertVits2ModelError(
                f"Unsafe model root file: {child.name}"
            )
        else:
            try:
                secure_file_path(child_path, must_exist=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise StyleBertVits2ModelError(
                    f"Unsafe model root file: {child.name}"
                ) from exc
    return result


def _snapshot_relative_path(relative_path: str) -> Path:
    return Path(*relative_path.split("/"))


def _copy_snapshot_to_staging(
    source: Path,
    snapshot: _TreeSnapshot,
    staging: Path,
) -> None:
    for entry in snapshot.entries:
        relative = _snapshot_relative_path(entry.relative_path)
        destination = staging / relative
        if entry.kind == "directory":
            try:
                os.mkdir(destination, mode=0o700)
                require_existing_real_directory(destination)
            except (OSError, RuntimeError, ValueError) as exc:
                raise StyleBertVits2ModelError(
                    f"Could not create staged model directory: {entry.relative_path}"
                ) from exc
            continue

        try:
            atomic_copy_secure_file(
                source / relative,
                destination,
                max_bytes=min(entry.size, _MAX_MODEL_FILE_BYTES),
                overwrite=False,
                mode=0o600,
            )
        except Exception as exc:
            raise StyleBertVits2ModelError(
                f"Could not securely copy model file: {entry.relative_path}"
            ) from exc


def _direct_child_path(root: Path, candidate: Path) -> Path:
    safe_root = require_existing_real_directory(root)
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    if os.path.normcase(os.fspath(lexical.parent)) != os.path.normcase(
        os.fspath(safe_root)
    ):
        raise StyleBertVits2ModelError(
            f"Unsafe managed model path: {candidate}"
        )
    return lexical


def _unused_private_sibling(root: Path, label: str) -> Path:
    safe_label = _safe_folder_name(label)[:80]
    for _ in range(64):
        candidate = root / f".{safe_label}.{secrets.token_hex(12)}.backup"
        if not os.path.lexists(candidate):
            return candidate
    raise StyleBertVits2ModelError("Could not allocate a private model backup path.")


def _remove_validated_tree(
    tree: Path,
    root: Path,
    expected_snapshot: _TreeSnapshot,
) -> None:
    candidate = _direct_child_path(root, tree)
    try:
        root_stat = os.lstat(candidate)
        if not stat.S_ISDIR(root_stat.st_mode) or _is_link_or_reparse(root_stat):
            raise RuntimeError("tree root is unsafe")
        validated, current_snapshot = _scan_model_tree(candidate)
    except (OSError, RuntimeError, ValueError, StyleBertVits2ModelError) as exc:
        raise StyleBertVits2ModelError(
            f"Refusing to remove an unsafe managed model tree: {candidate}"
        ) from exc
    if current_snapshot != expected_snapshot:
        raise StyleBertVits2ModelError(
            f"Managed model tree changed before cleanup: {candidate}"
        )

    for entry in reversed(current_snapshot.entries):
        relative = _snapshot_relative_path(entry.relative_path)
        path = validated / relative
        if entry.kind == "file":
            try:
                current_stat = os.lstat(path)
                if _snapshot_entry(relative, "file", current_stat) != entry:
                    raise RuntimeError("file identity changed")
                secure_unlink(path, expected_stat=current_stat)
            except (OSError, RuntimeError, ValueError) as exc:
                raise StyleBertVits2ModelError(
                    f"Could not remove managed model file: {path}"
                ) from exc
            continue
        try:
            current_stat = os.lstat(path)
            expected_identity = (
                entry.device,
                entry.inode,
                entry.mode_type,
            )
            current_identity = (
                int(getattr(current_stat, "st_dev", 0)),
                int(getattr(current_stat, "st_ino", 0)),
                stat.S_IFMT(current_stat.st_mode),
            )
            if (
                _is_link_or_reparse(current_stat)
                or current_identity != expected_identity
            ):
                raise RuntimeError("directory identity changed")
            os.rmdir(path)
        except (OSError, RuntimeError) as exc:
            raise StyleBertVits2ModelError(
                f"Could not remove managed model directory: {path}"
            ) from exc

    try:
        final_root_stat = os.lstat(validated)
        if not _same_identity(root_stat, final_root_stat):
            raise RuntimeError("tree root identity changed")
        os.rmdir(validated)
    except (OSError, RuntimeError) as exc:
        raise StyleBertVits2ModelError(
            f"Could not remove managed model tree: {validated}"
        ) from exc


def _install_staged_tree(
    staging: Path,
    staging_snapshot: _TreeSnapshot,
    target: Path,
    target_root: Path,
) -> StyleBertVits2ModelInfo:
    safe_root = require_existing_real_directory(target_root)
    staging = _direct_child_path(safe_root, staging)
    target = _direct_child_path(safe_root, target)

    try:
        _, current_staging_snapshot = _scan_model_tree(staging)
    except StyleBertVits2ModelError as exc:
        raise StyleBertVits2ModelError(
            "Staged model could not be revalidated before publication."
        ) from exc
    if current_staging_snapshot != staging_snapshot:
        raise StyleBertVits2ModelError(
            "Staged model changed before atomic publication."
        )

    backup: Path | None = None
    backup_snapshot: _TreeSnapshot | None = None
    installed = False
    if os.path.lexists(target):
        try:
            require_existing_real_directory(target)
            _, backup_snapshot = _scan_model_tree(target)
        except (OSError, RuntimeError, ValueError, StyleBertVits2ModelError) as exc:
            raise StyleBertVits2ModelError(
                f"Existing model target is unsafe: {target}"
            ) from exc
        backup = _unused_private_sibling(safe_root, target.name)
        try:
            os.rename(target, backup)
            _, preserved_snapshot = _scan_model_tree(backup)
            if preserved_snapshot != backup_snapshot:
                raise RuntimeError("preserved model changed during backup rename")
        except Exception as exc:
            # Restore only a backup that was revalidated as the exact tree we
            # inspected before the rename. A changed backup remains quarantined
            # under its private hidden name rather than being republished.
            try:
                if (
                    os.path.lexists(backup)
                    and not os.path.lexists(target)
                    and backup_snapshot is not None
                ):
                    _, current_backup_snapshot = _scan_model_tree(backup)
                    if current_backup_snapshot == backup_snapshot:
                        os.rename(backup, target)
            except Exception:
                logger.warning(
                    "Could not safely restore model after backup validation failed",
                    exc_info=True,
                )
            raise StyleBertVits2ModelError(
                f"Could not preserve the existing model installation: {target.name}"
            ) from exc

    try:
        # Revalidate immediately before the publication rename. This second
        # check closes the window spent preserving an existing target.
        _, current_staging_snapshot = _scan_model_tree(staging)
        if current_staging_snapshot != staging_snapshot:
            raise StyleBertVits2ModelError(
                "Staged model changed before atomic publication."
            )
        if os.path.lexists(target):
            raise StyleBertVits2ModelError(
                f"Model target appeared during installation: {target.name}"
            )
        os.rename(staging, target)
        installed = True
        installed_info = inspect_style_bert_model_dir(target)
        _, installed_snapshot = _scan_model_tree(target)
        if installed_snapshot != staging_snapshot:
            raise StyleBertVits2ModelError(
                "Installed model changed during atomic publication."
            )
    except Exception as exc:
        rollback_error: BaseException | None = None
        if installed:
            try:
                _remove_validated_tree(target, safe_root, staging_snapshot)
            except BaseException as cleanup_exc:  # noqa: BLE001
                rollback_error = cleanup_exc
        if backup is not None and os.path.lexists(backup):
            try:
                if backup_snapshot is None:
                    raise RuntimeError("missing backup snapshot")
                _, current_backup_snapshot = _scan_model_tree(backup)
                if current_backup_snapshot != backup_snapshot:
                    raise RuntimeError("preserved model backup changed")
                if os.path.lexists(target):
                    raise RuntimeError("installation target is occupied")
                os.rename(backup, target)
                _, restored_snapshot = _scan_model_tree(target)
                if restored_snapshot != backup_snapshot:
                    # Do not leave an unverified tree at the public model path.
                    if not os.path.lexists(backup):
                        os.rename(target, backup)
                    raise RuntimeError("restored model changed during publication")
            except BaseException as restore_exc:  # noqa: BLE001
                rollback_error = rollback_error or restore_exc
        if rollback_error is not None:
            raise StyleBertVits2ModelError(
                "Model installation failed and the previous installation "
                "could not be restored safely."
            ) from rollback_error
        if isinstance(exc, StyleBertVits2ModelError):
            raise
        raise StyleBertVits2ModelError(
            f"Could not install the imported model: {exc}"
        ) from exc

    if backup is not None and backup_snapshot is not None:
        try:
            _remove_validated_tree(backup, safe_root, backup_snapshot)
        except Exception:
            logger.warning(
                "Installed model successfully but could not remove validated backup %s",
                backup,
                exc_info=True,
            )
    return installed_info


def _import_one_model(candidate: Path) -> StyleBertVits2ModelInfo:
    source, source_snapshot = _scan_model_tree(candidate)
    inspected = _inspect_from_snapshot(source, source_snapshot)
    _, source_after_inspection = _scan_model_tree(source)
    if source_after_inspection != source_snapshot:
        raise StyleBertVits2ModelError(
            "Model source changed while it was being validated."
        )

    target_root = style_bert_models_dir()
    target_dir = _replaceable_target_dir(target_root, inspected.name)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{target_dir.name}.",
            suffix=".staging",
            dir=target_root,
        )
    )
    require_existing_real_directory(staging)
    staging_snapshot: _TreeSnapshot | None = None
    try:
        _copy_snapshot_to_staging(source, source_snapshot, staging)
        _, source_after_copy = _scan_model_tree(source)
        if source_after_copy != source_snapshot:
            raise StyleBertVits2ModelError(
                "Model source changed while it was being copied."
            )

        _write_import_metadata(staging, inspected)
        staged_info = inspect_style_bert_model_dir(staging)
        _, staging_snapshot = _scan_model_tree(staging)
        if staged_info.speakers != inspected.speakers or staged_info.styles != inspected.styles:
            raise StyleBertVits2ModelError(
                "Staged model metadata does not match the validated source."
            )
        return _install_staged_tree(
            staging,
            staging_snapshot,
            target_dir,
            target_root,
        )
    finally:
        if os.path.lexists(staging):
            try:
                _, cleanup_snapshot = _scan_model_tree(staging)
                _remove_validated_tree(
                    staging,
                    target_root,
                    cleanup_snapshot,
                )
            except Exception:
                logger.warning(
                    "Could not safely remove model import staging directory %s",
                    staging,
                    exc_info=True,
                )


def import_style_bert_model_path(source: Path) -> list[StyleBertVits2ModelInfo]:
    """Import one model folder, or each valid child folder in a model root."""
    with _IMPORT_LOCK:
        try:
            source_path = require_existing_real_directory(Path(source).expanduser())
        except (OSError, RuntimeError, ValueError) as exc:
            raise StyleBertVits2ModelError(
                "The selected path is not a safe folder."
            ) from exc

        root_error: StyleBertVits2ModelError | None = None
        try:
            inspect_style_bert_model_dir(source_path)
        except StyleBertVits2ModelError as exc:
            root_error = exc
            candidates = _safe_direct_child_directories(source_path)
        else:
            candidates = [source_path]

        imported: list[StyleBertVits2ModelInfo] = []
        validation_errors: list[str] = []
        for candidate in candidates:
            try:
                imported.append(_import_one_model(candidate))
            except StyleBertVits2ModelError as exc:
                validation_errors.append(f"{candidate.name}: {exc}")

        if imported:
            logger.info(
                "Imported %d Style-Bert-VITS2 model folder(s)",
                len(imported),
            )
            return imported

        detail = (
            validation_errors[0]
            if validation_errors
            else str(root_error or "No usable model folders were found.")
        )
        raise StyleBertVits2ModelError(detail)


def list_imported_style_bert_models() -> list[StyleBertVits2ModelInfo]:
    """List valid custom Style-Bert-VITS2 model folders already imported."""
    imported: list[StyleBertVits2ModelInfo] = []
    root = style_bert_models_dir()
    try:
        children = _safe_direct_child_directories(root)
    except StyleBertVits2ModelError as exc:
        logger.warning("Could not safely scan Style-Bert-VITS2 models: %s", exc)
        return imported
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            imported.append(inspect_style_bert_model_dir(child))
        except StyleBertVits2ModelError as exc:
            logger.warning(
                "Skipping invalid Style-Bert-VITS2 model folder %s: %s",
                child,
                exc,
            )
    return imported


def _read_bundled_catalog(path: Path) -> object:
    file_stat = path.lstat()
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or _is_link_or_reparse(file_stat)
        or int(file_stat.st_size) > _MAX_PRESET_CATALOG_BYTES
    ):
        raise ValueError("preset catalog is not a safe bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened_stat = os.fstat(fd)
        if not _same_snapshot(file_stat, opened_stat):
            raise RuntimeError("preset catalog changed while opening")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            payload = handle.read(_MAX_PRESET_CATALOG_BYTES + 1)
        if len(payload) > _MAX_PRESET_CATALOG_BYTES:
            raise ValueError("preset catalog exceeds the size limit")
        final_stat = os.fstat(fd)
        if not _same_snapshot(opened_stat, final_stat):
            raise RuntimeError("preset catalog changed while reading")
        return json.loads(payload.decode("utf-8"))
    finally:
        os.close(fd)


@lru_cache(maxsize=1)
def load_style_bert_vits2_presets() -> tuple[StyleBertVits2Preset, ...]:
    """Load bundled friendly-name metadata for known custom voice packs."""
    relative = Path("assets") / "tts" / "hololive_style_bert_vits2_catalog.json"
    for base in resource_base_dirs():
        candidate = base / relative
        try:
            if not os.path.lexists(candidate):
                continue
            payload = _read_bundled_catalog(candidate)
        except Exception as exc:
            logger.warning(
                "Failed to load Style-Bert-VITS2 preset catalog %s: %s",
                candidate,
                exc,
            )
            return ()
        if not isinstance(payload, list):
            return ()
        presets: list[StyleBertVits2Preset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            title = str(item.get("title") or "").strip()
            model_path = str(item.get("model_path") or "").strip()
            speaker_id = str(item.get("speaker_id") or "").strip()
            language = str(item.get("language") or "").strip().upper()
            if not all((key, title, model_path, speaker_id)):
                continue
            presets.append(
                StyleBertVits2Preset(
                    key=key,
                    title=title,
                    model_path=model_path,
                    speaker_id=speaker_id,
                    language=language or "JP",
                )
            )
        return tuple(presets)
    return ()


def style_bert_preset_title(model_name: str, speaker: str) -> str | None:
    """Resolve a friendly title for a bundled catalog voice."""
    for preset in load_style_bert_vits2_presets():
        if preset.model_path == model_name and preset.speaker_id == speaker:
            return preset.title
    return None


def style_bert_preset_language(model_name: str, speaker: str) -> str | None:
    """Resolve the catalog language for a known imported voice."""
    for preset in load_style_bert_vits2_presets():
        if preset.model_path == model_name and preset.speaker_id == speaker:
            return preset.language
    return None


def hololive_preset_choice_rows() -> tuple[tuple[str, str], ...]:
    """Return stable ``(display_label, model_path)`` rows for settings UI."""
    return tuple(
        (preset.title, preset.model_path)
        for preset in load_style_bert_vits2_presets()
    )


_HOLOLIVE_MODEL_REVISION = "c8a5451386963bf9c7b917f6f39268628b898f83"
_HOLOLIVE_MODEL_SHA256: dict[str, dict[str, str]] = {
    "SBV2_HoloLow": {
        "SBV2_HoloLow.safetensors": "21b0e0f9e570e5d45a27a18f2440b886243e974865a5aeae33b3b822a231dfd0",
        "config.json": "f5c0b3ab627e5cf16ef92596125ba063978bc79394dd36f56db89ff1e4ca5bad",
        "style_vectors.npy": "b9e8a92c5e738c94d06ed1c935c6dc05c2fdea5c0e34c8dcf28794213c4825f0",
    },
    "SBV2_TakanashiKiara": {
        "SBV2_TakanashiKiara.safetensors": "0b55114e24304834be59e20ecf33f967c2823fa350e50d065486fd9f8afdc6ac",
        "config.json": "95e725fd51d2037ec19524d454f8408c98b362b69069d63a0a953783f2d521d5",
        "style_vectors.npy": "0410d36de3edd37800aab6c7ab2a5a44f03fcfb9da031bd2acf73591856f0d76",
    },
    "SBV2_HoloHi": {
        "SBV2_HoloHi.safetensors": "5ae616a144f9fd7b70b6afc6e268fc99a7ab7c384e530234df101829ddc03f61",
        "config.json": "771beef2b295a97845a8e8cf14412374134f25f995503b1856e733f58b760d30",
        "style_vectors.npy": "209866d5cb3a0fd89c152f4687da9c5c1c35f0902b2043f529411b001cfd0d22",
    },
    "SBV2_HoloAus": {
        "SBV2_HoloAus.safetensors": "650d5652b862bbe0261d96f3b680528441ffcbaa84ef99377620ca23be91045a",
        "config.json": "d241d2e47c220031cdc99d218deecbf88786c4c5d9fce2df014ab606eebbc8e5",
        "style_vectors.npy": "1bdb15e3886c5cb7b26c05c426962001312adb0cbf95e7c78560ac1f4df09b5d",
    },
    "SBV2_KosekiBijou": {
        "SBV2_KosekiBijou.safetensors": "6178ab982c7542c398e0eacdc63283c66a7bbdb985abf41b1ebce4dc517b729c",
        "config.json": "82aed91fc0e503f2697922ae9de1cb25bdb5cce7812602b3e23729e5fe86e5bf",
        "style_vectors.npy": "e03261258a73fb64242ea1e31e35f9a47b5c1e184eed281bcf9b687ec5e9b2bb",
    },
    "SBV2_HoloESL": {
        "SBV2_HoloESL.safetensors": "df215c4b3f40265b6c190ad2b497e77194545e74bf48bb1774a9eaf57503606a",
        "config.json": "7551662d00854e38629b6b5593e9221427c339064ceacfc8086120f62aa21628",
        "style_vectors.npy": "5ad49c60a1b326a2710586e5806039add9da059d7dfd07d47d6f04d90de6e3a8",
    },
    "SBV2_HoloIDFlu": {
        "SBV2_HoloIDFlu.safetensors": "d5cce80bcbbe92aeb1a1006b782812f77e153e3cf30c1e5029bedd7f58b10a8f",
        "config.json": "beb690102a56a6d6623a89e46cc03ac71893550a5b73abe10e44f9f11867ca1f",
        "style_vectors.npy": "69c402a763f3015dcb971c310e44d21c1774fe0100855f675fb9f6cc58e19caf",
    },
    "SBV2_HoloJPTest2": {
        "SBV2_HoloJPTest2.safetensors": "0b63562a417d8fce700f74b4a4660b20571d1917528a212f95691815ee4218b3",
        "config.json": "ae3b4a6a54fa11edfa2b04c5448ac7e7f0b776bdc7e33ddcf24f59fa2a8a927d",
        "style_vectors.npy": "46baff316515c091c5a0c9c4b8423d470fc45b4e1d146301f49970dbf77e4cc7",
    },
    "SBV2_HoloJPTest2.5": {
        "SBV2_HoloJPTest2.5.safetensors": "41f5b7e81054d0d70d886e6fa340726dfd8ae59c2a6bda55ed568c98b8575f8e",
        "config.json": "f570c14999ba69015cd75e429f6b84624488cf88b4ec8674623232e1d5e608ef",
        "style_vectors.npy": "5437bd3af796eb2f7d95e0241913bfa45cb194b356d9ae36fbc392ffbc34e6be",
    },
    "SBV2_HoloJPTest": {
        "SBV2_HoloJPTest.safetensors": "0b2fd3b1cbe024ab5cbe2599fbbdd518558981bb109fd148412819a50c891684",
        "config.json": "cee0f7d6a3edd66a35dc0bc09a4ce5b26ee35ce0e1757886bd7a2850edacff9d",
        "style_vectors.npy": "1f8b1addb7304ca598ccd53f497e4b2479a395ff1574c5575b6e8c1ac0f5e347",
    },
}


def hololive_model_bundle(model_path: str) -> StyleBertVits2ModelBundle | None:
    """Return the shared downloadable files for one known Hololive model pack."""
    clean = str(model_path or "").strip()
    if not clean:
        return None
    if not any(
        preset.model_path == clean
        for preset in load_style_bert_vits2_presets()
    ):
        return None
    hashes = _HOLOLIVE_MODEL_SHA256.get(clean)
    if not hashes:
        return None
    files = (f"{clean}.safetensors", "config.json", "style_vectors.npy")
    if set(files) != set(hashes):
        return None
    return StyleBertVits2ModelBundle(
        model_path=clean,
        files=files,
        revision=_HOLOLIVE_MODEL_REVISION,
        file_sha256=tuple((filename, hashes[filename]) for filename in files),
    )


def style_bert_voice_id(model_name: str, speaker: str, style: str) -> str:
    """Build a stable, readable voice ID for settings and config."""
    return VOICE_ID_SEPARATOR.join((model_name, speaker, style))


def parse_style_bert_voice_id(value: str) -> tuple[str, str, str]:
    """Parse a Style-Bert-VITS2 voice ID from config or the settings menu."""
    parts = [
        part.strip()
        for part in str(value or "").split(VOICE_ID_SEPARATOR)
    ]
    if len(parts) != 3 or not all(parts):
        raise StyleBertVits2ModelError(
            "The selected custom voice is no longer valid."
        )
    return parts[0], parts[1], parts[2]


def _dict_keys_as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(
        str(key).strip()
        for key in value.keys()
        if str(key).strip()
    )


def _replaceable_target_dir(root: Path, raw_name: str) -> Path:
    safe_root = require_existing_real_directory(root)
    return _direct_child_path(safe_root, safe_root / _safe_folder_name(raw_name))


def _write_import_metadata(
    target_dir: Path,
    inspected: StyleBertVits2ModelInfo,
) -> None:
    payload = {
        "schema": "mio-style-bert-vits2-import-v1",
        "source_name": inspected.name,
        "speakers": list(inspected.speakers),
        "styles": list(inspected.styles),
    }
    try:
        atomic_write_text(
            secure_file_path(target_dir / ".mio-style-bert-vits2.json"),
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        raise StyleBertVits2ModelError(
            "Could not securely write model import metadata."
        ) from exc


def _safe_folder_name(value: str) -> str:
    name = Path(str(value or "").strip() or "custom-voice").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    name = name[:120].rstrip(" .") or "custom-voice"
    if os.name == "nt":
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if name.split(".", 1)[0].upper() in reserved:
            name = f"_{name}"
    return name
