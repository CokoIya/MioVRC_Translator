"""翻訳バックエンドと音声認識の設定ダイアログ。  実験機能の切り替えもここで扱う。"""

import customtkinter as ctk
from tkinter import messagebox

from src.utils import config_manager

# カラーパレット。  既存 UI の配色に合わせている。
BG_PRIMARY = "#f7f5f0"
BG_SECONDARY = "#edeae2"
GLASS_BG = "#daeaf8"
GLASS_BORDER = "#8ab8d8"
GLASS_HOVER = "#c4dcf2"
ACCENT = "#3a9fd8"
ACCENT_HOVER = "#2882bc"
TEXT_PRI = "#252535"
TEXT_SEC = "#686880"

BACKENDS = ["openai", "deepseek", "qianwen", "anthropic", "custom"]

ASR_ENGINES = [
    ("Whisper Small（稳定）", "whisper-small"),
    ("SenseVoice Small（实验性）", "sensevoice-small"),
]

TARGET_LANGS = [
    ("日语 (ja)", "ja"),
    ("English (en)", "en"),
    ("韩语 (ko)", "ko"),
    ("中文 (zh)", "zh"),
    ("Français (fr)", "fr"),
    ("Deutsch (de)", "de"),
    ("Español (es)", "es"),
]

OUTPUT_FORMATS = [
    ("日语（中文）", "ja(zh)"),
    ("仅日语", "ja_only"),
    ("仅中文", "zh_only"),
    ("中文（日语）", "zh(ja)"),
]

ASR_HINTS = {
    "whisper-small": (
        "Whisper Small 是当前默认的稳定后端。  如果安装包内已附带模型，用户可以直接开始监听。"
    ),
    "sensevoice-small": (
        "SenseVoice Small 属于实验性后端。  首次启用时会按需下载模型并使用分块识别与 partial 合并。  "
        "如果发布包未包含 SenseVoice 运行依赖，将无法启用此项。"
    ),
}

STREAMING_HINT = (
    "刷新间隔越短，partial 更新越快但 CPU 占用也会更高。  识别窗口需要大于等于刷新间隔，"
    "这样才会保留重叠片段并让稳定前缀策略生效。"
)

OUTPUT_FORMAT_HINT = (
    "日语（中文）：发送 翻译（原文）\n"
    "仅日语：只发送翻译结果\n"
    "仅中文：不调用翻译 API，只发送原文\n"
    "中文（日语）：发送 原文（翻译）"
)


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: dict, on_save=None):
        super().__init__(parent)
        self.title("Settings - Mio Translator")
        self.geometry("560x860")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_PRIMARY)

        self._config = config
        self._on_save = on_save
        self._field_vars: dict[str, ctk.StringVar] = {}
        self._backend_entries: list[ctk.CTkEntry] = []
        self._build()

    def _build(self):
        pad = {"padx": 16, "pady": 6}
        trans_cfg = self._config.get("translation", {})
        asr_cfg = self._config.get("asr", {})
        streaming_cfg = asr_cfg.get("streaming", {})

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PRIMARY, corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        def section_label(text: str):
            ctk.CTkLabel(
                scroll,
                text=text,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=TEXT_PRI,
            ).pack(padx=16, pady=(12, 2), anchor="w")

        section_label("翻译后端")
        self._backend_var = ctk.StringVar(value=trans_cfg.get("backend", "openai"))
        self._backend_menu = ctk.CTkOptionMenu(
            scroll,
            values=BACKENDS,
            variable=self._backend_var,
            command=self._on_backend_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
        )
        self._backend_menu.pack(**pad, fill="x")

        section_label("目标语言")
        lang_labels = [label for label, _ in TARGET_LANGS]
        self._lang_codes = {label: code for label, code in TARGET_LANGS}
        self._lang_reverse = {code: label for label, code in TARGET_LANGS}
        current_target = trans_cfg.get("target_language", "ja")
        self._lang_var = ctk.StringVar(
            value=self._lang_reverse.get(current_target, lang_labels[0])
        )
        ctk.CTkOptionMenu(
            scroll,
            values=lang_labels,
            variable=self._lang_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        section_label("VRC 聊天框输出格式")
        format_labels = [label for label, _ in OUTPUT_FORMATS]
        self._fmt_codes = {label: code for label, code in OUTPUT_FORMATS}
        self._fmt_reverse = {code: label for label, code in OUTPUT_FORMATS}
        current_format = trans_cfg.get("output_format", "ja(zh)")
        self._fmt_var = ctk.StringVar(
            value=self._fmt_reverse.get(current_format, format_labels[0])
        )
        self._format_menu = ctk.CTkOptionMenu(
            scroll,
            values=format_labels,
            variable=self._fmt_var,
            command=self._on_output_format_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
        )
        self._format_menu.pack(**pad, fill="x")
        self._build_hint_box(scroll, OUTPUT_FORMAT_HINT)

        section_label("翻译后端参数")
        self._fields_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._fields_frame.pack(**pad, fill="both", expand=True)
        self._on_backend_change(self._backend_var.get())
        self._translation_lock_label = ctk.CTkLabel(
            self._build_hint_box(scroll, ""),
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=500,
        )
        self._translation_lock_label.pack(padx=10, pady=8, anchor="w")
        self._apply_translation_mode_state()

        section_label("语音识别后端")
        asr_labels = [label for label, _ in ASR_ENGINES]
        self._asr_codes = {label: code for label, code in ASR_ENGINES}
        self._asr_reverse = {code: label for label, code in ASR_ENGINES}
        current_engine = asr_cfg.get("engine", "whisper-small")
        self._asr_var = ctk.StringVar(
            value=self._asr_reverse.get(current_engine, asr_labels[0])
        )
        ctk.CTkOptionMenu(
            scroll,
            values=asr_labels,
            variable=self._asr_var,
            command=self._on_asr_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
            width=440,
        ).pack(**pad, fill="x")

        self._asr_hint_label = ctk.CTkLabel(
            self._build_hint_box(scroll, ""),
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=500,
        )
        self._asr_hint_label.pack(padx=10, pady=8, anchor="w")
        self._on_asr_change(self._asr_var.get())

        section_label("流式识别参数")
        self._chunk_interval_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_interval_ms", 250))
        )
        self._chunk_window_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_window_s", 1.6))
        )
        self._partial_hits_var = ctk.StringVar(
            value=str(streaming_cfg.get("partial_stability_hits", 2))
        )

        self._build_entry(scroll, "Partial 刷新间隔 (ms)", self._chunk_interval_var, **pad)
        self._build_entry(scroll, "识别窗口长度 (秒)", self._chunk_window_var, **pad)
        self._build_entry(scroll, "稳定前缀命中次数", self._partial_hits_var, **pad)
        self._build_hint_box(scroll, STREAMING_HINT)

        section_label("VAD 静音阈值 (秒)")
        self._vad_var = ctk.StringVar(
            value=str(self._config.get("audio", {}).get("vad_silence_threshold", 0.8))
        )
        self._build_entry(scroll, "句尾静音判定", self._vad_var, **pad)

        btn_frame = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)

        ctk.CTkButton(
            btn_frame,
            text="保存",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=12,
            text_color="#ffffff",
            command=self._save,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_frame,
            text="取消",
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=12,
            text_color=TEXT_PRI,
            command=self.destroy,
        ).pack(side="right", padx=4)

    def _build_hint_box(self, parent, text: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=10)
        frame.pack(padx=16, pady=(0, 4), fill="x")
        if text:
            ctk.CTkLabel(
                frame,
                text=text,
                font=ctk.CTkFont(size=11),
                text_color=TEXT_SEC,
                justify="left",
                wraplength=500,
            ).pack(padx=10, pady=8, anchor="w")
        return frame

    def _build_entry(self, parent, label_text: str, variable: ctk.StringVar, **pack_kwargs):
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=20, pady=(2, 0))
        ctk.CTkEntry(
            parent,
            textvariable=variable,
            fg_color=GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pack_kwargs, fill="x")

    def _on_backend_change(self, backend: str):
        """翻訳バックエンドを切り替えたら、必要な入力欄だけを再描画する。"""
        for widget in self._fields_frame.winfo_children():
            widget.destroy()

        trans_cfg = self._config.get("translation", {})
        self._field_vars = {}
        self._backend_entries = []

        def add_field(label_text: str, key: str, secret: bool = False):
            ctk.CTkLabel(
                self._fields_frame,
                text=label_text,
                text_color=TEXT_SEC,
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=4, pady=(6, 0))

            variable = ctk.StringVar(value=trans_cfg.get(backend, {}).get(key, ""))
            entry = ctk.CTkEntry(
                self._fields_frame,
                textvariable=variable,
                show="*" if secret else "",
                fg_color=GLASS_BG,
                border_color=GLASS_BORDER,
                corner_radius=10,
                text_color=TEXT_PRI,
            )
            entry.pack(fill="x", padx=4, pady=(0, 2))
            self._field_vars[key] = variable
            self._backend_entries.append(entry)

        if backend in ("openai", "deepseek", "qianwen", "custom"):
            add_field("API Key", "api_key", secret=True)
            add_field("Base URL", "base_url")
            add_field("Model", "model")
        elif backend == "anthropic":
            add_field("API Key", "api_key", secret=True)
            add_field("Model", "model")
        self._apply_translation_mode_state()

    def _on_asr_change(self, selected_label: str):
        """ASR 選択に応じて説明文を切り替える。"""
        engine = self._asr_codes.get(selected_label, "whisper-small")
        self._asr_hint_label.configure(text=ASR_HINTS.get(engine, ""))

    def _on_output_format_change(self, _selected_label: str):
        """出力形式に応じて翻訳設定欄の有効・無効を切り替える。"""
        self._apply_translation_mode_state()

    def _translation_locked(self) -> bool:
        return self._fmt_codes.get(self._fmt_var.get(), "ja(zh)") == "zh_only"

    def _apply_translation_mode_state(self):
        locked = self._translation_locked()
        state = "disabled" if locked else "normal"

        if hasattr(self, "_backend_menu"):
            self._backend_menu.configure(state=state)
        for entry in self._backend_entries:
            entry.configure(
                state=state,
                fg_color=BG_SECONDARY if locked else GLASS_BG,
                text_color=TEXT_SEC if locked else TEXT_PRI,
            )

        if hasattr(self, "_translation_lock_label"):
            self._translation_lock_label.configure(
                text=(
                    "仅中文模式下不会调用翻译 API。  翻译后端与 API Key 已锁定。"
                    if locked
                    else "翻译模式下会在开始监听时检查当前后端的 API 配置。"
                )
            )

    def _parse_positive_float(self, value: str, field_name: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是数字") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} 必须大于 0")
        return parsed

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} 必须大于 0")
        return parsed

    def _save(self):
        """設定を保存して閉じる。  分块参数の基本整合性もここで検証する。"""
        backend = self._backend_var.get()
        target_lang = self._lang_codes.get(self._lang_var.get(), "ja")
        output_format = self._fmt_codes.get(self._fmt_var.get(), "ja(zh)")
        asr_engine = self._asr_codes.get(self._asr_var.get(), "whisper-small")

        try:
            vad_threshold = self._parse_positive_float(self._vad_var.get(), "VAD 静音阈值")
            chunk_interval_ms = self._parse_positive_int(
                self._chunk_interval_var.get(),
                "Partial 刷新间隔",
            )
            chunk_window_s = self._parse_positive_float(
                self._chunk_window_var.get(),
                "识别窗口长度",
            )
            partial_hits = self._parse_positive_int(
                self._partial_hits_var.get(),
                "稳定前缀命中次数",
            )
        except ValueError as exc:
            messagebox.showerror("错误", str(exc))
            return

        if chunk_window_s * 1000 < chunk_interval_ms:
            messagebox.showerror(
                "错误",
                "识别窗口长度不能小于 Partial 刷新间隔，否则无法形成重叠分块。",
            )
            return

        cfg = self._config
        cfg.setdefault("translation", {})
        cfg["translation"]["backend"] = backend
        cfg["translation"]["target_language"] = target_lang
        cfg["translation"]["output_format"] = output_format

        if not self._translation_locked():
            cfg["translation"].setdefault(backend, {})
            for key, variable in self._field_vars.items():
                cfg["translation"][backend][key] = variable.get().strip()

        audio_cfg = cfg.setdefault("audio", {})
        audio_cfg["vad_silence_threshold"] = vad_threshold

        asr_cfg = cfg.setdefault("asr", {})
        asr_cfg["engine"] = asr_engine
        asr_cfg.setdefault("sensevoice", {})

        streaming_cfg = asr_cfg.setdefault("streaming", {})
        streaming_cfg["chunk_interval_ms"] = chunk_interval_ms
        streaming_cfg["chunk_window_s"] = chunk_window_s
        streaming_cfg["partial_stability_hits"] = partial_hits
        streaming_cfg["ring_buffer_s"] = max(
            float(streaming_cfg.get("ring_buffer_s", 4.0)),
            chunk_window_s,
        )
        streaming_cfg.setdefault("recent_speech_hold_s", 0.8)

        config_manager.save_config(cfg)
        if self._on_save:
            self._on_save(cfg)
        self.destroy()
