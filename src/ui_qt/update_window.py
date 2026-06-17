# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import warnings
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from src.updater.update_checker import UpdateInfo, is_trusted_download_url, update_notes_for_language
from src.utils.app_paths import app_temp_dir
from src.utils.i18n import tr

logger = logging.getLogger(__name__)


def _safe_disconnect(signal: QObject, slot: QObject) -> None:
    """Disconnect a Qt signal without warning if it was already disconnected."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass

_CHUNK = 65_536
_DOWNLOAD_TIMEOUT = (8, 60)
_DOWNLOAD_HEADERS = {
    "Accept": "application/octet-stream",
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


def _verify_windows_signature(path: Path) -> bool:
    from src.version import TRUSTED_INSTALLER_SIGNER_THUMBPRINTS, TRUSTED_INSTALLER_SIGNER_SUBJECTS

    trusted_thumbprints = {
        str(value or "").replace(" ", "").lower()
        for value in TRUSTED_INSTALLER_SIGNER_THUMBPRINTS
        if str(value or "").strip()
    }
    trusted_subjects = tuple(
        str(value or "").strip()
        for value in TRUSTED_INSTALLER_SIGNER_SUBJECTS
        if str(value or "").strip()
    )
    if not trusted_thumbprints and not trusted_subjects:
        logger.info("Installer Authenticode trust list is empty; using signed manifest SHA256 trust path")
        return False
    if os.name != "nt":
        return False
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "if ($sig.Status -ne 'Valid') { "
        "Write-Error ($sig.Status.ToString() + ': ' + $sig.StatusMessage); exit 1 }; "
        "$cert = $sig.SignerCertificate; "
        "if ($null -eq $cert) { Write-Error 'Signer certificate is missing'; exit 1 }; "
        "$payload = [pscustomobject]@{ Thumbprint = $cert.Thumbprint; Subject = $cert.Subject }; "
        "$payload | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        logger.info("PowerShell is unavailable; skipping optional installer Authenticode verification")
        return False
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.info("Optional installer Authenticode verification failed: %s", detail or "unknown error")
        return False
    try:
        payload = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError:
        logger.info("Optional installer Authenticode verification returned invalid metadata")
        return False
    thumbprint = str(payload.get("Thumbprint", "") or "").replace(" ", "").lower()
    subject = str(payload.get("Subject", "") or "")
    if trusted_thumbprints and thumbprint in trusted_thumbprints:
        return True
    subject_folded = subject.casefold()
    if trusted_subjects and any(item.casefold() in subject_folded for item in trusted_subjects):
        return True
    logger.info("Optional installer Authenticode signer is not trusted")
    return False


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            if chunk:
                hasher.update(chunk)
    return hasher.hexdigest().lower()


def _launch_installer(path: Path) -> None:
    args = "/CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS"
    if os.name == "nt" and hasattr(os, "startfile"):
        try:
            os.startfile(str(path), "open", args)  # type: ignore[attr-defined]
            return
        except TypeError:
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
    subprocess.Popen([str(path), "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS"])


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

        self.setWindowTitle(self._t("update_title"))
        self.setFixedSize(440, 360)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._build()
        self._apply_style()

    def _t(self, key: str, **kwargs) -> str:  # type: ignore[assignment]
        if kwargs is None:
            kwargs = {}
        return tr(self._ui_lang, key, **kwargs)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(QLabel(self._t("update_title")))
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
        self._btn_secondary = QPushButton(self._t("update_later"))
        self._btn_secondary.clicked.connect(self._on_window_close)
        btn_row.addWidget(self._btn_secondary)
        self._btn_primary = QPushButton(self._t("update_now"))
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
        _safe_disconnect(self._btn_secondary.clicked, self._minimize_to_background)
        self._btn_secondary.clicked.connect(self._minimize_to_background)
        self._btn_primary.setText(self._t("update_downloading_btn"))
        self._btn_primary.setEnabled(False)

    def _switch_to_ready(self) -> None:
        self._notes_label.hide()
        self._progress_label.setText(self._t("update_ready_desc"))
        self._progress_label.setObjectName("successLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._sub_label.setText(self._t("update_install_note"))
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_install_later"))
        _safe_disconnect(self._btn_secondary.clicked, self._on_window_close)
        self._btn_secondary.clicked.connect(self._on_window_close)
        self._btn_primary.setText(self._t("update_install_now"))
        self._btn_primary.setEnabled(True)
        _safe_disconnect(self._btn_primary.clicked, self._run_installer)
        self._btn_primary.clicked.connect(self._run_installer)

    def _switch_to_error(self, message: str) -> None:
        self._progress_label.setText(self._t("update_error"))
        self._progress_label.setObjectName("errorLabel")
        self._progress_label.style().unpolish(self._progress_label)
        self._progress_label.style().polish(self._progress_label)
        self._progress_bar.setRange(0, 100)
        self._sub_label.setText(message)
        self._btn_ignore.hide()
        self._btn_secondary.setText(self._t("update_close"))
        _safe_disconnect(self._btn_secondary.clicked, self.close)
        self._btn_secondary.clicked.connect(self.close)
        self._btn_primary.setText(self._t("update_retry"))
        self._btn_primary.setEnabled(True)
        _safe_disconnect(self._btn_primary.clicked, self._start_download)
        self._btn_primary.clicked.connect(self._start_download)

    def _on_ignore_version(self) -> None:
        master = self.parent()
        if master is not None and hasattr(master, "_ignore_update_version"):
            master._ignore_update_version(self._version)
        self.close()

    def _start_download(self) -> None:
        if self._downloading and self._download_thread and self._download_thread.is_alive():
            return
        if self._final_path.exists() and self._expected_sha256:
            try:
                if _sha256_file(self._final_path) == self._expected_sha256:
                    self._installer_path = self._final_path
                    self._download_done = True
                    self._switch_to_ready()
                    return
                self._final_path.unlink()
            except Exception:
                logger.debug("Failed to reuse existing update installer", exc_info=True)
        self._downloading = True
        self._switch_to_downloading()
        self._download_thread = threading.Thread(target=self._download_worker, daemon=True)
        self._download_thread.start()

    def _download_worker(self) -> None:
        try:
            if not self._expected_sha256:
                raise RuntimeError("Update manifest is missing SHA256 verification data")
            if not is_trusted_download_url(self._download_url):
                raise RuntimeError("Update download URL is not trusted")
            response = requests.get(
                self._download_url,
                stream=True,
                timeout=_DOWNLOAD_TIMEOUT,
                headers=_DOWNLOAD_HEADERS,
                allow_redirects=True,
            )
            try:
                response.raise_for_status()
                if not is_trusted_download_url(response.url, allow_release_asset_redirect=True):
                    raise RuntimeError("Update download redirected to an untrusted URL")
                total = int(response.headers.get("content-length", 0))
                if self._expected_size is not None and total > 0 and total != self._expected_size:
                    logger.warning(
                        "Update content length differs from manifest (manifest=%s, server=%s); relying on SHA256",
                        self._expected_size,
                        total,
                    )

                hasher = hashlib.sha256()
                downloaded = 0
                with open(self._wip_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        self._bridge.progress.emit(downloaded, total or self._expected_size or 0)

                if self._expected_size is not None and downloaded != self._expected_size:
                    logger.warning(
                        "Update downloaded size differs from manifest (manifest=%s, downloaded=%s); relying on SHA256",
                        self._expected_size,
                        downloaded,
                    )
                if hasher.hexdigest().lower() != self._expected_sha256:
                    raise RuntimeError("Update download checksum verification failed")
                _verify_windows_signature(self._wip_path)
                self._wip_path.replace(self._final_path)
                self._installer_path = self._final_path
                self._bridge.complete.emit(self._final_path)
            finally:
                response.close()
        except Exception as exc:
            try:
                if self._wip_path.exists():
                    self._wip_path.unlink()
            except Exception:
                pass
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
            return
        try:
            _launch_installer(self._installer_path)
        except Exception as exc:
            self._switch_to_error(str(exc))
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
