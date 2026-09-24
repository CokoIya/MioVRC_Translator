"""Post-window startup maintenance and catalog refresh scheduling."""
from __future__ import annotations

import logging
import threading

from src.utils.startup_timing import startup_stage


logger = logging.getLogger(__name__)

_START_LOCK = threading.Lock()
_STARTED = False
_MAINTENANCE_THREAD: threading.Thread | None = None


def _apply_remote_catalog(data: dict) -> None:
    with startup_stage("background.catalog_apply"):
        from src.utils.catalog_loader import load_catalog_from_data
        from src.utils.ui_config import set_catalog

        set_catalog(load_catalog_from_data(data))
        try:
            from src.utils.signed_catalog import apply_signed_catalog

            apply_signed_catalog(data)
        except Exception:
            logger.exception("Could not apply the signed catalog section")


def _run_maintenance() -> None:
    try:
        with startup_stage("background.legacy_data_migration"):
            from src.utils.app_paths import run_deferred_legacy_data_migration

            run_deferred_legacy_data_migration()
    except Exception:
        logger.exception("Deferred legacy data migration failed")

    try:
        with startup_stage("background.obsolete_model_cleanup"):
            from src.utils.config_manager import cleanup_obsolete_runtime_models

            cleanup_obsolete_runtime_models()
    except Exception:
        logger.exception("Deferred obsolete model cleanup failed")


def schedule_post_ui_startup_tasks() -> bool:
    """Schedule non-critical startup work once, without blocking the UI.

    Call this from the first Qt event-loop turn after the main window has been
    shown.  Catalog networking and filesystem maintenance use separate daemon
    workers so neither operation delays interaction with the window.

    Returns ``True`` when work was newly scheduled and ``False`` on later calls.
    """

    global _STARTED, _MAINTENANCE_THREAD
    with _START_LOCK:
        if _STARTED:
            return False
        _STARTED = True

        maintenance_thread = threading.Thread(
            target=_run_maintenance,
            daemon=True,
            name="startup-maintenance",
        )
        _MAINTENANCE_THREAD = maintenance_thread

    try:
        from src.utils.catalog_fetcher import refresh_catalog

        refresh_catalog(_apply_remote_catalog)
    except Exception:
        logger.exception("Could not schedule deferred catalog refresh")

    try:
        maintenance_thread.start()
    except BaseException:
        with _START_LOCK:
            _STARTED = False
            _MAINTENANCE_THREAD = None
        raise
    return True


def run_post_ui_startup_tasks() -> bool:
    """Backward-friendly alias for :func:`schedule_post_ui_startup_tasks`."""

    return schedule_post_ui_startup_tasks()


def _reset_post_ui_startup_tasks_for_tests() -> None:
    global _STARTED, _MAINTENANCE_THREAD
    with _START_LOCK:
        if _MAINTENANCE_THREAD is not None and _MAINTENANCE_THREAD.is_alive():
            raise RuntimeError("cannot reset startup tasks while maintenance is active")
        _STARTED = False
        _MAINTENANCE_THREAD = None
