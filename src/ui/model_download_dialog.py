"""Model download dialog — multilingual, with retry support.

Two entry points:
  - ModelMissingDialog(parent, engine)  – modal dialog launched from main_window
  - run_setup_mode(engine)              – standalone window used by --setup CLI flag
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from src.asr.hf_model_downloader import (
    DownloadProgress,
    DownloadState,
    get_downloader,
)
from src.asr.model_manager import download_model, model_exists
from src.asr.model_registry import ASR_ENGINE_SPECS, get_asr_engine_spec
from src.ui.window_effects import apply_window_icon
from src.utils.locale_detect import get_system_language

logger = logging.getLogger(__name__)

BG = "#f5f5f7"
CARD_BG = "#ffffff"
CARD_BORDER = "#d8dde6"
ACCENT = "#0071e3"
ACCENT_HOVER = "#0059b8"
BTN_SECONDARY_BG = "#eef1f5"
BTN_SECONDARY_HOVER = "#e0e4ea"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#6e6e73"
WARN_COLOR = "#ff9f0a"
SUCCESS_COLOR = "#34c759"
DANGER_COLOR = "#ff453a"
RETRY_COLOR = "#6e6e73"
RETRY_HOVER = "#4a4a4f"

# ── Multilingual strings ─────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "dlg_title":    "语音识别模型未找到",
        "dlg_header":   "⚠  语音识别模型未找到",
        "engine_fmt":   "当前引擎：{label}（{size}）",
        "body":         "没有模型文件，应用将无法进行语音识别和翻译。\n建议立即下载。下载完成前仍可使用文字输入功能。",
        "privacy":      "✓ 完全本地运行，语音数据不会上传到任何服务器",
        "onetime":      "模型仅需下载一次，后续更新无需再次下载。",
        "btn_download": "立即下载",
        "btn_downloading": "正在下载",
        "btn_skip":     "暂时跳过",
        "btn_pause":    "⏸ 暂停",
        "btn_resume":   "▶ 继续",
        "btn_cancel":   "✕ 取消",
        "btn_retry":    "🔄 重新下载",
        "btn_skip2":    "跳过，稍后下载",
        "st_paused":    "已暂停",
        "st_done":      "下载完成 ✓",
        "st_cancelled": "已取消",
        "st_error_pfx": "下载失败: ",
        "hint_paused":  "下载已暂停，点击继续恢复",
        "hint_error":   "下载失败: ",
        "hint_ready":   "下载完成后窗口将自动关闭",
        "hint_done":    "✓ 安装完成，即将关闭…",
        "hint_existed": "模型已就绪，即将关闭…",
        "hint_sv_setup": "正在下载 SenseVoice 模型，下载完成后窗口将自动关闭。",
        "btn_close":    "关闭",
        "setup_title":  "Mio RealTime Translator — 初始设置",
        "setup_header": "🎙  正在安装语音识别模型",
        "engine_desc": {
            "sensevoice-small":      "适用于中文、粤语的高精度离线语音识别模型。",
        },
        "engine_size": {
            "sensevoice-small":      "约 950 MB",
        },
    },
    "ja": {
        "dlg_title":    "音声認識モデルが見つかりません",
        "dlg_header":   "⚠  音声認識モデルが見つかりません",
        "engine_fmt":   "エンジン：{label}（{size}）",
        "body":         "モデルファイルが見つかりません。音声認識・翻訳機能が使用できません。\n今すぐダウンロードすることをお勧めします。ダウンロード中もテキスト入力は使用できます。",
        "privacy":      "✓ 完全ローカル処理 — 音声データはどこにも送信されません",
        "onetime":      "モデルのダウンロードは初回のみ。アップデート後の再ダウンロードは不要です。",
        "btn_download": "今すぐダウンロード",
        "btn_downloading": "ダウンロード中",
        "btn_skip":     "後でダウンロード",
        "btn_pause":    "⏸ 一時停止",
        "btn_resume":   "▶ 再開",
        "btn_cancel":   "✕ キャンセル",
        "btn_retry":    "🔄 再ダウンロード",
        "btn_skip2":    "スキップ（後でダウンロード）",
        "st_paused":    "一時停止中",
        "st_done":      "ダウンロード完了 ✓",
        "st_cancelled": "キャンセルしました",
        "st_error_pfx": "エラー: ",
        "hint_paused":  "一時停止中。「再開」をクリックして続行。",
        "hint_error":   "ダウンロード失敗: ",
        "hint_ready":   "ダウンロード完了後、ウィンドウは自動的に閉じます",
        "hint_done":    "✓ インストール完了。まもなく閉じます…",
        "hint_existed": "モデルは準備済みです。まもなく閉じます…",
        "hint_sv_setup": "SenseVoice モデルをダウンロードしています。完了すると自動的に閉じます。",
        "btn_close":    "閉じる",
        "setup_title":  "Mio RealTime Translator — 初期設定",
        "setup_header": "🎙  音声認識モデルをインストール中",
        "engine_desc": {
            "sensevoice-small":      "中国語・広東語向け、高精度オフライン音声認識モデル。",
        },
        "engine_size": {
            "sensevoice-small":      "約 950 MB",
        },
    },
    "ko": {
        "dlg_title":    "음성 인식 모델을 찾을 수 없음",
        "dlg_header":   "⚠  음성 인식 모델을 찾을 수 없음",
        "engine_fmt":   "엔진: {label} ({size})",
        "body":         "모델 파일이 없습니다. 음성 인식 및 번역을 사용할 수 없습니다.\n지금 다운로드하는 것을 권장합니다. 다운로드 완료 전에도 텍스트 입력은 사용 가능합니다.",
        "privacy":      "✓ 완전 로컬 실행 — 음성 데이터는 어디에도 업로드되지 않습니다",
        "onetime":      "모델은 한 번만 다운로드하면 됩니다. 업데이트 시 재다운로드가 필요하지 않습니다.",
        "btn_download": "지금 다운로드",
        "btn_downloading": "다운로드 중",
        "btn_skip":     "나중에",
        "btn_pause":    "⏸ 일시정지",
        "btn_resume":   "▶ 재개",
        "btn_cancel":   "✕ 취소",
        "btn_retry":    "🔄 다시 다운로드",
        "btn_skip2":    "건너뛰기",
        "st_paused":    "일시정지됨",
        "st_done":      "다운로드 완료 ✓",
        "st_cancelled": "취소됨",
        "st_error_pfx": "오류: ",
        "hint_paused":  "다운로드가 일시정지되었습니다. 재개를 클릭하세요.",
        "hint_error":   "다운로드 실패: ",
        "hint_ready":   "다운로드 완료 후 창이 자동으로 닫힙니다",
        "hint_done":    "✓ 설치 완료. 곧 닫힙니다…",
        "hint_existed": "모델이 준비되었습니다. 곧 닫힙니다…",
        "hint_sv_setup": "SenseVoice 모델을 다운로드 중입니다. 완료되면 창이 자동으로 닫힙니다.",
        "btn_close":    "닫기",
        "setup_title":  "Mio RealTime Translator — 초기 설정",
        "setup_header": "🎙  음성 인식 모델 설치 중",
        "engine_desc": {
            "sensevoice-small":      "중국어, 광동어를 위한 고정밀 오프라인 음성 인식 모델.",
        },
        "engine_size": {
            "sensevoice-small":      "약 950 MB",
        },
    },
    "ru": {
        "dlg_title":    "Модель не найдена",
        "dlg_header":   "⚠  Модель распознавания речи не найдена",
        "engine_fmt":   "Движок: {label} ({size})",
        "body":         "Файл модели не найден. Распознавание речи и перевод недоступны.\nРекомендуется загрузить модель сейчас. Ввод текста доступен до завершения загрузки.",
        "privacy":      "✓ Полностью локально — голос никуда не передаётся",
        "onetime":      "Модель загружается только один раз. Обновления не потребуют повторной загрузки.",
        "btn_download": "Скачать сейчас",
        "btn_downloading": "Загрузка",
        "btn_skip":     "Пропустить",
        "btn_pause":    "⏸ Пауза",
        "btn_resume":   "▶ Продолжить",
        "btn_cancel":   "✕ Отмена",
        "btn_retry":    "🔄 Повторить",
        "btn_skip2":    "Пропустить",
        "st_paused":    "Приостановлено",
        "st_done":      "Загрузка завершена ✓",
        "st_cancelled": "Отменено",
        "st_error_pfx": "Ошибка: ",
        "hint_paused":  "Загрузка приостановлена. Нажмите «Продолжить».",
        "hint_error":   "Ошибка загрузки: ",
        "hint_ready":   "Окно закроется автоматически после завершения",
        "hint_done":    "✓ Установка завершена. Закрывается…",
        "hint_existed": "Модель готова. Закрывается…",
        "hint_sv_setup": "Модель SenseVoice загружается. Окно закроется автоматически после завершения.",
        "btn_close":    "Закрыть",
        "setup_title":  "Mio RealTime Translator — Настройка",
        "setup_header": "🎙  Установка модели распознавания речи",
        "engine_desc": {
            "sensevoice-small":      "Высокоточная офлайн-модель для китайского и кантонского языков.",
        },
        "engine_size": {
            "sensevoice-small":      "~950 МБ",
        },
    },
    "en": {
        "dlg_title":    "Speech Model Not Found",
        "dlg_header":   "⚠  Speech Recognition Model Not Found",
        "engine_fmt":   "Engine: {label} ({size})",
        "body":         "No model file found. Speech recognition and translation will not work.\nDownloading now is recommended. Text input remains available.",
        "privacy":      "✓ Fully local — your voice is never uploaded anywhere",
        "onetime":      "The model only needs to be downloaded once. Updates will not require re-downloading.",
        "btn_download": "Download Now",
        "btn_downloading": "Downloading",
        "btn_skip":     "Skip for Now",
        "btn_pause":    "⏸ Pause",
        "btn_resume":   "▶ Resume",
        "btn_cancel":   "✕ Cancel",
        "btn_retry":    "🔄 Retry Download",
        "btn_skip2":    "Skip, Download Later",
        "st_paused":    "Paused",
        "st_done":      "Download Complete ✓",
        "st_cancelled": "Cancelled",
        "st_error_pfx": "Error: ",
        "hint_paused":  "Download paused. Click Resume to continue.",
        "hint_error":   "Download failed: ",
        "hint_ready":   "Window will close automatically when download completes",
        "hint_done":    "✓ Setup complete. Closing…",
        "hint_existed": "Model is ready. Closing…",
        "hint_sv_setup": "Downloading the SenseVoice model. This window will close automatically when it finishes.",
        "btn_close":    "Close",
        "setup_title":  "Mio RealTime Translator — Setup",
        "setup_header": "🎙  Installing Speech Recognition Model",
        "engine_desc": {
            "sensevoice-small":      "High-accuracy offline speech recognition model for Chinese and Cantonese.",
        },
        "engine_size": {
            "sensevoice-small":      "~950 MB",
        },
    },
}

# Aliases
_STRINGS["zh-cn"] = _STRINGS["zh"]
_STRINGS["zh-tw"] = _STRINGS["zh"]
_STRINGS["zh-hk"] = _STRINGS["zh"]
_STRINGS["yue"]   = _STRINGS["zh"]


# Overrides get_system_language() when the app's UI language is known.
# Set via _set_dialog_lang() by each dialog/widget that knows ui_lang.
_dialog_lang: str = ""


def _set_dialog_lang(lang: str) -> None:
    global _dialog_lang
    _dialog_lang = lang.lower().split("-")[0] if lang else ""


def _effective_lang() -> str:
    return _dialog_lang or get_system_language().lower()


def _s(key: str, **fmt) -> str:
    """Return the localised string for key, falling back to English."""
    lang = _effective_lang()
    table = _STRINGS.get(lang) or _STRINGS.get(lang.split("-")[0]) or _STRINGS["en"]
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**fmt) if fmt else text


def _s_engine(sub: str, engine: str) -> str:
    """Return a per-engine localised string from the nested dicts."""
    lang = _effective_lang()
    table = _STRINGS.get(lang) or _STRINGS.get(lang.split("-")[0]) or _STRINGS["en"]
    en_table = _STRINGS["en"]
    return (table.get(sub) or en_table.get(sub, {})).get(engine, "")


def _model_id_for_engine(engine: str) -> str:
    spec = ASR_ENGINE_SPECS.get(engine)
    return spec.model_id if spec else ""


# ── Shared progress widget ───────────────────────────────────────────────────

class DownloadProgressWidget(ctk.CTkFrame):
    """Embeddable progress bar + speed/ETA + pause/retry/cancel buttons."""

    def __init__(
        self,
        master,
        engine: str,
        downloader=None,
        model_id: str | None = None,
        on_completed: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        compact: bool = False,
        ui_lang: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        if ui_lang:
            _set_dialog_lang(ui_lang)
        self._engine = engine
        self._model_id = model_id if model_id is not None else _model_id_for_engine(engine)
        self._on_completed = on_completed
        self._on_cancelled = on_cancelled
        self._compact = compact
        self._downloader = downloader if downloader is not None else (
            get_downloader(self._model_id) if self._model_id else None
        )
        self._completed_notified = False
        self._build()
        if self._downloader:
            self._downloader.add_listener(self._on_progress)
            self._on_progress(self._downloader.progress)

    def destroy(self) -> None:
        if self._downloader:
            self._downloader.remove_listener(self._on_progress)
        super().destroy()

    def _build(self) -> None:
        pad = {"padx": 0, "pady": 2}

        if not self._compact:
            self._status_label = ctk.CTkLabel(
                self, text="", font=ctk.CTkFont(size=12),
                text_color=TEXT_SEC, anchor="w",
            )
            self._status_label.pack(fill="x", **pad)

        self._bar = ctk.CTkProgressBar(
            self, height=8, corner_radius=4,
            fg_color=CARD_BORDER, progress_color=ACCENT,
        )
        self._bar.set(0)
        self._bar.pack(fill="x", pady=(4, 2))

        info_row = ctk.CTkFrame(self, fg_color="transparent")
        info_row.pack(fill="x", **pad)
        self._pct_label = ctk.CTkLabel(
            info_row, text="0%", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_PRI, anchor="w",
        )
        self._pct_label.pack(side="left")
        self._speed_label = ctk.CTkLabel(
            info_row, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC, anchor="e",
        )
        self._speed_label.pack(side="right")

        # Button row — pause+cancel shown while active; retry shown on error/cancel
        self._btn_row = ctk.CTkFrame(self, fg_color="transparent")
        self._btn_row.pack(fill="x", pady=(6, 0))

        self._pause_btn = ctk.CTkButton(
            self._btn_row, text=_s("btn_pause"), width=90, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SECONDARY_BG, hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_PRI, corner_radius=8,
            command=self._toggle_pause,
        )
        self._pause_btn.pack(side="left", padx=(0, 6))

        self._stop_btn = ctk.CTkButton(
            self._btn_row, text=_s("btn_cancel"), width=90, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SECONDARY_BG, hover_color="#fde8e8",
            text_color=DANGER_COLOR, corner_radius=8,
            command=self._cancel,
        )
        self._stop_btn.pack(side="left")

        self._retry_btn = ctk.CTkButton(
            self._btn_row, text=_s("btn_retry"), width=140, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SECONDARY_BG, hover_color=BTN_SECONDARY_HOVER,
            text_color=RETRY_COLOR, corner_radius=8,
            command=self._retry,
        )
        # Hidden initially; shown on error / cancelled

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
        # start() resets state and resumes from partial file
        self._completed_notified = False
        self._downloader.start()

    def _on_progress(self, p: DownloadProgress) -> None:
        try:
            self.after(0, lambda: self._apply_progress(p))
        except Exception:
            pass

    def _apply_progress(self, p: DownloadProgress) -> None:
        try:
            frac = p.overall_fraction
            self._bar.set(frac)
            self._pct_label.configure(text=f"{int(frac * 100)}%")

            show_retry = p.state in (DownloadState.ERROR, DownloadState.CANCELLED)

            # Toggle button visibility
            if show_retry:
                self._pause_btn.pack_forget()
                self._stop_btn.pack_forget()
                if not self._retry_btn.winfo_ismapped():
                    self._retry_btn.pack(in_=self._btn_row, side="left")
            else:
                self._retry_btn.pack_forget()
                if not self._pause_btn.winfo_ismapped():
                    self._pause_btn.pack(in_=self._btn_row, side="left", padx=(0, 6))
                if not self._stop_btn.winfo_ismapped():
                    self._stop_btn.pack(in_=self._btn_row, side="left")

            if p.state == DownloadState.PAUSED:
                speed_text = _s("st_paused")
                self._pause_btn.configure(text=_s("btn_resume"))
            elif p.state == DownloadState.DOWNLOADING:
                parts = []
                if p.speed_bps > 0:
                    parts.append(p.speed_mb)
                if p.eta_str:
                    parts.append(f"剩余 {p.eta_str}" if get_system_language() == "zh"
                                 else p.eta_str)
                speed_text = "  ".join(parts)
                self._pause_btn.configure(text=_s("btn_pause"), state="normal")
                self._stop_btn.configure(state="normal")
            elif p.state == DownloadState.COMPLETED:
                speed_text = _s("st_done")
                self._pause_btn.configure(state="disabled")
                self._stop_btn.configure(state="disabled")
                if self._on_completed and not self._completed_notified:
                    self._completed_notified = True
                    self._on_completed()
                return
            elif p.state == DownloadState.ERROR:
                speed_text = _s("st_error_pfx") + p.error[:50]
            elif p.state == DownloadState.CANCELLED:
                speed_text = _s("st_cancelled")
            else:
                speed_text = ""

            self._speed_label.configure(text=speed_text)

            if not self._compact and hasattr(self, "_status_label"):
                if p.state == DownloadState.DOWNLOADING and p.file_name:
                    done_gb = f"{p.total_bytes / 1_073_741_824:.2f} GB"
                    total_gb = f"{p.total_total / 1_073_741_824:.2f} GB" if p.total_total else "?"
                    self._status_label.configure(text=f"{p.file_name}  {done_gb} / {total_gb}")
                elif p.state == DownloadState.PAUSED:
                    self._status_label.configure(text=_s("hint_paused"))
                elif p.state == DownloadState.ERROR:
                    self._status_label.configure(text=_s("hint_error") + p.error)
                elif p.state == DownloadState.CANCELLED:
                    self._status_label.configure(text=_s("st_cancelled"))
        except tk.TclError:
            pass  # widget already destroyed


# ── "No model" prompt dialog ─────────────────────────────────────────────────

class ModelMissingDialog(ctk.CTkToplevel):
    """Shown on startup when the configured ASR model is absent."""

    def __init__(
        self,
        parent,
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
        self._result: str = "skip"
        self._close_scheduled = False
        self._watching_external_download = False
        self._on_download_click = on_download_click
        self._is_model_ready = is_model_ready
        self._is_download_running = is_download_running

        label = "SenseVoice Small" if engine == "sensevoice-small" else engine.replace("-", " ").title()
        size = _s_engine("engine_size", engine)

        self.title(_s("dlg_title"))
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()
        self.lift()
        apply_window_icon(self)

        self._build_prompt(label, size)
        self._center(parent)

    def _center(self, parent) -> None:
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build_prompt(self, label: str, size: str) -> None:
        card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=12,
                            border_width=1, border_color=CARD_BORDER)
        card.pack(padx=24, pady=24, fill="both")

        ctk.CTkLabel(
            card, text=_s("dlg_header"),
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=WARN_COLOR, anchor="w",
        ).pack(padx=20, pady=(18, 4), anchor="w")

        desc = _s_engine("engine_desc", self._engine)
        body = (
            _s("engine_fmt", label=label, size=size) + "\n" +
            (desc + "\n\n" if desc else "") +
            _s("body")
        )
        ctk.CTkLabel(
            card, text=body,
            font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
            justify="left", wraplength=360, anchor="w",
        ).pack(padx=20, pady=(0, 6), anchor="w")

        ctk.CTkLabel(
            card, text="✦  " + _s("onetime"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT, anchor="w",
        ).pack(padx=20, pady=(0, 4), anchor="w")

        ctk.CTkLabel(
            card, text=_s("privacy"),
            font=ctk.CTkFont(size=11), text_color=SUCCESS_COLOR, anchor="w",
        ).pack(padx=20, pady=(0, 16), anchor="w")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(padx=20, pady=(0, 18), fill="x")

        self._download_btn = ctk.CTkButton(
            btn_row, text=_s("btn_download"),
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff", corner_radius=10, height=36,
            command=self._start_download,
        )
        self._download_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._skip_btn = ctk.CTkButton(
            btn_row, text=_s("btn_skip"),
            font=ctk.CTkFont(size=13),
            fg_color=BTN_SECONDARY_BG, hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_SEC, corner_radius=10, height=36,
            command=self._skip,
        )
        self._skip_btn.pack(side="left", expand=True, fill="x")

        self._progress_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._progress_frame.pack(padx=20, pady=(0, 4), fill="x")

    def _start_download(self) -> None:
        self._result = "download"
        if self._on_download_click is not None:
            # SenseVoice: hand off to ModelScope flow in main_window, then show
            # a non-blocking status label so the window doesn't just vanish.
            self._download_btn.configure(state="disabled", text=_s("btn_downloading"))
            self._skip_btn.configure(state="disabled")
            self._on_download_click()
            for child in self._progress_frame.winfo_children():
                child.destroy()
            ctk.CTkLabel(
                self._progress_frame,
                text=_s("hint_sv_setup"),
                font=ctk.CTkFont(size=11),
                text_color=TEXT_SEC,
                justify="left",
                wraplength=360,
                anchor="w",
            ).pack(anchor="w", pady=(6, 4))
            self.update_idletasks()
            self._watching_external_download = True
            self.after(600, self._watch_external_download)
            return

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
            self.after(600, self._watch_external_download)
        except tk.TclError:
            return

    def _on_external_download_stopped(self) -> None:
        self._watching_external_download = False
        self._result = "failed"
        try:
            self._download_btn.configure(
                text=_s("btn_retry"),
                state="normal",
                command=self._start_download,
            )
            self._skip_btn.configure(
                text=_s("btn_close"),
                state="normal",
                command=self.destroy,
            )
        except tk.TclError:
            pass

    def _on_download_completed(self) -> None:
        if self._close_scheduled:
            return
        self._result = "completed"
        self._close_scheduled = True
        self.after(1200, self.destroy)

    def _on_download_cancelled(self) -> None:
        self._result = "cancelled"
        try:
            self._skip_btn.configure(
                text=_s("btn_close"),
                state="normal",
                command=self.destroy,
            )
        except tk.TclError:
            pass

    def _skip(self) -> None:
        self._result = "skip"
        self.destroy()

    @property
    def result(self) -> str:
        return self._result


# ── Standalone setup window (--setup CLI flag) ───────────────────────────────

class SetupWindow(ctk.CTk):
    """Full-window download UI for installer post-install setup mode."""

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
        self._setup_progress_bar: ctk.CTkProgressBar | None = None
        self._setup_progress_running = False
        self._exit_code = 0

        label = "SenseVoice Small" if engine == "sensevoice-small" else engine.replace("-", " ").title()
        size = _s_engine("engine_size", engine)

        self.title(_s("setup_title"))
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.geometry("480x430")
        apply_window_icon(self)
        self._build(label, size)
        self._center()

        if not self._runtime_spec.requires_local_model:
            self.after(300, self._already_complete)
        elif model_exists(self._runtime_spec):
            self.after(300, self._already_complete)
        else:
            self.after(300, self._auto_start)

    def _center(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build(self, label: str, size: str) -> None:
        card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=14,
                            border_width=1, border_color=CARD_BORDER)
        card.pack(padx=24, pady=24, fill="both", expand=True)

        ctk.CTkLabel(
            card, text=_s("setup_header"),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRI, anchor="w",
        ).pack(padx=20, pady=(20, 6), anchor="w")

        desc = _s_engine("engine_desc", self._engine)
        ctk.CTkLabel(
            card, text=f"{label}  ·  {size}" + (f"\n{desc}" if desc else ""),
            font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
            justify="left", wraplength=410, anchor="w",
        ).pack(padx=20, pady=(0, 8), anchor="w")

        ctk.CTkLabel(
            card, text="✦  " + _s("onetime"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT, anchor="w",
        ).pack(padx=20, pady=(0, 4), anchor="w")

        ctk.CTkLabel(
            card, text=_s("privacy"),
            font=ctk.CTkFont(size=11), text_color=SUCCESS_COLOR, anchor="w",
        ).pack(padx=20, pady=(0, 14), anchor="w")

        prog_frame = ctk.CTkFrame(card, fg_color="transparent")
        prog_frame.pack(padx=20, fill="x")

        self._progress_widget = None
        self._setup_progress_bar = ctk.CTkProgressBar(
            prog_frame, height=8, corner_radius=4,
            fg_color=CARD_BORDER, progress_color=ACCENT,
        )
        self._setup_progress_bar.set(0)
        self._setup_progress_bar.pack(fill="x", pady=(4, 2))
        info_row = ctk.CTkFrame(prog_frame, fg_color="transparent")
        info_row.pack(fill="x", pady=(2, 0))
        self._setup_pct_label = ctk.CTkLabel(
            info_row, text="0%",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_PRI, anchor="w",
        )
        self._setup_pct_label.pack(side="left")
        self._setup_detail_label = ctk.CTkLabel(
            info_row, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC, anchor="e",
        )
        self._setup_detail_label.pack(side="right")

        self._bottom_label = ctk.CTkLabel(
            card, text=_s("hint_ready"),
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        )
        self._bottom_label.pack(padx=20, pady=(12, 20), anchor="w")

        self._close_btn = ctk.CTkButton(
            card, text=_s("btn_close"),
            font=ctk.CTkFont(size=12),
            fg_color=BTN_SECONDARY_BG, hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_SEC, corner_radius=10, height=32,
            command=self.destroy,
        )

    def _auto_start(self) -> None:
        if self._download_thread is not None and self._download_thread.is_alive():
            return
        self._bottom_label.configure(text=_s("hint_sv_setup"))
        self._set_setup_progress(None, indeterminate=True)
        self._download_thread = threading.Thread(
            target=self._download_runtime_model,
            daemon=True,
            name="setup-model-download",
        )
        self._download_thread.start()

    def _already_complete(self) -> None:
        self._bottom_label.configure(text=_s("hint_existed"))
        self._schedule_close(1500)

    def _download_runtime_model(self) -> None:
        try:
            download_model(
                self._runtime_spec,
                progress_callback=lambda event: self._call_in_ui(
                    lambda e=event: self._apply_modelscope_progress(e)
                ),
            )
        except Exception as exc:
            self._call_in_ui(lambda message=str(exc): self._on_failed(message))
            return
        self._call_in_ui(self._on_completed)

    def _call_in_ui(self, callback: Callable[[], None]) -> None:
        if self._destroying:
            return
        try:
            self.after(0, callback)
        except tk.TclError:
            return

    def _set_setup_progress(self, progress: float | None, *, indeterminate: bool) -> None:
        if self._setup_progress_bar is None:
            return
        try:
            if self._setup_progress_running:
                self._setup_progress_bar.stop()
                self._setup_progress_running = False
            if indeterminate:
                self._setup_progress_bar.configure(mode="indeterminate")
                self._setup_progress_bar.start()
                self._setup_progress_running = True
                return
            self._setup_progress_bar.configure(mode="determinate")
            self._setup_progress_bar.set(max(0.0, min(float(progress or 0.0), 1.0)))
        except tk.TclError:
            return

    def _apply_modelscope_progress(self, event: dict[str, object]) -> None:
        if self._destroying:
            return
        stage = str(event.get("stage", "")).strip()
        progress_value = event.get("progress")
        progress = float(progress_value) if isinstance(progress_value, (int, float)) else None
        indeterminate = bool(event.get("indeterminate", False))

        if stage == "download_complete":
            self._set_setup_progress(1.0, indeterminate=False)
            self._setup_pct_label.configure(text="100%")
            self._setup_detail_label.configure(text=_s("st_done"))
            return

        if stage in {"download_prepare", "download"}:
            self._set_setup_progress(progress, indeterminate=indeterminate or progress is None)
            if progress is not None:
                self._setup_pct_label.configure(text=f"{int(progress * 100)}%")
            downloaded = event.get("downloaded_bytes")
            total = event.get("total_bytes")
            if isinstance(downloaded, int) and isinstance(total, int) and total > 0:
                done_gb = downloaded / 1_073_741_824
                total_gb = total / 1_073_741_824
                self._setup_detail_label.configure(text=f"{done_gb:.2f} / {total_gb:.2f} GB")
            return

        message = str(event.get("message", "")).strip()
        if message:
            self._setup_detail_label.configure(text=message)

    def _on_completed(self) -> None:
        if self._close_scheduled:
            return
        self._set_setup_progress(1.0, indeterminate=False)
        self._bottom_label.configure(text=_s("hint_done"))
        self._exit_code = 0
        self._schedule_close(1500)

    def _on_cancelled(self) -> None:
        self._exit_code = 0
        self._schedule_close(200)

    def _on_failed(self, message: str) -> None:
        self._set_setup_progress(0.0, indeterminate=False)
        self._bottom_label.configure(text=_s("hint_error") + message)
        self._exit_code = 0
        self._close_btn.configure(text=_s("btn_close"))
        self._close_btn.pack(padx=20, pady=(0, 20), anchor="e")

    def _schedule_close(self, delay_ms: int) -> None:
        if self._close_scheduled:
            return
        self._close_scheduled = True
        self.after(delay_ms, self.destroy)

    def destroy(self) -> None:
        self._destroying = True
        try:
            if self._setup_progress_bar is not None and self._setup_progress_running:
                self._setup_progress_bar.stop()
        except tk.TclError:
            pass
        super().destroy()


def run_setup_mode(engine: str) -> int:
    """Launch the setup window (blocking). Returns exit code."""
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    # Read the user's UI language from config so the setup window matches.
    ui_lang: str = ""
    try:
        from src.utils import config_manager
        ui_lang = str(
            config_manager.load_config().get("ui", {}).get("language", "") or ""
        ).strip()
    except Exception:
        pass
    app = SetupWindow(engine, ui_lang=ui_lang or None)
    app.mainloop()
    return app._exit_code
