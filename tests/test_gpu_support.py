import base64
from pathlib import Path
import subprocess
import types

from src.utils import gpu_support


def test_detect_nvidia_driver_uses_trusted_system_nvidia_smi(monkeypatch, tmp_path):
    smi = tmp_path / "Windows" / "System32" / "nvidia-smi.exe"

    def fake_run_probe(args, timeout=4.0):
        assert args[0] == str(smi)
        assert Path(args[0]).is_absolute()
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="NVIDIA GeForce RTX 4090, 555.85\n",
            stderr="",
        )

    monkeypatch.setattr(gpu_support.os, "name", "nt")
    monkeypatch.setattr(
        gpu_support,
        "trusted_windows_system_executable",
        lambda name: smi if name == "nvidia-smi.exe" else None,
    )
    monkeypatch.setattr(gpu_support, "_run_probe", fake_run_probe)

    status = gpu_support.detect_nvidia_driver()

    assert status.available is True
    assert status.source == "nvidia-smi"
    assert status.name == "NVIDIA GeForce RTX 4090"
    assert status.driver_version == "555.85"


def test_detect_nvidia_driver_falls_back_to_trusted_powershell(monkeypatch, tmp_path):
    smi = tmp_path / "Windows" / "System32" / "nvidia-smi.exe"
    powershell = (
        tmp_path
        / "Windows"
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    calls: list[list[str]] = []

    def fake_run_probe(args, timeout=4.0):
        calls.append(args)
        if args[0] == str(smi):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="Intel UHD Graphics\nNVIDIA GeForce RTX 3080\n",
            stderr="",
        )

    monkeypatch.setattr(gpu_support.os, "name", "nt")
    monkeypatch.setattr(gpu_support, "trusted_windows_system_executable", lambda _name: smi)
    monkeypatch.setattr(gpu_support, "trusted_windows_powershell_executable", lambda: powershell)
    monkeypatch.setattr(gpu_support, "_run_probe", fake_run_probe)

    status = gpu_support.detect_nvidia_driver()

    assert [call[0] for call in calls] == [str(smi), str(powershell)]
    assert calls[1][1:3] == ["-NoProfile", "-NonInteractive"]
    assert calls[1][-2] == "-EncodedCommand"
    decoded = base64.b64decode(calls[1][-1]).decode("utf-16-le")
    assert "$PSHOME" in decoded
    assert "CimCmdlets\\Get-CimInstance" in decoded
    assert status.available is True
    assert status.source == "win32_videocontroller"
    assert status.name == "NVIDIA GeForce RTX 3080"


def test_detect_nvidia_driver_never_falls_back_to_path_search(monkeypatch, tmp_path):
    powershell = tmp_path / "Windows" / "System32" / "powershell.exe"
    calls: list[list[str]] = []

    def missing_smi(_name):
        raise gpu_support.WindowsExecutableResolutionError("missing trusted nvidia-smi")

    monkeypatch.setattr(gpu_support.os, "name", "nt")
    monkeypatch.setattr(gpu_support, "trusted_windows_system_executable", missing_smi)
    monkeypatch.setattr(gpu_support, "trusted_windows_powershell_executable", lambda: powershell)
    monkeypatch.setattr(
        gpu_support,
        "_run_probe",
        lambda args, timeout=4.0: (
            calls.append(args)
            or subprocess.CompletedProcess(args, 0, stdout="Intel UHD Graphics\n", stderr="")
        ),
    )

    status = gpu_support.detect_nvidia_driver()

    assert status.available is False
    assert len(calls) == 1
    assert calls[0][0] == str(powershell)
    assert all(call[0] not in {"nvidia-smi", "powershell"} for call in calls)


def test_pytorch_cuda_install_command_uses_current_python(monkeypatch):
    monkeypatch.setattr(gpu_support.sys, "executable", "D:/Python/python.exe")
    if hasattr(gpu_support.sys, "frozen"):
        monkeypatch.delattr(gpu_support.sys, "frozen", raising=False)

    command = gpu_support.pytorch_cuda_install_command()

    assert command is not None
    program, args = command
    assert program == "D:/Python/python.exe"
    assert args[:6] == [
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "torch==2.8.0",
    ]
    assert "torchaudio==2.8.0" in args
    assert args[-2:] == ["--index-url", "https://download.pytorch.org/whl/cu128"]


def test_pip_check_and_bootstrap_commands_use_current_python(monkeypatch):
    monkeypatch.setattr(gpu_support.sys, "executable", "D:/Python/python.exe")
    if hasattr(gpu_support.sys, "frozen"):
        monkeypatch.delattr(gpu_support.sys, "frozen", raising=False)

    assert gpu_support.pip_check_command() == (
        "D:/Python/python.exe",
        ["-m", "pip", "--version"],
    )
    assert gpu_support.pip_bootstrap_commands() == (
        ("D:/Python/python.exe", ["-m", "ensurepip", "--upgrade"]),
        ("D:/Python/python.exe", ["-m", "pip", "install", "--upgrade", "pip"]),
    )


def test_cuda_install_environment_vars_stay_inside_mio_folder(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"

    def secure_subdirectory(*parts):
        path = app_root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(
        gpu_support,
        "secure_writable_subdirectory",
        secure_subdirectory,
    )

    env = gpu_support.cuda_install_environment_vars()

    assert env["PIP_CACHE_DIR"].startswith(str(app_root))
    assert env["PYTHONPYCACHEPREFIX"].startswith(str(app_root))
    assert env["XDG_CACHE_HOME"].startswith(str(app_root))
    assert env["TEMP"].startswith(str(app_root))
    assert env["TMP"].startswith(str(app_root))
    assert env["TMPDIR"].startswith(str(app_root))
    assert (app_root / "runtime_cache" / "pytorch_cuda" / "pip").is_dir()
    assert (app_root / "temp" / "pytorch_cuda").is_dir()


def test_cuda_runtime_site_packages_stays_inside_mio_folder(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    monkeypatch.setattr(
        gpu_support,
        "secure_writable_subdirectory",
        lambda *parts: app_root.joinpath(*parts),
    )

    assert gpu_support.cuda_runtime_site_packages() == app_root / "runtime_cuda" / "site-packages"


def test_packaged_cuda_pytorch_installed_detects_runtime_files(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    site_packages = app_root / "runtime_cuda" / "site-packages"
    torch_lib = site_packages / "torch" / "lib"
    torch_lib.mkdir(parents=True)
    (site_packages / "torch" / "__init__.py").write_text("", encoding="utf-8")
    (torch_lib / "torch_cuda.dll").write_bytes(b"")
    (torch_lib / "c10_cuda.dll").write_bytes(b"")
    monkeypatch.setattr(
        gpu_support,
        "secure_writable_subdirectory",
        lambda *parts: app_root.joinpath(*parts),
    )

    assert gpu_support.packaged_cuda_pytorch_installed() is True


def test_cuda_pytorch_installed_detects_imported_cuda_build(monkeypatch):
    fake_torch = types.SimpleNamespace(version=types.SimpleNamespace(cuda="12.8"))
    monkeypatch.setitem(gpu_support.sys.modules, "torch", fake_torch)
    monkeypatch.setattr(gpu_support, "packaged_cuda_pytorch_installed", lambda: False)

    assert gpu_support.cuda_pytorch_installed() is True


def test_torch_cuda_available_reflects_torch(monkeypatch):
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True)
    )
    monkeypatch.setitem(gpu_support.sys.modules, "torch", fake_torch)

    assert gpu_support.torch_cuda_available() is True


def test_torch_cuda_available_returns_false_when_torch_missing(monkeypatch):
    monkeypatch.setitem(gpu_support.sys.modules, "torch", None)

    assert gpu_support.torch_cuda_available() is False


def test_inspect_torch_cuda_runtime_reports_cpu_only_build(monkeypatch):
    fake_torch = types.SimpleNamespace(
        __version__="2.8.0+cpu",
        __file__="D:/Python/site-packages/torch/__init__.py",
        version=types.SimpleNamespace(cuda=None),
        cuda=types.SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(gpu_support.sys.modules, "torch", fake_torch)
    monkeypatch.setattr(gpu_support, "_is_packaged_runtime", lambda: False)

    status = gpu_support.inspect_torch_cuda_runtime()

    assert status.torch_importable is True
    assert status.cpu_only_build is True
    assert status.ready is False
    assert status.runtime_source == "python_environment"
    assert "CPU-only" in status.detail


def test_choose_torch_precision_prefers_float16_on_modern_gpu():
    status = gpu_support.TorchCudaRuntimeStatus(
        torch_importable=True,
        torch_version="2.8.0+cu128",
        cuda_build="12.8",
        cuda_available=True,
        device_count=1,
        device_name="RTX",
        capability=(8, 9),
        bf16_supported=True,
    )

    assert gpu_support.choose_torch_precision("auto", status) == "float16"
    assert gpu_support.choose_torch_precision("fp16", status) == "float16"


def test_gpu_runtime_available_uses_current_torch(monkeypatch):
    monkeypatch.setattr(gpu_support, "torch_cuda_available", lambda: True)
    monkeypatch.setattr(gpu_support, "_packaged_cuda_runtime_available", lambda: False)

    assert gpu_support.gpu_runtime_available() is True


def test_gpu_runtime_available_uses_packaged_cuda_runtime(monkeypatch):
    monkeypatch.setattr(gpu_support, "torch_cuda_available", lambda: False)
    monkeypatch.setattr(gpu_support, "_packaged_cuda_runtime_available", lambda: True)

    assert gpu_support.gpu_runtime_available() is True


def test_packaged_cuda_runtime_available_skips_verify_when_runtime_missing(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gpu_support, "_is_packaged_runtime", lambda: True)
    monkeypatch.setattr(gpu_support, "packaged_cuda_pytorch_installed", lambda: False)
    monkeypatch.setattr(gpu_support.subprocess, "run", fake_run)

    assert gpu_support._packaged_cuda_runtime_available() is False
    assert calls == []


def test_pytorch_cuda_install_command_uses_packaged_helper(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    monkeypatch.setattr(gpu_support.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gpu_support.sys, "executable", "D:/Mio/MioTranslator.exe")
    monkeypatch.setattr(
        gpu_support,
        "secure_writable_subdirectory",
        lambda *parts: app_root.joinpath(*parts),
    )

    assert gpu_support.pip_check_command() == (
        "D:/Mio/MioTranslator.exe",
        ["--mio-cuda-pip-check"],
    )
    assert gpu_support.pip_bootstrap_commands() == ()
    assert gpu_support.pytorch_cuda_install_command() == (
        "D:/Mio/MioTranslator.exe",
        ["--mio-install-cuda-pytorch", str(app_root / "runtime_cuda" / "site-packages")],
    )
    assert gpu_support.pytorch_cuda_verify_command() == (
        "D:/Mio/MioTranslator.exe",
        ["--mio-verify-cuda-pytorch"],
    )
