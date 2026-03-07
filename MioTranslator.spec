# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import copy_metadata


_APPLOCAL_RUNTIME_OVERRIDES = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "ucrtbase.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}
_API_SET_PREFIXES = ("api-ms-win-core-", "api-ms-win-crt-")
_VENDOR_PYTORCH_ROOT = Path(".vendor") / "pytorch_cpu"
_PRUNED_PACKAGE_DIRS = {"__pycache__", "include"}
_PRUNED_PACKAGE_SUFFIXES = {
    ".cmake",
    ".cuh",
    ".h",
    ".hpp",
    ".html",
    ".js",
    ".lib",
    ".mjs",
    ".pyc",
    ".pyi",
    ".pyo",
    ".thrift",
}


def _python_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _system32_dir() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def _preferred_runtime_binary(name: str) -> str | None:
    base_name = Path(name).name
    lowered = base_name.lower()

    search_dirs: list[Path] = []
    if lowered.startswith("vcruntime140"):
        search_dirs.append(_python_dir())
    search_dirs.append(_system32_dir())

    for directory in search_dirs:
        candidate = directory / base_name
        if candidate.exists():
            return str(candidate)
    return None


def _sanitize_analysis_binaries(entries) -> TOC:
    sanitized = []
    seen_names: set[str] = set()

    for name, src, kind in entries:
        base_name = Path(name).name
        lowered = base_name.lower()

        if lowered.startswith(_API_SET_PREFIXES):
            continue

        if lowered in _APPLOCAL_RUNTIME_OVERRIDES:
            replacement = _preferred_runtime_binary(base_name)
            if replacement is not None:
                src = replacement

        dedupe_key = name.lower()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        sanitized.append((name, src, kind))

    return TOC(sanitized)


def _vendor_package_dir(package_name: str) -> Path | None:
    candidate = _VENDOR_PYTORCH_ROOT / package_name.replace(".", os.sep)
    if candidate.exists():
        return candidate
    return None


def _package_dir(package_name: str) -> Path | None:
    vendor_dir = _vendor_package_dir(package_name)
    if vendor_dir is not None:
        return vendor_dir

    spec = importlib.util.find_spec(package_name)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def _append_dir_tree(datas_list, source_dir: Path, dest_root: str, *, prune: bool = False) -> None:
    if not source_dir.exists():
        return

    if not prune:
        datas_list.append((str(source_dir), dest_root))
        return

    dest_root_path = Path(dest_root)
    for file_path in source_dir.rglob("*"):
        if not file_path.is_file():
            continue

        rel_path = file_path.relative_to(source_dir)
        if any(part in _PRUNED_PACKAGE_DIRS for part in rel_path.parts):
            continue
        if file_path.suffix.lower() in _PRUNED_PACKAGE_SUFFIXES:
            continue

        datas_list.append(
            (
                str(file_path),
                str((dest_root_path / rel_path.parent).as_posix()).rstrip("/"),
            )
        )


def _append_package_tree(datas_list, package_name: str, *, prune: bool = False) -> None:
    package_dir = _package_dir(package_name)
    if package_dir is not None and package_dir.exists():
        _append_dir_tree(datas_list, package_dir, package_name.replace(".", "/"), prune=prune)


def _append_metadata(datas_list, distribution_name: str) -> None:
    try:
        datas_list += copy_metadata(distribution_name)
    except Exception:
        pass


def _append_vendor_dist_info(datas_list, distribution_prefix: str) -> None:
    if not _VENDOR_PYTORCH_ROOT.exists():
        return
    for path in sorted(_VENDOR_PYTORCH_ROOT.glob(f"{distribution_prefix}-*.dist-info")):
        _append_dir_tree(datas_list, path, path.name)


datas = [("config.example.json", "."), ("assets", "assets")]
models_root = Path("models")
sensevoice_bundle_dir = models_root / "sensevoice-small"
if sensevoice_bundle_dir.is_dir():
    datas.append((str(sensevoice_bundle_dir), "models/sensevoice-small"))

for package_name in ("funasr", "modelscope", "torch", "torchaudio", "torch_complex", "torchgen"):
    _append_package_tree(datas, package_name, prune=True)

for distribution_name in ("funasr", "modelscope", "torch-complex"):
    _append_metadata(datas, distribution_name)

if _VENDOR_PYTORCH_ROOT.exists():
    _append_vendor_dist_info(datas, "torch")
    _append_vendor_dist_info(datas, "torchaudio")
else:
    _append_metadata(datas, "torch")
    _append_metadata(datas, "torchaudio")

binaries = []
hiddenimports = [
    "src.asr.factory",
    "src.asr.sensevoice_asr",
    "src.asr.sensevoice_model_manager",
]

excludes = [
    "faster_whisper",
    "ctranslate2",
    "huggingface_hub",
    "funasr",
    "modelscope",
    "torch",
    "torchaudio",
    "torch_complex",
    "torchgen",
    "torchvision",
    "tensorflow",
    "keras",
    "numba",
    "llvmlite",
    "scipy",
    "sklearn",
    "scikit_learn",
    "matplotlib",
    "IPython",
    "ipykernel",
    "ipywidgets",
    "notebook",
    "jupyter",
    "pandas",
    "lxml",
    "aliyunsdkcore",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
a.binaries = _sanitize_analysis_binaries(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MioTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icons/app_icon_mio.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MioTranslator",
)
