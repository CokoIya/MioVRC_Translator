from __future__ import annotations

import os
import stat
import sys
import time
from importlib.machinery import ModuleSpec
from pathlib import Path

_RUNTIME_HOOK_STARTED_AT = time.perf_counter()


_STDIO_SINKS = []
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _ensure_stdio_streams() -> None:
    # Windowed PyInstaller apps run with stdout/stderr set to None. A few ML
    # dependencies still assume writable text streams during import.
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and hasattr(stream, "write"):
            continue
        sink = open(os.devnull, "w", encoding="utf-8")
        _STDIO_SINKS.append(sink)
        setattr(sys, attr, sink)


def _add_runtime_dir(path: Path) -> None:
    if not path.is_dir():
        return

    path_str = str(path)
    try:
        os.add_dll_directory(path_str)
    except (AttributeError, FileNotFoundError, OSError):
        pass

    current_path = os.environ.get("PATH", "")
    entries = current_path.split(os.pathsep) if current_path else []
    if path_str not in entries:
        os.environ["PATH"] = (
            path_str if not current_path else path_str + os.pathsep + current_path
        )


def _writable_app_dir() -> Path:
    override = os.environ.get("MIO_TRANSLATOR_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = (
            Path(local_app_data).expanduser()
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base / "Mio RealTime Translator"
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home
        else Path.home() / ".local" / "share"
    )
    return base / "mio-realtime-translator"


def _is_real_directory_chain(path: Path) -> bool:
    """Return whether every path component is a real directory, without links."""

    try:
        normalized = Path(os.path.abspath(os.fspath(path.expanduser())))
        if not normalized.is_absolute() or not normalized.anchor:
            return False
        anchor = Path(normalized.anchor)
        current = anchor
        candidates = [current]
        for part in normalized.parts[len(anchor.parts) :]:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            file_stat = candidate.lstat()
            attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
            if (
                not stat.S_ISDIR(file_stat.st_mode)
                or stat.S_ISLNK(file_stat.st_mode)
                or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                return False
    except (OSError, RuntimeError):
        return False
    return True


def _activate_external_cuda_runtime() -> None:
    site_packages = _writable_app_dir() / "runtime_cuda" / "site-packages"
    if not _is_real_directory_chain(site_packages):
        return

    site_text = str(site_packages)
    if site_text not in sys.path:
        sys.path.insert(0, site_text)
    for candidate in (
        site_packages,
        site_packages / "torch" / "lib",
        site_packages / "torchaudio" / "lib",
    ):
        if _is_real_directory_chain(candidate):
            _add_runtime_dir(candidate)


def _ensure_module_spec(module_name: str, module: object) -> None:
    if getattr(module, "__spec__", None) is not None:
        return
    try:
        module.__spec__ = ModuleSpec(
            module_name,
            getattr(module, "__loader__", None),
            origin=getattr(module, "__file__", None) or "frozen",
            is_package=hasattr(module, "__path__"),
        )
    except Exception:
        pass


def _register_distlib_finder() -> None:
    try:
        import pyimod02_importers
        from pip._vendor.distlib import resources as distlib_resources
    except Exception:
        return
    distlib_resources._finder_registry[pyimod02_importers.PyiFrozenLoader] = distlib_resources.ResourceFinder


def _needs_distlib_finder() -> bool:
    return any(
        argument in {"--mio-cuda-pip-check", "--mio-install-cuda-pytorch"}
        for argument in sys.argv[1:]
    )


if getattr(sys, "frozen", False):
    _ensure_stdio_streams()
    if _needs_distlib_finder():
        _register_distlib_finder()

    # Load only Mio's managed CUDA runtime. Importing a torch package from an
    # arbitrary system Python/Conda installation makes frozen behavior depend
    # on unrelated user environments and creates a code-loading trust bypass.
    _activate_external_cuda_runtime()

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # CUDA DLLs that belong to the optional managed runtime are removed from
    # the CPU application bundle by tools/pyinstaller_runtime_filter.py.  Do
    # not rescan and mutate torch/lib on every process launch.
    for candidate in (
        bundle_root,
        bundle_root / "torch" / "lib",
        bundle_root / "torchaudio" / "lib",
    ):
        _add_runtime_dir(candidate)

    _g2p_en_mod = sys.modules.get("g2p_en")
    if _g2p_en_mod is not None:
        _ensure_module_spec("g2p_en", _g2p_en_mod)

    os.environ["MIO_TRANSLATOR_RUNTIME_HOOK_MS"] = (
        f"{(time.perf_counter() - _RUNTIME_HOOK_STARTED_AT) * 1000.0:.3f}"
    )
