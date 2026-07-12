# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import faulthandler
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils.app_paths import (
    atomic_replace_secure_file,
    open_secure_append,
    require_real_directory,
    secure_file_path,
    writable_app_dir,
)

_LOG_INITIALIZED = False
_LOG_PATH: Path | None = None
_FAULT_HANDLER_FILE = None

_REDACTED = "[REDACTED]"
_URL_CREDENTIALS_RE = re.compile(
    r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@"
)
_BEARER_TOKEN_RE = re.compile(
    r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
)
_SECRET_FIELD_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>[\"']?(?:"
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|"
    r"password|client[_ -]?secret|private[_ -]?key|signing[_ -]?seed|"
    r"manifest[_ -]?seed"
    r")[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>(?:Bearer\s+)?[^\s,;}\]\"']{6,})"
    r"(?P=quote)"
)
_PROTECTED_SECRET_RE = re.compile(r"(?i)\bdpapi:v1:[A-Za-z0-9+/=]{12,}")
_PROVIDER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|tp)-[A-Za-z0-9_-]{12,}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_GOOGLE_API_KEY_RE = re.compile(r"(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{20,}")


def _redact_log_text(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIALS_RE.sub(rf"\1{_REDACTED}@", text)
    text = _BEARER_TOKEN_RE.sub(rf"\1 {_REDACTED}", text)

    def redact_field(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}{_REDACTED}{quote}"

    text = _SECRET_FIELD_RE.sub(redact_field, text)
    text = _PROTECTED_SECRET_RE.sub(f"dpapi:v1:{_REDACTED}", text)
    text = _PROVIDER_TOKEN_RE.sub(_REDACTED, text)
    return _GOOGLE_API_KEY_RE.sub(_REDACTED, text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _redact_log_text(super().format(record))


def logs_dir() -> Path:
    return require_real_directory(writable_app_dir() / "logs")


def log_path() -> Path:
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = secure_file_path(logs_dir() / "mio.log")
    return _LOG_PATH


class _SecureRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return open_secure_append(
            self.baseFilename,
            encoding=self.encoding or "utf-8",
        )

    def _secure_rotation_path(self, index: int) -> Path:
        base = Path(self.baseFilename)
        candidate = secure_file_path(
            self.rotation_filename(f"{self.baseFilename}.{index}")
        )
        if os.path.normcase(os.fspath(candidate.parent)) != os.path.normcase(
            os.fspath(base.parent)
        ):
            raise RuntimeError(
                f"Refusing log rotation outside the log directory: {candidate}"
            )
        return candidate

    def doRollover(self) -> None:
        """Rotate logs without following links or replacing shared files."""
        if self.stream is not None:
            self.stream.close()
            self.stream = None

        active = secure_file_path(self.baseFilename, must_exist=True)
        rotations = [
            self._secure_rotation_path(index)
            for index in range(1, self.backupCount + 1)
        ]

        # Validate every path before the first rename so unsafe hard links,
        # symbolic links, junctions, or custom namer escapes fail closed.
        for rotation in rotations:
            secure_file_path(rotation)

        if self.backupCount > 0:
            for index in range(self.backupCount - 1, 0, -1):
                source = rotations[index - 1]
                destination = rotations[index]
                if os.path.lexists(source):
                    atomic_replace_secure_file(source, destination)
            atomic_replace_secure_file(active, rotations[0])

        if not self.delay:
            self.stream = self._open()


def _build_formatter() -> logging.Formatter:
    return _RedactingFormatter(
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


def _exception_is(exc_type: object, expected: type[BaseException]) -> bool:
    """Safely classify exception-hook inputs without trusting their type."""
    return isinstance(exc_type, type) and issubclass(exc_type, expected)


def _request_application_shutdown() -> None:
    """Ask an active Qt event loop to stop after a console interrupt."""
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None:
            app.quit()
    except Exception:
        # Logging must remain usable even in headless/minimal installations.
        pass


def setup_logging(console_level: int = logging.INFO) -> Path:
    global _LOG_INITIALIZED, _LOG_PATH, _FAULT_HANDLER_FILE
    if _LOG_INITIALIZED:
        return log_path()

    warnings.filterwarnings(
        "ignore",
        message=r"`torch\.nn\.utils\.weight_norm` is deprecated.*",
        category=FutureWarning,
    )

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
    for backup_index in range(1, 11):
        secure_file_path(target.with_name(f"{target.name}.{backup_index}"))

    file_handler = _SecureRotatingFileHandler(
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

    fault_file = None
    try:
        fault_path = secure_file_path(target.with_name("native_crash.log"))
        fault_file = open_secure_append(
            fault_path, binary=True, buffering=0
        )
        faulthandler.enable(file=fault_file, all_threads=True)
        _FAULT_HANDLER_FILE = fault_file
    except Exception:
        if fault_file is not None:
            try:
                fault_file.close()
            except Exception:
                pass
        _FAULT_HANDLER_FILE = None

    def _log_unhandled_exception(exc_type, exc_value, exc_traceback):
        if _exception_is(exc_type, KeyboardInterrupt):
            logging.getLogger("mio.unhandled").info(
                "Application interruption requested"
            )
            _request_application_shutdown()
            return
        if _exception_is(exc_type, SystemExit):
            return
        logging.getLogger("mio.unhandled").error(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
        if _exception_is(args.exc_type, SystemExit):
            return
        if _exception_is(args.exc_type, KeyboardInterrupt):
            logging.getLogger("mio.thread").info(
                "Application interruption requested by thread %s",
                getattr(args.thread, "name", "unknown"),
            )
            _request_application_shutdown()
            return
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
