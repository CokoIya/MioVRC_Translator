# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all, copy_metadata


_APPLOCAL_RUNTIME_OVERRIDES = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "ucrtbase.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}
_API_SET_PREFIXES = ("api-ms-win-core-", "api-ms-win-crt-")


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

        # Java の PATH から拾われる API Set DLL は混在すると不安定になるため同梱しない。
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


def _package_dir(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def _append_package_tree(datas_list, package_name: str) -> None:
    package_dir = _package_dir(package_name)
    if package_dir is not None and package_dir.exists():
        datas_list.append((str(package_dir), package_name.replace(".", "/")))


def _append_metadata(datas_list, distribution_name: str) -> None:
    try:
        datas_list += copy_metadata(distribution_name)
    except Exception:
        pass


datas = [("config.example.json", "."), ("assets", "assets")]
models_root = Path("models")
sensevoice_bundle_dir = models_root / "sensevoice-small"
include_sensevoice_bundle = False

if models_root.exists():
    for model_dir in sorted(models_root.glob("whisper-*")):
        if model_dir.is_dir():
            datas.append((str(model_dir), f"models/{model_dir.name}"))
    if sensevoice_bundle_dir.is_dir():
        datas.append((str(sensevoice_bundle_dir), "models/sensevoice-small"))
        include_sensevoice_bundle = True

binaries = []
hiddenimports = []

for package_name in ("faster_whisper", "ctranslate2", "huggingface_hub"):
    tmp_ret = collect_all(package_name)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

if include_sensevoice_bundle:
    # Copy the package trees directly so the beta ASR stack stays importable
    # without forcing PyInstaller to analyze thousands of torch submodules.
    for package_name in ("funasr", "modelscope", "torch", "torchaudio", "torch_complex", "torchgen"):
        _append_package_tree(datas, package_name)
    for distribution_name in ("funasr", "modelscope", "torch", "torchaudio", "torch-complex"):
        _append_metadata(datas, distribution_name)

hiddenimports += [
    "faster_whisper",
    "ctranslate2",
    "huggingface_hub",
    "src.asr.factory",
    "src.asr.model_manager",
    "src.asr.sensevoice_asr",
    "src.asr.sensevoice_model_manager",
    "src.asr.whisper_asr",
]

excludes = [
    "torch",
    "torchaudio",
    "funasr",
    "modelscope",
    "torch_complex",
    "torchgen",
    # これらの大型依存関係は推移的に取り込まれるが、実行時には使用しない。
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
