# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all


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

datas = [('config.example.json', '.'), ('assets', 'assets')]
if Path('models').exists():
    datas.append(('models', 'models'))
binaries = []
hiddenimports = []

tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('huggingface_hub')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

hiddenimports += [
    'faster_whisper',
    'ctranslate2',
    'huggingface_hub',
    'src.asr.factory',
    'src.asr.model_manager',
    'src.asr.whisper_asr',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # これらの大型依存関係は推移的に取り込まれるが、実行時には使用しない。
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'numba', 'llvmlite',
        'scipy',
        'sklearn', 'scikit_learn',
        'matplotlib',
        'IPython', 'ipykernel', 'ipywidgets',
        'notebook', 'jupyter',
        'pandas',
        'lxml',
        'aliyunsdkcore',
    ],
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
    name='MioTranslator',
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
    icon='assets/icons/app_icon_mio.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MioTranslator',
)
