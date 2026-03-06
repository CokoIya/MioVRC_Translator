"""メインアプリケーションウィンドウ"""

import threading
import webbrowser
from pathlib import Path
import customtkinter as ctk
from tkinter import messagebox
import sounddevice as sd

from src.utils import config_manager
from src.audio.recorder import AudioRecorder
from src.asr.sense_voice import SenseVoiceASR
from src.translators.factory import create_translator
from src.osc.sender import VRCOSCSender
from src.osc.receiver import VRCOSCReceiver
from src.utils.lang_detect import detect_language
from .settings_window import SettingsWindow
from .floating_window import FloatingWindow

# ── カラーパレット（米白清新スタイル） ──────────────────────────────────────
BG_PRIMARY   = "#f7f5f0"   # 米白色主背景
BG_SECONDARY = "#edeae2"   # セカンダリ背景
BG_TOP       = "#e5e1d8"   # トップバー
BG_PANEL     = "#f2efe8"   # パネル背景
GLASS_BG     = "#daeaf8"   # 淡青色ボタン背景
GLASS_BORDER = "#8ab8d8"   # ボタン枠（天青色）
GLASS_HOVER  = "#c4dcf2"   # ホバー
ACCENT       = "#3a9fd8"   # 天青色アクセント
ACCENT_HOVER = "#2882bc"   # アクセントホバー
DANGER       = "#e05060"   # 赤（停止）
DANGER_HOVER = "#c03045"   # 赤ホバー
SUCCESS      = "#2ea85a"   # 緑（状態）
TEXT_PRI     = "#252535"   # プライマリテキスト（濃紺）
TEXT_SEC     = "#686880"   # セカンダリテキスト（灰）
DIVIDER      = "#d8d4cc"   # 区切り線

GITHUB_REPO_URL = "https://github.com/CokoIya/MioVRC_Translator"
QQ_GROUP_URL = "https://qm.qq.com/q/1PThd3QBTS"
LINE_GROUP_URL = "https://line.me/ti/g2/uLhASjhfQcsd5tYsEpFr8GWsCcuYVIq1I6iGwA?utm_source=invitation&utm_medium=link_copy&utm_campaign=default"
ICON_GITHUB_FILE = "github.png"
ICON_QQ_FILE = "qq.png"
ICON_LINE_FILE = "line.png"
ICON_SPONSOR_FILE = "sponsor.png"
SPONSOR_IMAGE_CANDIDATES = (
    "sponsor_qr.png",
    "sponsor_qr.jpg",
    "sponsor_qr.jpeg",
    "sponsor.png",
    "sponsor.jpg",
)

# 手動翻訳で選択できる言語
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
        # 元 620x560 → 幅+20% / 高さ-30%
        self.geometry("744x392")
        self.minsize(620, 320)
        self.configure(fg_color=BG_PRIMARY)

        # コアオブジェクト（開始時に生成）
        self._recorder: AudioRecorder | None = None
        self._asr = SenseVoiceASR(
            model_id=config.get("asr", {}).get("model"),
            device=config.get("asr", {}).get("device", "cpu"),
        )
        self._translator = None
        self._sender: VRCOSCSender | None = None
        self._receiver: VRCOSCReceiver | None = None
        self._own_msgs: set[str] = set()
        self._translating = False

        self._running = False
        self._float_win: FloatingWindow | None = None
        self._sponsor_win: ctk.CTkToplevel | None = None
        self._social_icons: dict[str, ctk.CTkImage] = {}

        self._build()
        self._load_devices()

    # ── UI構築 ──────────────────────────────────────────────────────────────

    def _build(self):
        # ── トップバー（タイトル・コントロールをまとめてコンパクトに） ─────
        top = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_TOP)
        top.pack(fill="x")

        ctk.CTkLabel(
            top, text="制作者：VRC玩家 酒寄 みお ｜ 开源项目，禁止收费",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_PRI,
        ).pack(side="left", padx=14, pady=7)

        # 右端ボタン群
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

        # ── マイク + 翻译至 を1行にまとめる ─────────────────────────────────
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

        # ── Google翻訳スタイル・手動入力パネル ─────────────────────────────
        self._build_translate_panel()

        # ── ステータスバー ──────────────────────────────────────────────────
        self._bottom_bar = ctk.CTkLabel(
            self, text="未加载模型", font=ctk.CTkFont(size=10), text_color=TEXT_SEC,
        )
        self._bottom_bar.pack(side="bottom", pady=2)

    def _build_translate_panel(self):
        """Google翻訳風の左右2ペイン翻訳パネルを構築する"""
        outer = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # ── 言語ヘッダー行 ──────────────────────────────────────────────────
        hdr = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # 入力言語ドロップダウン
        src_labels = [l for l, _ in MANUAL_LANGS]
        src_codes  = {l: c for l, c in MANUAL_LANGS}
        self._src_lang_var = ctk.StringVar(value=src_labels[0])
        self._src_lang_codes = src_codes
        ctk.CTkOptionMenu(
            hdr, values=src_labels, variable=self._src_lang_var, width=130,
            fg_color=BG_SECONDARY, button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER, corner_radius=6,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=8)

        # 入れ替えボタン
        ctk.CTkButton(
            hdr, text="⇄", width=32, height=24,
            fg_color="transparent", hover_color=GLASS_HOVER,
            corner_radius=6, text_color=TEXT_SEC,
            command=self._swap_langs,
        ).pack(side="left", padx=4)

        # 出力言語ラベル（翻译至と連動）
        self._tgt_lang_label = ctk.CTkLabel(
            hdr, text="日语", text_color=TEXT_PRI, font=ctk.CTkFont(size=12),
        )
        self._tgt_lang_label.pack(side="left", padx=12)
        self._tgt_var.trace_add("write", self._on_tgt_lang_change)
        self._on_tgt_lang_change()

        # ── テキストエリア行 ────────────────────────────────────────────────
        text_row = ctk.CTkFrame(outer, fg_color=BG_PANEL, corner_radius=0)
        text_row.pack(fill="both", expand=True)

        # 入力エリア
        left = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        left.pack(side="left", fill="both", expand=True)

        self._src_input = ctk.CTkTextbox(
            left,
            font=ctk.CTkFont(size=13), wrap="word",
            fg_color=BG_PANEL, corner_radius=0,
            text_color=TEXT_PRI, border_width=0,
        )
        self._src_input.pack(fill="both", expand=True, padx=8, pady=(6, 0))
        self._src_input.insert("1.0", "在此输入文字…")
        self._src_input.configure(text_color=TEXT_SEC)
        self._src_input.bind("<FocusIn>",  self._on_src_focus_in)
        self._src_input.bind("<FocusOut>", self._on_src_focus_out)
        self._src_input.bind("<KeyRelease>", self._on_src_key)

        # 入力下部ツールバー
        left_bar = ctk.CTkFrame(left, fg_color=BG_SECONDARY, corner_radius=0, height=30)
        left_bar.pack(fill="x")
        left_bar.pack_propagate(False)

        self._char_label = ctk.CTkLabel(
            left_bar, text="0 / 500", text_color=TEXT_SEC, font=ctk.CTkFont(size=10),
        )
        self._char_label.pack(side="left", padx=10)

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

        # 区切り線
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

        # 出力下部ツールバー
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

        # 底部图标按钮：保持窗口总高度不变，仅挤压文本区域
        social_bar = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=44)
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

    # ── 翻訳パネルのヘルパー ─────────────────────────────────────────────────

    def _on_src_focus_in(self, _event):
        """プレースホルダーをクリア"""
        if self._src_input.get("1.0", "end").strip() == "在此输入文字…":
            self._src_input.delete("1.0", "end")
            self._src_input.configure(text_color=TEXT_PRI)

    def _on_src_focus_out(self, _event):
        """空ならプレースホルダーを復元"""
        if not self._src_input.get("1.0", "end").strip():
            self._src_input.insert("1.0", "在此输入文字…")
            self._src_input.configure(text_color=TEXT_SEC)

    def _on_src_key(self, _event):
        """文字数カウントを更新"""
        n = len(self._src_input.get("1.0", "end").strip())
        self._char_label.configure(text=f"{n} / 500")

    def _on_tgt_lang_change(self, *_):
        """翻译至ラジオと出力言語ラベルを同期"""
        labels = {"ja": "日语", "en": "英语", "zh": "中文", "ko": "韩语"}
        self._tgt_lang_label.configure(text=labels.get(self._tgt_var.get(), ""))

    def _swap_langs(self):
        """入出力テキストを入れ替える"""
        src_text = self._src_input.get("1.0", "end").strip()
        tgt_text = self._tgt_output.get("1.0", "end").strip()
        if src_text == "在此输入文字…":
            src_text = ""
        self._src_input.delete("1.0", "end")
        self._src_input.insert("1.0", tgt_text)
        self._src_input.configure(text_color=TEXT_PRI)
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", src_text)
        self._tgt_output.configure(state="disabled")

    def _clear_input(self):
        self._src_input.delete("1.0", "end")
        self._src_input.insert("1.0", "在此输入文字…")
        self._src_input.configure(text_color=TEXT_SEC)
        self._char_label.configure(text="0 / 500")
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
    def _assets_dir() -> Path:
        return Path(__file__).resolve().parents[2] / "assets"

    @staticmethod
    def _icons_dir() -> Path:
        return MainWindow._assets_dir() / "icons"

    def _load_social_icon(self, filename: str) -> ctk.CTkImage | None:
        icon_path = self._icons_dir() / filename
        if not icon_path.exists():
            return None
        try:
            from PIL import Image
            img = Image.open(icon_path).convert("RGBA")
        except Exception:
            return None

        icon = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
        self._social_icons[filename] = icon
        return icon

    @staticmethod
    def _add_social_button(parent, icon, fallback_text: str, fg: str, hover: str, command):
        ctk.CTkButton(
            parent,
            image=icon,
            text="" if icon else fallback_text,
            width=56,
            height=32,
            corner_radius=8,
            fg_color=fg,
            hover_color=hover,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=command,
        ).pack(side="left", padx=6, pady=6)

    @staticmethod
    def _find_sponsor_image() -> Path | None:
        assets_dir = MainWindow._assets_dir()
        for name in SPONSOR_IMAGE_CANDIDATES:
            p = assets_dir / name
            if p.exists():
                return p

        # 兜底：如果用户未按指定命名，尝试直接取 assets 根目录中的第一张图片
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

    def _send_to_vrc(self):
        """翻訳結果をVRCチャットボックスに送信する"""
        text = self._tgt_output.get("1.0", "end").strip()
        if not text:
            return
        if self._sender is None:
            osc_cfg = self._config.get("osc", {})
            self._sender = VRCOSCSender(
                host=osc_cfg.get("send_host", "127.0.0.1"),
                port=osc_cfg.get("send_port", 9000),
            )
        sent = self._sender.send_chatbox(text)
        self._own_msgs.add(sent)

    def _translate_manual(self):
        """手動入力テキストを翻訳する"""
        src_text = self._src_input.get("1.0", "end").strip()
        if not src_text or src_text == "在此输入文字…":
            return
        if self._translating:
            return

        # 言語コードを決定
        src_code = self._src_lang_codes.get(self._src_lang_var.get(), "auto")
        if src_code == "auto":
            src_code = detect_language(src_text)
        tgt_code = self._tgt_var.get()

        # 翻訳APIが未初期化なら生成
        if self._translator is None:
            try:
                self._translator = create_translator(self._config)
            except Exception as e:
                self._show_tgt(f"[错误] {e}")
                return

        self._translating = True
        self._translate_btn.configure(state="disabled", text="翻译中…")
        threading.Thread(
            target=self._do_translate,
            args=(src_text, src_code, tgt_code),
            daemon=True,
        ).start()

    def _do_translate(self, text: str, src_lang: str, tgt_lang: str):
        """バックグラウンドスレッドで翻訳を実行する"""
        try:
            result = self._translator.translate(text, src_lang, tgt_lang)
            self.after(0, lambda: self._show_tgt(result))
        except Exception as e:
            self.after(0, lambda: self._show_tgt(f"[错误] {e}"))
        finally:
            self.after(0, self._reset_translate_btn)

    def _show_tgt(self, text: str):
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", text)
        self._tgt_output.configure(state="disabled")

    def _reset_translate_btn(self):
        self._translating = False
        self._translate_btn.configure(state="normal", text="翻译")

    # ── デバイスリスト ──────────────────────────────────────────────────────

    def _load_devices(self):
        devices = AudioRecorder.list_devices()
        self._devices = {d["name"]: d["index"] for d in devices}
        names = list(self._devices.keys()) or ["默认"]
        self._device_menu.configure(values=names)
        cfg_dev = self._config.get("audio", {}).get("input_device")
        if cfg_dev and cfg_dev in self._devices:
            self._device_var.set(cfg_dev)
        else:
            self._device_var.set(names[0])

    # ── 音声リスニング 開始 / 停止 ──────────────────────────────────────────

    def _toggle_listening(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._set_status("正在加载模型…", "orange")
        self._start_btn.configure(state="disabled")
        threading.Thread(target=self._init_and_run, daemon=True).start()

    def _init_and_run(self):
        try:
            self._asr.load(progress_callback=lambda m: self.after(0, self._set_bottom, m))
            self._translator = create_translator(self._config)
            osc_cfg = self._config.get("osc", {})
            self._sender = VRCOSCSender(
                host=osc_cfg.get("send_host", "127.0.0.1"),
                port=osc_cfg.get("send_port", 9000),
            )
            self._receiver = VRCOSCReceiver(
                on_message=self._on_incoming_chatbox,
                port=osc_cfg.get("receive_port", 9001),
                own_messages=self._own_msgs,
            )
            self._receiver.start()
            dev_name = self._device_var.get()
            dev_idx = self._devices.get(dev_name)
            audio_cfg = self._config.get("audio", {})
            self._recorder = AudioRecorder(
                on_segment=self._on_audio_segment,
                sample_rate=audio_cfg.get("sample_rate", 16000),
                frame_duration_ms=audio_cfg.get("frame_duration_ms", 30),
                vad_sensitivity=audio_cfg.get("vad_sensitivity", 2),
                silence_threshold_s=audio_cfg.get("vad_silence_threshold", 0.8),
                input_device=dev_idx,
            )
            self._recorder.start()
            self._running = True
            self.after(0, self._on_started)
        except Exception as e:
            self.after(0, lambda: self._on_start_error(str(e)))

    def _stop(self):
        self._running = False
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

    # ── 音声セグメントハンドラ（ログなし・VRC送信のみ） ─────────────────────

    def _on_audio_segment(self, audio):
        if not self._running:
            return
        try:
            text = self._asr.transcribe(audio)
            if not text:
                return
            src_lang = detect_language(text)
            tgt_lang = self._tgt_var.get()
            fmt = self._config.get("translation", {}).get("output_format", "ja(zh)")

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

            sent = self._sender.send_chatbox(chatbox_text)
            self._own_msgs.add(sent)
        except Exception:
            pass

    # ── 受信チャットボックス（逆翻訳・フローティングウィンドウに表示） ───────

    def _on_incoming_chatbox(self, text: str):
        try:
            src_lang = detect_language(text)
            if src_lang == "zh":
                self.after(0, lambda: self._show_incoming(text, None))
                return
            translated = self._translator.translate(text, src_lang, "zh")
            self.after(0, lambda: self._show_incoming(text, translated))
        except Exception:
            pass

    def _show_incoming(self, original: str, translated: str | None):
        if self._float_win and self._float_win.winfo_exists():
            self._float_win.add_message(original, translated)

    # ── フローティングウィンドウ ────────────────────────────────────────────

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

    # ── 設定 ────────────────────────────────────────────────────────────────

    def _open_settings(self):
        SettingsWindow(self, self._config, on_save=self._on_config_saved)

    def _on_config_saved(self, new_cfg: dict):
        self._config = new_cfg

    # ── ヘルパー ────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "white"):
        self._status_label.configure(text=text, text_color=color)

    def _set_bottom(self, text: str):
        self._bottom_bar.configure(text=text)

