from __future__ import annotations

import importlib.util
import sys

REQUIRED_PYTHON = (3, 11)
REQUIRED_MODULES = (
    "PyInstaller",
    "PySide6",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PIL",
    "requests",
    "charset_normalizer",
    "numpy",
    "sounddevice",
    "soundcard",
    "pyaudiowpatch",
    "webrtcvad",
    "funasr",
    "modelscope",
    "huggingface_hub",
    "tokenizers",
    "tqdm",
    "editdistance",
    "openai",
    "google.genai",
    "anthropic",
    "tiktoken",
    "pythonosc",
    "torch",
    "torchaudio",
    "whisper",
    "TTS",
    "edge_tts",
    "gtts",
    "pyttsx3",
    "style_bert_vits2",
    "cutlet",
    "fugashi",
    "unidic_lite",
    "mojimoji",
    "websockets",
    "aiohttp",
    "librosa",
    "scipy",
    "numba",
    "llvmlite",
    "av",
    "transformers",
    "sentencepiece",
    "google.protobuf",
    "jieba",
    "pypinyin",
    "cn2an",
    "g2p_en",
)


def _missing_modules() -> list[str]:
    missing: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            missing.append(module_name)
    return missing


def _runtime_import_errors() -> list[str]:
    errors: list[str] = []
    try:
        import cutlet as _cutlet  # noqa: F401
        import fugashi as _fugashi  # noqa: F401
        import unidic_lite as _unidic_lite  # noqa: F401
        import mojimoji as _mojimoji  # noqa: F401
        from TTS.api import TTS as _TTS  # noqa: F401
    except Exception as exc:
        errors.append(f"XTTS Japanese runtime / TTS.api: {exc}")

    try:
        from transformers.pytorch_utils import isin_mps_friendly as _isin  # noqa: F401
    except Exception as exc:
        errors.append(f"transformers.pytorch_utils.isin_mps_friendly: {exc}")

    try:
        import av as _av  # noqa: F401
    except Exception as exc:
        errors.append(f"TTS audio runtime imports: {exc}")
    return errors


def main() -> int:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        required = f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}"
        print(
            f"Release builds must use Python {required}; current interpreter is Python {version}.",
            file=sys.stderr,
        )
        print(
            "Create/activate the release venv first, then run: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    missing = _missing_modules()
    if missing:
        print(
            "Release environment is missing required modules: " + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "Install the locked release dependencies: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    import_errors = _runtime_import_errors()
    if import_errors:
        print(
            "Release environment has broken runtime imports: " + "; ".join(import_errors),
            file=sys.stderr,
        )
        print(
            "Install the locked release dependencies: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    import torch
    from packaging.version import Version

    torch_cuda = torch.version.cuda or ""
    if torch_cuda:
        print(
            f"This Python environment uses CUDA PyTorch ({torch_cuda}). Install CPU-only torch before building.",
            file=sys.stderr,
        )
        return 1
    torch_version = Version(torch.__version__.split("+", 1)[0])
    if torch_version >= Version("2.9"):
        print(
            "Release builds must use CPU PyTorch < 2.9 for Coqui XTTS on Windows; "
            f"current torch is {torch.__version__}.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Release environment OK: Python {sys.version.split()[0]}, CPU-only PyTorch {torch.__version__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
