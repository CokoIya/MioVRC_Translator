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
from pathlib import Path

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
_VENV_RELAUNCH_ENV = "MIO_TRANSLATOR_RELAUNCHED_VENV"


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


def _candidate_python_has_runtime(candidate: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                str(candidate),
                "-c",
                (
                    "import importlib.util, sys; "
                    "mods=('funasr','torch','torchaudio'); "
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
    for candidate in _local_source_python_candidates():
        if not candidate.exists():
            continue
        try:
            if candidate.resolve() == current:
                return
        except OSError:
            pass
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

    ok, message = validate_runtime_dependencies()
    output = sys.stdout if ok else sys.stderr
    print(message, file=output)
    return 0 if ok else 1


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

    expected = cuda_runtime_site_packages().resolve(strict=False)
    resolved = target.resolve(strict=False)
    if resolved != expected:
        raise RuntimeError(f"Refusing to clean unexpected CUDA runtime path: {target}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


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
    from src.utils.gpu_support import cuda_runtime_site_packages

    _activate_cuda_runtime_site(cuda_runtime_site_packages())
    try:
        import torch

        print("torch=" + str(torch.__version__))
        print("cuda=" + str(getattr(torch.version, "cuda", "") or ""))
        available = bool(torch.cuda.is_available())
        print("available=" + str(available))
        print("device=" + (torch.cuda.get_device_name(0) if available else ""))
        return 0 if available else 2
    except Exception as exc:
        print(f"CUDA PyTorch verification failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    from src.utils.gpu_support import (
        CUDA_PIP_CHECK_ARG,
        CUDA_PIP_INSTALL_ARG,
        CUDA_PIP_VERIFY_ARG,
    )

    if CUDA_PIP_CHECK_ARG in sys.argv:
        return _run_cuda_pip_check()

    if CUDA_PIP_INSTALL_ARG in sys.argv:
        idx = sys.argv.index(CUDA_PIP_INSTALL_ARG)
        target_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        return _run_cuda_pytorch_install(target_arg)

    if CUDA_PIP_VERIFY_ARG in sys.argv:
        return _run_cuda_pytorch_verify()

    if os.environ.get("MIO_TRANSLATOR_SELFTEST") == "1" or "--mio-selftest" in sys.argv:
        return _run_selftest()

    if "--setup" in sys.argv:
        return _run_setup_mode()

    _maybe_relaunch_local_source_venv()

    if not _create_app_mutex():
        # Another instance is running. Don't fight it for the audio devices —
        # that path produced silent C-level crashes in the field.
        print(
            "Mio RealTime Translator is already running. "
            "Please use the existing instance.",
            file=sys.stderr,
        )
        return 0

    from src.utils.logger import setup_logging
    log_file = setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Application startup requested")
    logger.info("Log file ready at %s", log_file)

    from src.utils import config_manager
    from src.utils import catalog_fetcher, catalog_loader

    # Load catalog from cache synchronously so config_manager uses the right defaults
    cached = catalog_fetcher._load_cache()
    if cached:
        from src.utils.ui_config import set_catalog
        merged = catalog_loader.load_catalog_from_data(cached)
        set_catalog(merged)
        logger.info("Translation catalog loaded from cache")

    def _on_catalog_loaded(data: dict) -> None:
        from src.utils.ui_config import set_catalog as _sc
        merged = catalog_loader.load_catalog_from_data(data)
        _sc(merged)

    catalog_fetcher.get_catalog(_on_catalog_loaded)

    try:
        config = config_manager.load_config()
        logger.info("Configuration loaded successfully")

        # First-run: auto-select ASR engine based on system locale
        asr_cfg = config.setdefault("asr", {})
        if (
            not asr_cfg.get("user_selected_engine")
            and (not asr_cfg.get("engine") or not asr_cfg.get("engine_source"))
        ):
            from src.utils.locale_detect import select_default_asr_engine
            engine = asr_cfg.get("engine") or select_default_asr_engine()
            asr_cfg["engine"] = engine
            asr_cfg.setdefault("engine_source", "auto")
            config_manager.save_config(config)
            logger.info("Auto-selected ASR engine: %s", engine)

        from src.ui_qt.app import run_qt_app
        logger.info("Launching Qt UI")
        exit_code = run_qt_app(config)
        logger.info("Qt UI closed normally")
        return int(exit_code or 0)
    except Exception:
        logger.exception("Fatal error during application startup/runtime")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
