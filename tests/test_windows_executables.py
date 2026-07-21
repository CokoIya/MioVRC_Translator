import os

import pytest

from src.utils import windows_executables
from src.utils.windows_executables import WindowsExecutableResolutionError


def test_trusted_windows_system_executable_validates_full_descendant_chain(tmp_path, monkeypatch):
    system_dir = tmp_path / "System32"
    executable = system_dir / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    monkeypatch.setattr(windows_executables, "windows_system_directory", lambda: system_dir)

    resolved = windows_executables.trusted_windows_system_executable(
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )

    assert resolved == executable
    assert resolved.is_absolute()


@pytest.mark.parametrize(
    "parts",
    [
        (),
        ("",),
        ("..", "cmd.exe"),
        ("folder/cmd.exe",),
        ("folder\\cmd.exe",),
        ("C:cmd.exe",),
    ],
)
def test_trusted_windows_system_executable_rejects_path_injection(parts):
    with pytest.raises(WindowsExecutableResolutionError, match="required|invalid"):
        windows_executables.trusted_windows_system_executable(*parts)


def test_trusted_windows_system_executable_rejects_symlink_component(tmp_path, monkeypatch):
    system_dir = tmp_path / "System32"
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "tool.exe").write_bytes(b"binary")
    system_dir.mkdir()
    linked_dir = system_dir / "linked"
    try:
        os.symlink(real_dir, linked_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(windows_executables, "windows_system_directory", lambda: system_dir)

    with pytest.raises(WindowsExecutableResolutionError, match="symlink|reparse"):
        windows_executables.trusted_windows_system_executable("linked", "tool.exe")
