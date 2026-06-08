from __future__ import annotations

import logging
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
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

logger = logging.getLogger(__name__)


def _safe_disconnect(signal: QObject, slot: QObject) -> None:
    """Disconnect a Qt signal without warning if it was already disconnected."""
    try:
        signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass

# ── Multilingual strings ─────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "dlg_title":    "模型未找到",
        "dlg_header":   "模型未找到",
        "engine_fmt":   "当前听写方式：{label}（{size}）",
        "body":         "缺少模型，Mio 暂时听不懂你说话，也不能把语音翻译出去。\n建议现在下载。下载完成前仍可使用打字翻译。",
        "privacy":      "下载到本机使用，你的声音不会上传到服务器",
        "onetime":      "这个模型只需要下载一次，更新 Mio 时通常不用重新下载。",
        "btn_download": "立即下载",
        "btn_downloading": "正在下载",
        "btn_skip":     "暂时跳过",
        "btn_pause":    "暂停",
        "btn_resume":   "继续",
        "btn_cancel":   "取消",
        "btn_retry":    "重新下载",
        "btn_skip2":    "跳过，稍后下载",
        "st_paused":    "已暂停",
        "st_done":      "下载完成",
        "st_cancelled": "已取消",
        "st_error_pfx": "下载失败: ",
        "hint_paused":  "下载已暂停，点击继续恢复",
        "hint_error":   "下载失败: ",
        "hint_ready":   "下载完成后窗口将自动关闭",
        "hint_done":    "安装完成，即将关闭…",
        "hint_existed": "模型已准备好，即将关闭…",
        "hint_model_setup": "正在下载 {label} 模型，下载完成后窗口将自动关闭。",
        "hint_retry":   "下载失败，请检查网络后重试。已经下载的缓存会保留，重新下载会尽量继续。",
        "btn_close":    "关闭",
        "setup_title":  "Mio RealTime Translator — 初始设置",
        "setup_header": "正在准备模型",
        "engine_desc": {
            "sensevoice-small":      "适合中文、粤语，下载后可离线把声音变成文字。",
            "whisper-large-v3-turbo": "适合英语听译的本地 Whisper Small，速度更快。",
        },
        "engine_size": {
            "sensevoice-small":      "约 950 MB",
            "whisper-large-v3-turbo": "约 461 MB",
        },
    },
    "ja": {
        "dlg_title":    "音声認識モデルが見つかりません",
        "dlg_header":   "音声認識モデルが見つかりません",
        "engine_fmt":   "エンジン：{label}（{size}）",
        "body":         "モデルファイルが見つかりません。音声認識・翻訳機能が使用できません。\n今すぐダウンロードすることをお勧めします。ダウンロード中もテキスト入力は使用できます。",
        "privacy":      "完全ローカル処理 — 音声データはどこにも送信されません",
        "onetime":      "モデルのダウンロードは初回のみ。アップデート後の再ダウンロードは不要です。",
        "btn_download": "今すぐダウンロード",
        "btn_downloading": "ダウンロード中",
        "btn_skip":     "後でダウンロード",
        "btn_pause":    "一時停止",
        "btn_resume":   "再開",
        "btn_cancel":   "キャンセル",
        "btn_retry":    "再ダウンロード",
        "btn_skip2":    "スキップ（後でダウンロード）",
        "st_paused":    "一時停止中",
        "st_done":      "ダウンロード完了",
        "st_cancelled": "キャンセルしました",
        "st_error_pfx": "エラー: ",
        "hint_paused":  "一時停止中。「再開」をクリックして続行。",
        "hint_error":   "ダウンロード失敗: ",
        "hint_ready":   "ダウンロード完了後、ウィンドウは自動的に閉じます",
        "hint_done":    "インストール完了。まもなく閉じます…",
        "hint_existed": "モデルは準備済みです。まもなく閉じます…",
        "hint_model_setup": "{label} モデルをダウンロードしています。完了すると自動的に閉じます。",
        "hint_retry":   "ダウンロードに失敗しました。ネットワークを確認して再試行してください。既存のキャッシュは保持され、可能な限り続きから再開します。",
        "btn_close":    "閉じる",
        "setup_title":  "Mio RealTime Translator — 初期設定",
        "setup_header": "音声認識モデルをインストール中",
        "engine_desc": {
            "sensevoice-small":      "中国語・広東語向け、高精度オフライン音声認識モデル。",
            "whisper-large-v3-turbo": "英語リスニング向けの高速なローカル Whisper Small モデル。",
        },
        "engine_size": {
            "sensevoice-small":      "約 950 MB",
            "whisper-large-v3-turbo": "約 461 MB",
        },
    },
    "en": {
        "dlg_title":    "Speech Model Not Found",
        "dlg_header":   "Speech Recognition Model Not Found",
        "engine_fmt":   "Engine: {label} ({size})",
        "body":         "No model file found. Speech recognition and translation will not work.\nDownloading now is recommended. Text input remains available.",
        "privacy":      "Fully local — your voice is never uploaded anywhere",
        "onetime":      "The model only needs to be downloaded once. Updates will not require re-downloading.",
        "btn_download": "Download Now",
        "btn_downloading": "Downloading",
        "btn_skip":     "Skip for Now",
        "btn_pause":    "Pause",
        "btn_resume":    "Resume",
        "btn_cancel":   "Cancel",
        "btn_retry":    "Retry Download",
        "btn_skip2":    "Skip, Download Later",
        "st_paused":    "Paused",
        "st_done":      "Download Complete",
        "st_cancelled": "Cancelled",
        "st_error_pfx": "Error: ",
        "hint_paused":  "Download paused. Click Resume to continue.",
        "hint_error":   "Download failed: ",
        "hint_ready":   "Window will close automatically when download completes",
        "hint_done":    "Setup complete. Closing…",
        "hint_existed": "Model is ready. Closing…",
        "hint_model_setup": "Downloading the {label} model. This window will close automatically when it finishes.",
        "hint_retry":   "Download failed. Check the network and retry. Existing cache is kept and the download will resume when possible.",
        "btn_close":    "Close",
        "setup_title":  "Mio RealTime Translator — Setup",
        "setup_header": "Installing Speech Recognition Model",
        "engine_desc": {
            "sensevoice-small":      "High-accuracy offline speech recognition model for Chinese and Cantonese.",
            "whisper-large-v3-turbo": "Fast local Whisper Small ASR model for English listening.",
        },
        "engine_size": {
            "sensevoice-small":      "~950 MB",
            "whisper-large-v3-turbo": "~461 MB",
        },
    },
}

_STRINGS["zh-cn"] = _STRINGS["zh"]
_STRINGS["zh-tw"] = _STRINGS["zh"]
_STRINGS["zh-hk"] = _STRINGS["zh"]
_STRINGS["yue"] = _STRINGS["zh"]
_STRINGS["ko"] = _STRINGS.get("zh", _STRINGS["en"])
_STRINGS["ru"] = _STRINGS.get("zh", _STRINGS["en"])


_dialog_lang: str = ""


def _set_dialog_lang(lang: str) -> None:
    global _dialog_lang
    _dialog_lang = lang.lower().split("-")[0] if lang else ""


def _effective_lang() -> str:
    return _dialog_lang or "en"


def _s(key: str, **fmt) -> str:
    lang = _effective_lang()
    table = _STRINGS.get(lang) or _STRINGS.get(lang.split("-")[0]) or _STRINGS["en"]
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**fmt) if fmt else text


def _s_engine(sub: str, engine: str) -> str:
    lang = _effective_lang()
    table = _STRINGS.get(lang) or _STRINGS.get(lang.split("-")[0]) or _STRINGS["en"]
    en_table = _STRINGS["en"]
    return (table.get(sub) or en_table.get(sub, {})).get(engine, "")


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
        if ui_lang:
            _set_dialog_lang(ui_lang)
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
        self._pct_label = QLabel("0%")
        self._pct_label.setObjectName("pctLabel")
        info_row.addWidget(self._pct_label)
        info_row.addStretch(1)
        self._speed_label = QLabel("")
        self._speed_label.setObjectName("speedLabel")
        info_row.addWidget(self._speed_label)
        layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._pause_btn = QPushButton(_s("btn_pause"))
        self._pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self._pause_btn)
        self._stop_btn = QPushButton(_s("btn_cancel"))
        self._stop_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._stop_btn)
        self._retry_btn = QPushButton(_s("btn_retry"))
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
        self._pct_label.setText(f"{int(frac * 100)}%")

        show_retry = p.state in (DownloadState.ERROR, DownloadState.CANCELLED)
        self._pause_btn.setVisible(not show_retry)
        self._stop_btn.setVisible(not show_retry)
        self._retry_btn.setVisible(show_retry)

        if p.state == DownloadState.PAUSED:
            self._speed_label.setText(_s("st_paused"))
            self._pause_btn.setText(_s("btn_resume"))
        elif p.state == DownloadState.DOWNLOADING:
            speed_parts = []
            if p.speed_bps > 0:
                speed_parts.append(p.speed_mb)
            if p.eta_str:
                speed_parts.append(p.eta_str)
            self._speed_label.setText("  ".join(speed_parts))
            self._pause_btn.setText(_s("btn_pause"))
        elif p.state == DownloadState.COMPLETED:
            self._speed_label.setText(_s("st_done"))
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            if self._on_completed and not self._completed_notified:
                self._completed_notified = True
                self._on_completed()
            return
        elif p.state == DownloadState.ERROR:
            self._speed_label.setText(_s("st_error_pfx") + p.error[:50])
        elif p.state == DownloadState.CANCELLED:
            self._speed_label.setText(_s("st_cancelled"))

        if not self._compact and hasattr(self, "_status_label"):
            if p.state == DownloadState.DOWNLOADING and p.file_name:
                done_gb = p.total_bytes / 1_073_741_824
                total_gb = p.total_total / 1_073_741_824 if p.total_total else 0
                total_str = f"{total_gb:.2f} GB" if total_gb else "?"
                self._status_label.setText(f"{p.file_name}  {done_gb:.2f} GB / {total_str}")
            elif p.state == DownloadState.PAUSED:
                self._status_label.setText(_s("hint_paused"))
            elif p.state == DownloadState.ERROR:
                self._status_label.setText(_s("hint_error") + p.error)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
        QLabel { color: #6e6e73; }
        #progressStatus { font-size: 13px; }
        #pctLabel { color: #1d1d1f; font-weight: 700; font-size: 13px; }
        #speedLabel { color: #6e6e73; font-size: 13px; }
        QProgressBar {
            border: none;
            background: #e0e4ea;
            border-radius: 6px;
            height: 8px;
        }
        QProgressBar::chunk { background: #0071e3; border-radius: 6px; }
        QPushButton {
            background: #eef1f5;
            border: 1px solid #e4e7ed;
            border-radius: 10px;
            color: #1d1d1f;
            padding: 6px 12px;
            font-weight: 600;
        }
        QPushButton:hover { background: #e0e4ea; }
        """)


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
        if ui_lang:
            _set_dialog_lang(ui_lang)
        self._engine = engine
        self._model_id = _model_id_for_engine(engine)
        self._result = "skip"
        self._close_scheduled = False
        self._watching_external_download = False
        self._on_download_click = on_download_click
        self._is_model_ready = is_model_ready
        self._is_download_running = is_download_running

        label = get_asr_engine_spec(engine).label
        size = _s_engine("engine_size", engine)

        self.setWindowTitle(_s("dlg_title"))
        self.setFixedSize(460, 380)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._build_prompt(label, size)
        self._apply_style()

    def _build_prompt(self, label: str, size: str) -> None:
        from PySide6.QtCore import QTimer

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        header = QLabel(_s("dlg_header"))
        header.setObjectName("warningLabel")
        root.addWidget(header)

        desc = _s_engine("engine_desc", self._engine)
        body = (
            _s("engine_fmt", label=label, size=size) + "\n" +
            (desc + "\n\n" if desc else "") +
            _s("body")
        )
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        root.addWidget(body_label)

        one_time = QLabel("" + _s("onetime"))
        one_time.setObjectName("accentLabel")
        root.addWidget(one_time)

        privacy = QLabel(_s("privacy"))
        privacy.setObjectName("successLabel")
        root.addWidget(privacy)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._download_btn = QPushButton(_s("btn_download"))
        self._download_btn.setObjectName("primaryButton")
        self._download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self._download_btn)
        self._skip_btn = QPushButton(_s("btn_skip"))
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
            self._download_btn.setText(_s("btn_downloading"))
            self._skip_btn.setEnabled(False)
            self._on_download_click()
            self._status_label.setText(_s("hint_model_setup", label=self._engine_label()))
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
        self._download_btn.setText(_s("btn_retry"))
        self._download_btn.setEnabled(True)
        _safe_disconnect(self._download_btn.clicked, self._start_download)
        self._download_btn.clicked.connect(self._start_download)
        self._skip_btn.setText(_s("btn_close"))
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
        self.setStyleSheet("""
        QDialog { background: #f5f5f7; }
        QLabel { color: #1d1d1f; font-size: 14px; }
        #warningLabel { color: #ff9f0a; font-weight: 700; font-size: 16px; }
        #accentLabel { color: #0071e3; font-weight: 700; }
        #successLabel { color: #34c759; font-size: 13px; }
        QPushButton {
            background: #eef1f5;
            border: 1px solid #e4e7ed;
            border-radius: 10px;
            color: #1d1d1f;
            padding: 9px 16px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover { background: #e0e4ea; }
        #primaryButton { background: #0071e3; color: #ffffff; border: 0; }
        #primaryButton:hover { background: #0059b8; }
        """)


# ── Standalone setup window (--setup CLI flag) ───────────────────────────────


class SetupWindow(QDialog):
    def __init__(self, engine: str, ui_lang: str | None = None) -> None:
        super().__init__()
        if ui_lang:
            _set_dialog_lang(ui_lang)
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
        size = _s_engine("engine_size", engine)

        self.setWindowTitle(_s("setup_title"))
        self.setFixedSize(480, 430)
        self._build(self._engine_label, size)
        self._center()

        from PySide6.QtCore import QTimer
        if not self._runtime_spec.requires_local_model:
            QTimer.singleShot(300, self._already_complete)
        elif model_exists(self._runtime_spec):
            QTimer.singleShot(300, self._already_complete)
        else:
            QTimer.singleShot(300, self._auto_start)

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

        header = QLabel(_s("setup_header"))
        header.setObjectName("setupHeader")
        root.addWidget(header)

        desc = _s_engine("engine_desc", self._engine)
        info = QLabel(f"{label}  ·  {size}" + (f"\n{desc}" if desc else ""))
        info.setWordWrap(True)
        root.addWidget(info)

        one_time = QLabel("" + _s("onetime"))
        one_time.setObjectName("accentLabel")
        root.addWidget(one_time)

        privacy = QLabel(_s("privacy"))
        privacy.setObjectName("successLabel")
        root.addWidget(privacy)

        self._progress_widget = DownloadProgressWidget(
            self,
            self._engine,
            use_hf_downloader=False,
        )
        root.addWidget(self._progress_widget)

        self._bottom_label = QLabel(_s("hint_ready"))
        self._bottom_label.setWordWrap(True)
        root.addWidget(self._bottom_label)
        self._retry_btn = QPushButton(_s("btn_retry"))
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
        self._progress_widget._pct_label.setText("0%")
        self._progress_widget._speed_label.setText("")
        self._bottom_label.setText(_s("hint_model_setup", label=self._engine_label))
        self._download_thread = threading.Thread(
            target=self._download_runtime_model,
            daemon=True,
            name="setup-model-download",
        )
        self._download_thread.start()

    def _already_complete(self) -> None:
        self._bottom_label.setText(_s("hint_existed"))
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
            self._progress_widget._pct_label.setText("100%")
            self._progress_widget._speed_label.setText(_s("st_done"))
            return

        if stage in {"download_prepare", "download"}:
            if progress is not None:
                self._progress_widget._bar.setValue(int(progress * 100))
                self._progress_widget._pct_label.setText(f"{int(progress * 100)}%")
            downloaded = event.get("downloaded_bytes")
            total = event.get("total_bytes")
            if isinstance(downloaded, int) and isinstance(total, int) and total > 0:
                done_gb = downloaded / 1_073_741_824
                total_gb = total / 1_073_741_824
                self._progress_widget._speed_label.setText(f"{done_gb:.2f} / {total_gb:.2f} GB")
            return

        message = str(event.get("message", "")).strip()
        if message:
            self._progress_widget._speed_label.setText(message)

    def _on_completed(self) -> None:
        if self._close_scheduled:
            return
        self._progress_widget._bar.setValue(100)
        self._bottom_label.setText(_s("hint_done"))
        self._exit_code = 0
        self._schedule_close(1500)

    def _on_failed(self, message: str) -> None:
        self._progress_widget._bar.setValue(0)
        self._bottom_label.setText(_s("hint_error") + message + "\n" + _s("hint_retry"))
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
        self.setStyleSheet("""
        QDialog { background: #f5f5f7; }
        QLabel { color: #1d1d1f; font-size: 14px; }
        #setupHeader { font-weight: 700; font-size: 16px; }
        #accentLabel { color: #0071e3; font-weight: 700; }
        #successLabel { color: #34c759; font-size: 13px; }
        """)


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
