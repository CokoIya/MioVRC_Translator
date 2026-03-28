from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> Path:
    """Configure root logger with a rotating file handler.

    Returns the absolute path to the log file so it can be printed at startup.
    Safe to call multiple times — duplicate handlers are not added.
    """
    from src.utils.app_paths import writable_app_dir  # local import avoids circular deps

    log_dir = writable_app_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mio_translator.log"

    root = logging.getLogger()
    # Don't add a second RotatingFileHandler if already configured
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return log_path

    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,  # 2 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root.setLevel(logging.WARNING)
    logging.getLogger("src").setLevel(logging.DEBUG)
    root.addHandler(handler)

    return log_path
