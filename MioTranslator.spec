# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all
from tools.pyinstaller_runtime_filter import filter_hiddenimports, filter_runtime_entries

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


import os as _os

# A test build (build_beta.ps1) stamps its version into the bundle, where
# src/version.py reads it back, and ships its own Windows version resource.
# A release sets neither and builds exactly as before.
_build_version_stamp = _os.environ.get("MIO_BUILD_VERSION_STAMP", "").strip()
_windows_version_info = (
    _os.environ.get("MIO_BUILD_VERSION_INFO", "").strip() or "windows_version_info.txt"
)

datas = [
    ("config.example.json", "."),
    ("LICENSE", "."),
    ("NOTICE", "."),
    ("assets", "assets"),
]
if _build_version_stamp:
    stamp_path = Path(_build_version_stamp)
    if stamp_path.name != "mio_build_version.txt" or not stamp_path.is_file():
        raise SystemExit(
            f"MIO_BUILD_VERSION_STAMP must point at an existing mio_build_version.txt: {_build_version_stamp}"
        )
    datas.append((str(stamp_path), "."))

for optional_data in ("BRANDING.md", "THIRD_PARTY_LICENSES.md"):
    if Path(optional_data).is_file():
        datas.append((optional_data, "."))

if Path("src/audio/models/silero_vad.jit").is_file():
    datas.append(("src/audio/models/silero_vad.jit", "src/audio/models"))

binaries: list = []
hiddenimports: list = []

for package_name in (
    "rapidocr_onnxruntime",
    "funasr",
    "modelscope",
    "torch",
    "pip",
    "PIL",
    "torchaudio",
    "whisper",
    "librosa",
    "soundfile",
    "soxr",
    "scipy",
    "numba",
    "llvmlite",
    "huggingface_hub",
    "tokenizers",
    "tqdm",
    "editdistance",
    "soundcard",
    # Ships openvr_api DLLs next to the Python package; a plain hiddenimport
    # would leave the module importable and the runtime unloadable.
    "openvr",
    # Each winrt namespace is its own compiled extension module.
    "winrt",
    "cffi",
    "pycparser",
    "pydantic",
    "pydantic_core",
    "typing_inspection",
    "annotated_types",
    "charset_normalizer",
    "websockets",
    "rich",
    "markdown_it",
    "mdurl",
    "aiohttp",
    "aiosignal",
    "aiohappyeyeballs",
    "frozenlist",
    "multidict",
    "yarl",
    "propcache",
    "av",
    "g2p_en",
    "nltk",
    "style_bert_vits2",
    "pyopenjtalk",
    "transformers",
    "packaging",
    "sentencepiece",
    "google.protobuf",
    "jieba",
    "pypinyin",
    "cn2an",
):
    tmp_ret = collect_all(package_name)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

binaries += _collect_qt_plugins(
    ("platforms", "imageformats", "styles", "iconengines", "graphicseffects")
)

hiddenimports += [
    "typing_extensions",
    "yaml",
    "src.asr.factory",
    "src.asr.base",
    "src.asr.errors",
    "src.asr.asr_cleaner",
    "src.asr.text_corrections",
    "src.asr.fallback_asr",
    "src.asr.sensevoice_asr",
    "src.asr.model_manager",
    "src.asr.qwen3_asr",
    "src.asr.edge_stt_asr",
    "src.asr.edge_stt_protocol",
    "src.translators.microsoft_edge_translator",
    "websockets",
    "openai",
    "sentencepiece",
    "sentencepiece._sentencepiece",
    "sentencepiece.sentencepiece_model_pb2",
    "google.protobuf",
    "google.protobuf.message",
    "google.protobuf.internal",
    "inflect",
    "typeguard",
    "av.audio.resampler",
    # Required by librosa, which funasr/SenseVoice and transformers pull in.
    "sklearn",
    "g2p_en",
    "nltk",
    "nltk.data",
    "transformers.generation",
    "transformers.modeling_outputs",
    "transformers.modeling_utils",
    "transformers.pytorch_utils",
    "jieba",
    "jieba.posseg",
    "pypinyin",
    "cn2an",
    "src.asr.sensevoice_model_manager",
    "src.utils.hf_model_downloader",
    "src.audio.device_inventory",
    "src.audio.desktop_recorder",
    "src.audio.recorder",
    "src.audio.windows_audio",
    "src.utils.locale_detect",
    "src.utils.signed_catalog",
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "src.ui_qt.app",
    "src.ui_qt.main_window",
    "src.ui_qt.settings_window",
    "src.ui_qt.floating_window",
    "src.ui_qt.vr_overlay_panel",
    "src.core.steamvr_overlay",
    "src.core.screen_capture",
    "src.core.screen_ocr",
    "src.core.screenshot_translation",
    "src.core.in_place_layout",
    "src.core.steamvr_inplace",
    "src.core.steamvr_dashboard",
    "src.core.steamvr_wrist",
    "src.core.steamvr_autolaunch",
    "src.core.vr_gesture",
    "src.ui_qt.vr_dashboard_panel",
    "src.core.local_ocr",
    "src.core.vr_eye_capture",
    "src.core.text_blocks",
    "src.core.vr_geometry",
    "src.core.overlay_texture",
    "cv2",
    "src.ui_qt.in_place_painter",
    "src.ui_qt.in_place_overlay_window",
    "src.core.vr_input",
    # The headset side, VRHandsFrame style: the two-hand frame, kept result
    # panels, the reads history and the vibration/sound cues.
    "src.core.vr_frame_gesture",
    "src.core.steamvr_frame",
    "src.core.vr_result_panels",
    "src.core.vr_history",
    "src.core.vr_feedback",
    "src.ui_qt.vr_result_panel",
    "src.core.qr_codes",
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.globalization",
    "src.ui_qt.sponsor_window",
    "src.ui_qt.update_window",
    "src.ui_qt.model_download_dialog",
    "src.ui_qt.text_input_window",
    "src.tts.qwen_voice_enrollment",
    "src.tts.reference_audio",
    "src.core.voice_clone_service",
]
hiddenimports = filter_hiddenimports(hiddenimports)

excludes = [
    "torchvision",
    # Recognition no longer runs in a browser page, so the whole Qt
    # WebEngine stack (a ~195 MB core DLL plus resource packs) is dead
    # weight in the bundle.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    # Removed with local voice cloning; excluded so a stale dev venv that
    # still has them installed cannot pull them back into the bundle.
    # unidic_lite alone is a 249 MB dictionary that only Coqui's cutlet/fugashi
    # frontend used; Style-Bert-VITS2 does Japanese through pyopenjtalk and its
    # BERT tokenizer is character-based, so none of these are reachable now.
    "TTS",
    "trainer",
    "coqpit",
    # Removed with the Edge/Google/pyttsx3 voices and Gemini Live ASR.
    "edge_tts",
    "gtts",
    "pyttsx3",
    "comtypes",
    "cutlet",
    "fugashi",
    "unidic_lite",
    "mojimoji",
    "torchcodec",
    "tensorflow",
    "keras",
    "matplotlib",
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "Cython",
    "Pythonwin",
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
a.binaries = TOC(filter_runtime_entries(_sanitize_analysis_binaries(a.binaries)))
a.datas = TOC(filter_runtime_entries(a.datas))
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
    version=_windows_version_info,
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
