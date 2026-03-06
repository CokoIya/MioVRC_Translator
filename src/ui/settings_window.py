"""翻訳バックエンド・音声・出力フォーマットの設定ダイアログ"""

import customtkinter as ctk
from tkinter import messagebox
from src.utils import config_manager

# ── カラーパレット（米白清新スタイル） ──────────────────────────────────────
BG_PRIMARY   = "#f7f5f0"   # 米白色主背景
BG_SECONDARY = "#edeae2"   # セカンダリ背景
GLASS_BG     = "#daeaf8"   # 淡青色ボタン背景
GLASS_BORDER = "#8ab8d8"   # 天青色ボタン枠
GLASS_HOVER  = "#c4dcf2"   # ホバー
ACCENT       = "#3a9fd8"   # 天青色アクセント
ACCENT_HOVER = "#2882bc"   # アクセントホバー
TEXT_PRI     = "#252535"   # プライマリテキスト（濃紺）
TEXT_SEC     = "#686880"   # セカンダリテキスト（灰）

BACKENDS = ["openai", "deepseek", "qianwen", "anthropic", "custom"]

# 音声認識エンジン選択肢（base と small のみ）
ASR_ENGINES = [
    ("Whisper Base  （推荐，快速启动）", "whisper-base"),
    ("Whisper Small （较慢，精度更高）", "whisper-small"),
]

TARGET_LANGS = [
    ("日本語 (ja)", "ja"),
    ("English (en)", "en"),
    ("한국어 (ko)", "ko"),
    ("中文 (zh)", "zh"),
    ("Français (fr)", "fr"),
    ("Deutsch (de)", "de"),
    ("Español (es)", "es"),
]

# 出力フォーマットの選択肢（表示ラベルは中文）
OUTPUT_FORMATS = [
    ("日语（中文）", "ja(zh)"),
    ("仅日语",      "ja_only"),
    ("仅中文",      "zh_only"),
    ("中文（日语）","zh(ja)"),
]


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: dict, on_save=None):
        super().__init__(parent)
        self.title("Settings — Mio Translator")
        self.geometry("520x700")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_PRIMARY)

        self._config = config
        self._on_save = on_save
        self._build()

    def _build(self):
        pad = {"padx": 16, "pady": 6}
        trans_cfg = self._config.get("translation", {})

        # スクロール可能コンテナ
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PRIMARY, corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        def label(text):
            ctk.CTkLabel(
                scroll, text=text,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=TEXT_PRI,
            ).pack(padx=16, pady=(12, 2), anchor="w")

        # ── 翻訳バックエンド ─────────────────────────────────────────────────
        label("翻译后端")
        self._backend_var = ctk.StringVar(value=trans_cfg.get("backend", "openai"))
        ctk.CTkOptionMenu(
            scroll, values=BACKENDS, variable=self._backend_var,
            command=self._on_backend_change,
            fg_color=GLASS_BG, button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER, corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        # ── 翻訳先言語 ───────────────────────────────────────────────────────
        label("目标语言")
        lang_labels = [lbl for lbl, _ in TARGET_LANGS]
        lang_codes = {lbl: code for lbl, code in TARGET_LANGS}
        self._lang_reverse = {code: lbl for lbl, code in TARGET_LANGS}
        cur_tgt = trans_cfg.get("target_language", "ja")
        self._lang_var = ctk.StringVar(value=self._lang_reverse.get(cur_tgt, lang_labels[0]))
        self._lang_codes = lang_codes
        ctk.CTkOptionMenu(
            scroll, values=lang_labels, variable=self._lang_var,
            fg_color=GLASS_BG, button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER, corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        # ── VRCチャットボックス出力フォーマット ──────────────────────────────
        label("VRC 聊天框输出格式")
        fmt_labels = [lbl for lbl, _ in OUTPUT_FORMATS]
        fmt_codes  = {lbl: code for lbl, code in OUTPUT_FORMATS}
        self._fmt_reverse = {code: lbl for lbl, code in OUTPUT_FORMATS}
        cur_fmt = trans_cfg.get("output_format", "ja(zh)")
        self._fmt_var = ctk.StringVar(value=self._fmt_reverse.get(cur_fmt, fmt_labels[0]))
        self._fmt_codes = fmt_codes
        ctk.CTkOptionMenu(
            scroll, values=fmt_labels, variable=self._fmt_var,
            fg_color=GLASS_BG, button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER, corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        # フォーマット説明ヒント
        hint_frame = ctk.CTkFrame(scroll, fg_color=BG_SECONDARY, corner_radius=10)
        hint_frame.pack(padx=16, pady=(0, 4), fill="x")
        ctk.CTkLabel(
            hint_frame,
            text=(
                "日语（中文）: 翻译（原文）\n"
                "仅日语: 只发送翻译\n"
                "仅中文: 不调用翻译API，只发送原文\n"
                "中文（日语）: 原文（翻译）"
            ),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
        ).pack(padx=10, pady=8, anchor="w")

        # ── バックエンド固有フィールド ────────────────────────────────────────
        self._fields_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._fields_frame.pack(**pad, fill="both", expand=True)
        self._on_backend_change(self._backend_var.get())

        # ── ASR エンジン選択 ──────────────────────────────────────────────────
        label("语音识别模型")
        asr_labels  = [lbl for lbl, _ in ASR_ENGINES]
        asr_codes   = {lbl: code for lbl, code in ASR_ENGINES}
        self._asr_reverse = {code: lbl for lbl, code in ASR_ENGINES}
        cur_engine  = self._config.get("asr", {}).get("engine", "whisper-base")
        self._asr_var   = ctk.StringVar(value=self._asr_reverse.get(cur_engine, asr_labels[0]))
        self._asr_codes = asr_codes
        ctk.CTkOptionMenu(
            scroll, values=asr_labels, variable=self._asr_var,
            fg_color=GLASS_BG, button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER, corner_radius=10,
            text_color=TEXT_PRI, width=440,
        ).pack(**pad, fill="x")

        asr_hint = ctk.CTkFrame(scroll, fg_color=BG_SECONDARY, corner_radius=10)
        asr_hint.pack(padx=16, pady=(0, 4), fill="x")
        ctk.CTkLabel(
            asr_hint,
            text="模型文件已内置，无需下载。切换模型需重启软件后生效。",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, justify="left",
        ).pack(padx=10, pady=6, anchor="w")

        # ── VAD 静音閾値 ─────────────────────────────────────────────────────
        label("VAD 静音阈值 (秒)")
        self._vad_var = ctk.StringVar(
            value=str(self._config.get("audio", {}).get("vad_silence_threshold", 0.8))
        )
        ctk.CTkEntry(
            scroll, textvariable=self._vad_var,
            fg_color=GLASS_BG, border_color=GLASS_BORDER, corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        # ── ボタン ──────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)

        ctk.CTkButton(
            btn_frame, text="保存",
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            corner_radius=12, text_color="#ffffff",
            command=self._save,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_frame, text="取消",
            fg_color=GLASS_BG, hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            corner_radius=12, text_color=TEXT_PRI,
            command=self.destroy,
        ).pack(side="right", padx=4)

    def _on_backend_change(self, backend: str):
        """バックエンド変更時に対応するフィールドを再描画する"""
        for w in self._fields_frame.winfo_children():
            w.destroy()

        trans_cfg = self._config.get("translation", {})
        self._field_vars = {}

        def add_field(label_text, key, secret=False):
            ctk.CTkLabel(
                self._fields_frame, text=label_text,
                text_color=TEXT_SEC, font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=4, pady=(6, 0))
            var = ctk.StringVar(value=trans_cfg.get(backend, {}).get(key, ""))
            ctk.CTkEntry(
                self._fields_frame, textvariable=var,
                show="●" if secret else "",
                fg_color=GLASS_BG, border_color=GLASS_BORDER,
                corner_radius=10, text_color=TEXT_PRI,
            ).pack(fill="x", padx=4, pady=(0, 2))
            self._field_vars[key] = var

        if backend in ("openai", "deepseek", "qianwen", "custom"):
            add_field("API Key", "api_key", secret=True)
            add_field("Base URL", "base_url")
            add_field("Model", "model")
        elif backend == "anthropic":
            add_field("API Key", "api_key", secret=True)
            add_field("Model", "model")

    def _save(self):
        """設定を保存してダイアログを閉じる"""
        backend = self._backend_var.get()
        tgt_lang = self._lang_codes.get(self._lang_var.get(), "ja")
        output_fmt = self._fmt_codes.get(self._fmt_var.get(), "ja(zh)")

        cfg = self._config
        cfg.setdefault("translation", {})["backend"] = backend
        cfg["translation"]["target_language"] = tgt_lang
        cfg["translation"]["output_format"] = output_fmt

        asr_engine = self._asr_codes.get(self._asr_var.get(), "whisper-small")
        cfg.setdefault("asr", {})["engine"] = asr_engine

        cfg["translation"].setdefault(backend, {})
        for key, var in self._field_vars.items():
            cfg["translation"][backend][key] = var.get().strip()

        try:
            cfg.setdefault("audio", {})["vad_silence_threshold"] = float(self._vad_var.get())
        except ValueError:
            messagebox.showerror("错误", "VAD 阈值必须是数字")
            return

        config_manager.save_config(cfg)
        if self._on_save:
            self._on_save(cfg)
        self.destroy()
