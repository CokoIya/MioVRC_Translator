# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.asr.hf_model_downloader import (
    DownloadProgress,
    DownloadState,
    get_downloader,
)
from src.asr.model_manager import download_model, model_exists
from src.asr.model_registry import ASR_ENGINE_SPECS, get_asr_engine_spec
from src.utils.i18n import tr
from src.utils.localization import (
    format_locale_number,
    format_locale_percent,
    normalize_ui_language,
)

logger = logging.getLogger(__name__)


def _dialog_palette_styles(extra: str = "") -> str:
    """Return a theme-following sheet for the standalone helper dialogs.

    These windows used to hardcode an Apple-style light palette, so on the dark
    theme they opened as a white slab in the middle of a dark application.
    """

    from src.ui_qt.styles import build_app_stylesheet
    from src.ui_qt.theme import theme_from_config, theme_tokens

    try:
        from src.utils import config_manager

        theme = theme_from_config(config_manager.load_config())
    except Exception:
        theme = "dark"
    c = theme_tokens(theme)
    return build_app_stylesheet(theme) + f"""
    QDialog {{
        background: {c["APP_BG"]};
    }}
    QLabel {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 14px;
    }}
    #accentLabel {{
        color: {c["ACCENT"]};
        font-weight: 700;
    }}
    #successLabel {{
        color: {c["SUCCESS"]};
        font-size: 13px;
    }}
    #warningLabel {{
        color: {c["WARNING"]};
        font-weight: 700;
        font-size: 16px;
        background: transparent;
        border: 0;
    }}
    #setupHeader {{
        font-weight: 700;
        font-size: 16px;
    }}
    #progressStatus {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
    }}
    #pctLabel {{
        color: {c["TEXT_PRIMARY"]};
        font-weight: 700;
        font-size: 13px;
    }}
    #speedLabel {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
    }}
    QProgressBar {{
        border: none;
        background: {c["PANEL_BORDER"]};
        border-radius: 6px;
        height: 8px;
    }}
    QProgressBar::chunk {{
        background: {c["ACCENT"]};
        border-radius: 6px;
    }}
    """ + extra


def _safe_disconnect(signal: QObject, slot: QObject) -> None:
    """Disconnect a Qt signal without warning if it was already disconnected."""
    try:
        signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass

# ── Shared localization helpers ──────────────────────────────────────────────

_ENGINE_TEXT_KEYS = {
    "sensevoice-small": (
        "model_download_sensevoice_description",
        "model_download_sensevoice_size",
    ),
    "whisper-large-v3-turbo": (
        "model_download_whisper_description",
        "model_download_whisper_size",
    ),
}


def _resolve_ui_language(parent: QWidget | None, explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return normalize_ui_language(explicit)
    for attribute in ("_ui_lang", "_ui_language"):
        value = str(getattr(parent, attribute, "") or "").strip()
        if value:
            return normalize_ui_language(value)
    try:
        from src.utils import config_manager

        configured = str(
            config_manager.load_config().get("ui", {}).get("language", "") or "en"
        ).strip()
        return normalize_ui_language(configured or "en")
    except Exception:
        return "en"


def _format_number(language: str, value: float, decimals: int) -> str:
    return format_locale_number(value, language, decimals=decimals)


def _engine_text(language: str, engine: str, index: int) -> str:
    keys = _ENGINE_TEXT_KEYS.get(engine)
    return tr(language, keys[index]) if keys else ""


def _model_id_for_engine(engine: str) -> str:
    spec = ASR_ENGINE_SPECS.get(engine)
    return spec.model_id if spec else ""


class _ProgressBridge(QObject):
    progress = Signal(object)


class _SetupBridge(QObject):
    progress = Signal(dict)
    completed = Signal()
    failed = Signal(str)


# ── Shared progress widget ───────────────────────────────────────────────────


class DownloadProgressWidget(QFrame):
    def __init__(
        self,
        master: QWidget,
        engine: str,
        downloader=None,
        model_id: str | None = None,
        on_completed: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        compact: bool = False,
        ui_lang: str | None = None,
        use_hf_downloader: bool = True,
    ) -> None:
        super().__init__(master)
        self._ui_lang = _resolve_ui_language(master, ui_lang)
        self._engine = engine
        self._model_id = model_id if model_id is not None else _model_id_for_engine(engine)
        self._on_completed = on_completed
        self._on_cancelled = on_cancelled
        self._compact = compact
        self._completed_notified = False
        self._downloader = downloader if downloader is not None else (
            get_downloader(self._model_id) if self._model_id else None
        ) if use_hf_downloader else None
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._apply_progress)
        self._build()
        if self._downloader:
            self._downloader.add_listener(self._on_progress)
            self._on_progress(self._downloader.progress)

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _format_percent(self, fraction: float) -> str:
        value = max(0, min(100, int(float(fraction) * 100)))
        return format_locale_percent(value, self._ui_lang)

    def _format_gb(self, byte_count: int) -> str:
        return self._t(
            "model_download_amount_gb",
            value=_format_number(self._ui_lang, byte_count / 1_073_741_824, 2),
        )

    def _format_speed_eta(self, progress: DownloadProgress) -> str:
        speed = ""
        if progress.speed_bps > 0:
            speed_mb = progress.speed_bps / 1_048_576
            if speed_mb >= 0.1:
                speed = self._t(
                    "model_download_speed_mb",
                    value=_format_number(self._ui_lang, speed_mb, 1),
                )
            else:
                speed = self._t(
                    "model_download_speed_kb",
                    value=_format_number(self._ui_lang, progress.speed_bps / 1024, 0),
                )

        eta = ""
        remaining = max(0, int(progress.eta_s))
        if remaining:
            minutes, seconds = divmod(remaining, 60)
            if minutes:
                eta = self._t(
                    "model_download_eta_minutes_seconds",
                    minutes=format_locale_number(minutes, self._ui_lang),
                    seconds=format_locale_number(seconds, self._ui_lang),
                )
            else:
                eta = self._t(
                    "model_download_eta_seconds",
                    seconds=format_locale_number(seconds, self._ui_lang),
                )
        if speed and eta:
            return self._t("model_download_speed_eta", speed=speed, eta=eta)
        return speed or eta

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        if not self._compact:
            self._status_label = QLabel("")
            self._status_label.setObjectName("progressStatus")
            layout.addWidget(self._status_label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self._pct_label = QLabel(self._format_percent(0))
        self._pct_label.setObjectName("pctLabel")
        info_row.addWidget(self._pct_label)
        info_row.addStretch(1)
        self._speed_label = QLabel("")
        self._speed_label.setObjectName("speedLabel")
        info_row.addWidget(self._speed_label)
        layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._pause_btn = QPushButton(self._t("model_download_pause"))
        self._pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self._pause_btn)
        self._stop_btn = QPushButton(self._t("model_download_cancel"))
        self._stop_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._stop_btn)
        self._retry_btn = QPushButton(self._t("model_download_retry"))
        self._retry_btn.clicked.connect(self._retry)
        self._retry_btn.hide()
        btn_row.addWidget(self._retry_btn)
        btn_row.addStretch(1)
        if self._downloader is not None:
            layout.addLayout(btn_row)

        self._apply_style()

    def _toggle_pause(self) -> None:
        if not self._downloader:
            return
        if self._downloader.state == DownloadState.PAUSED:
            self._downloader.resume()
        else:
            self._downloader.pause()

    def _cancel(self) -> None:
        if self._downloader:
            self._downloader.cancel()
        if self._on_cancelled:
            self._on_cancelled()

    def _retry(self) -> None:
        if not self._downloader:
            return
        self._completed_notified = False
        self._downloader.start()

    def _on_progress(self, p: DownloadProgress) -> None:
        self._bridge.progress.emit(p)

    def _apply_progress(self, p: DownloadProgress) -> None:
        frac = p.overall_fraction
        self._bar.setValue(int(frac * 100))
        self._pct_label.setText(self._format_percent(frac))

        show_retry = p.state in (DownloadState.ERROR, DownloadState.CANCELLED)
        self._pause_btn.setVisible(not show_retry)
        self._stop_btn.setVisible(not show_retry)
        self._retry_btn.setVisible(show_retry)

        if p.state == DownloadState.PAUSED:
            self._speed_label.setText(self._t("model_download_status_paused"))
            self._pause_btn.setText(self._t("model_download_resume"))
        elif p.state == DownloadState.DOWNLOADING:
            self._speed_label.setText(self._format_speed_eta(p))
            self._pause_btn.setText(self._t("model_download_pause"))
        elif p.state == DownloadState.COMPLETED:
            self._speed_label.setText(self._t("model_download_status_complete"))
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            if self._on_completed and not self._completed_notified:
                self._completed_notified = True
                self._on_completed()
            return
        elif p.state == DownloadState.ERROR:
            if p.error:
                logger.warning("Model download failed: %s", p.error)
            self._speed_label.setText(self._t("model_download_status_error"))
        elif p.state == DownloadState.CANCELLED:
            self._speed_label.setText(self._t("model_download_status_cancelled"))

        if not self._compact and hasattr(self, "_status_label"):
            if p.state == DownloadState.DOWNLOADING and p.file_name:
                total_text = self._format_gb(p.total_total) if p.total_total else "?"
                self._status_label.setText(
                    self._t(
                        "model_download_progress_file",
                        file=p.file_name,
                        downloaded=self._format_gb(p.total_bytes),
                        total=total_text,
                    )
                )
            elif p.state == DownloadState.PAUSED:
                self._status_label.setText(self._t("model_download_hint_paused"))
            elif p.state == DownloadState.ERROR:
                self._status_label.setText(self._t("model_download_hint_error"))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            _dialog_palette_styles(
                """
    QPushButton {
        border-radius: 10px;
        padding: 6px 12px;
        font-weight: 600;
    }
    """
            )
        )


# ── "No model" prompt dialog ─────────────────────────────────────────────────


class ModelMissingDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        engine: str,
        on_download_click: Callable[[], None] | None = None,
        is_model_ready: Callable[[], bool] | None = None,
        is_download_running: Callable[[], bool] | None = None,
        ui_lang: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._ui_lang = _resolve_ui_language(parent, ui_lang)
        self._engine = engine
        self._model_id = _model_id_for_engine(engine)
        self._result = "skip"
        self._close_scheduled = False
        self._watching_external_download = False
        self._on_download_click = on_download_click
        self._is_model_ready = is_model_ready
        self._is_download_running = is_download_running

        label = get_asr_engine_spec(engine).label
        size = _engine_text(self._ui_lang, engine, 1)

        self.setWindowTitle(self._t("model_download_dialog_title"))
        self.setMinimumSize(460, 380)
        self.resize(520, 420)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._build_prompt(label, size)
        self._apply_style()

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _build_prompt(self, label: str, size: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        header = QLabel(self._t("model_download_dialog_header"))
        header.setObjectName("warningLabel")
        header.setWordWrap(True)
        root.addWidget(header)

        desc = _engine_text(self._ui_lang, self._engine, 0)
        body = (
            self._t("model_download_engine_summary", label=label, size=size) + "\n" +
            (desc + "\n\n" if desc else "") +
            self._t("model_download_missing_body")
        )
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        root.addWidget(body_label)

        one_time = QLabel(self._t("model_download_one_time"))
        one_time.setObjectName("accentLabel")
        one_time.setWordWrap(True)
        root.addWidget(one_time)

        privacy = QLabel(self._t("model_download_privacy"))
        privacy.setObjectName("successLabel")
        privacy.setWordWrap(True)
        root.addWidget(privacy)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._download_btn = QPushButton(self._t("model_download_now"))
        self._download_btn.setObjectName("primaryButton")
        self._download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self._download_btn)
        self._skip_btn = QPushButton(self._t("model_download_skip"))
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)
        root.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        root.addWidget(self._status_label)

    def _start_download(self) -> None:
        self._result = "download"
        if self._on_download_click is not None:
            self._download_btn.setEnabled(False)
            self._download_btn.setText(self._t("model_downloading_button"))
            self._skip_btn.setEnabled(False)
            self._on_download_click()
            self._status_label.setText(
                self._t("model_download_hint_setup", label=self._engine_label())
            )
            self._status_label.show()
            self._watching_external_download = True

            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, self._watch_external_download)

    def _watch_external_download(self) -> None:
        if not self._watching_external_download:
            return
        try:
            if self._is_model_ready and self._is_model_ready():
                self._on_download_completed()
                return
            if self._is_download_running and not self._is_download_running():
                self._on_external_download_stopped()
                return
            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, self._watch_external_download)
        except Exception:
            pass

    def _on_external_download_stopped(self) -> None:
        self._watching_external_download = False
        self._result = "failed"
        self._download_btn.setText(self._t("model_download_retry"))
        self._download_btn.setEnabled(True)
        _safe_disconnect(self._download_btn.clicked, self._start_download)
        self._download_btn.clicked.connect(self._start_download)
        self._skip_btn.setText(self._t("model_download_close"))
        self._skip_btn.setEnabled(True)
        _safe_disconnect(self._skip_btn.clicked, self.close)
        self._skip_btn.clicked.connect(self.close)

    def _on_download_completed(self) -> None:
        if self._close_scheduled:
            return
        self._result = "completed"
        self._close_scheduled = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1200, self.close)

    def _skip(self) -> None:
        self._result = "skip"
        self.close()

    @property
    def result(self) -> str:
        return self._result

    def _engine_label(self) -> str:
        return get_asr_engine_spec(self._engine).label

    def _apply_style(self) -> None:
        self.setStyleSheet(
            _dialog_palette_styles(
                """
    QPushButton {
        border-radius: 10px;
        padding: 9px 16px;
        font-size: 14px;
        font-weight: 600;
    }
    """
            )
        )


# ── Standalone setup window (--setup CLI flag) ───────────────────────────────


class SetupWindow(QDialog):
    def __init__(self, engine: str, ui_lang: str | None = None) -> None:
        super().__init__()
        self._ui_lang = _resolve_ui_language(None, ui_lang)
        self._engine = engine
        self._model_id = _model_id_for_engine(engine)
        self._runtime_spec = get_asr_engine_spec(engine)
        self._destroying = False
        self._close_scheduled = False
        self._download_thread: threading.Thread | None = None
        self._exit_code = 0
        self._bridge = _SetupBridge(self)
        self._bridge.progress.connect(self._apply_modelscope_progress)
        self._bridge.completed.connect(self._on_completed)
        self._bridge.failed.connect(self._on_failed)

        label = self._runtime_spec.label or engine.replace("-", " ").title()
        self._engine_label = self._runtime_spec.label or label
        size = _engine_text(self._ui_lang, engine, 1)

        self.setWindowTitle(self._t("model_download_setup_title"))
        self.setMinimumSize(480, 430)
        self.resize(540, 470)
        self._build(self._engine_label, size)
        self._center()

        from PySide6.QtCore import QTimer
        if not self._runtime_spec.requires_local_model:
            QTimer.singleShot(300, self._already_complete)
        elif model_exists(self._runtime_spec):
            QTimer.singleShot(300, self._already_complete)
        else:
            QTimer.singleShot(300, self._auto_start)

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _format_percent(self, value: float) -> str:
        return format_locale_percent(max(0, min(100, int(value))), self._ui_lang)

    def _format_gb(self, byte_count: int) -> str:
        return self._t(
            "model_download_amount_gb",
            value=_format_number(self._ui_lang, byte_count / 1_073_741_824, 2),
        )

    def _center(self) -> None:
        geo = self.frameGeometry()
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo.moveCenter(screen.availableGeometry().center())
        self.move(geo.topLeft())

    def _build(self, label: str, size: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        header = QLabel(self._t("model_download_setup_header"))
        header.setObjectName("setupHeader")
        header.setWordWrap(True)
        root.addWidget(header)

        desc = _engine_text(self._ui_lang, self._engine, 0)
        info_text = self._t(
            "model_download_engine_info",
            label=label,
            size=size,
            description=desc,
        ).rstrip()
        info = QLabel(info_text)
        info.setWordWrap(True)
        root.addWidget(info)

        one_time = QLabel(self._t("model_download_one_time"))
        one_time.setObjectName("accentLabel")
        one_time.setWordWrap(True)
        root.addWidget(one_time)

        privacy = QLabel(self._t("model_download_privacy"))
        privacy.setObjectName("successLabel")
        privacy.setWordWrap(True)
        root.addWidget(privacy)

        self._progress_widget = DownloadProgressWidget(
            self,
            self._engine,
            ui_lang=self._ui_lang,
            use_hf_downloader=False,
        )
        root.addWidget(self._progress_widget)

        self._bottom_label = QLabel(self._t("model_download_hint_ready"))
        self._bottom_label.setWordWrap(True)
        root.addWidget(self._bottom_label)
        self._retry_btn = QPushButton(self._t("model_download_retry"))
        self._retry_btn.clicked.connect(self._retry_download)
        self._retry_btn.hide()
        root.addWidget(self._retry_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._apply_style()

    def _auto_start(self) -> None:
        if self._download_thread is not None and self._download_thread.is_alive():
            return
        self._close_scheduled = False
        self._retry_btn.hide()
        self._progress_widget._bar.setValue(0)
        self._progress_widget._pct_label.setText(self._format_percent(0))
        self._progress_widget._speed_label.setText("")
        self._bottom_label.setText(
            self._t("model_download_hint_setup", label=self._engine_label)
        )
        self._download_thread = threading.Thread(
            target=self._download_runtime_model,
            daemon=True,
            name="setup-model-download",
        )
        self._download_thread.start()

    def _already_complete(self) -> None:
        self._bottom_label.setText(self._t("model_download_hint_existed"))
        self._schedule_close(1500)

    def _download_runtime_model(self) -> None:
        try:
            download_model(
                self._runtime_spec,
                progress_callback=lambda event: self._bridge.progress.emit(event),
            )
        except Exception as exc:
            self._bridge.failed.emit(str(exc))
            return
        self._bridge.completed.emit()

    def _apply_modelscope_progress(self, event: dict) -> None:
        if self._destroying:
            return
        stage = str(event.get("stage", "")).strip()
        progress_value = event.get("progress")
        progress = float(progress_value) if isinstance(progress_value, (int, float)) else None

        if stage == "download_complete":
            self._progress_widget._bar.setValue(100)
            self._progress_widget._pct_label.setText(self._format_percent(100))
            self._progress_widget._speed_label.setText(
                self._t("model_download_status_complete")
            )
            return

        if stage == "download_retry":
            attempt = event.get("attempt")
            maximum = event.get("max_attempts")
            self._progress_widget._speed_label.setText(
                self._t(
                    "model_download_retrying",
                    attempt=format_locale_number(
                        int(attempt) if isinstance(attempt, int) else 1,
                        self._ui_lang,
                    ),
                    maximum=format_locale_number(
                        int(maximum) if isinstance(maximum, int) else 1,
                        self._ui_lang,
                    ),
                )
            )
            return

        if stage in {"download_prepare", "download"}:
            if progress is not None:
                self._progress_widget._bar.setValue(int(progress * 100))
                self._progress_widget._pct_label.setText(
                    self._format_percent(progress * 100)
                )
            downloaded = event.get("downloaded_bytes")
            total = event.get("total_bytes")
            if isinstance(downloaded, int) and isinstance(total, int) and total > 0:
                self._progress_widget._speed_label.setText(
                    self._t(
                        "model_download_progress_file",
                        file="",
                        downloaded=self._format_gb(downloaded),
                        total=self._format_gb(total),
                    ).strip()
                )
            elif stage == "download_prepare":
                self._progress_widget._speed_label.setText(
                    self._t("model_download_preparing")
                )
            return

        message = str(event.get("message", "")).strip()
        if message:
            logger.debug("Unhandled model download progress message: %s", message)

    def _on_completed(self) -> None:
        if self._close_scheduled:
            return
        self._progress_widget._bar.setValue(100)
        self._bottom_label.setText(self._t("model_download_hint_done"))
        self._exit_code = 0
        self._schedule_close(1500)

    def _on_failed(self, message: str) -> None:
        if message:
            logger.error("Model setup download failed: %s", message)
        self._progress_widget._bar.setValue(0)
        self._bottom_label.setText(self._t("model_download_hint_error"))
        self._retry_btn.show()
        self._exit_code = 1

    def _retry_download(self) -> None:
        self._auto_start()

    def _schedule_close(self, delay_ms: int) -> None:
        if self._close_scheduled:
            return
        self._close_scheduled = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(delay_ms, self.close)

    def _apply_style(self) -> None:
        self.setStyleSheet(_dialog_palette_styles())


def run_setup_mode(engine: str) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    ui_lang = "en"
    try:
        from src.utils import config_manager
        ui_lang = str(
            config_manager.load_config().get("ui", {}).get("language", "") or ""
        ).strip() or "en"
    except Exception:
        pass

    dialog = SetupWindow(engine, ui_lang=ui_lang)
    dialog.exec()
    return dialog._exit_code
