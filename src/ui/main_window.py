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
from src.audio.recorder import AudioRecorder
from src.asr.factory import create_asr
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

# 手動翻訳で選択可能な言語
MANUAL_LANGS = [
    ("检测语言", "auto"),
    ("中文",     "zh"),
    ("日语",     "ja"),
    ("英语",     "en"),
    ("韩语",     "ko"),
]

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class MainWindow(ctk.CTk):
    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self.title("Mio Translator")
        # 旧サイズ 620x560 から、幅を 20% 拡張し高さを 30% 縮小している。
        self.geometry("744x400")
        self.minsize(620, 320)
        self.configure(fg_color=BG_PRIMARY)

        # 起動時に必要な主要オブジェクト。
        self._recorder: AudioRecorder | None = None
        self._asr = create_asr(config)
        self._translator = None
        self._sender: VRCOSCSender | None = None
        self._receiver: VRCOSCReceiver | None = None
        self._own_msgs: set[str] = set()
        self._translating = False
        self._src_placeholder = "点击输入文字…"
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
        self._current_src_lang: str | None = None  # `None` は自動判定を表す。
        self._float_win: FloatingWindow | None = None
        self._sponsor_win: ctk.CTkToplevel | None = None
        self._social_icons: dict[str, ctk.CTkImage] = {}
        self._window_icon: PhotoImage | None = None

        self._set_window_icon()

        self._build()
        self._load_devices()

    # ── UI 構築 ────────────────────────────────────────────────────────────

    def _build(self):
        # ── 上部バー  タイトルと主要操作をまとめる ─────────────────────────
        top = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_TOP)
        top.pack(fill="x")

        ctk.CTkLabel(
            top, text="制作者：VRC玩家 酒寄 みお ｜ 开源项目，禁止收费",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_PRI,
        ).pack(side="left", padx=14, pady=7)

        # 右側の操作ボタン群
        ctk.CTkButton(
            top, text="⚙ 设置", width=76,
            fg_color=GLASS_BG, hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            corner_radius=10, text_color=TEXT_PRI,
            command=self._open_settings,
        ).pack(side="right", padx=6, pady=5)

        self._float_btn = ctk.CTkButton(
            top, text="悬浮窗 ▼", width=88,
            fg_color=GLASS_BG, hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            corner_radius=10, text_color=TEXT_PRI,
            command=self._toggle_float,
        )
        self._float_btn.pack(side="right", padx=2, pady=5)

        self._start_btn = ctk.CTkButton(
            top, text="▶ 开始监听", width=110,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            corner_radius=10, text_color="#ffffff",
            command=self._toggle_listening,
        )
        self._start_btn.pack(side="right", padx=6, pady=5)

        self._status_label = ctk.CTkLabel(
            top, text="● 就绪", text_color=SUCCESS,
            font=ctk.CTkFont(size=12),
        )
        self._status_label.pack(side="right", padx=8)

        # ── マイク設定と翻訳先設定を 1 行にまとめる ───────────────────────
        bar = ctk.CTkFrame(self, fg_color=BG_SECONDARY, corner_radius=0)
        bar.pack(fill="x")

        ctk.CTkLabel(
            bar, text="麦克风：", text_color=TEXT_SEC, font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(12, 2), pady=5)
        self._device_var = ctk.StringVar()
        self._device_menu = ctk.CTkOptionMenu(
            bar, variable=self._device_var, values=["加载中…"], width=200,
            fg_color=GLASS_BG, button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER, corner_radius=8,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=11),
        )
        self._device_menu.pack(side="left", padx=(0, 16), pady=5)

        ctk.CTkLabel(
            bar, text="翻译至：", text_color=TEXT_SEC, font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 2))
        self._tgt_var = ctk.StringVar(
            value=self._config.get("translation", {}).get("target_language", "ja")
        )
        for lbl, code in [("日语", "ja"), ("英语", "en"), ("中文", "zh"), ("韩语", "ko")]:
            ctk.CTkRadioButton(
                bar, text=lbl, variable=self._tgt_var, value=code,
                text_color=TEXT_PRI, font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=5, pady=5)

        # ── Google 翻訳風の手動入力パネル ─────────────────────────────────
        self._build_translate_panel()

        # ── ステータスバー ────────────────────────────────────────────────
        self._bottom_bar = ctk.CTkLabel(
            self, text="未加载模型", font=ctk.CTkFont(size=10), text_color=TEXT_SEC,
        )
        self._bottom_bar.pack(side="bottom", pady=2)

    def _build_translate_panel(self):
        """Google 翻訳風の左右 2 ペイン翻訳パネルを構築する。"""
        outer = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # ── 言語ヘッダー行 ────────────────────────────────────────────────
        hdr = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # 入力言語のドロップダウン
        self._manual_langs = MANUAL_LANGS[:]
        src_labels = [l for l, _ in self._manual_langs]
        self._src_lang_codes = {l: c for l, c in self._manual_langs}
        self._src_lang_var = ctk.StringVar(value=src_labels[0])
        self._src_lang_menu = ctk.CTkOptionMenu(
            hdr, values=src_labels, variable=self._src_lang_var, width=130,
            fg_color=BG_SECONDARY, button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER, corner_radius=6,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=12),
        )
        self._src_lang_menu.pack(side="left", padx=8)

        # 入出力言語の入れ替えボタン
        ctk.CTkButton(
            hdr, text="⇄", width=32, height=24,
            fg_color="transparent", hover_color=GLASS_HOVER,
            corner_radius=6, text_color=TEXT_SEC,
            command=self._swap_langs,
        ).pack(side="left", padx=4)

        # 出力言語ラベル。  上部の翻訳先ラジオと連動する。
        self._tgt_lang_label = ctk.CTkLabel(
            hdr, text="日语", text_color=TEXT_PRI, font=ctk.CTkFont(size=12),
        )
        self._tgt_lang_label.pack(side="left", padx=12)
        self._tgt_var.trace_add("write", self._on_tgt_lang_change)
        self._on_tgt_lang_change()
        self._src_lang_var.trace_add("write", self._on_src_lang_change)
        self._on_src_lang_change()

        # ── テキストエリア行 ──────────────────────────────────────────────
        text_row = ctk.CTkFrame(outer, fg_color=BG_PANEL, corner_radius=0)
        text_row.pack(fill="both", expand=True)

        # 入力エリア
        left = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        left.pack(side="left", fill="both", expand=True)

        self._src_input = ctk.CTkTextbox(
            left,
            font=ctk.CTkFont(size=13), wrap="word",
            state="disabled",
            fg_color=BG_PANEL, corner_radius=0,
            text_color=TEXT_SEC, border_width=0,
        )
        self._src_input.pack(fill="both", expand=True, padx=8, pady=(6, 0))
        self._set_source_text("")

        # 入力エリア下部のツールバー
        left_bar = ctk.CTkFrame(left, fg_color=BG_SECONDARY, corner_radius=0, height=30)
        left_bar.pack(fill="x")
        left_bar.pack_propagate(False)

        self._char_label = ctk.CTkLabel(
            left_bar, text="0 / 500", text_color=TEXT_SEC, font=ctk.CTkFont(size=10),
        )
        self._char_label.pack(side="left", padx=10)

        ctk.CTkButton(
            left_bar, text="✏ 文本输入", width=80, height=22,
            fg_color=GLASS_BG, hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            corner_radius=6, text_color=TEXT_PRI, font=ctk.CTkFont(size=11),
            command=self._open_text_input_popup,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            left_bar, text="清空", width=44, height=22,
            fg_color="transparent", hover_color=GLASS_HOVER,
            corner_radius=6, text_color=TEXT_SEC, font=ctk.CTkFont(size=11),
            command=self._clear_input,
        ).pack(side="right", padx=6)

        self._translate_btn = ctk.CTkButton(
            left_bar, text="翻译", width=60, height=22,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            corner_radius=8, text_color="#ffffff", font=ctk.CTkFont(size=11),
            command=self._translate_manual,
        )
        self._translate_btn.pack(side="right", padx=4)

        # 中央の区切り線
        ctk.CTkFrame(text_row, width=1, fg_color=DIVIDER).pack(
            side="left", fill="y", pady=6,
        )

        # 出力エリア
        right = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        right.pack(side="left", fill="both", expand=True)

        self._tgt_output = ctk.CTkTextbox(
            right,
            font=ctk.CTkFont(size=13), wrap="word", state="disabled",
            fg_color=BG_PANEL, corner_radius=0,
            text_color=TEXT_PRI, border_width=0,
        )
        self._tgt_output.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        # 出力エリア下部のツールバー
        right_bar = ctk.CTkFrame(right, fg_color=BG_SECONDARY, corner_radius=0, height=30)
        right_bar.pack(fill="x")
        right_bar.pack_propagate(False)

        ctk.CTkButton(
            right_bar, text="发送到VRC", width=86, height=22,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            corner_radius=8, text_color="#ffffff", font=ctk.CTkFont(size=11),
            command=self._send_to_vrc,
        ).pack(side="right", padx=6)

        ctk.CTkButton(
            right_bar, text="复制", width=44, height=22,
            fg_color="transparent", hover_color=GLASS_HOVER,
            corner_radius=6, text_color=TEXT_SEC, font=ctk.CTkFont(size=11),
            command=self._copy_result,
        ).pack(side="right", padx=2)

        # 下部のアイコンボタン列。  全体の高さは変えず、本文領域だけを圧縮する。
        social_bar = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=58)
        social_bar.pack(fill="x")
        social_bar.pack_propagate(False)

        social_center = ctk.CTkFrame(social_bar, fg_color="transparent")
        social_center.pack(expand=True)

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
        if hasattr(self, "_char_label"):
            self._char_label.configure(text=f"{len(safe)} / 500")

    def _open_text_input_popup(self, _event=None):
        popup = ctk.CTkToplevel(self)
        popup.title("文本输入")
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
            text="发送并翻译",
            width=100,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=do_send,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_row,
            text="关闭",
            width=80,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=popup.destroy,
        ).pack(side="right", padx=4)

    def _on_tgt_lang_change(self, *_):
        """翻訳先ラジオと出力言語ラベルを同期する。"""
        labels = {"ja": "日语", "en": "英语", "zh": "中文", "ko": "韩语"}
        tgt_code = self._tgt_var.get()
        self._current_tgt_lang = tgt_code  # `_on_audio_segment` から安全に参照できるように保持する。
        self._tgt_lang_label.configure(text=labels.get(tgt_code, ""))

        values = [lbl for lbl, code in self._manual_langs if code == "auto" or code != tgt_code]
        self._src_lang_menu.configure(values=values)
        if self._src_lang_var.get() not in values:
            self._src_lang_var.set(values[0])

    def _on_src_lang_change(self, *_):
        """選択中の入力言語を、スレッドセーフに参照できる形で保持する。"""
        label = self._src_lang_var.get()
        code = self._src_lang_codes.get(label, "auto")
        self._current_src_lang = None if code == "auto" else code

    def _swap_langs(self):
        """入出力テキストを入れ替える。"""
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

            # 指定名の画像がない場合は、`assets` 直下で最初に見つかった画像を使う。
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
            messagebox.showwarning("API 未配置", "API 未配置，请先在设置中填写 API Key 后再翻译。")
            return False
        except Exception as e:
            messagebox.showerror("翻译初始化失败", str(e))
            return False

    def _get_output_format(self) -> str:
        return self._config.get("translation", {}).get("output_format", "ja(zh)")

    def _listening_requires_translation(self) -> bool:
        return self._get_output_format() != "zh_only"

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
                self.after(0, lambda: self._set_status("识别中…", ACCENT))
                text = self._asr.transcribe(audio, language=asr_lang, is_final=True)
                with self._merge_lock:
                    text = self._partial_merger.ingest_final(text)
                if not text:
                    return

                src_lang = asr_lang if asr_lang else detect_language(text)
                tgt_lang = self._current_tgt_lang
                fmt = self._get_output_format()

                self.after(0, lambda t=text: self._set_source_text(t))

                if fmt == "zh_only":
                    chatbox_text = text
                else:
                    translated = self._translator.translate(text, src_lang, tgt_lang)
                    if fmt == "ja_only":
                        chatbox_text = translated
                    elif fmt == "zh(ja)":
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
                    self.after(0, lambda: self._set_status("● 监听中…", SUCCESS))

    def _on_own_chatbox_echo(self, text: str):
        self._last_own_chatbox_echo_text = text
        self._last_own_chatbox_echo_time = time.time()

    def _check_vrc_send_ack(self, sent_text: str):
        # シーンが対応していれば、VRChat は `/chatbox/input` をエコーバックする。
        if self._last_own_chatbox_echo_text == sent_text and (time.time() - self._last_own_chatbox_echo_time) < 2.0:
            return
        messagebox.showwarning("当前场景不支持发送消息", "当前场景不支持发送消息，或聊天框功能已被禁用。")

    def _send_to_vrc(self):
        """翻訳結果を VRC チャットボックスへ送信する。  出力形式の設定も適用する。"""
        tgt_text = self._last_tgt_text
        src_text = self._src_text
        if not tgt_text and not src_text:
            return
        if not self._is_vrchat_running():
            messagebox.showwarning("游戏未运行", "游戏未运行，请先启动 VRChat 后再发送消息。")
            return

        # 音声認識モードと同じ出力形式を適用する。
        fmt = self._config.get("translation", {}).get("output_format", "ja(zh)")
        if fmt == "zh_only":
            chatbox_text = src_text or tgt_text
        elif fmt == "ja_only":
            chatbox_text = tgt_text or src_text
        elif fmt == "zh(ja)":
            chatbox_text = f"{src_text}（{tgt_text}）" if src_text and tgt_text else src_text or tgt_text
        else:  # `ja(zh)`
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
            if self._running:
                self.after(1400, lambda s=sent: self._check_vrc_send_ack(s))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def _translate_manual(self):
        """手動入力したテキストを翻訳する。"""
        src_text = self._src_text
        if not src_text:
            return
        if self._translating:
            return

        # 言語コードを決定する。
        src_code = self._src_lang_codes.get(self._src_lang_var.get(), "auto")
        if src_code == "auto":
            src_code = detect_language(src_text)
        tgt_code = self._tgt_var.get()

        if not self._ensure_translator_ready():
            return

        self._translating = True
        self._translate_btn.configure(state="disabled", text="翻译中…")
        threading.Thread(
            target=self._do_translate,
            args=(src_text, src_code, tgt_code),
            daemon=True,
        ).start()

    def _do_translate(self, text: str, src_lang: str, tgt_lang: str):
        """バックグラウンドスレッドで翻訳を実行する。"""
        try:
            result = self._translator.translate(text, src_lang, tgt_lang)
            self.after(0, lambda: self._show_tgt(result))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._show_tgt(f"[错误] {msg}"))
        finally:
            self.after(0, self._reset_translate_btn)

    def _show_tgt(self, text: str):
        # 正常な翻訳結果のみを保持し、エラー文字列は保存しない。
        if not text.startswith("[错误]"):
            self._last_tgt_text = text
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", text)
        self._tgt_output.configure(state="disabled")

    def _reset_translate_btn(self):
        self._translating = False
        self._translate_btn.configure(state="normal", text="翻译")

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
        """現在の既定入力デバイス名を返す。  可能なら WASAPI を優先する。"""
        try:
            default_idx = sd.default.device[0]  # 0 番目は入力デバイス
            if default_idx is not None and default_idx >= 0:
                default_name = sd.query_devices(default_idx)["name"]
                # 重複排除済み一覧の中から、最適な API 側の同名デバイスを探す。
                match = next((d["name"] for d in devices if d["name"] == default_name), None)
                if match and match in names:
                    return match
        except Exception:
            pass
        # フォールバック時は、既知の Windows ループバック系デバイスを避ける。
        _SKIP = ("microsoft 映射", "microsoft sound mapper", "立体声混音", "stereo mix")
        return next(
            (n for n in names if not any(s in n.lower() for s in _SKIP)),
            names[0],
        )

    # ── 音声リスニングの開始と停止 ────────────────────────────────────────

    def _toggle_listening(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._set_status("正在准备语音…", ACCENT)
        self._start_btn.configure(state="disabled")
        self._reset_streaming_state()
        # ワーカースレッドへ渡す前に、Tkinter 変数はメインスレッドで読み出しておく。
        dev_name = self._device_var.get()
        dev_idx = self._devices.get(dev_name)
        threading.Thread(target=self._init_and_run, args=(dev_idx,), daemon=True).start()

    def _init_and_run(self, dev_idx):
        try:
            if self._listening_requires_translation():
                try:
                    self._translator = create_translator(self._config)
                except ValueError:
                    raise RuntimeError("您还没有设置API，请先在设置中填写")
            else:
                self._translator = None
            self._asr.load(progress_callback=lambda m: self.after(0, self._set_bottom, m))
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
        self._set_status("● 已停止", DANGER)
        self._start_btn.configure(
            text="▶ 开始监听", state="normal",
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
        )

    def _on_started(self):
        self._set_status("● 监听中…", SUCCESS)
        self._start_btn.configure(
            text="■ 停止", state="normal",
            fg_color=DANGER, hover_color=DANGER_HOVER,
        )

    def _on_start_error(self, msg: str):
        self._set_status("● 错误", DANGER)
        self._start_btn.configure(
            text="▶ 开始监听", state="normal",
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
        )
        messagebox.showerror("启动失败", msg)

    def _on_vad_state(self, in_speech: bool):
        """録音スレッドから呼ばれ、VAD 状態の変化を反映する。"""
        if in_speech:
            self.after(0, lambda: self._set_status("正在说话…", ACCENT))
        else:
            self.after(0, lambda: self._set_status("● 监听中…", SUCCESS))

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
        if self._float_win and self._float_win.winfo_exists():
            if self._float_win.winfo_viewable():
                self._float_win.hide()
                self._float_btn.configure(text="悬浮窗 ▼")
            else:
                self._float_win.show()
                self._float_btn.configure(text="悬浮窗 ▲")
        else:
            self._float_win = FloatingWindow(self)
            self._float_btn.configure(text="悬浮窗 ▲")

    # ── 設定 ──────────────────────────────────────────────────────────────

    def _open_settings(self):
        SettingsWindow(self, self._config, on_save=self._on_config_saved)

    def _on_config_saved(self, new_cfg: dict):
        was_running = self._running
        if was_running:
            self._stop()

        self._config = new_cfg
        self._asr = create_asr(new_cfg)
        self._translator = None
        self._tgt_var.set(new_cfg.get("translation", {}).get("target_language", "ja"))
        with self._merge_lock:
            self._partial_merger = self._create_streaming_merger()
        self._reset_streaming_state()
        if was_running:
            self._set_bottom("设置已更新，请重新点击开始监听以应用新配置")

    # ── 補助処理 ──────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "white"):
        self._status_label.configure(text=text, text_color=color)

    def _set_bottom(self, text: str):
        self._bottom_bar.configure(text=text)

