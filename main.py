import os
import sys
import logging
import warnings
import subprocess
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


def main() -> int:
    if os.environ.get("MIO_TRANSLATOR_SELFTEST") == "1":
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

        ui_backend = os.environ.get("MIO_TRANSLATOR_UI", "qt").strip().lower()
        if ui_backend == "tk":
            logger.warning("Tk UI is no longer used in release builds; launching Qt UI")
        elif ui_backend not in ("", "qt"):
            logger.warning("Unknown UI backend '%s'; launching Qt UI", ui_backend)

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
