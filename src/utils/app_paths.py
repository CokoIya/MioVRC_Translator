# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

_WINDOWS_APP_DIR_NAME = "Mio RealTime Translator"
_POSIX_APP_DIR_NAME = "mio-realtime-translator"
_MIGRATION_MARKER_NAME = ".legacy-data-migration-v1.done"
_MIGRATION_FILES_MARKER_NAME = ".legacy-data-migration-v1-files.done"
_MIGRATION_LOCK_NAME = ".legacy-data-migration-v1.lock"
_MIGRATION_FILES = (
    "config.json",
    "seren.json",
    "catalog_cache.json",
    "sponsors_cache.json",
)
_MIGRATION_FILE_MAX_BYTES = {
    "config.json": 4 * 1024 * 1024,
    "seren.json": 1024 * 1024,
    "catalog_cache.json": 4 * 1024 * 1024,
    "sponsors_cache.json": 4 * 1024 * 1024,
}
_MIGRATION_DIRS = (
    "backgrounds",
    "dictionaries",
    "runtime_cache",
    "runtime_models",
    "runtime_cuda",
    "tts_models",
    "logs",
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_COPY_CHUNK_SIZE = 1024 * 1024
_UNOWNED_COPY_TEMP_STALE_S = 24 * 60 * 60
_MIGRATION_LOCK = threading.Lock()
_MIGRATION_FILES_LOCK = threading.Lock()
_MIGRATED_DESTINATIONS: set[str] = set()
_MIGRATION_ATTEMPTED_DESTINATIONS: set[str] = set()
_MIGRATION_FILES_MIGRATED_DESTINATIONS: set[str] = set()
_MIGRATION_FILES_ATTEMPTED_DESTINATIONS: set[str] = set()
_MIGRATION_SKIPPED_DIRECTORY_NAMES = frozenset({"__pycache__", "pycache"})
_MIGRATION_SKIPPED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_base_dirs() -> list[Path]:
    if not getattr(sys, "frozen", False):
        return [project_root()]

    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass)
        if meipass_path not in dirs:
            dirs.append(meipass_path)
    return dirs


def _default_writable_app_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data).expanduser() if local_app_data else Path.home() / "AppData" / "Local"
        return base / _WINDOWS_APP_DIR_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / _POSIX_APP_DIR_NAME


def _legacy_writable_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return project_root()


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _lexical_absolute_path(path: Path) -> Path:
    """Return an absolute path without resolving links or reparse points."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _directory_chain(path: Path):
    anchor = Path(path.anchor)
    current = anchor
    yield current
    for part in path.parts[len(anchor.parts) :]:
        current = current / part
        yield current


def _same_file_object(first: os.stat_result, second: os.stat_result) -> bool:
    if stat.S_IFMT(first.st_mode) != stat.S_IFMT(second.st_mode):
        return False
    first_identity = (int(getattr(first, "st_dev", 0)), int(getattr(first, "st_ino", 0)))
    second_identity = (int(getattr(second, "st_dev", 0)), int(getattr(second, "st_ino", 0)))
    if first_identity[1] and second_identity[1]:
        return first_identity == second_identity
    return _is_link_or_reparse(first) == _is_link_or_reparse(second)


def _same_file_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _same_file_object(first, second)
        and int(first.st_size) == int(second.st_size)
        and int(getattr(first, "st_mtime_ns", 0))
        == int(getattr(second, "st_mtime_ns", 0))
        and int(getattr(first, "st_ctime_ns", 0))
        == int(getattr(second, "st_ctime_ns", 0))
    )


def _is_regular_private_file(file_stat: os.stat_result) -> bool:
    link_count = int(getattr(file_stat, "st_nlink", 1) or 1)
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not _is_link_or_reparse(file_stat)
        and link_count == 1
    )


def _validate_existing_real_directory(path: Path) -> bool:
    """Validate an existing directory chain without following redirected ancestors."""

    try:
        normalized = _lexical_absolute_path(path)
        if not normalized.is_absolute() or not normalized.anchor:
            return False
        snapshots: list[tuple[Path, os.stat_result]] = []
        for current in _directory_chain(normalized):
            file_stat = _safe_lstat(current)
            if (
                file_stat is None
                or not stat.S_ISDIR(file_stat.st_mode)
                or _is_link_or_reparse(file_stat)
            ):
                return False
            snapshots.append((current, file_stat))

        for current, original_stat in snapshots:
            current_stat = _safe_lstat(current)
            if (
                current_stat is None
                or not stat.S_ISDIR(current_stat.st_mode)
                or _is_link_or_reparse(current_stat)
                or not _same_file_object(original_stat, current_stat)
            ):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _ensure_real_directory(path: Path) -> bool:
    """Validate and create a directory without traversing redirected ancestors."""

    try:
        normalized = _lexical_absolute_path(path)
        if not normalized.is_absolute() or not normalized.anchor:
            return False
        anchor = Path(normalized.anchor)
        snapshots: list[tuple[Path, os.stat_result]] = []
        for current in _directory_chain(normalized):
            file_stat = _safe_lstat(current)
            if file_stat is None:
                if current == anchor:
                    return False
                try:
                    current.mkdir(mode=0o700, parents=False, exist_ok=False)
                except FileExistsError:
                    pass
                except OSError:
                    return False
                file_stat = _safe_lstat(current)
            if (
                file_stat is None
                or not stat.S_ISDIR(file_stat.st_mode)
                or _is_link_or_reparse(file_stat)
            ):
                return False
            snapshots.append((current, file_stat))

        # Revisit every component after creation. This catches an ancestor being
        # exchanged for a junction/symlink while a deeper component was created.
        for current, original_stat in snapshots:
            current_stat = _safe_lstat(current)
            if (
                current_stat is None
                or not stat.S_ISDIR(current_stat.st_mode)
                or _is_link_or_reparse(current_stat)
                or not _same_file_object(original_stat, current_stat)
            ):
                return False
    except (OSError, RuntimeError):
        return False
    return True


def _copy_temp_owner_pid(destination: Path, candidate: Path) -> int | None:
    prefix = f".{destination.name}."
    suffix = ".copy.tmp"
    name = candidate.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    owner_text = name[len(prefix) : -len(suffix)].split(".", 1)[0]
    if not owner_text.isdecimal():
        return None
    try:
        owner_pid = int(owner_text)
    except ValueError:
        return None
    return owner_pid if owner_pid > 0 else None


def _process_is_running(pid: int) -> bool:
    """Conservatively report whether a staging-file owner may still exist."""

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # ERROR_INVALID_PARAMETER means the PID does not identify a
            # process. Access-denied and other failures are treated as alive so
            # cleanup never races a process we cannot inspect.
            return ctypes.get_last_error() != 87
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _cleanup_stale_copy_temps(destination: Path) -> None:
    """Remove abandoned atomic-copy staging files for one migration target."""

    try:
        candidates = tuple(
            destination.parent.glob(f".{destination.name}.*.copy.tmp")
        )
    except (OSError, RuntimeError, ValueError):
        return
    now = time.time()
    for candidate in candidates:
        candidate_stat = _safe_lstat(candidate)
        if (
            candidate_stat is None
            or not stat.S_ISREG(candidate_stat.st_mode)
            or _is_link_or_reparse(candidate_stat)
        ):
            continue
        owner_pid = _copy_temp_owner_pid(destination, candidate)
        if owner_pid is not None:
            if _process_is_running(owner_pid):
                continue
        elif max(0.0, now - float(candidate_stat.st_mtime)) < _UNOWNED_COPY_TEMP_STALE_S:
            # Older releases did not include a PID in the staging filename.
            # Preserve recent unowned files because another live process may
            # still be copying them; age them out conservatively instead.
            continue
        try:
            if _is_regular_private_file(candidate_stat):
                secure_unlink(
                    candidate,
                    missing_ok=True,
                    expected_stat=candidate_stat,
                )
                continue

            # POSIX no-replace publication uses link(2) followed by unlink(2).
            # A crash between those calls leaves the staging and destination
            # names pointing at the same two-link inode. Remove only the stale
            # staging name after proving that exact relationship; the final
            # destination then becomes a normal one-link private file.
            destination_stat = _safe_lstat(destination)
            current_stat = _safe_lstat(candidate)
            if (
                int(getattr(candidate_stat, "st_nlink", 1) or 1) == 2
                and destination_stat is not None
                and current_stat is not None
                and _same_file_snapshot(candidate_stat, current_stat)
                and _same_file_object(candidate_stat, destination_stat)
            ):
                candidate.unlink()
                _fsync_directory(candidate.parent)
        except (OSError, RuntimeError, ValueError):
            logger.debug("Could not remove stale migration staging file: %s", candidate)


def _secure_copy_missing_file(
    source: Path,
    destination: Path,
    *,
    max_bytes: int | None = None,
) -> bool:
    """Atomically copy one regular file without replacing a destination."""
    _cleanup_stale_copy_temps(destination)
    if _lexists(destination):
        destination_stat = _safe_lstat(destination)
        return bool(
            destination_stat is not None
            and _is_regular_private_file(destination_stat)
        )
    source_stat = _safe_lstat(source)
    if source_stat is None:
        return True
    if not _is_regular_private_file(source_stat):
        return True
    if max_bytes is not None and int(source_stat.st_size) > int(max_bytes):
        logger.warning(
            "Skipping oversized legacy startup file name=%s size=%d max_bytes=%d",
            source.name,
            int(source_stat.st_size),
            int(max_bytes),
        )
        return True
    if not _ensure_real_directory(destination.parent):
        return False
    try:
        atomic_copy_secure_file(
            source,
            destination,
            max_bytes=max_bytes,
            overwrite=False,
            mode=0o600,
        )
    except FileExistsError:
        destination_stat = _safe_lstat(destination)
        return bool(
            destination_stat is not None
            and _is_regular_private_file(destination_stat)
        )
    except (OSError, RuntimeError, ValueError):
        return False
    destination_stat = _safe_lstat(destination)
    return bool(
        destination_stat
        and _is_regular_private_file(destination_stat)
    )


def _merge_missing_directory(source: Path, destination: Path) -> bool:
    source_stat = _safe_lstat(source)
    if source_stat is None:
        return True
    if not stat.S_ISDIR(source_stat.st_mode) or _is_link_or_reparse(source_stat):
        return True
    if not _ensure_real_directory(destination):
        return False

    success = True
    try:
        entries = list(os.scandir(source))
    except OSError:
        return False
    for entry in entries:
        source_child = Path(entry.path)
        destination_child = destination / entry.name
        try:
            if entry.is_symlink():
                continue
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError:
            success = False
            continue
        if _is_link_or_reparse(entry_stat):
            continue
        if stat.S_ISDIR(entry_stat.st_mode):
            # Bytecode caches are disposable, can contain thousands of deeply
            # nested paths, and may exceed the legacy Windows MAX_PATH limit.
            # Migrating them made every writable-path lookup spend seconds
            # walking the same tree and prevented the completion marker from
            # ever being written when one long .pyc path could not be copied.
            if entry.name.casefold() in _MIGRATION_SKIPPED_DIRECTORY_NAMES:
                continue
            if not _merge_missing_directory(source_child, destination_child):
                success = False
        elif stat.S_ISREG(entry_stat.st_mode):
            if source_child.suffix.casefold() in _MIGRATION_SKIPPED_FILE_SUFFIXES:
                continue
            if not _secure_copy_missing_file(source_child, destination_child):
                success = False
    return success


def _marker_exists(marker_path: Path) -> bool:
    marker_stat = _safe_lstat(marker_path)
    return bool(
        marker_stat
        and stat.S_ISREG(marker_stat.st_mode)
        and not _is_link_or_reparse(marker_stat)
    )


@contextmanager
def _cross_process_lock(lock_path: Path):
    if _lexists(lock_path):
        lock_stat = _safe_lstat(lock_path)
        if lock_stat is None or not stat.S_ISREG(lock_stat.st_mode) or _is_link_or_reparse(lock_stat):
            raise OSError("Migration lock path is not a regular file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(fd, "r+b", closefd=True)
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            # Do not stall a second application instance behind a potentially
            # large one-time migration. The process that acquired the lock will
            # finish it; this process can safely use the already-created data
            # directory without copying legacy files concurrently.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_migration_marker(
    marker_path: Path,
    *,
    message: str = "legacy writable data migrated without overwriting destination files",
) -> bool:
    if _marker_exists(marker_path):
        return True
    if _lexists(marker_path):
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(message).strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return _marker_exists(marker_path)
    except OSError:
        return False
    return True


def _migrate_legacy_files(destination: Path) -> None:
    """Copy only the small launch-critical legacy files.

    Large model, cache, background, and log trees are intentionally excluded
    from the synchronous writable-path lookup.  They are migrated by
    :func:`run_deferred_legacy_data_migration` after the first UI render.
    """

    source = _legacy_writable_app_dir()
    try:
        if source.resolve(strict=False) == destination.resolve(strict=False):
            return
    except OSError:
        return

    destination_key = os.path.normcase(os.path.abspath(os.fspath(destination)))
    if (
        destination_key in _MIGRATION_FILES_MIGRATED_DESTINATIONS
        or destination_key in _MIGRATION_FILES_ATTEMPTED_DESTINATIONS
    ):
        return
    # A lookup on the UI/config hot path must never queue behind another
    # migration attempt.  The active attempt owns the cross-process lock and
    # will either publish complete files atomically or leave no destination.
    if not _MIGRATION_FILES_LOCK.acquire(blocking=False):
        return
    try:
        if (
            destination_key in _MIGRATION_FILES_MIGRATED_DESTINATIONS
            or destination_key in _MIGRATION_FILES_ATTEMPTED_DESTINATIONS
        ):
            return
        _MIGRATION_FILES_ATTEMPTED_DESTINATIONS.add(destination_key)
        if not _ensure_real_directory(destination):
            logger.warning("Writable application directory is not a safe directory: %s", destination)
            return
        lock_path = destination / _MIGRATION_LOCK_NAME
        marker_path = destination / _MIGRATION_FILES_MARKER_NAME
        complete_marker_path = destination / _MIGRATION_MARKER_NAME
        try:
            with _cross_process_lock(lock_path):
                if _marker_exists(complete_marker_path) or _marker_exists(marker_path):
                    _MIGRATION_FILES_MIGRATED_DESTINATIONS.add(destination_key)
                    return
                success = True
                for name in _MIGRATION_FILES:
                    if not _secure_copy_missing_file(
                        source / name,
                        destination / name,
                        max_bytes=_MIGRATION_FILE_MAX_BYTES.get(name),
                    ):
                        success = False
                if success and _write_migration_marker(
                    marker_path,
                    message="legacy launch-critical files migrated without overwriting destination files",
                ):
                    _MIGRATION_FILES_MIGRATED_DESTINATIONS.add(destination_key)
                elif not success:
                    logger.warning(
                        "Legacy launch-critical file migration was incomplete; it will be retried"
                    )
        except (BlockingIOError, OSError) as exc:
            logger.debug("Legacy writable-data migration lock unavailable: %s", exc)
    finally:
        _MIGRATION_FILES_LOCK.release()


def _migrate_legacy_data(destination: Path) -> None:
    source = _legacy_writable_app_dir()
    try:
        if source.resolve(strict=False) == destination.resolve(strict=False):
            return
    except OSError:
        return

    destination_key = os.path.normcase(os.path.abspath(os.fspath(destination)))
    if (
        destination_key in _MIGRATED_DESTINATIONS
        or destination_key in _MIGRATION_ATTEMPTED_DESTINATIONS
    ):
        return
    # Deferred maintenance must not serialize unrelated writable-path users.
    # If another in-process attempt is already active, it is authoritative.
    if not _MIGRATION_LOCK.acquire(blocking=False):
        return
    try:
        if (
            destination_key in _MIGRATED_DESTINATIONS
            or destination_key in _MIGRATION_ATTEMPTED_DESTINATIONS
        ):
            return
        # An incomplete migration is retried on the next application launch,
        # not on every call to writable_app_dir() in the current process. The
        # latter is a hot path used by config, catalogs, TTS, models, and logs.
        _MIGRATION_ATTEMPTED_DESTINATIONS.add(destination_key)
        if not _ensure_real_directory(destination):
            logger.warning("Writable application directory is not a safe directory: %s", destination)
            return
        lock_path = destination / _MIGRATION_LOCK_NAME
        marker_path = destination / _MIGRATION_MARKER_NAME
        try:
            with _cross_process_lock(lock_path):
                if _marker_exists(marker_path):
                    _MIGRATED_DESTINATIONS.add(destination_key)
                    return
                success = True
                for name in _MIGRATION_FILES:
                    if not _secure_copy_missing_file(
                        source / name,
                        destination / name,
                        max_bytes=_MIGRATION_FILE_MAX_BYTES.get(name),
                    ):
                        success = False
                for name in _MIGRATION_DIRS:
                    if not _merge_missing_directory(source / name, destination / name):
                        success = False
                if success and _write_migration_marker(marker_path):
                    _MIGRATED_DESTINATIONS.add(destination_key)
                elif not success:
                    logger.warning("Legacy writable-data migration was incomplete; it will be retried")
        except (BlockingIOError, OSError) as exc:
            # Another process may already be migrating the same legacy tree.
            # This is not an application error and must not block startup.
            logger.debug("Legacy writable-data migration lock unavailable: %s", exc)
    finally:
        _MIGRATION_LOCK.release()


def run_deferred_legacy_data_migration() -> None:
    """Finish legacy directory migration outside the startup-critical path."""

    if os.environ.get("MIO_TRANSLATOR_HOME", "").strip():
        return
    destination = _require_real_directory(_default_writable_app_dir())
    _migrate_legacy_data(destination)


def writable_app_dir() -> Path:
    override = os.environ.get("MIO_TRANSLATOR_HOME", "").strip()
    destination = Path(override).expanduser() if override else _default_writable_app_dir()
    destination = _require_real_directory(destination)
    if not override:
        _migrate_legacy_files(destination)
    return destination


def _require_real_directory(path: Path) -> Path:
    normalized = _lexical_absolute_path(path)
    if not _ensure_real_directory(normalized):
        raise RuntimeError(f"Refusing to use an unsafe writable directory: {path}")
    return normalized


def require_real_directory(path: str | os.PathLike[str]) -> Path:
    """Return an absolute real directory, creating missing components safely."""

    return _require_real_directory(Path(path))


def require_existing_real_directory(path: str | os.PathLike[str]) -> Path:
    """Return an existing real directory without creating missing components."""

    normalized = _lexical_absolute_path(Path(path))
    if not _validate_existing_real_directory(normalized):
        raise RuntimeError(f"Refusing to use an unsafe or missing directory: {path}")
    return normalized


def _validate_file_name(path: Path) -> None:
    name = path.name
    if not name or name in (os.curdir, os.pardir) or "\x00" in name:
        raise ValueError(f"Invalid writable file name: {name!r}")
    if os.name != "nt":
        return
    if ":" in name or name.endswith((" ", ".")):
        raise ValueError(f"Invalid Windows writable file name: {name!r}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if name.split(".", 1)[0].upper() in reserved:
        raise ValueError(f"Reserved Windows writable file name: {name!r}")


def secure_file_path(
    path: str | os.PathLike[str],
    *,
    must_exist: bool = False,
) -> Path:
    """Validate a writable regular-file path without following filesystem links."""

    normalized = _lexical_absolute_path(Path(path))
    _validate_file_name(normalized)
    parent = (
        require_existing_real_directory(normalized.parent)
        if must_exist
        else _require_real_directory(normalized.parent)
    )
    candidate = parent / normalized.name
    file_stat = _safe_lstat(candidate)
    if file_stat is None:
        if must_exist:
            raise FileNotFoundError(candidate)
        return candidate
    if not _is_regular_private_file(file_stat):
        raise RuntimeError(f"Refusing to use an unsafe writable file: {candidate}")
    return candidate


@contextmanager
def open_secure_read(
    path: str | os.PathLike[str],
    *,
    binary: bool = True,
    encoding: str = "utf-8",
    buffering: int = -1,
):
    """Open a private regular file and detect replacement or mutation races."""

    candidate = secure_file_path(path, must_exist=True)
    expected_stat = _safe_lstat(candidate)
    if expected_stat is None or not _is_regular_private_file(expected_stat):
        raise RuntimeError(f"Refusing to read an unsafe writable file: {candidate}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(candidate, flags)
    handle = None
    try:
        opened_stat = os.fstat(fd)
        on_disk_stat = _safe_lstat(candidate)
        if (
            not _is_regular_private_file(opened_stat)
            or on_disk_stat is None
            or not _is_regular_private_file(on_disk_stat)
            or not _same_file_snapshot(expected_stat, opened_stat)
            or not _same_file_snapshot(opened_stat, on_disk_stat)
        ):
            raise RuntimeError(f"Writable file changed while it was opened: {candidate}")

        if binary:
            handle = os.fdopen(fd, "rb", buffering=buffering, closefd=False)
        else:
            handle = os.fdopen(
                fd,
                "r",
                encoding=encoding,
                buffering=buffering,
                closefd=False,
            )
        yield handle

        final_stat = os.fstat(fd)
        final_disk_stat = _safe_lstat(candidate)
        if (
            not _same_file_snapshot(opened_stat, final_stat)
            or final_disk_stat is None
            or not _is_regular_private_file(final_disk_stat)
            or not _same_file_snapshot(final_stat, final_disk_stat)
        ):
            raise RuntimeError(f"Writable file changed while it was read: {candidate}")
    finally:
        if handle is not None:
            handle.close()
        os.close(fd)


def secure_file_size(
    path: str | os.PathLike[str],
    *,
    missing_ok: bool = False,
) -> int:
    """Return the size of one validated private regular file."""

    try:
        candidate = secure_file_path(path, must_exist=True)
    except FileNotFoundError:
        if missing_ok:
            return 0
        raise
    file_stat = _safe_lstat(candidate)
    if file_stat is None or not _is_regular_private_file(file_stat):
        raise RuntimeError(f"Refusing to inspect an unsafe writable file: {candidate}")
    return int(file_stat.st_size)


def read_secure_bytes(
    path: str | os.PathLike[str],
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read one private regular file while checking for path replacement races."""

    candidate = secure_file_path(path, must_exist=True)
    expected_size = secure_file_size(candidate)
    if max_bytes is not None and (max_bytes < 0 or expected_size > max_bytes):
        raise ValueError(f"Writable file exceeds the allowed size: {candidate}")

    with open_secure_read(candidate, binary=True) as handle:
        payload = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
    if max_bytes is not None and len(payload) > max_bytes:
        raise ValueError(f"Writable file exceeds the allowed size: {candidate}")
    return payload


def read_secure_text(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
) -> str:
    return read_secure_bytes(path, max_bytes=max_bytes).decode(encoding)


def open_secure_append(
    path: str | os.PathLike[str],
    *,
    binary: bool = False,
    encoding: str = "utf-8",
    buffering: int = -1,
):
    """Open a private regular file for append after validating the opened handle."""

    candidate = secure_file_path(path)
    expected_stat = _safe_lstat(candidate)
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(candidate, flags, 0o600)
    try:
        opened_stat = os.fstat(fd)
        on_disk_stat = _safe_lstat(candidate)
        if (
            not _is_regular_private_file(opened_stat)
            or on_disk_stat is None
            or not _is_regular_private_file(on_disk_stat)
            or not _same_file_snapshot(opened_stat, on_disk_stat)
            or (
                expected_stat is not None
                and not _same_file_snapshot(expected_stat, opened_stat)
            )
        ):
            raise RuntimeError(f"Writable append target changed while opening: {candidate}")
        if binary:
            return os.fdopen(fd, "ab", buffering=buffering, closefd=True)
        return os.fdopen(
            fd,
            "a",
            encoding=encoding,
            buffering=buffering,
            closefd=True,
        )
    except BaseException:
        os.close(fd)
        raise


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink_if_same_file(path: Path, expected_stat: os.stat_result | None) -> None:
    if expected_stat is None:
        return
    try:
        current_stat = path.lstat()
        if _same_file_object(expected_stat, current_stat):
            path.unlink()
    except OSError:
        pass


def secure_unlink(
    path: str | os.PathLike[str],
    *,
    missing_ok: bool = False,
    expected_stat: os.stat_result | None = None,
) -> None:
    """Remove only an unchanged private regular file without following links.

    When ``expected_stat`` is supplied, cleanup is restricted to that exact file
    snapshot so a replaced temporary path is never removed accidentally.
    """

    normalized = _lexical_absolute_path(Path(path))
    _validate_file_name(normalized)
    parent = require_existing_real_directory(normalized.parent)
    candidate = parent / normalized.name
    initial_stat = _safe_lstat(candidate)
    if initial_stat is None:
        if missing_ok:
            return
        raise FileNotFoundError(candidate)
    if not _is_regular_private_file(initial_stat):
        raise RuntimeError(f"Refusing to remove an unsafe writable file: {candidate}")
    if expected_stat is not None and (
        not _is_regular_private_file(expected_stat)
        or not _same_file_snapshot(expected_stat, initial_stat)
    ):
        raise RuntimeError(f"Writable file was replaced before removal: {candidate}")

    require_existing_real_directory(parent)
    current_stat = _safe_lstat(candidate)
    if (
        current_stat is None
        or not _is_regular_private_file(current_stat)
        or not _same_file_snapshot(initial_stat, current_stat)
    ):
        raise RuntimeError(f"Writable file changed before removal: {candidate}")
    candidate.unlink()
    _fsync_directory(parent)


def _publish_staged_secure_file(
    staged: Path,
    candidate: Path,
    *,
    initial_destination_stat: os.stat_result | None,
    expected_size: int | None,
    overwrite: bool = True,
) -> Path:
    staged_stat = _safe_lstat(staged)
    if staged_stat is None or not _is_regular_private_file(staged_stat):
        raise RuntimeError(f"Refusing to publish an unsafe staged file: {staged}")
    if expected_size is not None and (
        expected_size < 0 or int(staged_stat.st_size) != int(expected_size)
    ):
        raise ValueError(
            f"Staged file has an unexpected size: {staged} "
            f"({staged_stat.st_size} != {expected_size})"
        )

    with open_secure_read(staged, binary=True) as handle:
        opened_stat = os.fstat(handle.fileno())
        if not _same_file_snapshot(staged_stat, opened_stat):
            raise RuntimeError(f"Staged file changed before publication: {staged}")
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass

    require_existing_real_directory(candidate.parent)
    current_destination_stat = _safe_lstat(candidate)
    if initial_destination_stat is None:
        if current_destination_stat is not None:
            raise RuntimeError(
                f"Writable destination appeared during publication: {candidate}"
            )
    elif (
        current_destination_stat is None
        or not _is_regular_private_file(current_destination_stat)
        or not _same_file_snapshot(
            initial_destination_stat,
            current_destination_stat,
        )
    ):
        raise RuntimeError(f"Writable destination changed during publication: {candidate}")

    current_staged_stat = _safe_lstat(staged)
    if (
        current_staged_stat is None
        or not _is_regular_private_file(current_staged_stat)
        or not _same_file_snapshot(staged_stat, current_staged_stat)
    ):
        raise RuntimeError(f"Staged file changed during publication: {staged}")

    if overwrite:
        os.replace(staged, candidate)
    else:
        if initial_destination_stat is not None:
            raise FileExistsError(candidate)
        _publish_staged_no_replace(staged, candidate)
    published_stat = _safe_lstat(candidate)
    if (
        published_stat is None
        or not _is_regular_private_file(published_stat)
        or not _same_file_object(current_staged_stat, published_stat)
        or (
            expected_size is not None
            and int(published_stat.st_size) != int(expected_size)
        )
    ):
        raise RuntimeError(f"Published file failed post-publication checks: {candidate}")
    _fsync_directory(candidate.parent)
    return candidate


def _publish_staged_no_replace(staged: Path, candidate: Path) -> None:
    """Atomically move ``staged`` into place without replacing ``candidate``."""

    if os.name == "nt":
        # Windows rename is atomic on one volume and fails when the destination
        # already exists.  Unlike a hard-link-then-unlink sequence, it cannot
        # leave both names behind after an interrupted migration.
        os.rename(staged, candidate)
        return

    # POSIX os.rename replaces an existing destination, so use link(2)'s
    # atomic create-if-absent semantics.  Roll back the published name if the
    # source unlink fails, avoiding a persistent two-link file during ordinary
    # errors; stale migration staging names are also cleaned on the next run.
    os.link(staged, candidate, follow_symlinks=False)
    try:
        staged.unlink()
    except BaseException:
        staged_stat = _safe_lstat(staged)
        candidate_stat = _safe_lstat(candidate)
        if (
            staged_stat is not None
            and candidate_stat is not None
            and _same_file_object(staged_stat, candidate_stat)
        ):
            try:
                candidate.unlink()
            except OSError:
                pass
        raise


def atomic_replace_secure_file(
    staged_path: str | os.PathLike[str],
    destination_path: str | os.PathLike[str],
    *,
    expected_size: int | None = None,
) -> Path:
    """Atomically publish a completed staged file from the destination directory."""

    staged = secure_file_path(staged_path, must_exist=True)
    candidate = secure_file_path(destination_path)
    if os.path.normcase(os.fspath(staged.parent)) != os.path.normcase(
        os.fspath(candidate.parent)
    ):
        raise ValueError("Staged and destination files must share a directory")
    if os.path.normcase(os.fspath(staged)) == os.path.normcase(os.fspath(candidate)):
        raise ValueError("Staged and destination files must be different paths")

    initial_destination_stat = _safe_lstat(candidate)
    if initial_destination_stat is not None and not _is_regular_private_file(
        initial_destination_stat
    ):
        raise RuntimeError(f"Refusing to replace an unsafe writable file: {candidate}")
    return _publish_staged_secure_file(
        staged,
        candidate,
        initial_destination_stat=initial_destination_stat,
        expected_size=expected_size,
    )


def atomic_copy_secure_file(
    source_path: str | os.PathLike[str],
    destination_path: str | os.PathLike[str],
    *,
    max_bytes: int | None = None,
    overwrite: bool = True,
    mode: int = 0o600,
) -> Path:
    """Stream-copy a private regular file and atomically publish the result."""

    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    source = secure_file_path(source_path, must_exist=True)
    source_stat = _safe_lstat(source)
    if source_stat is None or not _is_regular_private_file(source_stat):
        raise RuntimeError(f"Refusing to copy an unsafe source file: {source}")
    if max_bytes is not None and int(source_stat.st_size) > max_bytes:
        raise ValueError(f"Source file exceeds the allowed size: {source}")

    candidate = secure_file_path(destination_path)
    if os.path.normcase(os.fspath(source)) == os.path.normcase(os.fspath(candidate)):
        raise ValueError("Source and destination files must be different paths")
    initial_destination_stat = _safe_lstat(candidate)
    if initial_destination_stat is not None:
        if not _is_regular_private_file(initial_destination_stat):
            raise RuntimeError(f"Refusing to replace an unsafe writable file: {candidate}")
        if not overwrite:
            raise FileExistsError(candidate)

    fd = -1
    temp_path: Path | None = None
    temp_stat: os.stat_result | None = None
    published = False
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.{os.getpid()}.",
            suffix=".copy.tmp",
            dir=candidate.parent,
        )
        temp_path = Path(temp_name)
        temp_stat = os.fstat(fd)
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass

        copied = 0
        with open_secure_read(source, binary=True) as source_handle:
            with os.fdopen(fd, "wb", closefd=True) as destination_handle:
                fd = -1
                while True:
                    chunk = source_handle.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if max_bytes is not None and copied > max_bytes:
                        raise ValueError(f"Source file exceeds the allowed size: {source}")
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
                temp_stat = os.fstat(destination_handle.fileno())

        if copied != int(source_stat.st_size):
            raise RuntimeError(f"Source file changed while it was copied: {source}")
        _publish_staged_secure_file(
            temp_path,
            candidate,
            initial_destination_stat=initial_destination_stat,
            expected_size=copied,
            overwrite=overwrite,
        )
        published = True
        return candidate
    finally:
        if fd >= 0:
            os.close(fd)
        if not published and temp_path is not None:
            _unlink_if_same_file(temp_path, temp_stat)


def atomic_write_bytes(
    path: str | os.PathLike[str],
    payload: bytes | bytearray | memoryview,
    *,
    overwrite: bool = True,
    mode: int = 0o600,
) -> Path:
    """Durably publish bytes without following links or exposing partial data."""

    candidate = secure_file_path(path)
    initial_stat = _safe_lstat(candidate)
    if initial_stat is not None and not _is_regular_private_file(initial_stat):
        raise RuntimeError(f"Refusing to replace an unsafe writable file: {candidate}")
    if initial_stat is not None and not overwrite:
        raise FileExistsError(candidate)

    fd = -1
    temp_path: Path | None = None
    temp_stat: os.stat_result | None = None
    published = False
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.",
            suffix=".tmp",
            dir=candidate.parent,
        )
        temp_path = Path(temp_name)
        temp_stat = os.fstat(fd)
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = -1
            handle.write(bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
            temp_stat = os.fstat(handle.fileno())

        if not _ensure_real_directory(candidate.parent):
            raise RuntimeError(f"Writable file parent became unsafe: {candidate.parent}")
        current_stat = _safe_lstat(candidate)
        if initial_stat is None:
            if current_stat is not None:
                raise RuntimeError(f"Writable file appeared during atomic write: {candidate}")
        elif (
            current_stat is None
            or not _is_regular_private_file(current_stat)
            or not _same_file_snapshot(initial_stat, current_stat)
        ):
            raise RuntimeError(f"Writable file changed during atomic write: {candidate}")

        on_disk_temp_stat = _safe_lstat(temp_path)
        if (
            temp_stat is None
            or on_disk_temp_stat is None
            or not _is_regular_private_file(on_disk_temp_stat)
            or not _same_file_snapshot(temp_stat, on_disk_temp_stat)
        ):
            raise RuntimeError(f"Atomic-write temporary file became unsafe: {temp_path}")

        if overwrite:
            os.replace(temp_path, candidate)
        else:
            os.link(temp_path, candidate, follow_symlinks=False)
            temp_path.unlink()
        published = True
        _fsync_directory(candidate.parent)
        return candidate
    finally:
        if fd >= 0:
            os.close(fd)
        if not published and temp_path is not None:
            _unlink_if_same_file(temp_path, temp_stat)


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    overwrite: bool = True,
    mode: int = 0o600,
) -> Path:
    return atomic_write_bytes(
        path,
        text.encode(encoding),
        overwrite=overwrite,
        mode=mode,
    )


def secure_writable_subdirectory(*relative_parts: str | os.PathLike[str]) -> Path:
    """Create and return a real directory below the writable application root.

    The path is checked lexically and every existing or newly-created component
    is rejected if it is a symbolic link, junction, or other reparse point.
    """

    relative = Path(*relative_parts)
    if (
        not relative_parts
        or not relative.parts
        or relative.is_absolute()
        or relative.anchor
        or any(part in (os.curdir, os.pardir) for part in relative.parts)
    ):
        raise ValueError(f"Invalid writable subdirectory path: {relative}")

    root = _lexical_absolute_path(writable_app_dir())
    candidate = _lexical_absolute_path(root / relative)
    try:
        common = Path(os.path.commonpath((os.fspath(root), os.fspath(candidate))))
    except ValueError as exc:
        raise RuntimeError(
            f"Writable subdirectory escapes application root: {relative}"
        ) from exc
    if os.path.normcase(os.fspath(common)) != os.path.normcase(os.fspath(root)):
        raise RuntimeError(f"Writable subdirectory escapes application root: {relative}")
    return _require_real_directory(candidate)


def app_temp_dir() -> Path:
    return _require_real_directory(writable_app_dir() / "temp")


def backgrounds_dir() -> Path:
    return _require_real_directory(writable_app_dir() / "backgrounds")
