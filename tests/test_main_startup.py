from pathlib import Path
import subprocess
import sys

import main
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_importing_entrypoint_does_not_import_gpu_support_on_normal_launch():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import main; "
                "print('src.utils.gpu_support' in sys.modules)"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    assert completed.stdout.strip() == "False"


def test_importing_config_manager_does_not_import_requests_network_stack():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from src.utils import config_manager; "
                "print('requests' in sys.modules); "
                "print('src.utils.provider_network' in sys.modules); "
                "print('src.utils.secure_http' in sys.modules)"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    assert completed.stdout.splitlines() == ["False", "False", "False"]


def test_runtime_hook_duration_rejects_invalid_or_unbounded_values(monkeypatch):
    monkeypatch.setenv("MIO_TRANSLATOR_RUNTIME_HOOK_MS", "123.5")
    assert main._runtime_hook_duration_ms() == 123.5

    monkeypatch.setenv("MIO_TRANSLATOR_RUNTIME_HOOK_MS", "nan")
    assert main._runtime_hook_duration_ms() == 0.0

    monkeypatch.setenv("MIO_TRANSLATOR_RUNTIME_HOOK_MS", "999999999")
    assert main._runtime_hook_duration_ms() == 0.0


def test_estimated_process_origin_prefers_os_process_age(monkeypatch):
    monkeypatch.setattr(main._startup_clock, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main, "_windows_process_age_seconds", lambda: 12.5)

    assert main._estimated_process_origin(250.0) == 87.5


def test_estimated_process_origin_falls_back_to_runtime_hook(monkeypatch):
    monkeypatch.setattr(main, "_windows_process_age_seconds", lambda: None)

    assert main._estimated_process_origin(250.0) == main._STARTUP_MAIN_ORIGIN - 0.25


def test_windows_process_age_probe_returns_a_plausible_age():
    age_s = main._windows_process_age_seconds()
    if sys.platform != "win32":
        assert age_s is None
        return

    assert age_s is not None
    assert 0.0 <= age_s <= 60.0 * 60.0


def _make_local_venv_candidate(
    root: Path,
    *,
    runtime_modules: tuple[str, ...] = (),
) -> Path:
    candidate = root / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    site_packages = root / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    for module_name in runtime_modules:
        (site_packages / module_name).mkdir()
    return candidate


def test_source_venv_relaunch_respects_any_selected_local_candidate(
    monkeypatch,
    tmp_path,
):
    preferred = _make_local_venv_candidate(tmp_path / "preferred")
    selected = _make_local_venv_candidate(tmp_path / "selected")
    monkeypatch.setattr(
        main,
        "_local_source_python_candidates",
        lambda: [preferred, selected],
    )
    monkeypatch.setattr(sys, "executable", str(selected))
    monkeypatch.delenv(main._VENV_RELAUNCH_ENV, raising=False)
    monkeypatch.delenv("MIO_TRANSLATOR_NO_VENV_RELAUNCH", raising=False)
    monkeypatch.setattr(
        main,
        "_candidate_python_has_runtime",
        lambda _candidate: pytest.fail("local interpreter must not be probed"),
    )

    main._maybe_relaunch_local_source_venv()


def test_source_venv_relaunch_skips_candidates_without_runtime_layout(
    monkeypatch,
    tmp_path,
):
    incomplete = _make_local_venv_candidate(
        tmp_path / "incomplete",
        runtime_modules=("torch", "torchaudio"),
    )
    complete = _make_local_venv_candidate(
        tmp_path / "complete",
        runtime_modules=main._VENV_RUNTIME_MODULES,
    )
    monkeypatch.setattr(
        main,
        "_local_source_python_candidates",
        lambda: [incomplete, complete],
    )
    monkeypatch.setattr(sys, "executable", str(tmp_path / "system-python.exe"))
    monkeypatch.delenv(main._VENV_RELAUNCH_ENV, raising=False)
    monkeypatch.delenv("MIO_TRANSLATOR_NO_VENV_RELAUNCH", raising=False)

    probes = []
    monkeypatch.setattr(
        main,
        "_candidate_python_has_runtime",
        lambda candidate: probes.append(candidate) or True,
    )

    class Relaunched(Exception):
        pass

    exec_calls = []

    def _execv(executable, argv):
        exec_calls.append((executable, argv))
        raise Relaunched

    monkeypatch.setattr(main.os, "execv", _execv)

    with pytest.raises(Relaunched):
        main._maybe_relaunch_local_source_venv()

    assert probes == [complete]
    assert exec_calls == [(str(complete), [str(complete), *sys.argv])]
