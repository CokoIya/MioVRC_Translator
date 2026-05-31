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


def _pyside6_plugins_dir() -> Path | None:
    for candidate in [
        _python_dir().parent / "Lib" / "site-packages" / "PySide6" / "plugins",
        Path(sys.base_prefix) / "Lib" / "site-packages" / "PySide6" / "plugins",
    ]:
        if candidate.exists():
            return candidate
    return None


def _collect_qt_plugins(subdirs: tuple[str, ...]) -> list[tuple[str, str]]:
    plugins_base = _pyside6_plugins_dir()
    collected = []
    if plugins_base is None:
        return collected
    for subdir in subdirs:
        plugin_dir = plugins_base / subdir
        if not plugin_dir.is_dir():
            continue
        for src_file in plugin_dir.iterdir():
            if src_file.is_file() and src_file.suffix in (".dll", ".so", ".dylib"):
                collected.append((str(src_file), str(Path("PySide6") / subdir)))
    return collected


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


datas = [
    ("config.example.json", "."),
    ("assets", "assets"),
]

if Path("src/audio/models/silero_vad.jit").is_file():
    datas.append(("src/audio/models/silero_vad.jit", "src/audio/models"))

binaries: list = []
hiddenimports: list = []

for package_name in (
    "funasr",
    "modelscope",
    "torch",
    "PIL",
    "torchaudio",
    "huggingface_hub",
    "tokenizers",
    "tqdm",
    "editdistance",
    "soundcard",
    "cffi",
    "pycparser",
    "pydantic",
    "pydantic_core",
    "typing_inspection",
    "annotated_types",
    "charset_normalizer",
    "google.auth",
    "google.genai",
    "websockets",
    "rich",
    "markdown_it",
    "mdurl",
    "av",
    "style_bert_vits2",
    "transformers",
    "packaging",
    "sentencepiece",
    "google.protobuf",
    "jieba",
    "pypinyin",
    "cn2an",
    "g2p_en",
):
    tmp_ret = collect_all(package_name)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

binaries += _collect_qt_plugins(("platforms", "imageformats", "styles", "iconengines", "graphicseffects"))

hiddenimports += [
    "typing_extensions",
    "src.asr.factory",
    "src.asr.base",
    "src.asr.errors",
    "src.asr.asr_cleaner",
    "src.asr.text_corrections",
    "src.asr.fallback_asr",
    "src.asr.sensevoice_asr",
    "src.asr.model_manager",
    "src.asr.qwen3_asr",
    "src.asr.gemini_live_asr",
    "src.asr.webspeech_asr",
    "google.genai",
    "google.auth",
    "websockets",
    "openai",
    "sentencepiece",
    "sentencepiece._sentencepiece",
    "sentencepiece.sentencepiece_model_pb2",
    "google.protobuf",
    "google.protobuf.message",
    "google.protobuf.internal",
    "jieba",
    "jieba.posseg",
    "pypinyin",
    "cn2an",
    "g2p_en",
    "src.asr.sensevoice_model_manager",
    "src.asr.hf_model_downloader",
    "src.utils.locale_detect",
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "src.ui_qt.app",
    "src.ui_qt.main_window",
    "src.ui_qt.settings_window",
    "src.ui_qt.floating_window",
    "src.ui_qt.sponsor_window",
    "src.ui_qt.update_window",
    "src.ui_qt.model_download_dialog",
    "src.ui_qt.text_input_window",
]

excludes = [
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
    "tkinter",
    "_tkinter",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["tools/pyinstaller_hooks"],
    hooksconfig={},
    runtime_hooks=["src/runtime_hooks/pyi_rth_bundle_paths.py"],
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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icons/app_icon_mio.ico",
    version="windows_version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MioTranslator",
)
