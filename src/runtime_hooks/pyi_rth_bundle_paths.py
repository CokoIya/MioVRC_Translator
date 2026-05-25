from __future__ import annotations

import os
import sys
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


if getattr(sys, "frozen", False):
    _ensure_stdio_streams()
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    _remove_stale_cuda_dlls(bundle_root)
    for candidate in (
        bundle_root,
        bundle_root / "torch" / "lib",
        bundle_root / "torchaudio" / "lib",
    ):
        _add_runtime_dir(candidate)
