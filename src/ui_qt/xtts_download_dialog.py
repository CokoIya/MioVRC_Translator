"""Voice Cloning model download dialog."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QVBoxLayout

from src.asr.hf_model_downloader import DownloadState
from src.tts.xtts_downloader import (
    XTTSDownloader,
    XTTS_V2_REPO,
    estimate_xtts_download_size,
    xtts_models_ready,
)
from src.ui_qt.model_download_dialog import DownloadProgressWidget
from src.utils.i18n import tr

logger = logging.getLogger(__name__)


class XTTSDownloadDialog(QDialog):
    """SBV2-style dialog for downloading the Voice Cloning model."""

    download_complete = Signal()

    def __init__(self, parent=None, ui_lang: str | None = None):
        super().__init__(parent)
        self._ui_lang = ui_lang or self._resolve_ui_lang(parent)
        self._downloader = XTTSDownloader()
        self._close_scheduled = False

        self.setWindowTitle(self._t("xtts_download_title"))
        self.setFixedSize(480, 430)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._build()
        if xtts_models_ready():
            QTimer.singleShot(300, self._already_complete)
        else:
            QTimer.singleShot(300, self._auto_start)

    @staticmethod
    def _resolve_ui_lang(parent) -> str:
        parent_lang = str(getattr(parent, "_ui_lang", "") or "").strip()
        if parent_lang:
            return parent_lang
        if parent is None:
            return "en"
        try:
            from src.utils import config_manager

            return str(
                config_manager.load_config().get("ui", {}).get("language", "") or "en"
            ).strip() or "en"
        except Exception:
            return "en"

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        header = QLabel(self._t("xtts_download_header"))
        header.setObjectName("setupHeader")
        root.addWidget(header)

        size_gb = estimate_xtts_download_size() / 1_073_741_824
        info = QLabel(self._t("xtts_download_info", size=size_gb))
        info.setWordWrap(True)
        root.addWidget(info)

        one_time = QLabel(self._t("xtts_download_once"))
        one_time.setObjectName("accentLabel")
        root.addWidget(one_time)

        privacy = QLabel(self._t("xtts_download_privacy"))
        privacy.setObjectName("successLabel")
        privacy.setWordWrap(True)
        root.addWidget(privacy)

        self._progress_widget = DownloadProgressWidget(
            self,
            "xtts",
            downloader=self._downloader,
            model_id=XTTS_V2_REPO,
            on_completed=self._on_completed,
            on_cancelled=self._on_cancelled,
            ui_lang=self._ui_lang,
        )
        root.addWidget(self._progress_widget)

        self._bottom_label = QLabel(self._t("xtts_download_preparing"))
        self._bottom_label.setWordWrap(True)
        root.addWidget(self._bottom_label)

        self._apply_style()

    def _auto_start(self) -> None:
        if self._close_scheduled:
            return
        if xtts_models_ready():
            self._already_complete()
            return
        try:
            self._bottom_label.setText(self._t("xtts_download_downloading"))
            self._downloader.start()
            logger.info("Started Voice Cloning model download")
        except Exception as exc:
            logger.error("Failed to start Voice Cloning model download: %s", exc)
            self._bottom_label.setText(self._t("xtts_download_failed"))
            QMessageBox.critical(
                self,
                self._t("xtts_download_error_title"),
                self._t("xtts_download_start_failed", error=exc),
            )

    def _already_complete(self) -> None:
        if self._close_scheduled:
            return
        self._progress_widget._bar.setValue(100)
        self._progress_widget._pct_label.setText("100%")
        self._progress_widget._speed_label.setText(self._t("xtts_download_complete"))
        self._progress_widget._pause_btn.setEnabled(False)
        self._progress_widget._stop_btn.setEnabled(False)
        self._progress_widget._retry_btn.hide()
        self._bottom_label.setText(self._t("xtts_download_ready_closing"))
        self._schedule_accept(1200)

    def _on_completed(self) -> None:
        if self._close_scheduled:
            return
        self._bottom_label.setText(self._t("xtts_download_complete_closing"))
        self.download_complete.emit()
        self._schedule_accept(1200)

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _on_cancelled(self) -> None:
        self.reject()

    def _schedule_accept(self, delay_ms: int) -> None:
        if self._close_scheduled:
            return
        self._close_scheduled = True
        QTimer.singleShot(delay_ms, self.accept)

    def closeEvent(self, event) -> None:
        if self._downloader.state in (DownloadState.DOWNLOADING, DownloadState.PAUSED):
            self._downloader.cancel()
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
        QDialog { background: #f5f5f7; }
        QLabel { color: #1d1d1f; font-size: 14px; }
        #setupHeader { font-weight: 700; font-size: 16px; }
        #accentLabel { color: #0071e3; font-weight: 700; }
        #successLabel { color: #34c759; font-size: 13px; }
        """)
