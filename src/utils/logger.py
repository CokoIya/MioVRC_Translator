from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FILENAME = "mio_translator.log"
_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_BACKUP_COUNT = 2
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(log_dir: Path | None = None) -> Path | None:
    """初始化全局日志。调用一次即可；重复调用无效。返回日志文件路径（无法创建时返回 None）。"""
    global _initialized
    if _initialized:
        return None
    _initialized = True

    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 控制台输出 WARNING 及以上
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件输出 DEBUG 及以上
    log_file: Path | None = None
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / _LOG_FILENAME
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception as exc:
            root.warning("Failed to create log file at %s: %s", log_dir, exc)
            log_file = None

    return log_file


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
