from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.utils import logger as logger_module


def test_log_formatter_redacts_credentials_and_protected_secrets():
    formatter = logger_module._build_formatter()
    record = logging.LogRecord(
        "security-test",
        logging.ERROR,
        __file__,
        1,
        (
            "Authorization: Bearer bearer-secret-token-123; "
            "api_key='sk-super-secret-token-456'; "
            "https://user:password@example.test/path; "
            "dpapi:v1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
        ),
        (),
        None,
    )

    rendered = formatter.format(record)

    assert "bearer-secret-token-123" not in rendered
    assert "sk-super-secret-token-456" not in rendered
    assert "user:password" not in rendered
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" not in rendered
    assert rendered.count("[REDACTED]") >= 4


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("directory symlink and junction creation are unavailable")


def _remove_directory_link(link: Path) -> None:
    if not os.path.lexists(link):
        return
    if os.name == "nt" and not link.is_symlink():
        os.rmdir(link)
    else:
        link.unlink()


@pytest.fixture
def isolated_logger_state():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_log_initialized = logger_module._LOG_INITIALIZED
    original_log_path = logger_module._LOG_PATH
    original_fault_file = logger_module._FAULT_HANDLER_FILE
    original_excepthook = sys.excepthook
    original_threading_excepthook = threading.excepthook

    logger_module._LOG_INITIALIZED = False
    logger_module._LOG_PATH = None
    logger_module._FAULT_HANDLER_FILE = None
    try:
        yield
    finally:
        current_fault_file = logger_module._FAULT_HANDLER_FILE
        if current_fault_file is not None and current_fault_file is not original_fault_file:
            try:
                current_fault_file.close()
            except Exception:
                pass
        for handler in list(root.handlers):
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(original_level)
        sys.excepthook = original_excepthook
        threading.excepthook = original_threading_excepthook
        logger_module._LOG_INITIALIZED = original_log_initialized
        logger_module._LOG_PATH = original_log_path
        logger_module._FAULT_HANDLER_FILE = original_fault_file


def test_logs_dir_rejects_redirected_writable_root(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / "redirected"
    _make_directory_link(redirected, outside)
    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: redirected)

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            logger_module.logs_dir()
        assert not (outside / "logs").exists()
    finally:
        _remove_directory_link(redirected)


def test_secure_rotation_rejects_hardlinked_active_log(tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"shared")
    active = tmp_path / "mio.log"
    try:
        os.link(outside, active)
    except (OSError, NotImplementedError):
        pytest.skip("hard-link creation is unavailable")
    handler = logger_module._SecureRotatingFileHandler(
        active,
        maxBytes=1,
        backupCount=1,
        delay=True,
    )
    try:
        with pytest.raises(RuntimeError, match="unsafe writable file"):
            handler.doRollover()
        assert active.read_bytes() == b"shared"
        assert outside.read_bytes() == b"shared"
    finally:
        handler.close()


def test_secure_rotation_rejects_hardlinked_numbered_target(tmp_path):
    active = tmp_path / "mio.log"
    active.write_bytes(b"active")
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"shared")
    rotation = tmp_path / "mio.log.1"
    try:
        os.link(outside, rotation)
    except (OSError, NotImplementedError):
        pytest.skip("hard-link creation is unavailable")
    handler = logger_module._SecureRotatingFileHandler(
        active,
        maxBytes=1,
        backupCount=1,
        delay=True,
    )
    try:
        with pytest.raises(RuntimeError, match="unsafe writable file"):
            handler.doRollover()
        assert active.read_bytes() == b"active"
        assert rotation.read_bytes() == b"shared"
    finally:
        handler.close()


def test_secure_rotation_rejects_custom_namer_escape(tmp_path):
    active = tmp_path / "logs" / "mio.log"
    active.parent.mkdir()
    active.write_bytes(b"active")
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = outside / "escaped.log"
    handler = logger_module._SecureRotatingFileHandler(
        active,
        maxBytes=1,
        backupCount=1,
        delay=True,
    )
    handler.namer = lambda _name: str(escaped)
    try:
        with pytest.raises(RuntimeError, match="outside the log directory"):
            handler.doRollover()
        assert active.read_bytes() == b"active"
        assert not escaped.exists()
    finally:
        handler.close()


def test_hardlinked_native_crash_log_fails_closed(
    monkeypatch,
    tmp_path,
    isolated_logger_state,
):
    app_root = tmp_path / "app"
    logs = app_root / "logs"
    logs.mkdir(parents=True)
    outside = tmp_path / "outside-crash.log"
    outside.write_bytes(b"shared")
    crash_log = logs / "native_crash.log"
    try:
        os.link(outside, crash_log)
    except (OSError, NotImplementedError):
        pytest.skip("hard-link creation is unavailable")
    enable_calls: list[object] = []
    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(logger_module, "_stdout_is_usable", lambda: False)
    monkeypatch.setattr(logger_module.logging, "captureWarnings", lambda _enabled: None)
    monkeypatch.setattr(
        logger_module.faulthandler,
        "enable",
        lambda **kwargs: enable_calls.append(kwargs),
    )

    logger_module.setup_logging()

    assert logger_module._FAULT_HANDLER_FILE is None
    assert enable_calls == []
    assert crash_log.read_bytes() == b"shared"
    assert outside.read_bytes() == b"shared"


def test_fault_handler_enable_failure_closes_secure_file(
    monkeypatch,
    tmp_path,
    isolated_logger_state,
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    opened_fault_files: list[object] = []
    original_open = logger_module.open_secure_append

    def tracking_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if Path(path).name == "native_crash.log":
            opened_fault_files.append(handle)
        return handle

    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(logger_module, "_stdout_is_usable", lambda: False)
    monkeypatch.setattr(logger_module.logging, "captureWarnings", lambda _enabled: None)
    monkeypatch.setattr(logger_module, "open_secure_append", tracking_open)
    monkeypatch.setattr(
        logger_module.faulthandler,
        "enable",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("enable failed")),
    )

    logger_module.setup_logging()

    assert len(opened_fault_files) == 1
    assert opened_fault_files[0].closed is True
    assert logger_module._FAULT_HANDLER_FILE is None


def test_keyboard_interrupt_is_a_clean_shutdown_request(
    monkeypatch,
    tmp_path,
    isolated_logger_state,
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    shutdown_requests: list[bool] = []
    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(logger_module, "_stdout_is_usable", lambda: False)
    monkeypatch.setattr(logger_module.faulthandler, "enable", lambda **_kwargs: None)
    monkeypatch.setattr(
        logger_module,
        "_request_application_shutdown",
        lambda: shutdown_requests.append(True),
    )

    path = logger_module.setup_logging()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert shutdown_requests == [True]
    assert "[INFO] [mio.unhandled] Application interruption requested" in content
    assert "[ERROR] [mio.unhandled] Unhandled exception" not in content


def test_unhandled_exception_traceback_is_redacted(
    monkeypatch,
    tmp_path,
    isolated_logger_state,
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(logger_module, "_stdout_is_usable", lambda: False)
    monkeypatch.setattr(logger_module.faulthandler, "enable", lambda **_kwargs: None)

    path = logger_module.setup_logging()
    error = RuntimeError("api_key=sk-unhandled-secret-token-123")
    sys.excepthook(RuntimeError, error, error.__traceback__)
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "sk-unhandled-secret-token-123" not in content
    assert "api_key=[REDACTED]" in content


def test_expected_thread_exit_is_not_logged_as_an_error(
    monkeypatch,
    tmp_path,
    isolated_logger_state,
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setattr(logger_module, "writable_app_dir", lambda: app_root)
    monkeypatch.setattr(logger_module, "_stdout_is_usable", lambda: False)
    monkeypatch.setattr(logger_module.faulthandler, "enable", lambda **_kwargs: None)

    path = logger_module.setup_logging()
    threading.excepthook(
        SimpleNamespace(
            exc_type=SystemExit,
            exc_value=SystemExit(0),
            exc_traceback=None,
            thread=threading.current_thread(),
        )
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "Unhandled thread exception" not in content
