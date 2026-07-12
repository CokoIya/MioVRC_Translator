# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ??_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import warnings
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from src.updater.installer_signature import (
    InstallerSignatureError,
    normalize_trusted_public_keys,
)
from src.updater.installer_verifier import (
    InstallerVerificationError,
    verify_installer,
    windows_powershell_executable,
)
from src.updater.update_checker import UpdateInfo, is_trusted_download_url, update_notes_for_language
from src.version import TRUSTED_INSTALLER_PUBLIC_KEYS
from src.utils.app_paths import app_temp_dir, atomic_replace_secure_file
from src.utils.secure_http import open_validated_requests_response
from src.utils.i18n import tr

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


def _installer_filename(update_info: UpdateInfo) -> str:
    raw_name = update_info.installer_name
    if not raw_name:
        raw_name = Path(urlparse(update_info.download_url).path).name
    filename = Path(str(raw_name or "MioTranslator-Setup.exe")).name
    if not filename.lower().endswith(".exe"):
        raise ValueError("Update installer must be a Windows .exe file")
    return filename


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _restart_executable() -> Path | None:
    executable = Path(sys.executable or "")
    if not executable:
        return None
    if bool(getattr(sys, "frozen", False)):
        return executable
    if executable.name.lower() == "miotranslator.exe":
        return executable
    return None


def _write_windows_update_helper(
    installer_path: Path,
    restart_exe: Path | None,
    *,
    expected_sha256: str,
    expected_size: int | None,
) -> Path:
    expected_digest = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise InstallerVerificationError("A valid installer SHA256 digest is required")
    try:
        normalized_size = int(expected_size)
    except (TypeError, ValueError) as exc:
        raise InstallerVerificationError("Installer size metadata is invalid") from exc
    if normalized_size <= 0:
        raise InstallerVerificationError("Installer size metadata is invalid")

    temp_dir = app_temp_dir()
    descriptor: int | None = None
    helper_path: Path | None = None
    try:
        descriptor, helper_name = tempfile.mkstemp(
            prefix="mio-update-install-",
            suffix=".ps1",
            dir=temp_dir,
        )
        helper_path = Path(helper_name)
        log_path = helper_path.with_suffix(".log")
        install_dir = restart_exe.parent if restart_exe is not None else None
        restart_literal = "$null" if restart_exe is None else _ps_literal(restart_exe)
        install_dir_literal = "$null" if install_dir is None else _ps_literal(install_dir)
        script = f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$managementModule = [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')
$utilityModule = [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')
Import-Module -Name $managementModule -Force -ErrorAction Stop
Import-Module -Name $utilityModule -Force -ErrorAction Stop
$PSModuleAutoLoadingPreference = 'None'
$installer = {_ps_literal(installer_path)}
$restartExe = {restart_literal}
$installDir = {install_dir_literal}
$installerLog = {_ps_literal(log_path)}
$expectedSha256 = {_ps_literal(expected_digest)}
$expectedSize = [Int64]{normalized_size}
$waitPid = {os.getpid()}
$launchStream = $null

try {{
    try {{
        Wait-Process -Id $waitPid -Timeout 90 -ErrorAction SilentlyContinue
    }} catch {{
    }}

    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {{
        throw 'Update installer is missing or is not a regular file'
    }}
    $item = Get-Item -LiteralPath $installer -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {{
        throw 'Update installer must not be a reparse point'
    }}
    if ([Int64]$item.Length -ne $expectedSize) {{
        throw ('Update installer size mismatch: ' + $item.Length)
    }}

    $arguments = @(
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NOCANCEL',
        '/CLOSEAPPLICATIONS',
        '/RESTARTAPPLICATIONS'
    )
    if ($null -ne $installDir) {{
        $arguments += ('/DIR="' + $installDir + '"')
    }}
    $arguments += ('/LOG="' + $installerLog + '"')
    $arguments = $arguments -join ' '

    $launchStream = [System.IO.File]::Open(
        $installer,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $launchItem = Get-Item -LiteralPath $installer -Force
    if (($launchItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {{
        throw 'Update installer became a reparse point before launch'
    }}
    if ([Int64]$launchStream.Length -ne $expectedSize) {{
        throw ('Update installer size changed before launch: ' + $launchStream.Length)
    }}

    $launchStream.Position = 0
    $launchSha256 = (Get-FileHash -InputStream $launchStream -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($launchSha256 -ne $expectedSha256) {{
        throw 'Update installer SHA256 verification failed immediately before launch'
    }}
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    $exitCode = 0
    if ($null -ne $process) {{
        $exitCode = [int]$process.ExitCode
    }}

    if (($exitCode -eq 0) -or ($exitCode -eq 3010)) {{
        if (($null -ne $restartExe) -and (Test-Path -LiteralPath $restartExe -PathType Leaf)) {{
            Start-Process -FilePath $restartExe -WorkingDirectory (Split-Path -Parent $restartExe)
        }}
    }}
}} finally {{
    if ($null -ne $launchStream) {{
        $launchStream.Dispose()
    }}
    try {{
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    }} catch {{
    }}
}}
"""
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            descriptor = None
            handle.write(script)
            handle.flush()
            os.fsync(handle.fileno())
        return helper_path
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if helper_path is not None:
            try:
                helper_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove incomplete update helper", exc_info=True)
        raise


def _launch_installer(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int | None,
    installer_signature: str,
    signature_algorithm: str,
    signature_key_id: str,
    trusted_public_keys=None,
) -> None:
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

    if os.name == "nt":
        restart_exe = _restart_executable()
        if restart_exe is not None and not restart_exe.is_file():
            restart_exe = None
        powershell_path = windows_powershell_executable()
        helper_path = _write_windows_update_helper(
            path,
            restart_exe,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        try:
            subprocess.Popen(
                [
                    str(powershell_path),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(helper_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
            )
        except Exception:
            try:
                helper_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove unlaunched update helper", exc_info=True)
            raise
        return

    subprocess.Popen([str(path), "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])


def _display_version(version: str) -> str:
    text = str(version or "").strip()
    if not text:
        return "v?"
    return text if text.lower().startswith("v") else f"v{text}"


class _UpdateBridge(QObject):
    progress = Signal(int, int)
    complete = Signal(Path)
    error = Signal(str)


class UpdateWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        update_info: UpdateInfo,
        ui_lang: str,
    ) -> None:
        super().__init__(parent)
        self._info = update_info
        self._version = update_info.version
        self._download_url = update_info.download_url
        self._expected_size = update_info.size_bytes
        self._expected_sha256 = update_info.sha256.lower()
        self._installer_signature = update_info.installer_signature
        self._signature_algorithm = update_info.installer_signature_algorithm
        self._signature_key_id = update_info.installer_signature_key_id
        self._repair_mode = str(getattr(update_info, "flow", "update") or "update").casefold() == "repair"
        self._ui_lang = ui_lang
        self._downloading = False
        self._download_done = False
        self._minimized = False
        self._destroying = False
        self._installer_path: Path | None = None
        self._download_thread: threading.Thread | None = None
        self._bridge = _UpdateBridge(self)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.complete.connect(self._on_download_complete)
        self._bridge.error.connect(self._on_download_error)

        filename = _installer_filename(update_info)
        download_dir = app_temp_dir()
        self._final_path = download_dir / filename
        self._wip_path = download_dir / (filename + ".tmp")
        if self._wip_path.exists():
            try:
                self._wip_path.unlink()
            except Exception:
                pass

        self.setWindowTitle(self._dialog_title())
        self.setFixedSize(440, 360)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._build()
        self._apply_style()

    def _t(self, key: str, **kwargs) -> str:  # type: ignore[assignment]
        if kwargs is None:
            kwargs = {}
        return tr(self._ui_lang, key, **kwargs)

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
        return self._t("xtts_runtime_repair_title") if self._repair_mode else self._t("update_title")

    def _download_button_text(self) -> str:
        return self._t("xtts_runtime_download_installer") if self._repair_mode else self._t("update_now")

    def _ready_description(self) -> str:
        return self._t("xtts_runtime_repair_ready") if self._repair_mode else self._t("update_ready_desc")

    def _install_note(self) -> str:
        return self._t("xtts_runtime_repair_install_note") if self._repair_mode else self._t("update_install_note")

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(QLabel(self._dialog_title()))
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

    def _switch_to_ready(self) -> None:
        self._notes_label.hide()
        self._progress_label.setText(self._ready_description())
        self._progress_label.setObjectName("successLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._sub_label.setText(self._install_note())
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_install_later"))
        _set_button_action(self._btn_secondary, self._on_window_close)
        self._btn_primary.setText(self._t("update_install_now"))
        self._btn_primary.setEnabled(True)
        _set_button_action(self._btn_primary, self._run_installer)

    def _switch_to_error(self, message: str) -> None:
        self._progress_label.setText(self._t("update_error"))
        self._progress_label.setObjectName("errorLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._sub_label.setText(message)
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_close"))
        _set_button_action(self._btn_secondary, self.close)
        self._btn_primary.setText(self._t("update_retry"))
        self._btn_primary.setEnabled(True)
        _set_button_action(self._btn_primary, self._start_download)

    def _on_ignore_version(self) -> None:
        if self._repair_mode:
            self.close()
            return
        master = self.parent()
        if master is not None and hasattr(master, "_ignore_update_version"):
            master._ignore_update_version(self._version)
        self.close()

    def _start_download(self) -> None:
        if self._downloading and self._download_thread and self._download_thread.is_alive():
            return
        try:
            normalize_trusted_public_keys(TRUSTED_INSTALLER_PUBLIC_KEYS)
        except InstallerSignatureError as exc:
            self._switch_to_error(str(exc))
            return
        if self._final_path.exists() and self._expected_sha256:
            try:
                verify_installer(
                    self._final_path,
                    **self._installer_verification_kwargs(),
                )
                self._installer_path = self._final_path
                self._download_done = True
                self._switch_to_ready()
                return
            except InstallerVerificationError:
                logger.warning("Existing update installer failed verification", exc_info=True)
                try:
                    self._final_path.unlink()
                except OSError:
                    logger.debug("Failed to remove invalid update installer", exc_info=True)
        self._downloading = True
        self._switch_to_downloading()
        self._download_thread = threading.Thread(target=self._download_worker, daemon=True)
        self._download_thread.start()

    def _download_worker(self) -> None:
        promoted = False
        try:
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
                self._installer_path = self._final_path
                self._bridge.complete.emit(self._final_path)
        except Exception as exc:
            for candidate in (self._wip_path, self._final_path if promoted else None):
                if candidate is None:
                    continue
                try:
                    if candidate.exists():
                        candidate.unlink()
                except Exception:
                    logger.debug("Failed to remove rejected update download", exc_info=True)
            self._bridge.error.emit(str(exc))

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            if self._progress_bar.minimum() != 0 or self._progress_bar.maximum() != 100:
                self._progress_bar.setRange(0, 100)
            ratio = downloaded / total
            dl_mb = downloaded / 1_048_576
            total_mb = total / 1_048_576
            pct = int(ratio * 100)
            self._progress_bar.setValue(pct)
            self._sub_label.setText(
                self._t("update_progress_mb", downloaded=f"{dl_mb:.1f}", total=f"{total_mb:.1f}", pct=pct)
            )
        else:
            if self._progress_bar.minimum() != 0 or self._progress_bar.maximum() != 0:
                self._progress_bar.setRange(0, 0)
            dl_mb = downloaded / 1_048_576
            self._sub_label.setText(self._t("update_progress_mb_unknown", downloaded=f"{dl_mb:.1f}"))

    def _on_download_complete(self, path: Path) -> None:  # type: ignore[override]
        self._downloading = False
        self._download_done = True
        if self._minimized:
            self._minimized = False
            self.show()
            self.raise_()
        self._switch_to_ready()

    def _on_download_error(self, message: str) -> None:
        self._downloading = False
        self._switch_to_error(message)

    def _minimize_to_background(self) -> None:
        self._minimized = True
        self.hide()

    def _run_installer(self) -> None:
        if self._installer_path is None or not self._installer_path.exists():
            self._switch_to_error(self._t("update_installer_missing"))
            return
        self._btn_primary.setEnabled(False)
        self._btn_primary.setText(self._t("update_launching_installer"))
        self._sub_label.setText(self._t("update_launching_note"))
        try:
            _launch_installer(
                self._installer_path,
                **self._installer_verification_kwargs(),
            )
        except Exception as exc:
            self._switch_to_error(self._t("update_installer_launch_failed", message=exc))
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
