# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ??_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import warnings
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.updater.installer_signature import (
    InstallerSignatureError,
    normalize_trusted_public_keys,
    verify_installer_metadata_signature,
)
from src.updater.installer_verifier import (
    InstallerVerificationError,
    verify_installer,
)
from src.updater.update_checker import (
    UpdateInfo,
    is_newer_version,
    is_trusted_download_url,
    update_notes_for_language,
)
from src.version import APP_VERSION, TRUSTED_INSTALLER_PUBLIC_KEYS
from src.utils.app_paths import (
    app_temp_dir,
    atomic_replace_secure_file,
    atomic_write_text,
    read_secure_text,
    secure_file_size,
    secure_unlink,
    secure_writable_subdirectory,
)
from src.utils.secure_http import open_validated_requests_response
from src.utils.i18n import tr
from src.utils.localization import format_locale_number, normalize_ui_language

logger = logging.getLogger(__name__)


def _safe_disconnect(signal: QObject, slot: QObject | None = None) -> None:
    """Disconnect a Qt signal without warning if it was already disconnected."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            if slot is None:
                signal.disconnect()
            else:
                signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass


def _set_button_action(button: QPushButton, action) -> None:
    _safe_disconnect(button.clicked)
    button.clicked.connect(action)


_CHUNK = 65_536
_DOWNLOAD_TIMEOUT = (8, 60)
_DOWNLOAD_HEADERS = {
    "Accept": "application/octet-stream",
    "Accept-Encoding": "identity",
    "User-Agent": "MioTranslator-Updater/1.0",
}
_UPDATE_RESULT_ARG_PREFIX = "--mio-update-result="
_UPDATE_RESULT_MAX_BYTES = 4096
_UPDATE_RESULT_FILENAME_RE = re.compile(
    r"mio-update-install-[A-Za-z0-9_-]+\.result\.json"
)
_DEFERRED_UPDATE_SCHEMA_VERSION = 1
_DEFERRED_UPDATE_METADATA_FILENAME = "pending-update.json"
_DEFERRED_UPDATE_METADATA_MAX_BYTES = 256 * 1024
_MANAGED_RETAINED_INSTALLER_RE = re.compile(r"mio-update-[0-9a-f]{64}\.exe")


@dataclass(frozen=True, slots=True)
class UpdateInstallResult:
    succeeded: bool
    exit_code: int | None = None


def _retained_update_dir() -> Path:
    """Return the persistent private directory used for deferred updates."""

    return secure_writable_subdirectory("updates")


def consume_update_install_result(
    argv: list[str] | None = None,
) -> UpdateInstallResult | None:
    """Consume a result marker supplied by a pre-visible-installer build.

    Current ``Install Now`` never creates this marker or starts a detached
    helper; retaining the reader lets an older Mio release finish a one-time
    upgrade into this version safely.  The command-line switch is removed
    before Qt processes application arguments. Any malformed or missing marker
    is treated as a recoverable installation failure so the relaunched
    application can explain what happened locally.
    """

    arguments = sys.argv if argv is None else argv
    marker_value: str | None = None
    retained_arguments: list[str] = []
    for argument in arguments:
        text = str(argument)
        if text.startswith(_UPDATE_RESULT_ARG_PREFIX):
            if marker_value is None:
                marker_value = text[len(_UPDATE_RESULT_ARG_PREFIX) :].strip()
            continue
        retained_arguments.append(argument)
    if marker_value is None:
        return None
    arguments[:] = retained_arguments

    marker_path: Path | None = None
    try:
        candidate = Path(marker_value).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Update result marker path must be absolute")
        candidate_path = Path(os.path.abspath(os.fspath(candidate)))
        expected_parent = Path(os.path.abspath(os.fspath(app_temp_dir())))
        if os.path.normcase(os.fspath(candidate_path.parent)) != os.path.normcase(
            os.fspath(expected_parent)
        ):
            raise ValueError("Update result marker is outside the application temp directory")
        if _UPDATE_RESULT_FILENAME_RE.fullmatch(candidate_path.name) is None:
            raise ValueError("Update result marker has an invalid file name")
        marker_path = candidate_path

        payload = json.loads(
            read_secure_text(
                marker_path,
                encoding="utf-8",
                max_bytes=_UPDATE_RESULT_MAX_BYTES,
            )
        )
        if not isinstance(payload, dict):
            raise ValueError("Update result marker must contain an object")
        status = str(payload.get("status") or "").strip().casefold()
        if status not in {"success", "failed"}:
            raise ValueError("Update result marker contains an invalid status")
        raw_exit_code = payload.get("exit_code")
        exit_code = (
            int(raw_exit_code)
            if isinstance(raw_exit_code, int) and not isinstance(raw_exit_code, bool)
            else None
        )
        result = UpdateInstallResult(status == "success", exit_code)
        if result.succeeded:
            try:
                _clear_deferred_update_state()
            except Exception:
                logger.warning(
                    "Unable to clean the installed deferred update",
                    exc_info=True,
                )
            try:
                log_name = marker_path.name.removesuffix(".result.json") + ".log"
                _secure_remove_private_snapshot(marker_path.with_name(log_name))
            except Exception:
                logger.debug("Unable to remove the successful installer log", exc_info=True)
        return result
    except Exception:
        logger.warning("Unable to read the update installation result", exc_info=True)
        return UpdateInstallResult(False, None)
    finally:
        if marker_path is not None:
            try:
                secure_unlink(marker_path, missing_ok=True)
            except Exception:
                logger.debug("Unable to remove the update result marker", exc_info=True)


def show_update_install_result(
    parent: QWidget | None,
    result: UpdateInstallResult,
    ui_lang: str,
) -> None:
    """Show the localized outcome after the installer relaunches Mio."""

    language = normalize_ui_language(ui_lang)
    if result.succeeded:
        logger.info("Update installation completed successfully")
        message = tr(language, "update_install_success_message")
        status_setter = getattr(parent, "_set_bottom", None)
        if callable(status_setter):
            status_setter(
                message,
                "success",
                key="update_install_success_message",
            )
        return

    logger.warning(
        "Update installation failed or did not return a valid result (exit_code=%s)",
        result.exit_code,
    )
    QMessageBox.warning(
        parent,
        tr(language, "update_install_failed_title"),
        tr(language, "update_install_failed_message"),
    )


def _installer_filename(update_info: UpdateInfo, ui_lang: str = "en") -> str:
    raw_name = update_info.installer_name
    if not raw_name:
        raw_name = Path(urlparse(update_info.download_url).path).name
    filename = Path(str(raw_name or "MioTranslator-Setup.exe")).name
    if not filename.lower().endswith(".exe"):
        raise ValueError(
            tr(normalize_ui_language(ui_lang), "update_error_installer_type")
        )
    return filename


def _deferred_update_metadata_path() -> Path:
    return _retained_update_dir() / _DEFERRED_UPDATE_METADATA_FILENAME


def _retained_installer_path(
    update_info: UpdateInfo,
    ui_lang: str = "en",
) -> Path:
    _installer_filename(update_info, ui_lang)
    digest = str(update_info.sha256 or "").strip().lower()
    token = digest if re.fullmatch(r"[0-9a-f]{64}", digest) else "invalid"
    return _retained_update_dir() / f"mio-update-{token}.exe"


def _same_lexical_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(first))) == os.path.normcase(
        os.path.abspath(os.fspath(second))
    )


def _secure_remove_private_snapshot(path: Path) -> bool:
    try:
        expected_stat = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        logger.debug("Unable to inspect deferred update file for cleanup", exc_info=True)
        return False
    try:
        secure_unlink(path, expected_stat=expected_stat)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        logger.warning(
            "Refusing to remove an unsafe or replaced deferred update file: %s",
            path,
            exc_info=True,
        )
        return False


def _cleanup_managed_retained_installers(*, keep: Path | None = None) -> None:
    directory = _retained_update_dir()
    try:
        entries = list(os.scandir(directory))
    except OSError:
        logger.debug("Unable to scan retained update directory", exc_info=True)
        return
    for entry in entries:
        if _MANAGED_RETAINED_INSTALLER_RE.fullmatch(entry.name) is None:
            continue
        candidate = Path(entry.path)
        if keep is not None and _same_lexical_path(candidate, keep):
            continue
        _secure_remove_private_snapshot(candidate)


def _clear_deferred_update_state() -> None:
    _secure_remove_private_snapshot(_deferred_update_metadata_path())
    _cleanup_managed_retained_installers()


def _normalized_deferred_update_info(update_info: UpdateInfo) -> UpdateInfo:
    flow = str(getattr(update_info, "flow", "update") or "update").strip().casefold()
    if flow != "update":
        raise ValueError("Repair installers must not be persisted as application updates")

    version = str(update_info.version or "").strip()
    if not version or len(version) > 64:
        raise ValueError("Deferred update version is invalid")
    if not is_newer_version(version, APP_VERSION):
        raise ValueError("Deferred update is not newer than the installed version")

    download_url = str(update_info.download_url or "").strip()
    if len(download_url) > 4096 or not is_trusted_download_url(download_url):
        raise ValueError("Deferred update download URL is not trusted")

    installer_name = _installer_filename(update_info, "en")
    if len(installer_name) > 255:
        raise ValueError("Deferred update installer name is too long")

    size_bytes = update_info.size_bytes
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise ValueError("Deferred update installer size is invalid")
    digest = str(update_info.sha256 or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Deferred update SHA256 digest is invalid")

    notes = str(update_info.notes or "")
    localized_notes: dict[str, str] = {}
    if not isinstance(update_info.localized_notes, dict):
        raise ValueError("Deferred update localized notes are invalid")
    for raw_language, raw_text in update_info.localized_notes.items():
        language = str(raw_language or "").strip().lower().replace("_", "-")
        text = str(raw_text or "")
        if not language or len(language) > 32 or len(text) > 100_000:
            raise ValueError("Deferred update localized notes are invalid")
        localized_notes[language] = text

    installer_signature = str(update_info.installer_signature or "").strip()
    signature_algorithm = str(
        update_info.installer_signature_algorithm or ""
    ).strip()
    signature_key_id = str(update_info.installer_signature_key_id or "").strip()
    verify_installer_metadata_signature(
        sha256=digest,
        size_bytes=size_bytes,
        signature=installer_signature,
        signature_algorithm=signature_algorithm,
        signature_key_id=signature_key_id,
        trusted_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
    )

    return UpdateInfo(
        version=version,
        download_url=download_url,
        notes=notes,
        localized_notes=localized_notes,
        installer_name=installer_name,
        size_bytes=size_bytes,
        sha256=digest,
        installer_signature=installer_signature,
        installer_signature_algorithm=signature_algorithm,
        installer_signature_key_id=signature_key_id,
        flow="update",
    )


def _deferred_update_payload(update_info: UpdateInfo) -> dict[str, object]:
    normalized = _normalized_deferred_update_info(update_info)
    return {
        "schema_version": _DEFERRED_UPDATE_SCHEMA_VERSION,
        "update": {
            "version": normalized.version,
            "download_url": normalized.download_url,
            "notes": normalized.notes,
            "localized_notes": normalized.localized_notes,
            "installer_name": normalized.installer_name,
            "size_bytes": normalized.size_bytes,
            "sha256": normalized.sha256,
            "installer_signature": normalized.installer_signature,
            "installer_signature_algorithm": normalized.installer_signature_algorithm,
            "installer_signature_key_id": normalized.installer_signature_key_id,
            "flow": "update",
        },
    }


def _update_info_from_deferred_payload(payload: object) -> UpdateInfo:
    if not isinstance(payload, dict):
        raise ValueError("Deferred update metadata must contain an object")
    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or schema_version != _DEFERRED_UPDATE_SCHEMA_VERSION
    ):
        raise ValueError("Deferred update metadata schema is unsupported")
    raw_update = payload.get("update")
    if not isinstance(raw_update, dict):
        raise ValueError("Deferred update metadata is missing update information")

    raw_installer_name = raw_update.get("installer_name")
    if not isinstance(raw_installer_name, str) or not raw_installer_name.strip():
        raise ValueError("Deferred update installer name is invalid")
    if Path(raw_installer_name).name != raw_installer_name:
        raise ValueError("Deferred update installer name must be a basename")

    raw_localized_notes = raw_update.get("localized_notes", {})
    if not isinstance(raw_localized_notes, dict):
        raise ValueError("Deferred update localized notes are invalid")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_localized_notes.items()
    ):
        raise ValueError("Deferred update localized notes are invalid")

    for field_name in (
        "version",
        "download_url",
        "notes",
        "sha256",
        "installer_signature",
        "installer_signature_algorithm",
        "installer_signature_key_id",
        "flow",
    ):
        if not isinstance(raw_update.get(field_name), str):
            raise ValueError(f"Deferred update field is invalid: {field_name}")
    raw_size = raw_update.get("size_bytes")
    if isinstance(raw_size, bool) or not isinstance(raw_size, int):
        raise ValueError("Deferred update installer size is invalid")

    info = UpdateInfo(
        version=raw_update["version"],
        download_url=raw_update["download_url"],
        notes=raw_update["notes"],
        localized_notes=dict(raw_localized_notes),
        installer_name=raw_installer_name,
        size_bytes=raw_size,
        sha256=raw_update["sha256"],
        installer_signature=raw_update["installer_signature"],
        installer_signature_algorithm=raw_update[
            "installer_signature_algorithm"
        ],
        installer_signature_key_id=raw_update["installer_signature_key_id"],
        flow=raw_update["flow"],
    )
    normalized = _normalized_deferred_update_info(info)
    if normalized.installer_name != raw_installer_name:
        raise ValueError("Deferred update installer name is not sanitized")
    return normalized


def _write_deferred_update_metadata(
    update_info: UpdateInfo,
    installer_path: Path,
) -> UpdateInfo:
    normalized = _normalized_deferred_update_info(update_info)
    expected_installer = _retained_installer_path(normalized)
    if not _same_lexical_path(Path(installer_path), expected_installer):
        raise ValueError("Deferred update installer path is not managed by Mio")
    if secure_file_size(expected_installer) != normalized.size_bytes:
        raise InstallerVerificationError("Deferred update installer size changed")

    payload_text = json.dumps(
        _deferred_update_payload(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload_text.encode("utf-8")) > _DEFERRED_UPDATE_METADATA_MAX_BYTES:
        raise ValueError("Deferred update metadata is too large")
    atomic_write_text(
        _deferred_update_metadata_path(),
        payload_text,
        encoding="utf-8",
        overwrite=True,
    )
    _cleanup_managed_retained_installers(keep=expected_installer)
    return normalized


def persist_deferred_update(
    update_info: UpdateInfo,
    installer_path: Path,
) -> bool:
    if str(getattr(update_info, "flow", "update") or "update").casefold() == "repair":
        return False
    normalized = _normalized_deferred_update_info(update_info)
    verify_installer(
        installer_path,
        expected_sha256=normalized.sha256,
        expected_size=normalized.size_bytes,
        installer_signature=normalized.installer_signature,
        signature_algorithm=normalized.installer_signature_algorithm,
        signature_key_id=normalized.installer_signature_key_id,
        trusted_public_keys=TRUSTED_INSTALLER_PUBLIC_KEYS,
    )
    _write_deferred_update_metadata(normalized, Path(installer_path))
    return True


def load_deferred_update_info() -> UpdateInfo | None:
    metadata_path = _deferred_update_metadata_path()
    try:
        payload = json.loads(
            read_secure_text(
                metadata_path,
                encoding="utf-8",
                max_bytes=_DEFERRED_UPDATE_METADATA_MAX_BYTES,
            )
        )
        update_info = _update_info_from_deferred_payload(payload)
        installer_path = _retained_installer_path(update_info)
        if secure_file_size(installer_path) != update_info.size_bytes:
            raise InstallerVerificationError("Deferred update installer size changed")
    except FileNotFoundError:
        try:
            _cleanup_managed_retained_installers()
        except Exception:
            logger.warning("Unable to clean orphaned retained installers", exc_info=True)
        return None
    except Exception:
        logger.warning("Discarding malformed or stale deferred update metadata", exc_info=True)
        try:
            _clear_deferred_update_state()
        except Exception:
            logger.warning("Unable to clean stale deferred update state", exc_info=True)
        return None

    _cleanup_managed_retained_installers(keep=installer_path)
    return update_info


def _localized_update_error(language: str, error: BaseException) -> str:
    """Convert updater implementation failures into safe, localized UI copy."""

    language = normalize_ui_language(language)
    message = str(error or "").strip()
    lowered = message.casefold()
    logger.error("Update operation failed: %s", message or error.__class__.__name__)

    if isinstance(error, (InstallerVerificationError, InstallerSignatureError)):
        return tr(language, "update_error_verification")
    if isinstance(error, requests.RequestException):
        return tr(language, "update_error_network")
    if isinstance(error, FileExistsError):
        return tr(language, "update_error_temp_file")
    if isinstance(error, PermissionError):
        return tr(language, "update_error_storage")

    if "redirect" in lowered and "not trusted" in lowered:
        return tr(language, "update_error_untrusted_redirect")
    if "not trusted" in lowered or "untrusted" in lowered:
        return tr(language, "update_error_untrusted_url")
    if "content-encoding" in lowered:
        return tr(language, "update_error_encoding")
    if "content-length" in lowered:
        if "does not match" in lowered or "exceeded" in lowered:
            return tr(language, "update_error_server_size")
        return tr(language, "update_error_content_length")
    if "signed manifest" in lowered:
        return tr(language, "update_error_manifest_size")
    if "sha256" in lowered or "checksum" in lowered:
        return tr(language, "update_error_checksum")
    if "manifest" in lowered or "metadata" in lowered:
        return tr(language, "update_error_metadata")
    if any(
        token in lowered
        for token in ("signature", "verification", "reparse point", "hard link")
    ):
        return tr(language, "update_error_verification")
    if isinstance(error, OSError):
        return tr(language, "update_error_storage")
    if lowered.startswith("http ") or "connection" in lowered or "timed out" in lowered:
        return tr(language, "update_error_network")
    return tr(language, "update_error_unexpected")


def _launch_installer(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int | None,
    installer_signature: str,
    signature_algorithm: str,
    signature_key_id: str,
    trusted_public_keys=None,
) -> subprocess.Popen:
    """Start a freshly verified installer with its normal visible UI.

    The application intentionally does not use an updater helper or installer
    command-line switches here.  A helper cannot prove that the eventual
    installer process started before Mio exits, and silent switches prevent
    players from seeing the installer they explicitly requested.
    """
    if trusted_public_keys is None:
        trusted_public_keys = TRUSTED_INSTALLER_PUBLIC_KEYS
    verify_installer(
        path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        installer_signature=installer_signature,
        signature_algorithm=signature_algorithm,
        signature_key_id=signature_key_id,
        trusted_public_keys=trusted_public_keys,
    )

    return subprocess.Popen([str(path)], cwd=str(path.parent))


def _confirm_installer_process_started(process: object) -> None:
    """Reject a failed process creation before Mio begins its final shutdown.

    ``Popen`` returning a positive PID is the normal process-start guarantee.
    A GUI bootstrapper is allowed to exit with status zero immediately after it
    hands control to its visible child installer; a non-zero immediate exit is
    treated as a failed launch so Mio can remain available to the player.
    """

    try:
        pid = int(getattr(process, "pid"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise OSError("The installer process did not provide a valid PID") from exc
    if pid <= 0:
        raise OSError("The installer process did not provide a valid PID")

    poll = getattr(process, "poll", None)
    if not callable(poll):
        return
    exit_code = poll()
    if exit_code is not None and int(exit_code) != 0:
        raise OSError("The installer exited before startup completed")


def _display_version(version: str) -> str:
    text = str(version or "").strip()
    if not text:
        return "v?"
    return text if text.lower().startswith("v") else f"v{text}"


class _UpdateBridge(QObject):
    progress = Signal(int, int)
    complete = Signal(Path)
    error = Signal(str)


class _UpdateDownloadCancelled(Exception):
    pass


class UpdateWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        update_info: UpdateInfo,
        ui_lang: str,
    ) -> None:
        super().__init__(parent)
        # Update dialogs are normally parented by MainWindow.  Without
        # delete-on-close, every completed/abandoned update kept its complete
        # widget tree alive as a hidden QObject child until the application
        # exited.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._info = update_info
        self._version = update_info.version
        self._download_url = update_info.download_url
        self._expected_size = update_info.size_bytes
        self._expected_sha256 = update_info.sha256.lower()
        self._installer_signature = update_info.installer_signature
        self._signature_algorithm = update_info.installer_signature_algorithm
        self._signature_key_id = update_info.installer_signature_key_id
        self._repair_mode = str(getattr(update_info, "flow", "update") or "update").casefold() == "repair"
        self._ui_lang = normalize_ui_language(ui_lang)
        self._downloading = False
        self._download_done = False
        self._minimized = False
        self._destroying = False
        self._installing = False
        self._view_state = "initial"
        self._last_progress: tuple[int, int] | None = None
        self._retained_download = False
        self._deferred_metadata_ready = False
        self._installer_path: Path | None = None
        self._download_thread: threading.Thread | None = None
        self._download_cancel_event = threading.Event()
        self._download_response_lock = threading.Lock()
        self._active_download_response = None
        self._bridge = _UpdateBridge(self)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.complete.connect(self._on_download_complete)
        self._bridge.error.connect(self._on_download_error)

        self._final_path = _retained_installer_path(update_info, self._ui_lang)
        self._wip_path = self._final_path.with_suffix(".exe.tmp")
        if self._wip_path.exists():
            try:
                self._wip_path.unlink()
            except Exception:
                pass

        self.setWindowTitle(self._dialog_title())
        self.setMinimumSize(440, 360)
        self.resize(520, 420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._build()
        self._apply_style()
        self._restore_retained_installer()

    def _t(self, key: str, **kwargs) -> str:  # type: ignore[assignment]
        if kwargs is None:
            kwargs = {}
        return tr(self._ui_lang, key, **kwargs)

    def update_language(self, ui_lang: str) -> None:
        previous_language = self._ui_lang
        normalized = normalize_ui_language(ui_lang)
        if normalized == previous_language:
            return

        previous_error = self._sub_label.text() if self._view_state == "error" else ""
        self._ui_lang = normalized
        title = self._dialog_title()
        self.setWindowTitle(title)
        self._title_label.setText(title)
        self._notes_label.setText(
            update_notes_for_language(
                self._info,
                self._ui_lang,
                fallback=self._t("update_no_notes"),
            )
        )
        self._btn_ignore.setText(self._t("update_ignore"))

        if self._view_state == "downloading":
            self._progress_label.setText(self._t("update_downloading"))
            self._btn_secondary.setText(self._t("update_minimize"))
            self._btn_primary.setText(self._t("update_downloading_btn"))
            if self._last_progress is not None:
                self._on_progress(*self._last_progress)
        elif self._view_state == "ready":
            self._progress_label.setText(self._ready_description())
            self._sub_label.setText(self._ready_note())
            self._btn_secondary.setText(self._t("update_install_later"))
            self._btn_primary.setText(self._t("update_install_now"))
        elif self._view_state == "error":
            self._progress_label.setText(self._t("update_error"))
            self._sub_label.setText(
                self._relocalize_error(previous_error, previous_language)
            )
            self._btn_secondary.setText(self._t("update_close"))
            self._btn_primary.setText(self._t("update_retry"))
        elif self._view_state == "launching":
            self._btn_primary.setText(self._t("update_launching_installer"))
            self._sub_label.setText(self._t("update_launching_note"))
        else:
            self._progress_label.setText("")
            self._sub_label.setText("")
            self._btn_secondary.setText(self._t("update_later"))
            self._btn_primary.setText(self._download_button_text())

    def _relocalize_error(self, message: str, previous_language: str) -> str:
        for key in (
            "update_error_verification",
            "update_error_network",
            "update_error_temp_file",
            "update_error_storage",
            "update_error_untrusted_redirect",
            "update_error_untrusted_url",
            "update_error_encoding",
            "update_error_server_size",
            "update_error_content_length",
            "update_error_manifest_size",
            "update_error_checksum",
            "update_error_metadata",
            "update_error_unexpected",
            "update_installer_missing",
            "update_installer_launch_failed",
            "update_shutdown_failed",
        ):
            if message == tr(previous_language, key):
                return self._t(key)
        return message

    def _installer_verification_kwargs(self) -> dict[str, object]:
        return {
            "expected_sha256": self._expected_sha256,
            "expected_size": self._expected_size,
            "installer_signature": self._installer_signature,
            "signature_algorithm": self._signature_algorithm,
            "signature_key_id": self._signature_key_id,
            "trusted_public_keys": TRUSTED_INSTALLER_PUBLIC_KEYS,
        }

    def _dialog_title(self) -> str:
        return self._t("runtime_repair_title") if self._repair_mode else self._t("update_title")

    def _download_button_text(self) -> str:
        return self._t("runtime_repair_download_installer") if self._repair_mode else self._t("update_now")

    def _ready_description(self) -> str:
        return self._t("runtime_repair_ready") if self._repair_mode else self._t("update_ready_desc")

    def _install_note(self) -> str:
        return self._t("runtime_repair_install_note") if self._repair_mode else self._t("update_install_note")

    def _ready_note(self) -> str:
        if self._retained_download and not self._repair_mode:
            return self._t("update_retained_note")
        return self._install_note()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self._title_label = QLabel(self._dialog_title())
        title_row.addWidget(self._title_label)
        badge = QLabel(_display_version(self._version))
        badge.setObjectName("versionBadge")
        title_row.addWidget(badge)
        title_row.addStretch(1)
        root.addLayout(title_row)

        self._notes_label = QLabel()
        self._notes_label.setWordWrap(True)
        self._notes_label.setObjectName("notesLabel")
        notes_text = update_notes_for_language(self._info, self._ui_lang, fallback=self._t("update_no_notes"))
        self._notes_label.setText(notes_text)
        root.addWidget(self._notes_label, 1)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("progressLabel")
        root.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        root.addWidget(self._progress_bar)

        self._sub_label = QLabel("")
        self._sub_label.setObjectName("subLabel")
        self._sub_label.setWordWrap(True)
        root.addWidget(self._sub_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._btn_ignore = QPushButton(self._t("update_ignore"))
        self._btn_ignore.setObjectName("ignoreButton")
        self._btn_ignore.clicked.connect(self._on_ignore_version)
        btn_row.addWidget(self._btn_ignore)
        if self._repair_mode:
            self._btn_ignore.hide()
        self._btn_secondary = QPushButton(self._t("update_later"))
        self._btn_secondary.clicked.connect(self._on_window_close)
        btn_row.addWidget(self._btn_secondary)
        self._btn_primary = QPushButton(self._download_button_text())
        self._btn_primary.setObjectName("primaryButton")
        self._btn_primary.clicked.connect(self._start_download)
        btn_row.addWidget(self._btn_primary)
        root.addLayout(btn_row)

    def _switch_to_downloading(self) -> None:
        self._view_state = "downloading"
        self._last_progress = None
        self._notes_label.hide()
        self._progress_label.setText(self._t("update_downloading"))
        self._progress_label.setObjectName("progressLabel")
        self._progress_label.show()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._sub_label.setText("")
        self._sub_label.show()
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_minimize"))
        _set_button_action(self._btn_secondary, self._minimize_to_background)
        self._btn_primary.setText(self._t("update_downloading_btn"))
        self._btn_primary.setEnabled(False)

    def _switch_to_ready(self, *, retained: bool = False) -> None:
        self._installing = False
        self._view_state = "ready"
        self._retained_download = retained
        self._notes_label.hide()
        self._progress_label.setText(self._ready_description())
        self._progress_label.setObjectName("successLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._sub_label.setText(self._ready_note())
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_install_later"))
        self._btn_secondary.setEnabled(True)
        _set_button_action(self._btn_secondary, self._defer_installation)
        self._btn_primary.setText(self._t("update_install_now"))
        self._btn_primary.setEnabled(True)
        _set_button_action(self._btn_primary, self._run_installer)

    def _switch_to_error(
        self,
        message: str,
        *,
        retry_action=None,
        retain_installer: bool = False,
    ) -> None:
        self._installing = False
        self._view_state = "error"
        self._progress_label.setText(self._t("update_error"))
        self._progress_label.setObjectName("errorLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._sub_label.setText(message)
        self._btn_ignore.hide()
        self._btn_secondary.setEnabled(True)
        if retain_installer:
            self._btn_secondary.setText(self._t("update_install_later"))
            _set_button_action(self._btn_secondary, self._defer_installation)
        else:
            self._btn_secondary.setText(self._t("update_close"))
            _set_button_action(self._btn_secondary, self.close)
        self._btn_primary.setText(self._t("update_retry"))
        self._btn_primary.setEnabled(True)
        _set_button_action(self._btn_primary, retry_action or self._start_download)

    def _on_ignore_version(self) -> None:
        if self._repair_mode:
            self.close()
            return
        master = self.parent()
        if master is not None and hasattr(master, "_ignore_update_version"):
            master._ignore_update_version(self._version)
        self.close()

    def _discard_retained_installer(self) -> None:
        self._installer_path = None
        self._download_done = False
        self._retained_download = False
        self._deferred_metadata_ready = False
        if self._repair_mode:
            _secure_remove_private_snapshot(self._final_path)
            return
        try:
            _clear_deferred_update_state()
        except Exception:
            logger.warning("Unable to clear invalid deferred update state", exc_info=True)

    def _restore_retained_installer(self) -> bool:
        if not self._final_path.exists() or not self._expected_sha256:
            return False
        try:
            verify_installer(
                self._final_path,
                **self._installer_verification_kwargs(),
            )
        except InstallerVerificationError:
            logger.warning("Retained update installer failed verification", exc_info=True)
            self._discard_retained_installer()
            return False

        if not self._repair_mode:
            try:
                _write_deferred_update_metadata(self._info, self._final_path)
                self._deferred_metadata_ready = True
            except Exception:
                logger.warning(
                    "Unable to persist deferred update metadata",
                    exc_info=True,
                )
                self._switch_to_error(self._t("update_error_storage"))
                return True

        self._installer_path = self._final_path
        self._download_done = True
        self._switch_to_ready(retained=True)
        return True

    def _start_download(self) -> None:
        if self._downloading and self._download_thread and self._download_thread.is_alive():
            return
        try:
            normalize_trusted_public_keys(TRUSTED_INSTALLER_PUBLIC_KEYS)
        except InstallerSignatureError as exc:
            self._switch_to_error(_localized_update_error(self._ui_lang, exc))
            return
        if self._restore_retained_installer():
            return
        self._download_cancel_event.clear()
        self._downloading = True
        self._switch_to_downloading()
        self._download_thread = threading.Thread(
            target=self._download_worker,
            daemon=True,
            name="mio-update-download",
        )
        self._download_thread.start()

    def _download_worker(self) -> None:
        promoted = False
        try:
            if self._download_cancel_event.is_set():
                raise _UpdateDownloadCancelled()
            if not self._expected_sha256:
                raise RuntimeError("Update manifest is missing SHA256 verification data")
            if self._expected_size is None or self._expected_size <= 0:
                raise RuntimeError("Update manifest is missing installer size verification data")
            normalize_trusted_public_keys(TRUSTED_INSTALLER_PUBLIC_KEYS)
            if not is_trusted_download_url(self._download_url):
                raise RuntimeError("Update download URL is not trusted")

            with open_validated_requests_response(
                self._download_url,
                url_validator=lambda candidate: is_trusted_download_url(
                    candidate,
                    allow_release_asset_redirect=True,
                ),
                timeout=_DOWNLOAD_TIMEOUT,
                label="Update download",
                headers=_DOWNLOAD_HEADERS,
                stream=True,
                max_redirects=5,
                request_get=requests.get,
            ) as response:
                with self._download_response_lock:
                    self._active_download_response = response
                response.raise_for_status()
                content_encoding = str(
                    response.headers.get("content-encoding") or ""
                ).strip()
                if content_encoding and content_encoding.casefold() != "identity":
                    raise RuntimeError(
                        "Update server returned an unsupported Content-Encoding"
                    )

                content_length = response.headers.get("content-length")
                total = 0
                if content_length not in (None, ""):
                    normalized_length = str(content_length).strip()
                    if not re.fullmatch(r"[0-9]+", normalized_length):
                        raise RuntimeError(
                            "Update server returned an invalid Content-Length"
                        )
                    total = int(normalized_length)
                    if total <= 0:
                        raise RuntimeError(
                            "Update server returned an invalid Content-Length"
                        )
                if total and total != self._expected_size:
                    raise RuntimeError(
                        "Update content length does not match the signed manifest "
                        f"(manifest={self._expected_size}, server={total})"
                    )

                hasher = hashlib.sha256()
                downloaded = 0
                with self._wip_path.open("xb") as handle:
                    for chunk in response.iter_content(chunk_size=_CHUNK):
                        if self._download_cancel_event.is_set():
                            raise _UpdateDownloadCancelled()
                        if not chunk:
                            continue
                        projected = downloaded + len(chunk)
                        if projected > self._expected_size:
                            raise RuntimeError(
                                "Update download exceeded the signed manifest size"
                            )
                        if total and projected > total:
                            raise RuntimeError(
                                "Update download exceeded the server Content-Length"
                            )
                        handle.write(chunk)
                        hasher.update(chunk)
                        downloaded = projected
                        self._bridge.progress.emit(
                            downloaded,
                            total or self._expected_size,
                        )
                    handle.flush()
                    os.fsync(handle.fileno())

                if total and downloaded != total:
                    raise RuntimeError(
                        "Update download size does not match Content-Length "
                        f"({downloaded} != {total})"
                    )
                if downloaded != self._expected_size:
                    raise RuntimeError(
                        "Update download size does not match the signed manifest "
                        f"({downloaded} != {self._expected_size})"
                    )
                if hasher.hexdigest().lower() != self._expected_sha256:
                    raise RuntimeError("Update download checksum verification failed")
                if self._download_cancel_event.is_set():
                    raise _UpdateDownloadCancelled()

                atomic_replace_secure_file(
                    self._wip_path,
                    self._final_path,
                    expected_size=downloaded,
                )
                promoted = True
                verify_installer(
                    self._final_path,
                    **self._installer_verification_kwargs(),
                )
                if self._download_cancel_event.is_set():
                    raise _UpdateDownloadCancelled()
                if not self._repair_mode:
                    _write_deferred_update_metadata(self._info, self._final_path)
                    self._deferred_metadata_ready = True
                self._installer_path = self._final_path
                if not self._destroying and not self._download_cancel_event.is_set():
                    self._bridge.complete.emit(self._final_path)
        except _UpdateDownloadCancelled:
            logger.info("Update download cancelled during shutdown")
        except Exception as exc:
            for candidate in (self._wip_path, self._final_path if promoted else None):
                if candidate is None:
                    continue
                try:
                    if candidate.exists():
                        candidate.unlink()
                except Exception:
                    logger.debug("Failed to remove rejected update download", exc_info=True)
            if not self._destroying:
                self._bridge.error.emit(_localized_update_error(self._ui_lang, exc))
        finally:
            with self._download_response_lock:
                self._active_download_response = None
            if self._download_cancel_event.is_set():
                for candidate in (self._wip_path, self._final_path if promoted else None):
                    if candidate is None:
                        continue
                    try:
                        if candidate.exists():
                            candidate.unlink()
                    except Exception:
                        logger.debug(
                            "Failed to remove cancelled update download",
                            exc_info=True,
                        )
            if self._download_thread is threading.current_thread():
                self._download_thread = None

    def shutdown(self) -> None:
        """Cancel an active transfer and release its response/thread on app exit."""

        self._destroying = True
        self._download_cancel_event.set()
        with self._download_response_lock:
            response = self._active_download_response
        close_response = getattr(response, "close", None)
        if callable(close_response):
            try:
                close_response()
            except Exception:
                logger.debug("Failed to close update response", exc_info=True)
        thread = self._download_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if thread is None or not thread.is_alive():
            self._download_thread = None
        self._downloading = False
        self.close()

    def _on_progress(self, downloaded: int, total: int) -> None:
        self._last_progress = (downloaded, total)
        if total > 0:
            if self._progress_bar.minimum() != 0 or self._progress_bar.maximum() != 100:
                self._progress_bar.setRange(0, 100)
            ratio = downloaded / total
            dl_mb = downloaded / 1_048_576
            total_mb = total / 1_048_576
            pct = int(ratio * 100)
            self._progress_bar.setValue(pct)
            self._sub_label.setText(
                self._t(
                    "update_progress_mb",
                    downloaded=format_locale_number(dl_mb, self._ui_lang, decimals=1),
                    total=format_locale_number(total_mb, self._ui_lang, decimals=1),
                    pct=format_locale_number(pct, self._ui_lang),
                )
            )
        else:
            if self._progress_bar.minimum() != 0 or self._progress_bar.maximum() != 0:
                self._progress_bar.setRange(0, 0)
            dl_mb = downloaded / 1_048_576
            self._sub_label.setText(
                self._t(
                    "update_progress_mb_unknown",
                    downloaded=format_locale_number(dl_mb, self._ui_lang, decimals=1),
                )
            )

    def _on_download_complete(self, path: Path) -> None:  # type: ignore[override]
        self._downloading = False
        self._download_done = True
        self._installer_path = Path(path)
        if self._minimized:
            self._minimized = False
            self.show()
            self.raise_()
        self._switch_to_ready(retained=False)

    def _on_download_error(self, message: str) -> None:
        self._downloading = False
        self._switch_to_error(message)

    def _minimize_to_background(self) -> None:
        self._minimized = True
        self.hide()

    def _defer_installation(self) -> None:
        if (
            not self._repair_mode
            and not self._deferred_metadata_ready
            and self._installer_path is not None
        ):
            try:
                persist_deferred_update(self._info, self._installer_path)
                self._deferred_metadata_ready = True
            except Exception:
                logger.warning(
                    "Unable to retain the update for later installation",
                    exc_info=True,
                )
                self._switch_to_error(self._t("update_error_storage"))
                return
        if self._installer_path is not None:
            logger.info(
                "Retaining downloaded update for later installation: %s",
                self._installer_path,
            )
        self._retained_download = True
        self.close()

    def _run_installer(self) -> None:
        if self._installing:
            return
        if self._installer_path is None or not self._installer_path.exists():
            self._switch_to_error(self._t("update_installer_missing"))
            return
        self._installing = True
        self._btn_primary.setEnabled(False)
        self._btn_secondary.setEnabled(False)
        self._view_state = "launching"
        self._btn_primary.setText(self._t("update_launching_installer"))
        self._sub_label.setText(self._t("update_launching_note"))
        master = self.parent()
        prepare_master = None
        prepared_master = None
        try:
            # Validate before touching the active runtime.  _launch_installer
            # validates a second time immediately before Popen to close the
            # verify-to-launch race.
            verify_installer(
                self._installer_path,
                **self._installer_verification_kwargs(),
            )
        except Exception:
            logger.error("Update installer verification failed before shutdown", exc_info=True)
            self._switch_to_error(self._t("update_error_verification"))
            return

        try:
            prepare = getattr(master, "_prepare_for_update_install", None)
            if callable(prepare):
                prepare_master = master
                if prepare() is False:
                    raise RuntimeError("Mio runtime did not quiesce for update")
                prepared_master = master
            process = _launch_installer(
                self._installer_path,
                **self._installer_verification_kwargs(),
            )
            _confirm_installer_process_started(process)
        except Exception:
            logger.error("Failed to launch verified update installer", exc_info=True)
            abort_prepare = getattr(
                prepare_master,
                "_abort_update_install_preparation",
                None,
            )
            if callable(abort_prepare):
                try:
                    abort_prepare()
                except Exception:
                    logger.warning(
                        "Failed to restore Mio after installer launch failure",
                        exc_info=True,
                    )
            message_key = (
                "update_shutdown_failed"
                if prepare_master is not None and prepared_master is None
                else "update_installer_launch_failed"
            )
            self._switch_to_error(
                self._t(message_key),
                retry_action=self._run_installer,
                retain_installer=True,
            )
            return
        self._destroy_master_if_alive()

    def _destroy_master_if_alive(self) -> None:
        master = self.parent()
        if master is not None:
            try:
                if master.__class__.__name__ == "MainWindow" and hasattr(master, "destroy"):
                    master.destroy()
                else:
                    master.close()
            except Exception:
                pass
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(120, app.quit)

    def _on_window_close(self) -> None:
        if self._minimized:
            return
        if self._download_done:
            self.close()
            return
        if self._downloading:
            self._minimize_to_background()
            return
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._downloading and not self._download_done and not self._destroying:
            self._minimized = True
            self.hide()
            event.ignore()
            return
        self._destroying = True
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
        QDialog { background: #f5f5f7; }
        QLabel { color: #1d1d1f; font-size: 14px; }
        #notesLabel {
            background: #f7f8fc;
            border: 1px solid #d8dde6;
            border-radius: 12px;
            padding: 12px;
            color: #6e6e73;
        }
        #versionBadge {
            background: #e8f2ff;
            border-radius: 999px;
            color: #0071e3;
            font-weight: 700;
            font-size: 12px;
            padding: 3px 10px;
        }
        #progressLabel { color: #6e6e73; }
        #successLabel { color: #1f6b3d; }
        #errorLabel { color: #b91c1c; }
        #subLabel { color: #8e8e93; font-size: 13px; }
        QProgressBar {
            border: none;
            background: #e0e4ea;
            border-radius: 6px;
            height: 8px;
        }
        QProgressBar::chunk { background: #0071e3; border-radius: 6px; }
        QPushButton {
            background: #eef1f5;
            border: 1px solid #d8dde6;
            border-radius: 12px;
            color: #1d1d1f;
            padding: 8px 16px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover { background: #e0e4ea; }
        #primaryButton { background: #0071e3; color: #ffffff; border: 0; }
        #primaryButton:hover { background: #0059b8; }
        #ignoreButton {
            background: transparent;
            color: #8e8e93;
            border: 1px solid #d8dde6;
            font-size: 13px;
        }
        #ignoreButton:hover { background: #e0e4ea; color: #1d1d1f; }
        """)
