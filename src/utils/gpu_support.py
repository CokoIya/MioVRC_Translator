from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.utils.app_paths import app_temp_dir, writable_app_dir


PYTORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"
PYTORCH_CUDA_PACKAGES = ("torch", "torchaudio")
CUDA_PIP_CHECK_ARG = "--mio-cuda-pip-check"
CUDA_PIP_INSTALL_ARG = "--mio-install-cuda-pytorch"
CUDA_PIP_VERIFY_ARG = "--mio-verify-cuda-pytorch"
_CUDA_TORCH_DLL_MARKERS = ("torch_cuda.dll", "c10_cuda.dll")


@dataclass(frozen=True)
class NvidiaDriverStatus:
    available: bool
    source: str = ""
    name: str = ""
    driver_version: str = ""
    detail: str = ""


def _subprocess_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _run_probe(args: list[str], timeout: float = 4.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=_subprocess_startupinfo(),
            check=False,
        )
    except Exception:
        return None


def detect_nvidia_driver() -> NvidiaDriverStatus:
    """Return whether Windows exposes a usable NVIDIA driver."""
    smi = _run_probe(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
    )
    if smi is not None and smi.returncode == 0:
        line = next((item.strip() for item in smi.stdout.splitlines() if item.strip()), "")
        if line:
            parts = [part.strip() for part in line.split(",", 1)]
            return NvidiaDriverStatus(
                available=True,
                source="nvidia-smi",
                name=parts[0],
                driver_version=parts[1] if len(parts) > 1 else "",
                detail=line,
            )

    if os.name != "nt":
        detail = (smi.stderr if smi is not None else "").strip()
        return NvidiaDriverStatus(False, detail=detail)

    ps = _run_probe(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object -ExpandProperty Name",
        ]
    )
    if ps is not None and ps.returncode == 0:
        names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
        for name in names:
            normalized = name.lower()
            if "nvidia" in normalized and "basic display" not in normalized:
                return NvidiaDriverStatus(
                    available=True,
                    source="win32_videocontroller",
                    name=name,
                    detail=name,
                )

    detail = ""
    if smi is not None:
        detail = (smi.stderr or smi.stdout or "").strip()
    if not detail and ps is not None:
        detail = (ps.stderr or ps.stdout or "").strip()
    return NvidiaDriverStatus(False, detail=detail)


def nvidia_driver_available() -> bool:
    return detect_nvidia_driver().available


def torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def torch_cuda_build_installed() -> bool:
    try:
        import torch

        return bool(getattr(getattr(torch, "version", None), "cuda", None))
    except Exception:
        return False


def _cuda_torch_files_present(site_packages: Path) -> bool:
    torch_pkg = site_packages / "torch"
    torch_lib = torch_pkg / "lib"
    if not torch_pkg.is_dir() or not torch_lib.is_dir():
        return False
    if not (torch_pkg / "__init__.py").is_file():
        return False
    return all((torch_lib / name).is_file() for name in _CUDA_TORCH_DLL_MARKERS)


def packaged_cuda_pytorch_installed() -> bool:
    return _cuda_torch_files_present(cuda_runtime_site_packages())


def cuda_pytorch_installed() -> bool:
    """Return whether a CUDA PyTorch build is already present, even if CUDA is unavailable now."""
    return torch_cuda_build_installed() or packaged_cuda_pytorch_installed()


def _packaged_cuda_runtime_available(timeout: float = 20.0) -> bool:
    if not _is_packaged_runtime():
        return False
    executable = python_executable_for_cuda_install()
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, CUDA_PIP_VERIFY_ARG],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            startupinfo=_subprocess_startupinfo(),
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def gpu_runtime_available() -> bool:
    """Return whether GPU support is usable now or installed for this packaged app."""
    if torch_cuda_available():
        return True
    return _packaged_cuda_runtime_available()


def python_executable_for_cuda_install(python_executable: str | None = None) -> str | None:
    """Return the Python executable that can be modified for CUDA PyTorch."""
    executable = python_executable or sys.executable
    if not executable:
        return None
    return executable


def cuda_runtime_site_packages() -> Path:
    return writable_app_dir() / "runtime_cuda" / "site-packages"


def _is_packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def pip_check_command(python_executable: str | None = None) -> tuple[str, list[str]] | None:
    executable = python_executable_for_cuda_install(python_executable)
    if not executable:
        return None
    if _is_packaged_runtime():
        return executable, [CUDA_PIP_CHECK_ARG]
    return executable, ["-m", "pip", "--version"]


def pip_bootstrap_commands(python_executable: str | None = None) -> tuple[tuple[str, list[str]], ...]:
    executable = python_executable_for_cuda_install(python_executable)
    if not executable:
        return ()
    if _is_packaged_runtime():
        return ()
    return (
        executable,
        ["-m", "ensurepip", "--upgrade"],
    ), (
        executable,
        ["-m", "pip", "install", "--upgrade", "pip"],
    )


def cuda_install_environment_vars() -> dict[str, str]:
    """Environment overrides that keep installer cache/temp files inside Mio."""
    cache_root = writable_app_dir() / "runtime_cache" / "pytorch_cuda"
    pip_cache = cache_root / "pip"
    pycache = cache_root / "pycache"
    temp_root = app_temp_dir() / "pytorch_cuda"
    for path in (pip_cache, pycache, temp_root):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "PIP_CACHE_DIR": str(pip_cache),
        "PYTHONPYCACHEPREFIX": str(pycache),
        "XDG_CACHE_HOME": str(cache_root),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "TMPDIR": str(temp_root),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONUTF8": "1",
    }


def pytorch_cuda_install_command(python_executable: str | None = None) -> tuple[str, list[str]] | None:
    """Return the pip command for installing CUDA PyTorch in the current Python env."""
    executable = python_executable_for_cuda_install(python_executable)
    if not executable:
        return None
    if _is_packaged_runtime():
        return (
            executable,
            [
                CUDA_PIP_INSTALL_ARG,
                str(cuda_runtime_site_packages()),
            ],
        )
    return (
        executable,
        [
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            *PYTORCH_CUDA_PACKAGES,
            "--index-url",
            PYTORCH_CUDA_INDEX_URL,
        ],
    )


def pytorch_cuda_verify_command(python_executable: str | None = None) -> tuple[str, list[str]] | None:
    executable = python_executable_for_cuda_install(python_executable)
    if not executable:
        return None
    if _is_packaged_runtime():
        return executable, [CUDA_PIP_VERIFY_ARG]
    script = (
        "import torch\n"
        "print('torch=' + str(torch.__version__))\n"
        "print('cuda=' + str(getattr(torch.version, 'cuda', '') or ''))\n"
        "print('available=' + str(torch.cuda.is_available()))\n"
        "print('device=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''))\n"
        "raise SystemExit(0 if torch.cuda.is_available() else 2)\n"
    )
    return executable, ["-c", script]
