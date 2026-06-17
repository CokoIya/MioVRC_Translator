# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import os
import sys
import threading
import faulthandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils.app_paths import writable_app_dir

_LOG_INITIALIZED = False
_LOG_PATH: Path | None = None
_FAULT_HANDLER_FILE = None


def logs_dir() -> Path:
    target = writable_app_dir() / "logs"
    target.mkdir(parents=True, exist_ok=True)
    return target


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


def _stdout_is_usable() -> bool:
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return False
    try:
        stream.write("")
        stream.flush()
        return True
    except Exception:
        return False


def setup_logging(console_level: int = logging.INFO) -> Path:
    global _LOG_INITIALIZED, _LOG_PATH, _FAULT_HANDLER_FILE
    if _LOG_INITIALIZED:
        return log_path()

    target = log_path()
    formatter = _build_formatter()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Determine file log level based on environment
    # MIO_DEBUG=1 -> DEBUG level (very verbose)
    # MIO_LOG_LEVEL=DEBUG/INFO/WARNING/ERROR -> specific level
    # Default -> INFO level
    debug_mode = os.environ.get("MIO_DEBUG") == "1"
    log_level_env = os.environ.get("MIO_LOG_LEVEL", "").upper()

    if debug_mode:
        file_level = logging.DEBUG
    elif log_level_env in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        file_level = getattr(logging, log_level_env)
    else:
        file_level = logging.INFO

    # Use larger file size (10MB) and more backups (10)
    file_handler = RotatingFileHandler(
        target,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if _stdout_is_usable():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    logging.captureWarnings(True)

    try:
        fault_path = target.with_name("native_crash.log")
        _FAULT_HANDLER_FILE = open(fault_path, "ab", buffering=0)
        faulthandler.enable(file=_FAULT_HANDLER_FILE, all_threads=True)
    except Exception:
        _FAULT_HANDLER_FILE = None

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
    log_level_name = logging.getLevelName(file_level)
    logging.getLogger(__name__).info(
        "Logging initialized at %s (level=%s, max_size=10MB, backups=10)",
        target,
        log_level_name
    )
    return target


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
