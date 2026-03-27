from __future__ import annotations

from collections import deque

import customtkinter as ctk

from src.utils.i18n import tr
from .window_effects import apply_window_icon

WINDOW_BG = "#f5f9ff"
CARD_BG = "#ffffff"
CARD_BORDER = "#cfdceb"
HISTORY_BG = "#f0f4f8"
HISTORY_ITEM_BG = "#ffffff"
TEXT_PRI = "#10243b"
TEXT_SEC = "#58708b"
SOURCE_COLORS = {
    "listen": "#0f766e",
    "mic": "#1d4ed8",
    "error": "#b91c1c",
}
SOURCE_TITLES = {
    "listen": {
        "zh-CN": "VRC Listen",
        "en": "VRC Listen",
        "ja": "VRC Listen",
    },
    "mic": {
        "zh-CN": "麦克风",
        "en": "Microphone",
        "ja": "マイク",
    },
    "error": {
        "zh-CN": "错误",
        "en": "Error",
        "ja": "エラー",
    },
}

MAX_HISTORY = 15


class FloatingWindow(ctk.CTkToplevel):
    def __init__(self, parent, ui_language: str):
        super().__init__(parent)
        self._ui_lang = ui_language
        self._last_text = ""
        self._visible = False
        self._history: deque[dict] = deque(maxlen=MAX_HISTORY)

        self.title(tr(self._ui_lang, "floating_window_title"))
        apply_window_icon(self)
        self.geometry("480x520+24+96")
        self._popup_size = (480, 520)
        self.resizable(True, True)
        self.minsize(320, 280)
        self.attributes("-topmost", True)
        self.configure(fg_color=WINDOW_BG)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        # Outer card
        outer = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=18,
            border_width=1,
            border_color=CARD_BORDER,
        )
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # Header
        self._header_label = ctk.CTkLabel(
            outer,
            text=tr(self._ui_lang, "floating_window_header"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self._header_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        # Scrollable history area
        self._scroll_frame = ctk.CTkScrollableFrame(
            outer,
            fg_color=HISTORY_BG,
            corner_radius=10,
        )
        self._scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self._scroll_frame.columnconfigure(0, weight=1)

        self._empty_label = ctk.CTkLabel(
            self._scroll_frame,
            text="—",
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        )
        self._empty_label.grid(row=0, column=0, pady=20)

        # Opacity slider row
        opacity_frame = ctk.CTkFrame(outer, fg_color="transparent")
        opacity_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        opacity_frame.columnconfigure(1, weight=1)

        self._opacity_title_label = ctk.CTkLabel(
            opacity_frame,
            text=tr(self._ui_lang, "opacity") + ":",
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
        )
        self._opacity_title_label.grid(row=0, column=0, padx=(0, 8))

        self._opacity_var = ctk.DoubleVar(value=1.0)
        self._opacity_slider = ctk.CTkSlider(
            opacity_frame,
            from_=0.2,
            to=1.0,
            variable=self._opacity_var,
            command=self._on_opacity_change,
        )
        self._opacity_slider.grid(row=0, column=1, sticky="ew")

        self._opacity_pct_label = ctk.CTkLabel(
            opacity_frame,
            text="100%",
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            width=42,
            anchor="e",
        )
        self._opacity_pct_label.grid(row=0, column=2, padx=(6, 0))

        self.withdraw()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_opacity_change(self, value: float) -> None:
        self.attributes("-alpha", float(value))
        self._opacity_pct_label.configure(text=f"{int(round(float(value) * 100))}%")

    def _source_title(self, source: str) -> str:
        labels = SOURCE_TITLES.get(source, SOURCE_TITLES["listen"])
        if self._ui_lang in labels:
            return labels[self._ui_lang]
        base_lang = self._ui_lang.split("-", 1)[0]
        for lang, label in labels.items():
            if lang.split("-", 1)[0] == base_lang:
                return label
        return labels.get("en", source)

    def _refresh_history(self) -> None:
        # 清空并重绘历史列表，最新条目在底部
        for widget in self._scroll_frame.winfo_children():
            widget.destroy()

        if not self._history:
            lbl = ctk.CTkLabel(
                self._scroll_frame,
                text="—",
                text_color=TEXT_SEC,
                font=ctk.CTkFont(size=12),
            )
            lbl.grid(row=0, column=0, pady=20)
            return

        for idx, entry in enumerate(self._history):
            color = SOURCE_COLORS.get(entry["source"], TEXT_PRI)
            source_title = self._source_title(entry["source"])

            row_frame = ctk.CTkFrame(
                self._scroll_frame,
                fg_color=HISTORY_ITEM_BG,
                corner_radius=8,
                border_width=1,
                border_color=CARD_BORDER,
            )
            row_frame.grid(row=idx, column=0, sticky="ew", padx=6, pady=3)
            row_frame.columnconfigure(0, weight=1)

            ctk.CTkLabel(
                row_frame,
                text=source_title,
                text_color=color,
                font=ctk.CTkFont(size=10, weight="bold"),
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))

            ctk.CTkLabel(
                row_frame,
                text=entry["text"],
                text_color=TEXT_PRI,
                font=ctk.CTkFont(size=13),
                justify="left",
                anchor="nw",
                wraplength=380,
            ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

        self._scroll_frame.after(60, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        try:
            self._scroll_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_language(self, ui_language: str) -> None:
        # 切换 UI 语言后刷新所有文本
        self._ui_lang = ui_language
        self.title(tr(self._ui_lang, "floating_window_title"))
        self._header_label.configure(text=tr(self._ui_lang, "floating_window_header"))
        self._opacity_title_label.configure(text=tr(self._ui_lang, "opacity") + ":")
        self._refresh_history()

    def show_translation(self, text: str, *, source: str = "listen") -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._last_text = message
        self.add_history_entry(message, source=source)
        if not self._visible:
            self.deiconify()
            self._visible = True
        self.lift()

    def add_history_entry(self, text: str, *, source: str = "listen") -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._history.append({"text": message, "source": source})
        self._refresh_history()

    def reveal(self) -> None:
        if not self._visible:
            self.deiconify()
            self._visible = True
        self.lift()

    def hide(self) -> None:
        self._visible = False
        self.withdraw()
