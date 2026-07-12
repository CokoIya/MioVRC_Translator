# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 Mio RealTime Translator contributors

"""Resolve trusted inbox Windows executables without PATH search."""

from __future__ import annotations

import os
from pathlib import Path
import stat

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class WindowsExecutableResolutionError(RuntimeError):
    """Raised when a trusted Windows system executable cannot be resolved."""


def windows_system_directory() -> Path:
    """Return the native Windows system directory using the Win32 API."""

    if os.name != "nt":
        raise WindowsExecutableResolutionError("Windows system executables are unavailable")
    try:
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(32768)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_system_directory = kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = (wintypes.LPWSTR, wintypes.UINT)
        get_system_directory.restype = wintypes.UINT
        length = int(get_system_directory(buffer, len(buffer)))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsExecutableResolutionError(
            f"Unable to resolve the Windows system directory: {exc}"
        ) from exc

    if length <= 0 or length >= len(buffer):
        error_code = int(ctypes.get_last_error())
        detail = f" (Windows error {error_code})" if error_code else ""
        raise WindowsExecutableResolutionError(
            f"Unable to resolve the Windows system directory{detail}"
        )

    system_directory = Path(buffer.value)
    if not system_directory.is_absolute():
        raise WindowsExecutableResolutionError(
            "Windows returned a non-absolute system directory"
        )
    return system_directory


def _inspect_path(path: Path, *, expect_directory: bool) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise WindowsExecutableResolutionError(
            f"Trusted Windows system path is unavailable: {exc}"
        ) from exc

    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    if path.is_symlink() or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsExecutableResolutionError(
            "Trusted Windows system path must not be a symlink or reparse point"
        )
    expected_type = stat.S_ISDIR if expect_directory else stat.S_ISREG
    if not expected_type(file_stat.st_mode):
        kind = "directory" if expect_directory else "regular file"
        raise WindowsExecutableResolutionError(
            f"Trusted Windows system path is not a {kind}"
        )


def trusted_windows_system_executable(*relative_parts: str) -> Path:
    """Resolve and validate an executable beneath the native system directory.

    Each component must be a single literal path component.  The system
    directory, every descendant directory, and the executable itself must be
    non-reparse filesystem objects.
    """

    if not relative_parts:
        raise WindowsExecutableResolutionError("A system executable name is required")
    normalized_parts: list[str] = []
    for raw_part in relative_parts:
        part = str(raw_part or "")
        if (
            not part
            or part in {".", ".."}
            or Path(part).name != part
            or "/" in part
            or "\\" in part
            or ":" in part
        ):
            raise WindowsExecutableResolutionError(
                "System executable path contains an invalid component"
            )
        normalized_parts.append(part)

    system_directory = windows_system_directory()
    _inspect_path(system_directory, expect_directory=True)

    current = system_directory
    for part in normalized_parts[:-1]:
        current = current / part
        _inspect_path(current, expect_directory=True)

    executable = current / normalized_parts[-1]
    _inspect_path(executable, expect_directory=False)
    return executable


def trusted_windows_powershell_executable() -> Path:
    """Resolve the inbox Windows PowerShell executable."""

    return trusted_windows_system_executable(
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
