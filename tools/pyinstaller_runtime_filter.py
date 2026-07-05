from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable, Sequence, TypeAlias


TocEntry: TypeAlias = tuple[str, str, str]

_DEVELOPMENT_SUFFIXES = {
    ".a",
    ".c",
    ".cc",
    ".cmake",
    ".cpp",
    ".cxx",
    ".exp",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".ilk",
    ".ipynb",
    ".lib",
    ".obj",
    ".pdb",
    ".pxd",
    ".pxi",
    ".pyx",
}

_DEVELOPMENT_PATH_PARTS = {
    "__pycache__",
    ".github",
    "benchmark",
    "benchmarks",
    "doc",
    "docs",
    "example",
    "examples",
    "test",
    "tests",
}

_UNUSED_PACKAGE_ROOTS = {
    "cython",
    "pythonwin",
}

_HIDDEN_IMPORT_DROP_PARTS = _DEVELOPMENT_PATH_PARTS | {
    "__pyinstaller",
    "testing",
}

_HIDDEN_IMPORT_DROP_ROOTS = {
    "_pytest",
    "pytest",
    "pluggy",
    "iniconfig",
}

_QT_TRANSLATION_LOCALES = {
    "en",
    "ja",
    "ko",
    "ru",
    "zh_cn",
    "zh_tw",
}

_QT_WEBENGINE_LOCALE_PACKS = {
    "en-gb.pak",
    "en-us.pak",
    "ja.pak",
    "ko.pak",
    "ru.pak",
    "zh-cn.pak",
    "zh-tw.pak",
}

_UNUSED_PACKAGED_ASSETS = {
    "assets/icons/2.png",
    "assets/icons/img_0600.png",
    "assets/icons/mio_1.png",
}


def _normalize_path(path: object) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _lower_parts(path: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in _normalize_path(path).split("/") if part)


def _has_development_suffix(path: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered.endswith("/py.typed"):
        return True
    return PurePosixPath(lowered).suffix in _DEVELOPMENT_SUFFIXES


def _is_development_path(path: str) -> bool:
    parts = _lower_parts(path)
    return any(part in _DEVELOPMENT_PATH_PARTS for part in parts)


def _is_torch_header_path(parts: Sequence[str]) -> bool:
    return len(parts) >= 2 and parts[0] == "torch" and parts[1] == "include"


def _is_qt_debug_or_devtools_resource(parts: Sequence[str]) -> bool:
    if len(parts) < 3 or parts[0] != "pyside6" or parts[1] != "resources":
        return False
    filename = parts[-1]
    return (
        ".debug." in filename
        or filename.endswith(".debug.bin")
        or filename.endswith(".debug.pak")
        or "devtools" in filename
    )


def _is_qt_qml_path(parts: Sequence[str]) -> bool:
    return len(parts) >= 2 and parts[0] == "pyside6" and parts[1] == "qml"


def _is_unneeded_qt_webengine_locale_pack(parts: Sequence[str]) -> bool:
    if (
        len(parts) < 4
        or parts[0] != "pyside6"
        or parts[1] != "translations"
        or parts[2] != "qtwebengine_locales"
    ):
        return False
    filename = parts[-1]
    return filename.endswith(".pak") and filename not in _QT_WEBENGINE_LOCALE_PACKS


def _is_unneeded_qt_translation(parts: Sequence[str]) -> bool:
    if len(parts) < 3 or parts[0] != "pyside6" or parts[1] != "translations":
        return False
    filename = parts[-1]
    if not filename.endswith(".qm"):
        return False
    stem = filename[:-3]
    return not any(stem.endswith(f"_{locale}") for locale in _QT_TRANSLATION_LOCALES)


def _is_unused_packaged_asset(path: str) -> bool:
    return _normalize_path(path).lower() in _UNUSED_PACKAGED_ASSETS


def _is_unused_package_root(parts: Sequence[str]) -> bool:
    return bool(parts) and parts[0] in _UNUSED_PACKAGE_ROOTS


def _is_torch_build_tool_path(parts: Sequence[str]) -> bool:
    return len(parts) >= 2 and parts[0] == "torch" and parts[1] == "bin"


def should_keep_runtime_entry(dest_name: str, source_name: str = "") -> bool:
    """Return whether a PyInstaller TOC entry is needed at runtime.

    These rules intentionally target build-time artifacts and unused bundled
    resources. Runtime DLLs, PySide6 WebEngine resources, model dictionaries,
    package metadata, and application assets that are referenced by the app are
    left intact.
    """
    dest = _normalize_path(dest_name)
    source = _normalize_path(source_name)
    parts = _lower_parts(dest)

    if _is_unused_package_root(parts):
        return False
    if _is_unused_packaged_asset(dest):
        return False
    if _is_torch_build_tool_path(parts):
        return False
    if _is_torch_header_path(parts):
        return False
    if _is_qt_debug_or_devtools_resource(parts):
        return False
    if _is_qt_qml_path(parts):
        return False
    if _is_unneeded_qt_webengine_locale_pack(parts):
        return False
    if _is_unneeded_qt_translation(parts):
        return False

    for path in (dest, source):
        if _has_development_suffix(path):
            return False
        if _is_development_path(path):
            return False

    return True


def filter_runtime_entries(entries: Iterable[TocEntry]) -> list[TocEntry]:
    return [
        entry
        for entry in entries
        if should_keep_runtime_entry(entry[0], entry[1] if len(entry) > 1 else "")
    ]


def should_keep_hidden_import(module_name: str) -> bool:
    parts = tuple(part.lower() for part in str(module_name or "").split(".") if part)
    if not parts:
        return False
    if parts[0] in _HIDDEN_IMPORT_DROP_ROOTS:
        return False
    for part in parts:
        if part in _HIDDEN_IMPORT_DROP_PARTS:
            return False
        if "pytest" in part:
            return False
        if part.startswith("test_") or part.startswith("testing_") or part.endswith("_test"):
            return False
    return True


def filter_hiddenimports(module_names: Iterable[str]) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for module_name in module_names:
        if not should_keep_hidden_import(module_name):
            continue
        if module_name in seen:
            continue
        seen.add(module_name)
        filtered.append(module_name)
    return filtered
