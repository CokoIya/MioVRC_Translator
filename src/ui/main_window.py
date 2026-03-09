"""メインアプリケーションウィンドウ"""

import threading
import webbrowser
import sys
import subprocess
import time
from pathlib import Path
import customtkinter as ctk
from tkinter import messagebox, PhotoImage
import sounddevice as sd

from src.utils import config_manager
from src.utils.i18n import tr
from src.utils.ui_config import (
    MANUAL_SOURCE_LANGUAGE_OPTIONS,
    TARGET_LANGUAGE_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    get_ui_language,
    normalize_output_format,
)
from src.audio.recorder import AudioRecorder
from src.asr.factory import create_asr
from src.asr.sensevoice_model_manager import ensure_model, model_exists
from src.asr.streaming_merger import StreamingMerger
from src.translators.factory import create_translator
from src.osc.sender import VRCOSCSender
from src.osc.receiver import VRCOSCReceiver
from src.utils.lang_detect import detect_language
from .settings_window import SettingsWindow
from .floating_window import FloatingWindow

# ── カラーパレット ────────────────────────────────────────────────────────
BG_PRIMARY   = "#f7f5f0"   # メイン背景色
BG_SECONDARY = "#edeae2"   # 補助背景色
BG_TOP       = "#e5e1d8"   # 上部バーの背景色
BG_PANEL     = "#f2efe8"   # パネル背景色
GLASS_BG     = "#daeaf8"   # ボタン背景色
GLASS_BORDER = "#8ab8d8"   # ボタン枠線色
GLASS_HOVER  = "#c4dcf2"   # ホバー時の背景色
ACCENT       = "#3a9fd8"   # 強調色
ACCENT_HOVER = "#2882bc"   # 強調色のホバー時背景色
DANGER       = "#e05060"   # 停止系の強調色
DANGER_HOVER = "#c03045"   # 停止系ホバー色
SUCCESS      = "#2ea85a"   # 成功状態の色
TEXT_PRI     = "#252535"   # 主テキスト色
TEXT_SEC     = "#686880"   # 補助テキスト色
DIVIDER      = "#d8d4cc"   # 区切り線の色

GITHUB_REPO_URL = "https://github.com/CokoIya/MioVRC_Translator"
QQ_GROUP_URL = "https://qm.qq.com/q/1PThd3QBTS"
LINE_GROUP_URL = "https://line.me/ti/g2/uLhASjhfQcsd5tYsEpFr8GWsCcuYVIq1I6iGwA?utm_source=invitation&utm_medium=link_copy&utm_campaign=default"
ICON_GITHUB_FILE = "github.png"
ICON_QQ_FILE = "qq.png"
ICON_LINE_FILE = "line.png"
ICON_SPONSOR_FILE = "sponsor.png"
APP_ICON_ICO_FILE = "app_icon_mio.ico"
APP_ICON_PNG_FILE = "app_icon_mio.png"
SPONSOR_IMAGE_CANDIDATES = (
    "zanzhu.png",
    "sponsor_qr.png",
    "sponsor_qr.jpg",
    "sponsor_qr.jpeg",
    "sponsor.png",
    "sponsor.jpg",
)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class MainWindow(ctk.CTk):
    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._ui_lang = get_ui_language(config)
        self.title(tr(self._ui_lang, "window_title"))
        self.geometry("860x500")
        self.minsize(760, 450)
        self.configure(fg_color=BG_PRIMARY)

        # 起動時に必要な主要オブジェクト  
        self._recorder: AudioRecorder | None = None
        self._asr = create_asr(config)
        self._translator = None
        self._sender: VRCOSCSender | None = None
        self._receiver: VRCOSCReceiver | None = None
        self._own_msgs: set[str] = set()
        self._osc_echo_capable = False
        self._translating = False
        self._src_placeholder = tr(self._ui_lang, "source_placeholder")
        self._src_text = ""
        self._last_tgt_text = ""
        self._last_own_chatbox_echo_text = ""
        self._last_own_chatbox_echo_time = 0.0

        self._running = False
        self._partial_worker_busy = False
        self._partial_worker_lock = threading.Lock()
        self._final_worker_lock = threading.Lock()
        self._merge_lock = threading.Lock()
        self._partial_generation = 0
        self._partial_merger = self._create_streaming_merger()
        self._current_tgt_lang: str = self._config.get("translation", {}).get("target_language", "ja")
        self._current_src_lang: str | None = None  #   None   は自動判定を表す  
        self._float_win: FloatingWindow | None = None
        self._sponsor_win: ctk.CTkToplevel | None = None
        self._social_icons: dict[str, ctk.CTkImage] = {}
        self._window_icon: PhotoImage | None = None
        self._status_text = self._t("status_ready")
        self._status_color = SUCCESS
        self._bottom_text = self._t("model_unloaded")
        self._bottom_progress_visible = False
        self._bottom_progress_value = 0.0
        self._bottom_progress_indeterminate = False
        self._bottom_progress_running = False
        self._model_prepare_running = False

        self._set_window_icon()

        self._build()
        self._load_devices()
        self.after(180, self._position_near_float_window)
        self.after(420, self._maybe_prepare_runtime_model)
        self.after(300, self._maybe_show_osc_guide)

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    # ── UI 構築 ────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_TOP)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=self._t("creator_banner"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_PRI,
            justify="left",
            wraplength=900,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        actions.grid_columnconfigure(0, weight=1)

        self._status_label = ctk.CTkLabel(
            actions,
            text=self._status_text,
            text_color=self._status_color,
            font=ctk.CTkFont(size=12),
        )
        self._status_label.grid(row=0, column=0, sticky="w")

        action_buttons = ctk.CTkFrame(actions, fg_color="transparent")
        action_buttons.grid(row=0, column=1, sticky="e")

        self._start_btn = ctk.CTkButton(
            action_buttons,
            text=self._t("start_listening"),
            width=134,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=10,
            text_color="#ffffff",
            command=self._toggle_listening,
        )
        self._start_btn.pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            action_buttons,
            text=self._t("settings_button"),
            width=116,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._open_settings,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            action_buttons,
            text=self._t("guide_button"),
            width=112,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._open_osc_guide,
        ).pack(side="right", padx=(8, 0))

        controls = ctk.CTkFrame(self, fg_color=BG_SECONDARY, corner_radius=0)
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=0)

        mic_group = ctk.CTkFrame(controls, fg_color="transparent")
        mic_group.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=6)
        ctk.CTkLabel(
            mic_group,
            text=self._t("microphone"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._device_var = ctk.StringVar()
        self._device_menu = ctk.CTkOptionMenu(
            mic_group,
            variable=self._device_var,
            values=["Loading..."],
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=8,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            width=250,
        )
        self._device_menu.grid(row=0, column=1, sticky="w")

        self._target_lang_labels = [label for label, _ in TARGET_LANGUAGE_OPTIONS]
        self._target_lang_codes = {label: code for label, code in TARGET_LANGUAGE_OPTIONS}
        self._target_lang_reverse = {code: label for label, code in TARGET_LANGUAGE_OPTIONS}
        initial_tgt = self._config.get("translation", {}).get("target_language", "ja")
        self._tgt_var = ctk.StringVar(
            value=self._target_lang_reverse.get(initial_tgt, self._target_lang_labels[0])
        )

        float_group = ctk.CTkFrame(controls, fg_color="transparent")
        float_group.grid(row=0, column=1, sticky="e", padx=(6, 12), pady=6)
        self._float_btn = ctk.CTkButton(
            float_group,
            text=self._t("floating_hidden"),
            width=120,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._toggle_float,
        )
        self._float_btn.grid(row=0, column=0, sticky="ew")

        self._build_translate_panel()

        bottom = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)

        self._bottom_bar = ctk.CTkLabel(
            bottom,
            text=self._bottom_text,
            font=ctk.CTkFont(size=10),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=920,
        )
        self._bottom_bar.grid(row=0, column=0, sticky="w", padx=12, pady=(4, 2))

        self._bottom_progress = ctk.CTkProgressBar(
            bottom,
            height=10,
            fg_color=BG_SECONDARY,
            progress_color=ACCENT,
        )
        self._bottom_progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._bottom_progress.set(self._bottom_progress_value)

        self._set_status(self._status_text, self._status_color)
        self._set_bottom(self._bottom_text)
        self._refresh_start_button()
        self._apply_float_btn_state()
        if self._bottom_progress_visible:
            self._show_bottom_progress(
                self._bottom_progress_value,
                indeterminate=self._bottom_progress_indeterminate,
            )
        else:
            self._hide_bottom_progress()

    def _build_translate_panel(self):
        outer = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        outer.grid(row=2, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=40)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(3, weight=1)
        hdr.grid_propagate(False)

        self._manual_langs = list(MANUAL_SOURCE_LANGUAGE_OPTIONS)
        src_labels = [label for label, _ in self._manual_langs]
        self._src_lang_codes = {label: code for label, code in self._manual_langs}
        self._src_lang_var = ctk.StringVar(value=src_labels[0])
        self._src_lang_menu = ctk.CTkOptionMenu(
            hdr,
            values=src_labels,
            variable=self._src_lang_var,
            width=132,
            fg_color=BG_SECONDARY,
            button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
        )
        self._src_lang_menu.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=6)

        ctk.CTkButton(
            hdr,
            text="<->",
            width=36,
            height=24,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            command=self._swap_langs,
        ).grid(row=0, column=1, sticky="w", padx=(0, 6), pady=6)

        self._tgt_menu = ctk.CTkOptionMenu(
            hdr,
            values=self._target_lang_labels,
            variable=self._tgt_var,
            fg_color=BG_SECONDARY,
            button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12, weight="bold"),
            width=154,
        )
        self._tgt_menu.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=6)

        ui_lang_labels = [label for label, _ in UI_LANGUAGE_OPTIONS]
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_var = ctk.StringVar(
            value=self._ui_lang_reverse.get(self._ui_lang, ui_lang_labels[0])
        )

        lang_badge = ctk.CTkFrame(
            hdr,
            fg_color="#d5ecfb",
            corner_radius=12,
            border_width=1,
            border_color="#97c4e6",
        )
        lang_badge.grid(row=0, column=4, sticky="e", padx=(8, 10), pady=5)

        ctk.CTkLabel(
            lang_badge,
            text="🌐",
            text_color=ACCENT,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=(8, 5), pady=2)

        self._ui_lang_menu = ctk.CTkOptionMenu(
            lang_badge,
            values=ui_lang_labels,
            variable=self._ui_lang_var,
            command=self._on_ui_lang_selected,
            width=112,
            height=26,
            fg_color="#d5ecfb",
            button_color="#b6daf3",
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=BG_PRIMARY,
            corner_radius=10,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
        )
        self._ui_lang_menu.pack(side="left", padx=(0, 4), pady=2)
        self._tgt_var.trace_add("write", self._on_tgt_lang_change)
        self._on_tgt_lang_change()
        self._src_lang_var.trace_add("write", self._on_src_lang_change)
        self._on_src_lang_change()

        text_row = ctk.CTkFrame(outer, fg_color=BG_PANEL, corner_radius=0)
        text_row.grid(row=1, column=0, sticky="nsew")
        text_row.grid_columnconfigure(0, weight=1)
        text_row.grid_columnconfigure(2, weight=1)
        text_row.grid_rowconfigure(0, weight=1)
        text_row.configure(height=220)

        left = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self._src_input = ctk.CTkTextbox(
            left,
            height=180,
            font=ctk.CTkFont(size=13),
            wrap="word",
            state="disabled",
            fg_color=BG_PANEL,
            corner_radius=0,
            text_color=TEXT_SEC,
            border_width=0,
        )
        self._src_input.grid(row=0, column=0, sticky="nsew", padx=8, pady=(6, 0))
        self._set_source_text("")

        left_bar = ctk.CTkFrame(left, fg_color=BG_SECONDARY, corner_radius=0, height=34)
        left_bar.grid(row=1, column=0, sticky="ew")
        left_bar.grid_propagate(False)

        self._char_label = ctk.CTkLabel(
            left_bar,
            text=self._t("char_count", count=0),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=10),
        )
        self._char_label.pack(side="left", padx=10)

        ctk.CTkButton(
            left_bar,
            text=self._t("manual_input"),
            width=108,
            height=24,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            command=self._open_text_input_popup,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            left_bar,
            text=self._t("clear"),
            width=68,
            height=24,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            command=self._clear_input,
        ).pack(side="right", padx=6)

        self._translate_btn = ctk.CTkButton(
            left_bar,
            text=self._t("translate"),
            width=88,
            height=24,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=8,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
            command=self._translate_manual,
        )
        self._translate_btn.pack(side="right", padx=4)

        ctk.CTkFrame(text_row, width=1, fg_color=DIVIDER).grid(
            row=0,
            column=1,
            sticky="ns",
            pady=6,
        )

        right = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self._tgt_output = ctk.CTkTextbox(
            right,
            height=180,
            font=ctk.CTkFont(size=13),
            wrap="word",
            state="disabled",
            fg_color=BG_PANEL,
            corner_radius=0,
            text_color=TEXT_PRI,
            border_width=0,
        )
        self._tgt_output.grid(row=0, column=0, sticky="nsew", padx=8, pady=(6, 0))

        right_bar = ctk.CTkFrame(right, fg_color=BG_SECONDARY, corner_radius=0, height=34)
        right_bar.grid(row=1, column=0, sticky="ew")
        right_bar.grid_propagate(False)

        ctk.CTkButton(
            right_bar,
            text=self._t("send_to_vrc"),
            width=114,
            height=24,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=8,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
            command=self._send_to_vrc,
        ).pack(side="right", padx=6)

        ctk.CTkButton(
            right_bar,
            text=self._t("copy"),
            width=84,
            height=24,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            command=self._copy_result,
        ).pack(side="right", padx=2)

        social_bar = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=56)
        social_bar.grid(row=2, column=0, sticky="ew")
        social_bar.grid_propagate(False)

        social_center = ctk.CTkFrame(social_bar, fg_color="transparent")
        social_center.pack(expand=True, pady=8)

        github_icon = self._load_social_icon(ICON_GITHUB_FILE)
        qq_icon = self._load_social_icon(ICON_QQ_FILE)
        line_icon = self._load_social_icon(ICON_LINE_FILE)
        sponsor_icon = self._load_social_icon(ICON_SPONSOR_FILE)

        self._add_social_button(
            parent=social_center,
            icon=github_icon,
            fallback_text="Git",
            fg="#2b3137",
            hover="#1f2328",
            command=lambda: self._open_external_url(GITHUB_REPO_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=qq_icon,
            fallback_text="QQ",
            fg="#12B7F5",
            hover="#0F9ED8",
            command=lambda: self._open_external_url(QQ_GROUP_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=line_icon,
            fallback_text="LINE",
            fg="#06C755",
            hover="#04A946",
            command=lambda: self._open_external_url(LINE_GROUP_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=sponsor_icon,
            fallback_text="赞助",
            fg="#D4A638",
            hover="#BC912D",
            command=self._open_sponsor_window,
        )

    def _refresh_start_button(self):
        if self._running:
            self._start_btn.configure(
                text=self._t("stop_listening"),
                state="normal",
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
            )
            return

        self._start_btn.configure(
            text=self._t("start_listening"),
            state="disabled" if self._model_prepare_running else "normal",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        )

    def _float_is_visible(self) -> bool:
        return bool(
            self._float_win
            and self._float_win.winfo_exists()
            and self._float_win.winfo_viewable()
        )

    def _apply_float_btn_state(self):
        if hasattr(self, "_float_btn"):
            self._float_btn.configure(
                text=self._t("floating_shown") if self._float_is_visible() else self._t("floating_hidden")
            )

    # ── 翻訳パネルの補助処理 ─────────────────────────────────────────────

    def _set_source_text(self, text: str, text_color: str | None = None):
        safe = (text or "").strip()
        if len(safe) > 500:
            safe = safe[:500]
        self._src_text = safe

        shown = safe or self._src_placeholder
        color = text_color or (TEXT_PRI if safe else TEXT_SEC)

        self._src_input.configure(state="normal")
        self._src_input.delete("1.0", "end")
        self._src_input.insert("1.0", shown)
        self._src_input.configure(text_color=color, state="disabled")
        char_label = getattr(self, "_char_label", None)
        if char_label is not None:
            try:
                if char_label.winfo_exists():
                    char_label.configure(text=self._t("char_count", count=len(safe)))
            except Exception:
                pass

    def _open_text_input_popup(self, _event=None):
        popup = ctk.CTkToplevel(self)
        popup.title(self._t("manual_input"))
        popup.geometry("460x210")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.grab_set()

        box = ctk.CTkTextbox(
            popup,
            height=92,
            font=ctk.CTkFont(size=13),
            fg_color=BG_PANEL,
            text_color=TEXT_PRI,
        )
        box.pack(fill="x", padx=16, pady=(16, 8))
        if self._src_text:
            box.insert("1.0", self._src_text)

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(2, 12))

        def do_send():
            text = box.get("1.0", "end").strip()
            self._set_source_text(text)
            popup.destroy()
            if text:
                self._translate_manual()

        ctk.CTkButton(
            btn_row,
            text=self._t("apply"),
            width=100,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=do_send,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_row,
            text=self._t("cancel"),
            width=80,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=popup.destroy,
        ).pack(side="right", padx=4)

    def _on_tgt_lang_change(self, *_):
        """翻訳先の状態と入力言語候補を同期する。"""
        tgt_code = self._target_lang_codes.get(self._tgt_var.get(), "ja")
        self._current_tgt_lang = tgt_code  #     on  audio  segment   から安全に参照できるように保持する  

        values = [lbl for lbl, code in self._manual_langs if code == "auto" or code != tgt_code]
        self._src_lang_menu.configure(values=values)
        if self._src_lang_var.get() not in values:
            self._src_lang_var.set(values[0])

    def _on_src_lang_change(self, *_):
        """選択中の入力言語を  スレッドセーフに参照できる形で保持する  """
        label = self._src_lang_var.get()
        code = self._src_lang_codes.get(label, "auto")
        self._current_src_lang = None if code == "auto" else code

    def _swap_langs(self):
        """入出力テキストを入れ替える  """
        src_text = self._src_text
        tgt_text = self._tgt_output.get("1.0", "end").strip()
        self._set_source_text(tgt_text)
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", src_text)
        self._tgt_output.configure(state="disabled")

    def _clear_input(self):
        self._set_source_text("")
        self._last_tgt_text = ""
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.configure(state="disabled")

    def _copy_result(self):
        text = self._tgt_output.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _on_ui_lang_selected(self, selected_label: str):
        new_lang = self._ui_lang_codes.get(selected_label, self._ui_lang)
        if new_lang == self._ui_lang:
            return

        device_name = self._current_device_name()
        restore_float = self._float_is_visible()

        ui_cfg = self._config.setdefault("ui", {})
        ui_cfg["language"] = new_lang
        ui_cfg["language_source"] = "manual"
        config_manager.save_config(self._config)

        self._ui_lang = new_lang
        self.title(self._t("window_title"))
        model_id, _model_revision = self._sensevoice_model_spec()
        if self._running:
            self._status_text = self._t("status_listening")
            self._status_color = SUCCESS
        elif self._status_color == SUCCESS:
            self._status_text = self._t("status_ready")
        if self._model_prepare_running:
            self._bottom_text = self._t("model_downloading")
        elif model_exists(model_id):
            self._bottom_text = self._t("model_ready")

        self._rebuild_ui(device_name=device_name, restore_float=restore_float)

    def _sensevoice_model_spec(self) -> tuple[str, str]:
        asr_cfg = self._config.get("asr", {})
        sensevoice_cfg = asr_cfg.get("sensevoice", {})
        model_id = str(sensevoice_cfg.get("model_id", "iic/SenseVoiceSmall"))
        model_revision = str(sensevoice_cfg.get("model_revision", "master"))
        return model_id, model_revision

    def _maybe_prepare_runtime_model(self):
        if self._model_prepare_running:
            return

        model_id, model_revision = self._sensevoice_model_spec()
        if model_exists(model_id):
            if self._bottom_text == self._t("model_unloaded"):
                self._set_bottom(self._t("model_ready"))
            return

        self._model_prepare_running = True
        self._refresh_start_button()
        self._set_bottom(self._t("model_downloading"))
        self._show_bottom_progress(0.0, indeterminate=True)
        threading.Thread(
            target=self._prepare_runtime_model,
            args=(model_id, model_revision),
            daemon=True,
        ).start()

    def _prepare_runtime_model(self, model_id: str, model_revision: str):
        try:
            ensure_model(
                model_id=model_id,
                model_revision=model_revision,
                progress_callback=lambda event: self.after(0, self._handle_model_progress, event),
            )
        except Exception as exc:
            self.after(0, lambda m=str(exc): self._on_model_prepare_failed(m))
            return
        self.after(0, self._on_model_prepare_ready)

    def _on_model_prepare_ready(self):
        self._model_prepare_running = False
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(self._t("model_ready"))

    def _on_model_prepare_failed(self, message: str):
        self._model_prepare_running = False
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(message)

    def _handle_model_progress(self, event):
        if isinstance(event, dict):
            stage = str(event.get("stage", "")).strip()
            progress_value = event.get("progress")
            progress = float(progress_value) if isinstance(progress_value, (int, float)) else None
            indeterminate = bool(event.get("indeterminate", False))

            if stage == "download_complete":
                self._set_bottom(self._t("model_ready"))
                self._show_bottom_progress(1.0, indeterminate=False)
                return

            if stage in {"download_prepare", "download"}:
                text = self._t("model_downloading")
                if progress is not None:
                    text = f"{text} {progress * 100:.0f}%"
                self._set_bottom(text)
                self._show_bottom_progress(progress, indeterminate=indeterminate)
                return

            if stage == "loading":
                self._set_bottom(self._t("model_loading"))
                self._show_bottom_progress(progress, indeterminate=True)
                return

            if stage == "ready":
                self._set_bottom(self._t("model_ready"))
                self._hide_bottom_progress()
                return

            message = str(event.get("message", "")).strip()
            if message:
                self._set_bottom(message)
                self._show_bottom_progress(progress, indeterminate=indeterminate)
            return

        message = str(event).strip()
        if not message:
            return
        self._set_bottom(message)
        self._show_bottom_progress(None, indeterminate=True)

    def _show_bottom_progress(self, progress: float | None, *, indeterminate: bool):
        self._bottom_progress_visible = True
        self._bottom_progress_indeterminate = indeterminate
        if progress is not None:
            self._bottom_progress_value = max(0.0, min(float(progress), 1.0))

        if not hasattr(self, "_bottom_progress"):
            return

        self._bottom_progress.grid()
        if self._bottom_progress_running:
            self._bottom_progress.stop()
            self._bottom_progress_running = False

        if indeterminate:
            self._bottom_progress.configure(mode="indeterminate")
            self._bottom_progress.start()
            self._bottom_progress_running = True
            return

        self._bottom_progress.configure(mode="determinate")
        self._bottom_progress.set(self._bottom_progress_value if progress is not None else 0.0)

    def _hide_bottom_progress(self):
        self._bottom_progress_visible = False
        self._bottom_progress_indeterminate = False
        self._bottom_progress_value = 0.0

        if not hasattr(self, "_bottom_progress"):
            return

        if self._bottom_progress_running:
            self._bottom_progress.stop()
            self._bottom_progress_running = False
        self._bottom_progress.configure(mode="determinate")
        self._bottom_progress.set(0.0)
        self._bottom_progress.grid_remove()

    def _position_near_float_window(self):
        if not self._float_win or not self._float_win.winfo_exists():
            return
        self.update_idletasks()
        self._float_win.update_idletasks()
        x = self.winfo_x() + self.winfo_width() + 18
        y = self.winfo_y() + 118
        self._float_win.geometry(f"+{x}+{y}")

    @staticmethod
    def _open_external_url(url: str):
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass

    @staticmethod
    def _runtime_base_dirs() -> list[Path]:
        if not getattr(sys, "frozen", False):
            return [Path(__file__).resolve().parents[2]]

        dirs = [Path(sys.executable).resolve().parent]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_path = Path(meipass)
            if meipass_path not in dirs:
                dirs.append(meipass_path)
        return dirs

    @staticmethod
    def _assets_dirs() -> list[Path]:
        dirs = []
        for base in MainWindow._runtime_base_dirs():
            assets_dir = base / "assets"
            if assets_dir.exists():
                dirs.append(assets_dir)
        if dirs:
            return dirs
        return [MainWindow._runtime_base_dirs()[0] / "assets"]

    @staticmethod
    def _icons_dirs() -> list[Path]:
        dirs = []
        for assets_dir in MainWindow._assets_dirs():
            icons_dir = assets_dir / "icons"
            if icons_dir.exists():
                dirs.append(icons_dir)
        if dirs:
            return dirs
        return [MainWindow._assets_dirs()[0] / "icons"]

    @staticmethod
    def _find_asset_file(filename: str) -> Path | None:
        for assets_dir in MainWindow._assets_dirs():
            candidate = assets_dir / filename
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _find_icon_file(filename: str) -> Path | None:
        for icons_dir in MainWindow._icons_dirs():
            candidate = icons_dir / filename
            if candidate.exists():
                return candidate
        return MainWindow._find_asset_file(filename)

    def _set_window_icon(self):
        ico_path = self._find_icon_file(APP_ICON_ICO_FILE)
        if ico_path:
            try:
                self.iconbitmap(default=str(ico_path))
            except Exception:
                pass

        png_path = self._find_icon_file(APP_ICON_PNG_FILE)
        if png_path:
            try:
                self._window_icon = PhotoImage(file=str(png_path))
                self.iconphoto(True, self._window_icon)
            except Exception:
                pass

    def _load_social_icon(self, filename: str) -> ctk.CTkImage | None:
        icon_path = None
        for icons_dir in self._icons_dirs():
            candidate = icons_dir / filename
            if candidate.exists():
                icon_path = candidate
                break
        if icon_path is None:
            return None
        try:
            from PIL import Image
            img = Image.open(icon_path).convert("RGBA")
        except Exception:
            return None

        icon = ctk.CTkImage(light_image=img, dark_image=img, size=(28, 28))
        self._social_icons[filename] = icon
        return icon

    @staticmethod
    def _add_social_button(parent, icon, fallback_text: str, fg: str, hover: str, command):
        ctk.CTkButton(
            parent,
            image=icon,
            text="" if icon else fallback_text,
            width=68,
            height=40,
            corner_radius=10,
            fg_color=fg,
            hover_color=hover,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=command,
        ).pack(side="left", padx=8, pady=8)

    @staticmethod
    def _find_sponsor_image() -> Path | None:
        for assets_dir in MainWindow._assets_dirs():
            for name in SPONSOR_IMAGE_CANDIDATES:
                p = assets_dir / name
                if p.exists():
                    return p

            # 指定名の画像がない場合は    assets   直下で最初に見つかった画像を使う  
            if not assets_dir.exists():
                continue
            for p in sorted(assets_dir.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    return p
        return None

    def _open_sponsor_window(self):
        image_path = self._find_sponsor_image()
        if image_path is None:
            messagebox.showinfo(
                "赞助入口",
                "未找到赞助收款图。\n请把图片放到 assets/sponsor_qr.png（或 sponsor_qr.jpg）后重试。",
            )
            return

        if self._sponsor_win and self._sponsor_win.winfo_exists():
            self._sponsor_win.deiconify()
            self._sponsor_win.lift()
            return

        try:
            from PIL import Image
        except Exception:
            messagebox.showerror("赞助入口", "缺少 Pillow 依赖，无法显示赞助图片。")
            return

        img = Image.open(image_path)
        img.thumbnail((520, 520))
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

        self._sponsor_win = ctk.CTkToplevel(self)
        self._sponsor_win.title("赞助入口")
        self._sponsor_win.geometry(f"{img.size[0] + 40}x{img.size[1] + 90}")
        self._sponsor_win.resizable(False, False)
        self._sponsor_win.attributes("-topmost", True)
        self._sponsor_win.configure(fg_color=BG_PRIMARY)

        ctk.CTkLabel(
            self._sponsor_win,
            text="感谢支持：酒寄 みお",
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(12, 8))

        img_label = ctk.CTkLabel(self._sponsor_win, text="", image=ctk_img)
        img_label.image = ctk_img
        img_label.pack(padx=12, pady=(0, 12))

    @staticmethod
    def _is_vrchat_running() -> bool:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq VRChat.exe"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return "VRChat.exe" in out
        except Exception:
            return False

    def _ensure_receiver_started(self):
        if self._receiver is not None:
            return
        osc_cfg = self._config.get("osc", {})
        self._receiver = VRCOSCReceiver(
            on_message=self._on_incoming_chatbox,
            port=osc_cfg.get("receive_port", 9001),
            own_messages=self._own_msgs,
            on_own_message=self._on_own_chatbox_echo,
        )
        self._receiver.start()

    def _ensure_translator_ready(self) -> bool:
        if self._translator is not None:
            return True

        try:
            self._translator = create_translator(self._config)
            return True
        except ValueError:
            messagebox.showwarning(
                self._t("api_missing_title"),
                self._t("api_missing_message"),
            )
            return False
        except Exception as e:
            messagebox.showerror(self._t("translation_init_failed_title"), str(e))
            return False

    def _get_output_format(self) -> str:
        return normalize_output_format(
            self._config.get("translation", {}).get("output_format")
        )

    def _listening_requires_translation(self) -> bool:
        return self._get_output_format() != "original_only"

    def _streaming_config(self) -> dict:
        return self._config.get("asr", {}).get("streaming", {})

    def _create_streaming_merger(self) -> StreamingMerger:
        streaming_cfg = self._streaming_config()
        return StreamingMerger(
            stable_repeats=streaming_cfg.get("partial_stability_hits", 2)
        )

    def _reset_streaming_state(self):
        self._partial_generation += 1
        with self._partial_worker_lock:
            self._partial_worker_busy = False
        with self._merge_lock:
            self._partial_merger.reset()

    def _on_audio_chunk(self, audio):
        if not self._running:
            return

        generation = self._partial_generation
        asr_lang = self._current_src_lang
        with self._partial_worker_lock:
            if self._partial_worker_busy:
                return
            self._partial_worker_busy = True

        threading.Thread(
            target=self._process_partial_audio_chunk,
            args=(audio, asr_lang, generation),
            daemon=True,
        ).start()

    def _process_partial_audio_chunk(self, audio, asr_lang, generation: int):
        try:
            text = self._asr.transcribe(audio, language=asr_lang, is_final=False)
            if not text or generation != self._partial_generation or not self._running:
                return

            with self._merge_lock:
                merged = self._partial_merger.ingest_partial(text)
            if merged and generation == self._partial_generation and self._running:
                self.after(0, lambda t=merged, g=generation: self._show_partial_text(t, g))
        except Exception:
            pass
        finally:
            with self._partial_worker_lock:
                self._partial_worker_busy = False

    def _show_partial_text(self, text: str, generation: int):
        if not self._running or generation != self._partial_generation or not text:
            return
        self._set_source_text(text, text_color=TEXT_SEC)

    def _process_final_audio_segment(self, audio, asr_lang):
        with self._final_worker_lock:
            if not self._running:
                return
            try:
                self.after(0, lambda: self._set_status(self._t("translating"), ACCENT))
                text = self._asr.transcribe(audio, language=asr_lang, is_final=True)
                with self._merge_lock:
                    text = self._partial_merger.ingest_final(text)
                if not text:
                    return

                src_lang = asr_lang if asr_lang else detect_language(text)
                tgt_lang = self._current_tgt_lang
                fmt = self._get_output_format()

                self.after(0, lambda t=text: self._set_source_text(t))

                if fmt == "original_only":
                    chatbox_text = text
                else:
                    translated = self._translator.translate(text, src_lang, tgt_lang)
                    if fmt == "translated_only":
                        chatbox_text = translated
                    elif fmt == "original_with_translated":
                        chatbox_text = f"{text}（{translated}）"
                    else:
                        chatbox_text = f"{translated}（{text}）"
                    self.after(0, lambda t=translated: self._show_tgt(t))

                sent = self._sender.send_chatbox(chatbox_text)
                self._own_msgs.add(sent)
            except Exception as exc:
                error_text = str(exc).strip() or exc.__class__.__name__
                self.after(
                    0,
                    lambda m=error_text[:120]: self._set_bottom(f"语音处理失败：{m}"),
                )
            finally:
                if self._running:
                    self.after(0, lambda: self._set_status(self._t("status_listening"), SUCCESS))

    def _on_own_chatbox_echo(self, text: str):
        self._osc_echo_capable = True
        self._last_own_chatbox_echo_text = text
        self._last_own_chatbox_echo_time = time.time()

    def _check_vrc_send_ack(self, sent_text: str):
        # シーンが対応していれば  VRChat は     chatbox  input   をエコーバックする  
        if not self._osc_echo_capable:
            return
        if self._last_own_chatbox_echo_text == sent_text and (time.time() - self._last_own_chatbox_echo_time) < 2.0:
            return

    def _send_to_vrc(self):
        """翻訳結果を VRC チャットボックスへ送信する    出力形式の設定も適用する  """
        tgt_text = self._last_tgt_text
        src_text = self._src_text
        if not tgt_text and not src_text:
            return
        if not self._is_vrchat_running():
            messagebox.showwarning(
                self._t("game_not_running_title"),
                self._t("game_not_running_message"),
            )
            return

        # 音声認識モードと同じ出力形式を適用する  
        fmt = self._get_output_format()
        if fmt == "original_only":
            chatbox_text = src_text or tgt_text
        elif fmt == "translated_only":
            chatbox_text = tgt_text or src_text
        elif fmt == "original_with_translated":
            chatbox_text = f"{src_text}（{tgt_text}）" if src_text and tgt_text else src_text or tgt_text
        else:
            chatbox_text = f"{tgt_text}（{src_text}）" if src_text and tgt_text else tgt_text or src_text

        self._ensure_receiver_started()
        if self._sender is None:
            osc_cfg = self._config.get("osc", {})
            self._sender = VRCOSCSender(
                host=osc_cfg.get("send_host", "127.0.0.1"),
                port=osc_cfg.get("send_port", 9000),
            )
        try:
            sent = self._sender.send_chatbox(chatbox_text)
            self._own_msgs.add(sent)
            if self._running and self._osc_echo_capable:
                self.after(1400, lambda s=sent: self._check_vrc_send_ack(s))
        except Exception as e:
            messagebox.showerror(self._t("send_failed_title"), str(e))

    def _translate_manual(self):
        """手動入力したテキストを翻訳する  """
        src_text = self._src_text
        if not src_text:
            return
        if self._translating:
            return

        # 言語コードを決定する  
        src_code = self._src_lang_codes.get(self._src_lang_var.get(), "auto")
        if src_code == "auto":
            src_code = detect_language(src_text)
        tgt_code = self._target_lang_codes.get(self._tgt_var.get(), "ja")

        if not self._ensure_translator_ready():
            return

        self._translating = True
        self._translate_btn.configure(state="disabled", text=self._t("translating"))
        threading.Thread(
            target=self._do_translate,
            args=(src_text, src_code, tgt_code),
            daemon=True,
        ).start()

    def _do_translate(self, text: str, src_lang: str, tgt_lang: str):
        """バックグラウンドスレッドで翻訳を実行する  """
        try:
            result = self._translator.translate(text, src_lang, tgt_lang)
            self.after(0, lambda: self._show_tgt(result))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._show_tgt(f"[Error] {msg}"))
        finally:
            self.after(0, self._reset_translate_btn)

    def _show_tgt(self, text: str):
        # 正常な翻訳結果のみを保持し  エラー文字列は保存しない  
        if not text.startswith("[Error]"):
            self._last_tgt_text = text
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", text)
        self._tgt_output.configure(state="disabled")

    def _reset_translate_btn(self):
        self._translating = False
        self._translate_btn.configure(state="normal", text=self._t("translate"))

    # ── デバイス一覧 ──────────────────────────────────────────────────────

    def _load_devices(self):
        devices = AudioRecorder.list_devices()
        self._devices = {d["name"]: d["index"] for d in devices}
        names = list(self._devices.keys()) or ["默认"]
        self._device_menu.configure(values=names)
        cfg_dev = self._config.get("audio", {}).get("input_device")
        if cfg_dev and cfg_dev in self._devices:
            self._device_var.set(cfg_dev)
        else:
            preferred = self._get_system_default_input(devices, names)
            self._device_var.set(preferred)

    @staticmethod
    def _get_system_default_input(devices: list[dict], names: list[str]) -> str:
        """現在の既定入力デバイス名を返す    可能なら WASAPI を優先する  """
        try:
            default_idx = sd.default.device[0]  # 0 番目は入力デバイス
            if default_idx is not None and default_idx >= 0:
                default_name = sd.query_devices(default_idx)["name"]
                # 重複排除済み一覧の中から  最適な API 側の同名デバイスを探す  
                match = next((d["name"] for d in devices if d["name"] == default_name), None)
                if match and match in names:
                    return match
        except Exception:
            pass
        # フォールバック時は  既知の Windows ループバック系デバイスを避ける  
        _SKIP = ("microsoft 映射", "microsoft sound mapper", "立体声混音", "stereo mix")
        return next(
            (n for n in names if not any(s in n.lower() for s in _SKIP)),
            names[0],
        )

    # ── 音声リスニングの開始と停止 ────────────────────────────────────────

    def _toggle_listening(self):
        if self._running:
            self._stop()
            return

        if self._model_prepare_running:
            self._set_bottom(self._t("model_download_wait"))
            return

        model_id, _model_revision = self._sensevoice_model_spec()
        if not model_exists(model_id):
            self._maybe_prepare_runtime_model()
            return

        self._start()

    def _start(self):
        self._set_status(self._t("starting"), ACCENT)
        self._start_btn.configure(state="disabled", text=self._t("starting"))
        self._reset_streaming_state()
        # ワーカースレッドへ渡す前に  Tkinter 変数はメインスレッドで読み出しておく  
        dev_name = self._device_var.get()
        dev_idx = self._devices.get(dev_name)
        threading.Thread(target=self._init_and_run, args=(dev_idx,), daemon=True).start()

    def _init_and_run(self, dev_idx):
        try:
            if self._listening_requires_translation():
                try:
                    self._translator = create_translator(self._config)
                except ValueError:
                    raise RuntimeError(self._t("listen_requires_api"))
            else:
                self._translator = None
            self._asr.load(progress_callback=lambda event: self.after(0, self._handle_model_progress, event))
            osc_cfg = self._config.get("osc", {})
            self._sender = VRCOSCSender(
                host=osc_cfg.get("send_host", "127.0.0.1"),
                port=osc_cfg.get("send_port", 9000),
            )
            self._ensure_receiver_started()
            audio_cfg = self._config.get("audio", {})
            streaming_cfg = self._streaming_config()
            self._recorder = AudioRecorder(
                on_segment=self._on_audio_segment,
                on_chunk=self._on_audio_chunk,
                sample_rate=audio_cfg.get("sample_rate", 16000),
                frame_duration_ms=audio_cfg.get("frame_duration_ms", 30),
                vad_sensitivity=audio_cfg.get("vad_sensitivity", 2),
                silence_threshold_s=audio_cfg.get("vad_silence_threshold", 0.8),
                vad_speech_ratio=audio_cfg.get("vad_speech_ratio", 0.72),
                vad_activation_threshold_s=audio_cfg.get("vad_activation_threshold_s", 0.24),
                vad_min_rms=audio_cfg.get("vad_min_rms", 0.012),
                min_segment_s=audio_cfg.get("min_segment_s", 0.45),
                partial_min_speech_s=audio_cfg.get("partial_min_speech_s", 0.45),
                max_segment_s=audio_cfg.get("max_segment_s", 12.0),
                input_device=dev_idx,
                on_vad_state=self._on_vad_state,
                chunk_interval_ms=streaming_cfg.get("chunk_interval_ms", 250),
                chunk_window_s=streaming_cfg.get("chunk_window_s", 1.6),
                ring_buffer_s=streaming_cfg.get("ring_buffer_s", 4.0),
                recent_speech_hold_s=streaming_cfg.get("recent_speech_hold_s", 0.8),
            )
            self._recorder.start()
            self._running = True
            self.after(0, self._on_started)
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._on_start_error(msg))

    def _stop(self):
        self._running = False
        self._reset_streaming_state()
        if self._recorder:
            self._recorder.stop()
            self._recorder = None
        if self._receiver:
            self._receiver.stop()
            self._receiver = None
        self._sender = None
        self._translator = None
        self._set_status(self._t("status_stopped"), DANGER)
        self._refresh_start_button()
        if not self._model_prepare_running:
            self._hide_bottom_progress()
            self._set_bottom(self._t("model_ready"))

    def _on_started(self):
        self._set_status(self._t("status_listening"), SUCCESS)
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(self._t("model_ready"))

    def _on_start_error(self, msg: str):
        self._set_status(self._t("status_error"), DANGER)
        self._refresh_start_button()
        self._hide_bottom_progress()
        messagebox.showerror(self._t("listen_start_failed_title"), msg)

    def _on_vad_state(self, in_speech: bool):
        """録音スレッドから呼ばれ  VAD 状態の変化を反映する  """
        if in_speech:
            self.after(0, lambda: self._set_status(self._t("status_speaking"), ACCENT))
        else:
            self.after(0, lambda: self._set_status(self._t("status_listening"), SUCCESS))

    # ── 音声セグメント処理  ログ出力はせず VRC 送信のみ行う ─────────────────

    def _on_audio_segment(self, audio):
        if not self._running:
            return
        self._partial_generation += 1
        asr_lang = self._current_src_lang
        threading.Thread(
            target=self._process_final_audio_segment,
            args=(audio, asr_lang),
            daemon=True,
        ).start()

    # ── 受信チャットボックス  逆翻訳してフローティングウィンドウへ表示 ─────

    def _on_incoming_chatbox(self, text: str):
        try:
            src_lang = detect_language(text)
            if src_lang == "zh" or self._translator is None:
                self.after(0, lambda: self._show_incoming(text, None))
                return
            translated = self._translator.translate(text, src_lang, "zh")
            self.after(0, lambda: self._show_incoming(text, translated))
        except Exception:
            pass

    def _show_incoming(self, original: str, translated: str | None):
        if self._float_win and self._float_win.winfo_exists():
            self._float_win.add_message(original, translated)

    # ── フローティングウィンドウ ──────────────────────────────────────────

    def _toggle_float(self):
        ui_cfg = self._config.setdefault("ui", {})
        opacity = float(ui_cfg.get("floating_window_opacity", 0.92))
        if self._float_win and self._float_win.winfo_exists():
            if self._float_is_visible():
                self._float_win.hide()
                ui_cfg["show_floating_window"] = False
            else:
                self._float_win.show()
                self._position_near_float_window()
                ui_cfg["show_floating_window"] = True
        else:
            self._float_win = FloatingWindow(
                self,
                ui_language=self._ui_lang,
                opacity=opacity,
                on_opacity_change=self._on_float_opacity_changed,
            )
            self._position_near_float_window()
            ui_cfg["show_floating_window"] = True
        config_manager.save_config(self._config)
        self._apply_float_btn_state()

    def _on_float_opacity_changed(self, opacity: float):
        ui_cfg = self._config.setdefault("ui", {})
        ui_cfg["floating_window_opacity"] = round(float(opacity), 2)
        config_manager.save_config(self._config)

    # ── 設定 ──────────────────────────────────────────────────────────────

    def _open_settings(self):
        SettingsWindow(self, self._config, on_save=self._on_config_saved)

    def _current_device_name(self) -> str | None:
        if hasattr(self, "_device_var"):
            return self._device_var.get()
        return None

    def _rebuild_ui(self, device_name: str | None = None, restore_float: bool = False):
        source_text = self._src_text
        target_text = self._last_tgt_text

        if self._float_win and self._float_win.winfo_exists():
            try:
                self._float_win.destroy()
            except Exception:
                pass
        self._float_win = None

        for child in list(self.winfo_children()):
            child.destroy()

        self._char_label = None
        self._src_input = None
        self._tgt_output = None
        self._bottom_bar = None
        self._bottom_progress = None
        self._social_icons.clear()
        self._src_placeholder = self._t("source_placeholder")
        self._build()
        self._load_devices()

        if device_name and device_name in getattr(self, "_devices", {}):
            self._device_var.set(device_name)

        self._set_source_text(source_text)
        if target_text:
            self._show_tgt(target_text)
        else:
            self._last_tgt_text = ""
            self._tgt_output.configure(state="normal")
            self._tgt_output.delete("1.0", "end")
            self._tgt_output.configure(state="disabled")

        if restore_float:
            ui_cfg = self._config.setdefault("ui", {})
            self._float_win = FloatingWindow(
                self,
                ui_language=self._ui_lang,
                opacity=float(ui_cfg.get("floating_window_opacity", 0.92)),
                on_opacity_change=self._on_float_opacity_changed,
            )
            self._position_near_float_window()
            ui_cfg["show_floating_window"] = True
        self._apply_float_btn_state()

    def _maybe_show_osc_guide(self):
        ui_cfg = self._config.setdefault("ui", {})
        if ui_cfg.get("osc_guide_seen"):
            return
        ui_cfg["osc_guide_seen"] = True
        config_manager.save_config(self._config)
        self._open_osc_guide()

    def _guide_pages(self) -> list[dict[str, object]]:
        return [
            {
                "title": self._t("guide_step_1_title"),
                "body": self._t("guide_step_1_body"),
                "path": ["Action Menu", "Options"],
            },
            {
                "title": self._t("guide_step_2_title"),
                "body": self._t("guide_step_2_body"),
                "path": ["Options", "OSC"],
            },
            {
                "title": self._t("guide_step_3_title"),
                "body": self._t("guide_step_3_body"),
                "path": ["OSC", "Enabled"],
            },
        ]

    def _open_osc_guide(self):
        pages = self._guide_pages()
        if not pages:
            return

        if getattr(self, "_guide_win", None) and self._guide_win.winfo_exists():
            self._guide_win.deiconify()
            self._guide_win.lift()
            self._render_guide_page()
            return

        self._guide_page_index = 0
        self._guide_win = ctk.CTkToplevel(self)
        self._guide_win.title(self._t("guide_title"))
        self._guide_win.geometry("520x430")
        self._guide_win.resizable(False, False)
        self._guide_win.attributes("-topmost", True)
        self._guide_win.grab_set()
        self._guide_win.configure(fg_color=BG_PRIMARY)

        outer = ctk.CTkFrame(self._guide_win, fg_color=BG_PRIMARY)
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        self._guide_title_label = ctk.CTkLabel(
            outer,
            text=self._t("guide_title"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self._guide_title_label.pack(anchor="w", pady=(0, 4))

        self._guide_subtitle_label = ctk.CTkLabel(
            outer,
            text=self._t("guide_subtitle"),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=470,
            font=ctk.CTkFont(size=12),
        )
        self._guide_subtitle_label.pack(anchor="w", pady=(0, 14))

        card = ctk.CTkFrame(outer, fg_color="#0b5960", corner_radius=20)
        card.pack(fill="both", expand=True)

        self._guide_page_label = ctk.CTkLabel(
            card,
            text="",
            text_color="#d4fbff",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._guide_page_label.pack(anchor="w", padx=22, pady=(18, 10))

        self._guide_step_title_label = ctk.CTkLabel(
            card,
            text="",
            text_color="#ffffff",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self._guide_step_title_label.pack(anchor="w", padx=22)

        self._guide_step_body_label = ctk.CTkLabel(
            card,
            text="",
            text_color="#d4fbff",
            justify="left",
            wraplength=430,
            font=ctk.CTkFont(size=13),
        )
        self._guide_step_body_label.pack(anchor="w", padx=22, pady=(10, 18))

        self._guide_path_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._guide_path_frame.pack(fill="x", padx=20)

        self._guide_footer_label = ctk.CTkLabel(
            card,
            text=self._t("guide_footer"),
            text_color="#b7eef3",
            justify="left",
            wraplength=430,
            font=ctk.CTkFont(size=12),
        )
        self._guide_footer_label.pack(anchor="w", padx=22, pady=(18, 22))

        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.pack(fill="x", pady=(14, 0))

        self._guide_prev_btn = ctk.CTkButton(
            nav,
            text=self._t("guide_prev"),
            width=90,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._guide_prev,
        )
        self._guide_prev_btn.pack(side="left")

        self._guide_next_btn = ctk.CTkButton(
            nav,
            text=self._t("guide_next"),
            width=90,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=10,
            text_color="#ffffff",
            command=self._guide_next,
        )
        self._guide_next_btn.pack(side="right")

        self._render_guide_page()

    def _render_guide_page(self):
        pages = self._guide_pages()
        total = len(pages)
        index = max(0, min(getattr(self, "_guide_page_index", 0), total - 1))
        page = pages[index]

        self._guide_page_label.configure(
            text=self._t("guide_page", current=index + 1, total=total)
        )
        self._guide_step_title_label.configure(text=str(page["title"]))
        self._guide_step_body_label.configure(text=str(page["body"]))

        for child in self._guide_path_frame.winfo_children():
            child.destroy()
        for i, item in enumerate(page["path"]):
            ctk.CTkLabel(
                self._guide_path_frame,
                text=str(item),
                fg_color="#19b8c3" if i == len(page["path"]) - 1 else "#083f45",
                text_color="#ffffff",
                corner_radius=16,
                padx=12,
                pady=6,
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left", padx=(2, 8), pady=(0, 4))

        self._guide_prev_btn.configure(state="normal" if index > 0 else "disabled")
        self._guide_next_btn.configure(
            text=self._t("guide_done") if index == total - 1 else self._t("guide_next")
        )

    def _guide_prev(self):
        self._guide_page_index = max(0, getattr(self, "_guide_page_index", 0) - 1)
        self._render_guide_page()

    def _guide_next(self):
        total = len(self._guide_pages())
        if getattr(self, "_guide_page_index", 0) >= total - 1:
            if self._guide_win and self._guide_win.winfo_exists():
                self._guide_win.destroy()
            return
        self._guide_page_index += 1
        self._render_guide_page()

    def _on_config_saved(self, new_cfg: dict):
        was_running = self._running
        device_name = self._current_device_name()
        restore_float = self._float_is_visible()
        if was_running:
            self._set_bottom(self._t("settings_saved_reloading"))
            self._set_status(self._t("status_restarting"), ACCENT)
            self._stop()

        self._config = new_cfg
        self._ui_lang = get_ui_language(new_cfg)
        self.title(self._t("window_title"))
        self._asr = create_asr(new_cfg)
        self._translator = None
        self._osc_echo_capable = False
        with self._merge_lock:
            self._partial_merger = self._create_streaming_merger()
        self._reset_streaming_state()

        self._rebuild_ui(device_name=device_name, restore_float=restore_float)
        self.after(120, self._maybe_prepare_runtime_model)
        if was_running:
            self.after(100, self._start)
        else:
            self._set_bottom(self._t("settings_saved"))

    # ── 補助処理 ──────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "white"):
        self._status_text = text
        self._status_color = color
        if hasattr(self, "_status_label"):
            self._status_label.configure(text=text, text_color=color)

    def _set_bottom(self, text: str):
        self._bottom_text = text
        if hasattr(self, "_bottom_bar"):
            self._bottom_bar.configure(text=text)
