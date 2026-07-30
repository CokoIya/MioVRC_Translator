# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

import os
import sys
import logging
import warnings
import subprocess
import shutil
import stat
import time as _startup_clock
import uuid
from pathlib import Path

_STARTUP_MAIN_ORIGIN = _startup_clock.perf_counter()

# pydub emits a RuntimeWarning at import time when ffmpeg is not on PATH.
# The app uses torchaudio/av for audio processing and does not need ffmpeg,
# so this warning is irrelevant and would confuse users reading the log.
warnings.filterwarnings(
    "ignore",
    message="Couldn't find ffmpeg or avconv",
    category=RuntimeWarning,
    module="pydub",
)

sys.path.insert(0, os.path.dirname(__file__))

_APP_MUTEX_HANDLE = None
_ERROR_ALREADY_EXISTS = 183
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_VENV_RELAUNCH_ENV = "MIO_TRANSLATOR_RELAUNCHED_VENV"
_VENV_RUNTIME_MODULES = ("funasr", "torch", "torchaudio")
_CUDA_PIP_CHECK_ARG = "--mio-cuda-pip-check"
_CUDA_PIP_INSTALL_ARG = "--mio-install-cuda-pytorch"
_CUDA_PIP_VERIFY_ARG = "--mio-verify-cuda-pytorch"
_RUNTIME_HOOK_TIMING_ENV = "MIO_TRANSLATOR_RUNTIME_HOOK_MS"
_WINDOWS_EPOCH_OFFSET_SECONDS = 11_644_473_600.0


def _runtime_hook_duration_ms() -> float:
    try:
        duration_ms = float(os.environ.get(_RUNTIME_HOOK_TIMING_ENV, "") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not 0.0 <= duration_ms <= 60.0 * 60.0 * 1000.0:
        return 0.0
    return duration_ms


def _windows_process_age_seconds() -> float | None:
    """Return elapsed wall time since OS process creation on Windows."""

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        created_ticks = (int(creation.dwHighDateTime) << 32) | int(
            creation.dwLowDateTime
        )
        created_at = (
            created_ticks / 10_000_000.0 - _WINDOWS_EPOCH_OFFSET_SECONDS
        )
        age_s = _startup_clock.time() - created_at
    except Exception:
        return None
    if not 0.0 <= age_s <= 60.0 * 60.0:
        return None
    return age_s


def _estimated_process_origin(runtime_hook_ms: float) -> float:
    """Estimate a monotonic origin that includes bootloader/interpreter work."""

    process_age_s = _windows_process_age_seconds()
    # Pair the wall-clock age with a monotonic sample taken immediately after
    # the native probe; sampling before the ctypes import/API call would count
    # the probe's own latency twice and shift the process origin too early.
    observed_at = _startup_clock.perf_counter()
    if process_age_s is not None:
        candidate = observed_at - process_age_s
        if candidate <= _STARTUP_MAIN_ORIGIN:
            return candidate
    return _STARTUP_MAIN_ORIGIN - max(0.0, float(runtime_hook_ms)) / 1000.0


def _create_app_mutex() -> bool:
    """Create the single-instance mutex.

    Returns True if this process is the sole holder, False if another
    instance is already running (in which case the caller should abort
    rather than racing the existing instance for audio devices).
    """
    global _APP_MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    if _APP_MUTEX_HANDLE is not None:
        return True
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(
            None,
            False,
            "MioTranslatorRuntimeMutex",
        )
        if not handle:
            return True  # Best-effort: don't block startup on mutex failure.
        last_error = ctypes.get_last_error()
        if last_error == _ERROR_ALREADY_EXISTS:
            # Another instance owns the mutex; release our handle and bail.
            kernel32.CloseHandle(handle)
            return False
        _APP_MUTEX_HANDLE = handle
        return True
    except Exception:
        _APP_MUTEX_HANDLE = None
        return True


def _local_source_python_candidates() -> list[Path]:
    if getattr(sys, "frozen", False) or sys.platform != "win32":
        return []
    root = Path(__file__).resolve().parent
    return [
        root / ".venv-release311" / "Scripts" / "python.exe",
        root / ".venv311" / "Scripts" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
    ]


def _candidate_python_has_runtime_layout(candidate: Path) -> bool:
    """Cheaply reject local venvs that cannot contain the required runtime.

    These candidates are conventional Windows ``venv`` environments.  A
    missing top-level package in their own site-packages means launching the
    interpreter merely to run ``find_spec`` cannot make the candidate a valid
    self-contained runtime.  Positive results are still verified in the
    candidate interpreter below.
    """

    site_packages = candidate.parent.parent / "Lib" / "site-packages"
    try:
        if not site_packages.is_dir():
            return False
        for module_name in _VENV_RUNTIME_MODULES:
            if (site_packages / module_name).exists():
                continue
            if any(site_packages.glob(f"{module_name}.*")):
                continue
            return False
    except OSError:
        # Preserve the subprocess fallback when the filesystem cannot be
        # inspected reliably (permissions, transient device errors, etc.).
        return True
    return True


def _candidate_python_has_runtime(candidate: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                str(candidate),
                "-c",
                (
                    "import importlib.util, sys; "
                    f"mods={_VENV_RUNTIME_MODULES!r}; "
                    "sys.exit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def _maybe_relaunch_local_source_venv() -> None:
    if os.environ.get(_VENV_RELAUNCH_ENV) == "1":
        return
    if os.environ.get("MIO_TRANSLATOR_NO_VENV_RELAUNCH") == "1":
        return
    if sys.argv and sys.argv[0] == "-c":
        return
    current = Path(sys.executable).resolve()
    candidates: list[tuple[Path, Path | None]] = []
    for candidate in _local_source_python_candidates():
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = None
        candidates.append((candidate, resolved))

    # Respect an explicitly selected project-local interpreter.  Previously a
    # lower-priority local venv could synchronously probe (and even relaunch
    # into) every higher-priority sibling before its own match was reached.
    if any(resolved == current for _, resolved in candidates):
        return

    for candidate, _resolved in candidates:
        if not _candidate_python_has_runtime_layout(candidate):
            continue
        if not _candidate_python_has_runtime(candidate):
            continue
        os.environ[_VENV_RELAUNCH_ENV] = "1"
        print(
            f"Relaunching Mio Translator with local runtime venv: {candidate}",
            file=sys.stderr,
        )
        os.execv(str(candidate), [str(candidate), *sys.argv])


def _run_selftest() -> int:
    from src.asr.sensevoice_asr import validate_runtime_dependencies

    asr_ok, asr_message = validate_runtime_dependencies()
    print(asr_message, file=sys.stdout if asr_ok else sys.stderr)

    from src.tts.xtts_engine import xtts_packaging_selftest, xtts_runtime_status

    xtts_status = xtts_runtime_status(require_api=True)
    xtts_packaging_ok, xtts_packaging_message = xtts_packaging_selftest()
    xtts_ok = xtts_status.ready and xtts_packaging_ok
    if xtts_status.ready:
        print("Voice Cloning runtime dependencies are available.", file=sys.stdout)
    else:
        components = ", ".join(xtts_status.missing_component_names) or "unknown"
        print(
            f"Voice Cloning runtime dependencies are missing: {components}",
            file=sys.stderr,
        )
    print(
        xtts_packaging_message,
        file=sys.stdout if xtts_packaging_ok else sys.stderr,
    )
    return 0 if asr_ok and xtts_ok else 1


def _run_setup_mode() -> int:
    """Installer post-install setup mode.

    Current releases no longer open the SenseVoice model setup window during
    installation because online ASR providers are available immediately.
    """
    from src.utils.logger import setup_logging
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("Installer setup mode requested; SenseVoice model prompt is disabled.")
    return 0


def _activate_cuda_runtime_site(site_packages: Path) -> None:
    if not site_packages.is_dir():
        return
    site_text = str(site_packages)
    if site_text not in sys.path:
        sys.path.insert(0, site_text)
    for candidate in (
        site_packages,
        site_packages / "torch" / "lib",
        site_packages / "torchaudio" / "lib",
    ):
        if not candidate.is_dir():
            continue
        try:
            os.add_dll_directory(str(candidate))
        except (AttributeError, FileNotFoundError, OSError):
            pass
        current_path = os.environ.get("PATH", "")
        entries = current_path.split(os.pathsep) if current_path else []
        candidate_text = str(candidate)
        if candidate_text not in entries:
            os.environ["PATH"] = (
                candidate_text if not current_path else candidate_text + os.pathsep + current_path
            )


def _run_cuda_pip_check() -> int:
    try:
        import pip

        print("pip=" + str(getattr(pip, "__version__", "")))
        return 0
    except Exception as exc:
        print(f"pip import failed: {exc}", file=sys.stderr)
        return 1


def _clean_cuda_runtime_target(target: Path) -> None:
    from src.utils.gpu_support import cuda_runtime_site_packages

    expected = cuda_runtime_site_packages()

    def _lexical_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path.expanduser())))

    expected_key = _lexical_key(expected)
    if _lexical_key(target) != expected_key:
        raise RuntimeError(f"Refusing to clean unexpected CUDA runtime path: {target}")

    try:
        target_stat = expected.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"CUDA runtime target disappeared before cleanup: {expected}"
        ) from exc
    attributes = int(getattr(target_stat, "st_file_attributes", 0) or 0)
    if (
        not stat.S_ISDIR(target_stat.st_mode)
        or stat.S_ISLNK(target_stat.st_mode)
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise RuntimeError(f"Refusing to clean unsafe CUDA runtime target: {expected}")

    quarantine = expected.parent / (
        f".{expected.name}.cleanup-{os.getpid()}-{uuid.uuid4().hex}"
    )
    renamed = False
    try:
        os.rename(expected, quarantine)
        renamed = True
        quarantine_stat = quarantine.lstat()
        quarantine_attributes = int(
            getattr(quarantine_stat, "st_file_attributes", 0) or 0
        )
        if (
            not stat.S_ISDIR(quarantine_stat.st_mode)
            or stat.S_ISLNK(quarantine_stat.st_mode)
            or quarantine_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError(
                f"CUDA runtime target changed during cleanup: {expected}"
            )
        shutil.rmtree(quarantine)
    finally:
        if renamed:
            recreated = cuda_runtime_site_packages()
            if _lexical_key(recreated) != expected_key:
                raise RuntimeError(
                    f"CUDA runtime target changed during recreation: {recreated}"
                )


def _run_cuda_pytorch_install(target_arg: str | None) -> int:
    from src.utils.gpu_support import PYTORCH_CUDA_INDEX_URL, PYTORCH_CUDA_PACKAGES

    if not target_arg:
        print("CUDA runtime target path is missing.", file=sys.stderr)
        return 2

    target = Path(target_arg)
    try:
        _clean_cuda_runtime_target(target)
    except Exception as exc:
        print(f"Could not prepare CUDA runtime target: {exc}", file=sys.stderr)
        return 2

    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass and _meipass not in sys.path:
        sys.path.insert(0, _meipass)

    try:
        from pip._internal.cli.main import main as pip_main
    except Exception as exc:
        print(f"pip is not bundled in this installer: {exc}", file=sys.stderr)
        return 3

    args = [
        "install",
        "--upgrade",
        "--force-reinstall",
        "--target",
        str(target),
        *PYTORCH_CUDA_PACKAGES,
        "--index-url",
        PYTORCH_CUDA_INDEX_URL,
    ]
    print("> pip " + " ".join(args), flush=True)
    return int(pip_main(args) or 0)


def _run_cuda_pytorch_verify() -> int:
    from src.utils.gpu_support import (
        cuda_runtime_site_packages,
        inspect_torch_cuda_runtime,
    )

    _activate_cuda_runtime_site(cuda_runtime_site_packages())
    try:
        status = inspect_torch_cuda_runtime()
        print("torch=" + status.torch_version)
        print("cuda=" + status.cuda_build)
        print("available=" + str(status.cuda_available))
        print("device_count=" + str(status.device_count))
        print("device=" + status.device_name)
        print("capability=" + (".".join(map(str, status.capability)) if status.capability else ""))
        print("runtime_source=" + status.runtime_source)
        if status.detail:
            print("detail=" + status.detail)
        return 0 if status.ready else 2
    except Exception as exc:
        print(f"CUDA PyTorch verification failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    from src.utils.startup_timing import (
        enable_startup_timing_logging,
        initialize_startup_timing,
        record_startup_stage,
        startup_stage,
    )

    runtime_hook_ms = _runtime_hook_duration_ms()
    process_origin = _estimated_process_origin(runtime_hook_ms)
    initialize_startup_timing(process_origin)
    if process_origin < _STARTUP_MAIN_ORIGIN:
        record_startup_stage(
            "process.bootstrap_before_main",
            started_at=process_origin,
            finished_at=_STARTUP_MAIN_ORIGIN,
        )
    if runtime_hook_ms:
        record_startup_stage(
            "runtime_hook",
            started_at=max(
                process_origin,
                _STARTUP_MAIN_ORIGIN - (runtime_hook_ms / 1000.0),
            ),
            finished_at=_STARTUP_MAIN_ORIGIN,
        )
    record_startup_stage(
        "entrypoint.module_imports",
        started_at=_STARTUP_MAIN_ORIGIN,
    )

    # Keep gpu_support (and its writable-path/runtime dependencies) entirely
    # off normal launches.  These literal flags are a private subprocess
    # protocol and gpu_support itself is imported only inside the matching
    # special-mode helper.
    if _CUDA_PIP_CHECK_ARG in sys.argv:
        return _run_cuda_pip_check()

    if _CUDA_PIP_INSTALL_ARG in sys.argv:
        idx = sys.argv.index(_CUDA_PIP_INSTALL_ARG)
        target_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        return _run_cuda_pytorch_install(target_arg)

    if _CUDA_PIP_VERIFY_ARG in sys.argv:
        return _run_cuda_pytorch_verify()

    if os.environ.get("MIO_TRANSLATOR_SELFTEST") == "1" or "--mio-selftest" in sys.argv:
        return _run_selftest()

    if "--setup" in sys.argv:
        return _run_setup_mode()

    with startup_stage("startup.source_venv"):
        _maybe_relaunch_local_source_venv()

    with startup_stage("startup.single_instance_mutex"):
        sole_instance = _create_app_mutex()
    if not sole_instance:
        # Another instance is running. Don't fight it for the audio devices —
        # that path produced silent C-level crashes in the field.
        print(
            "Mio RealTime Translator is already running. "
            "Please use the existing instance.",
            file=sys.stderr,
        )
        return 0

    # Attribute legacy-file/path preparation separately from logger creation;
    # this was previously hidden inside the logging_setup stage.
    with startup_stage("startup.writable_path_prepare"):
        from src.utils.app_paths import writable_app_dir

        writable_app_dir()

    with startup_stage("startup.logging_setup"):
        from src.utils.logger import setup_logging

        log_file = setup_logging()
    enable_startup_timing_logging()
    logger = logging.getLogger(__name__)
    logger.info("Application startup requested")
    logger.info("Log file ready at %s", log_file)

    with startup_stage("startup.catalog_imports"):
        from src.utils import catalog_fetcher, catalog_loader
        from src.utils.ui_config import set_catalog

    # Apply the cache exactly once, before config_manager captures provider
    # defaults.  The remote refresh is scheduled from the first Qt event-loop
    # turn by src.utils.startup_tasks, after the window is visible.
    with startup_stage("startup.catalog_cache_load"):
        cached = catalog_fetcher.load_cached_catalog()
        if cached:
            set_catalog(catalog_loader.load_catalog_from_data(cached))
            logger.info("Translation catalog loaded from cache")

    with startup_stage("startup.config_manager_import"):
        from src.utils import config_manager

    try:
        with startup_stage("startup.configuration_load"):
            config = config_manager.load_config()
        logger.info("Configuration loaded successfully")

        # First-run: auto-select ASR engine based on system locale
        asr_cfg = config.setdefault("asr", {})
        if (
            not asr_cfg.get("user_selected_engine")
            and (not asr_cfg.get("engine") or not asr_cfg.get("engine_source"))
        ):
            with startup_stage("startup.asr_locale_default"):
                from src.utils.locale_detect import select_default_asr_engine

                engine = asr_cfg.get("engine") or select_default_asr_engine()
                asr_cfg["engine"] = engine
                asr_cfg.setdefault("engine_source", "auto")
                config_manager.save_config(config)
                logger.info("Auto-selected ASR engine: %s", engine)

        with startup_stage("startup.qt_app_import"):
            from src.ui_qt.app import run_qt_app

        logger.info("Launching Qt UI")
        record_startup_stage("startup.qt_handoff")
        exit_code = run_qt_app(config)
        logger.info("Qt UI closed normally")
        return int(exit_code or 0)
    except Exception:
        logger.exception("Fatal error during application startup/runtime")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
