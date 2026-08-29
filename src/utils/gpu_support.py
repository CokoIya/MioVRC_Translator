from __future__ import annotations

import base64
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils.app_paths import secure_writable_subdirectory
from src.utils.windows_executables import (
    WindowsExecutableResolutionError,
    trusted_windows_powershell_executable,
    trusted_windows_system_executable,
)


PYTORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"
PYTORCH_CUDA_VERSION = "2.8.0"
PYTORCH_CUDA_PACKAGES = (
    f"torch=={PYTORCH_CUDA_VERSION}",
    f"torchaudio=={PYTORCH_CUDA_VERSION}",
)
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


@dataclass(frozen=True)
class TorchCudaRuntimeStatus:
    torch_importable: bool
    torch_version: str = ""
    cuda_build: str = ""
    cuda_available: bool = False
    device_count: int = 0
    device_index: int = 0
    device_name: str = ""
    capability: tuple[int, int] | None = None
    total_memory_bytes: int = 0
    bf16_supported: bool = False
    runtime_source: str = "unavailable"
    detail: str = ""

    @property
    def ready(self) -> bool:
        return bool(
            self.torch_importable
            and self.cuda_build
            and self.cuda_available
            and self.device_count > self.device_index
        )

    @property
    def cpu_only_build(self) -> bool:
        return self.torch_importable and not self.cuda_build


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _torch_runtime_source(torch_module: Any) -> str:
    module_file = str(getattr(torch_module, "__file__", "") or "").strip()
    if module_file and _path_is_within(
        Path(module_file),
        cuda_runtime_site_packages(),
    ):
        return "managed_cuda_runtime"
    if _is_packaged_runtime():
        return "bundled_runtime"
    return "python_environment"


def inspect_torch_cuda_runtime(device_index: int = 0) -> TorchCudaRuntimeStatus:
    """Inspect the imported PyTorch/CUDA stack without changing device state."""

    try:
        import torch
    except Exception as exc:
        return TorchCudaRuntimeStatus(
            torch_importable=False,
            device_index=max(int(device_index), 0),
            detail=f"PyTorch import failed: {exc}",
        )

    index = max(int(device_index), 0)
    torch_version = str(getattr(torch, "__version__", "") or "")
    cuda_build = str(
        getattr(getattr(torch, "version", None), "cuda", "") or ""
    )
    runtime_source = _torch_runtime_source(torch)
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:
        return TorchCudaRuntimeStatus(
            torch_importable=True,
            torch_version=torch_version,
            cuda_build=cuda_build,
            device_index=index,
            runtime_source=runtime_source,
            detail=f"torch.cuda.is_available() failed: {exc}",
        )

    try:
        count = max(int(torch.cuda.device_count()), 0) if available else 0
    except Exception:
        count = 1 if available else 0

    name = ""
    capability: tuple[int, int] | None = None
    total_memory = 0
    bf16_supported = False
    detail = ""
    if available and index < count:
        try:
            name = str(torch.cuda.get_device_name(index) or "")
        except Exception as exc:
            detail = f"CUDA device name query failed: {exc}"
        try:
            raw_capability = torch.cuda.get_device_capability(index)
            capability = (int(raw_capability[0]), int(raw_capability[1]))
        except Exception:
            capability = None
        try:
            properties = torch.cuda.get_device_properties(index)
            total_memory = max(int(getattr(properties, "total_memory", 0) or 0), 0)
        except Exception:
            total_memory = 0
        try:
            bf16_supported = bool(torch.cuda.is_bf16_supported())
        except Exception:
            bf16_supported = bool(capability and capability[0] >= 8)
    elif cuda_build and not available:
        detail = (
            "CUDA-enabled PyTorch is installed, but the NVIDIA driver/device is "
            "not available to this process."
        )
    elif not cuda_build:
        detail = "The imported PyTorch build is CPU-only."

    return TorchCudaRuntimeStatus(
        torch_importable=True,
        torch_version=torch_version,
        cuda_build=cuda_build,
        cuda_available=available,
        device_count=count,
        device_index=index,
        device_name=name,
        capability=capability,
        total_memory_bytes=total_memory,
        bf16_supported=bf16_supported,
        runtime_source=runtime_source,
        detail=detail,
    )


def normalize_torch_precision(value: object) -> str:
    normalized = str(value or "auto").strip().lower().replace("fp", "float")
    aliases = {
        "half": "float16",
        "float16": "float16",
        "float32": "float32",
        "full": "float32",
        "bf16": "bfloat16",
        "bfloat16": "bfloat16",
        "auto": "auto",
    }
    return aliases.get(normalized, "auto")


def choose_torch_precision(
    requested: object,
    status: TorchCudaRuntimeStatus,
) -> str:
    """Choose a CUDA autocast precision, falling back to reliable FP32.

    FP16 is the automatic choice on modern CUDA devices because the local
    inference stacks still contain operations and output paths that do
    not consistently support BF16.  BF16 remains available as an explicit
    opt-in, but selecting it automatically adds a costly failed first pass on
    otherwise compatible hardware.
    """

    precision = normalize_torch_precision(requested)
    if not status.ready:
        return "float32"
    if precision == "auto":
        capability = status.capability or (0, 0)
        if capability[0] >= 7:
            return "float16"
        return "float32"
    if precision == "bfloat16" and not status.bf16_supported:
        return "float16" if (status.capability or (0, 0))[0] >= 7 else "float32"
    if precision == "float16" and (status.capability or (0, 0))[0] < 7:
        return "float32"
    return precision


def clear_torch_cuda_cache() -> None:
    try:
        import torch

        if bool(torch.cuda.is_available()):
            torch.cuda.empty_cache()
            ipc_collect = getattr(torch.cuda, "ipc_collect", None)
            if callable(ipc_collect):
                ipc_collect()
    except Exception:
        return


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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            startupinfo=_subprocess_startupinfo(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except Exception:
        return None


def _windows_video_controller_probe_command() -> list[str]:
    powershell = trusted_windows_powershell_executable()
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$ProgressPreference = 'SilentlyContinue'; "
        "$WarningPreference = 'SilentlyContinue'; "
        "$cimModule = [System.IO.Path]::Combine($PSHOME, 'Modules', "
        "'CimCmdlets', 'CimCmdlets.psd1'); "
        "Import-Module -Name $cimModule -Force -ErrorAction Stop; "
        "$PSModuleAutoLoadingPreference = 'None'; "
        "$controllers = CimCmdlets\\Get-CimInstance -ClassName Win32_VideoController; "
        "foreach ($controller in $controllers) { "
        "[Console]::Out.WriteLine([string]$controller.Name) }"
    )
    encoded_command = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return [
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded_command,
    ]


def detect_nvidia_driver() -> NvidiaDriverStatus:
    """Return whether Windows exposes a usable NVIDIA driver."""

    if os.name != "nt":
        return NvidiaDriverStatus(False)

    smi: subprocess.CompletedProcess[str] | None = None
    smi_resolution_error = ""
    try:
        smi_path = trusted_windows_system_executable("nvidia-smi.exe")
    except WindowsExecutableResolutionError as exc:
        smi_resolution_error = str(exc)
    else:
        smi = _run_probe(
            [
                str(smi_path),
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

    ps: subprocess.CompletedProcess[str] | None = None
    ps_resolution_error = ""
    try:
        ps_command = _windows_video_controller_probe_command()
    except WindowsExecutableResolutionError as exc:
        ps_resolution_error = str(exc)
    else:
        ps = _run_probe(ps_command)

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
    if not detail:
        detail = smi_resolution_error or ps_resolution_error
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
    if not packaged_cuda_pytorch_installed():
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
    return secure_writable_subdirectory("runtime_cuda", "site-packages")


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
    cache_root = secure_writable_subdirectory("runtime_cache", "pytorch_cuda")
    pip_cache = secure_writable_subdirectory(
        "runtime_cache",
        "pytorch_cuda",
        "pip",
    )
    pycache = secure_writable_subdirectory(
        "runtime_cache",
        "pytorch_cuda",
        "pycache",
    )
    temp_root = secure_writable_subdirectory("temp", "pytorch_cuda")
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
