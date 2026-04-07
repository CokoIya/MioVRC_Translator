from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils.app_paths import resource_base_dirs, writable_app_dir

_LOG_INITIALIZED = False
_LOG_PATH: Path | None = None


def _preferred_logs_dir() -> Path:
    return resource_base_dirs()[0] / "logs"


def _fallback_logs_dir() -> Path:
    return writable_app_dir() / "logs"


def logs_dir() -> Path:
    preferred = _preferred_logs_dir()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except Exception:
        fallback = _fallback_logs_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def log_path() -> Path:
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = logs_dir() / "mio.log"
    return _LOG_PATH


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(console_level: int = logging.INFO) -> Path:
    global _LOG_INITIALIZED, _LOG_PATH
    if _LOG_INITIALIZED:
        return log_path()

    target = log_path()
    formatter = _build_formatter()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        target,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    logging.captureWarnings(True)

    def _log_unhandled_exception(exc_type, exc_value, exc_traceback):
        logging.getLogger("mio.unhandled").error(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("mio.thread").error(
            "Unhandled thread exception in %s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _log_unhandled_exception
    threading.excepthook = _log_thread_exception

    _LOG_INITIALIZED = True
    logging.getLogger(__name__).info("Logging initialized at %s", target)
    return target


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
