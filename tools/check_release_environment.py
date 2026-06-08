from __future__ import annotations

import importlib.util
import sys

REQUIRED_PYTHON = (3, 11)
REQUIRED_MODULES = (
    "PyInstaller",
    "funasr",
    "google.genai",
    "tiktoken",
    "torch",
    "torchaudio",
    "websockets",
    "whisper",
    "librosa",
    "scipy",
    "numba",
    "llvmlite",
    "edge_tts",
    "aiohttp",
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

    import torch

    torch_cuda = torch.version.cuda or ""
    if torch_cuda:
        print(
            f"This Python environment uses CUDA PyTorch ({torch_cuda}). Install CPU-only torch before building.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Release environment OK: Python {sys.version.split()[0]}, CPU-only PyTorch {torch.__version__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
