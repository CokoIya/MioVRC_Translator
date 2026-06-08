import subprocess
import types

from src.utils import gpu_support


def test_detect_nvidia_driver_uses_nvidia_smi(monkeypatch):
    def fake_run_probe(args, timeout=4.0):
        assert args[0] == "nvidia-smi"
        return subprocess.CompletedProcess(args, 0, stdout="NVIDIA GeForce RTX 4090, 555.85\n", stderr="")

    monkeypatch.setattr(gpu_support, "_run_probe", fake_run_probe)

    status = gpu_support.detect_nvidia_driver()

    assert status.available is True
    assert status.source == "nvidia-smi"
    assert status.name == "NVIDIA GeForce RTX 4090"
    assert status.driver_version == "555.85"


def test_detect_nvidia_driver_falls_back_to_windows_video_controller(monkeypatch):
    calls: list[str] = []

    def fake_run_probe(args, timeout=4.0):
        calls.append(args[0])
        if args[0] == "nvidia-smi":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(args, 0, stdout="Intel UHD Graphics\nNVIDIA GeForce RTX 3080\n", stderr="")

    monkeypatch.setattr(gpu_support, "_run_probe", fake_run_probe)
    monkeypatch.setattr(gpu_support.os, "name", "nt")

    status = gpu_support.detect_nvidia_driver()

    assert calls == ["nvidia-smi", "powershell"]
    assert status.available is True
    assert status.source == "win32_videocontroller"
    assert status.name == "NVIDIA GeForce RTX 3080"


def test_pytorch_cuda_install_command_uses_current_python(monkeypatch):
    monkeypatch.setattr(gpu_support.sys, "executable", "D:/Python/python.exe")
    if hasattr(gpu_support.sys, "frozen"):
        monkeypatch.delattr(gpu_support.sys, "frozen", raising=False)

    command = gpu_support.pytorch_cuda_install_command()

    assert command is not None
    program, args = command
    assert program == "D:/Python/python.exe"
    assert args[:6] == ["-m", "pip", "install", "--upgrade", "--force-reinstall", "torch"]
    assert "torchaudio" in args
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
    monkeypatch.setattr(gpu_support, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(gpu_support, "app_temp_dir", lambda: app_root / "temp")

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
    monkeypatch.setattr(gpu_support, "writable_app_dir", lambda: app_root)

    assert gpu_support.cuda_runtime_site_packages() == app_root / "runtime_cuda" / "site-packages"


def test_packaged_cuda_pytorch_installed_detects_runtime_files(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    site_packages = app_root / "runtime_cuda" / "site-packages"
    torch_lib = site_packages / "torch" / "lib"
    torch_lib.mkdir(parents=True)
    (site_packages / "torch" / "__init__.py").write_text("", encoding="utf-8")
    (torch_lib / "torch_cuda.dll").write_bytes(b"")
    (torch_lib / "c10_cuda.dll").write_bytes(b"")
    monkeypatch.setattr(gpu_support, "writable_app_dir", lambda: app_root)

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


def test_gpu_runtime_available_uses_current_torch(monkeypatch):
    monkeypatch.setattr(gpu_support, "torch_cuda_available", lambda: True)
    monkeypatch.setattr(gpu_support, "_packaged_cuda_runtime_available", lambda: False)

    assert gpu_support.gpu_runtime_available() is True


def test_gpu_runtime_available_uses_packaged_cuda_runtime(monkeypatch):
    monkeypatch.setattr(gpu_support, "torch_cuda_available", lambda: False)
    monkeypatch.setattr(gpu_support, "_packaged_cuda_runtime_available", lambda: True)

    assert gpu_support.gpu_runtime_available() is True


def test_pytorch_cuda_install_command_uses_packaged_helper(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    monkeypatch.setattr(gpu_support.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gpu_support.sys, "executable", "D:/Mio/MioTranslator.exe")
    monkeypatch.setattr(gpu_support, "writable_app_dir", lambda: app_root)

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
