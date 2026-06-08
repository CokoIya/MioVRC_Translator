from __future__ import annotations

import os
import re
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path


_STDIO_SINKS = []
_STALE_CUDA_DLL_PATTERNS = (
    "torch_cuda*.dll",
    "c10_cuda*.dll",
    "caffe2_nvrtc*.dll",
    "cudart*.dll",
    "cublas*.dll",
    "cublasLt*.dll",
    "cudnn*.dll",
    "cufft*.dll",
    "curand*.dll",
    "cusolver*.dll",
    "cusparse*.dll",
    "nvrtc*.dll",
    "nvToolsExt*.dll",
)


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


def _remove_stale_cuda_dlls(bundle_root: Path) -> None:
    torch_lib = bundle_root / "torch" / "lib"
    if not torch_lib.is_dir():
        return

    for pattern in _STALE_CUDA_DLL_PATTERNS:
        for candidate in torch_lib.glob(pattern):
            if not candidate.is_file():
                continue
            try:
                candidate.unlink()
            except OSError:
                pass


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
    override = os.environ.get("MIO_TRANSLATOR_HOME")
    if override:
        return Path(override).expanduser()
    return Path(sys.executable).resolve().parent


def _activate_external_cuda_runtime() -> None:
    site_packages = _writable_app_dir() / "runtime_cuda" / "site-packages"
    if not site_packages.is_dir():
        return

    site_text = str(site_packages)
    if site_text not in sys.path:
        sys.path.insert(0, site_text)
    for candidate in (
        site_packages,
        site_packages / "torch" / "lib",
        site_packages / "torchaudio" / "lib",
    ):
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


# --- System CUDA torch detection (no subprocess, DLL presence check only) ---
_REQUIRED_CUDA_DLLS = frozenset(("torch_cuda.dll", "c10_cuda.dll"))


def _find_python_site_packages_roots() -> list[Path]:
    """Enumerate likely site-packages directories across common Python installations."""
    roots: list[Path] = []
    seen: set[str] = set()
    username = os.environ.get("USERNAME", "")

    def _add(path: Path) -> None:
        if path.is_dir():
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                roots.append(path)

    bp = getattr(sys, "base_prefix", None)
    if bp:
        _add(Path(bp, "Lib", "site-packages"))

    for sp in (
        Path("C:/Python/python311/Lib/site-packages"),
        Path("C:/Program Files/Python311/Lib/site-packages"),
        Path("C:/Users", username, "AppData", "Local", "Programs", "Python", "Python311", "Lib", "site-packages"),
        Path("C:/Users", username, "AppData", "Roaming", "Python", "Python311", "site-packages"),
    ):
        _add(sp)

    try:
        import site
        for sp in site.getsitepackages():
            _add(Path(sp))
        us = site.getusersitepackages()
        if us:
            _add(Path(us))
    except Exception:
        pass

    home = Path.home()
    for conda_root in (
        home / "anaconda3",
        home / "miniconda3",
        home / "miniforge3",
        home / "mambaforge",
        Path("C:/ProgramData/Anaconda3"),
        Path("C:/ProgramData/miniconda3"),
    ):
        _add(conda_root / "Lib" / "site-packages")
        envs = conda_root / "envs"
        if envs.is_dir():
            for env_dir in envs.iterdir():
                if env_dir.is_dir():
                    _add(env_dir / "Lib" / "site-packages")

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        _add(Path(conda_prefix) / "Lib" / "site-packages")

    return roots


def _site_packages_has_cuda_torch(sp: Path) -> bool:
    """Return True if site-packages contains a CUDA-enabled torch installation."""
    torch_lib = sp / "torch" / "lib"
    if not torch_lib.is_dir():
        return False
    present = {f.name for f in torch_lib.iterdir() if f.is_file()}
    if not _REQUIRED_CUDA_DLLS.issubset(present):
        return False
    if not (sp / "torch" / "__init__.py").is_file():
        return False
    return True


def _read_version_major_minor(version_file: Path) -> str | None:
    """Extract 'X.Y' from torch/version.py or torchaudio/version.py."""
    try:
        text = version_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not m:
        return None
    parts = m.group(1).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else None


def _version_compatible(sys_sp: Path, bundle_root: Path) -> bool:
    """Return True when system torch/torchaudio major.minor match the bundled versions."""
    bundled_torch_ver = _read_version_major_minor(bundle_root / "torch" / "version.py")
    if not bundled_torch_ver:
        return False
    sys_torch_ver = _read_version_major_minor(sys_sp / "torch" / "version.py")
    if not sys_torch_ver or sys_torch_ver != bundled_torch_ver:
        return False

    bundled_ta_ver = _read_version_major_minor(bundle_root / "torchaudio" / "version.py")
    if bundled_ta_ver:
        sys_ta_ver = _read_version_major_minor(sys_sp / "torchaudio" / "version.py")
        if not sys_ta_ver or sys_ta_ver != bundled_ta_ver:
            return False

    return True


def _prepend_system_cuda_torch() -> bool:
    """Search common Python paths for a CUDA-enabled torch and prepend it to sys.path.
    Returns True when system CUDA torch was found and prepended.
    """
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    for sp in _find_python_site_packages_roots():
        if not _site_packages_has_cuda_torch(sp):
            continue
        if not _version_compatible(sp, bundle_root):
            continue
        sp_str = str(sp)
        if sp_str not in sys.path:
            sys.path.insert(0, sp_str)
        for d in (sp / "torch" / "lib", sp / "torch", sp / "torchaudio", sp / "torchaudio" / "lib"):
            if d.is_dir():
                _add_runtime_dir(d)
        return True
    return False


if getattr(sys, "frozen", False):
    _ensure_stdio_streams()
    _register_distlib_finder()

    # Priority 1: system CUDA torch
    system_cuda_active = _prepend_system_cuda_torch()

    # Priority 2: runtime_cuda/site-packages when no compatible system CUDA torch exists
    if not system_cuda_active:
        _activate_external_cuda_runtime()

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    _remove_stale_cuda_dlls(bundle_root)
    for candidate in (
        bundle_root,
        bundle_root / "torch" / "lib",
        bundle_root / "torchaudio" / "lib",
    ):
        _add_runtime_dir(candidate)

    _g2p_en_mod = sys.modules.get("g2p_en")
    if _g2p_en_mod is not None:
        _ensure_module_spec("g2p_en", _g2p_en_mod)