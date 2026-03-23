from __future__ import annotations

import customtkinter as ctk

from src.utils.i18n import tr
from .window_effects import apply_window_icon

WINDOW_BG = "#f5f9ff"
CARD_BG = "#ffffff"
CARD_BORDER = "#cfdceb"
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
        "zh-CN": "Microphone",
        "en": "Microphone",
        "ja": "Microphone",
    },
    "error": {
        "zh-CN": "Error",
        "en": "Error",
        "ja": "Error",
    },
}


class FloatingWindow(ctk.CTkToplevel):
    def __init__(self, parent, ui_language: str):
        super().__init__(parent)
        self._ui_lang = ui_language
        self._last_text = ""
        self._visible = False

        self.title(tr(self._ui_lang, "floating_window_title"))
        apply_window_icon(self)
        self.geometry("420x180+24+96")
        self._popup_size = (420, 180)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=WINDOW_BG)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        outer = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=18,
            border_width=1,
            border_color=CARD_BORDER,
        )
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        self._header_label = ctk.CTkLabel(
            outer,
            text=tr(self._ui_lang, "floating_window_header"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self._header_label.pack(fill="x", padx=14, pady=(12, 6))

        self._source_label = ctk.CTkLabel(
            outer,
            text="",
            text_color=SOURCE_COLORS["listen"],
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self._source_label.pack(fill="x", padx=14, pady=(0, 4))

        self._text_label = ctk.CTkLabel(
            outer,
            text="",
            text_color=TEXT_PRI,
            justify="left",
            anchor="nw",
            wraplength=360,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self._text_label.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.withdraw()

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = ui_language
        self.title(tr(self._ui_lang, "floating_window_title"))
        self._header_label.configure(text=tr(self._ui_lang, "floating_window_header"))

    def show_translation(self, text: str, *, source: str = "listen") -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._last_text = message
        color = SOURCE_COLORS.get(source, TEXT_PRI)
        self._source_label.configure(
            text=self._source_title(source),
            text_color=color,
        )
        self._text_label.configure(
            text=message,
            text_color=color if source in SOURCE_COLORS else TEXT_PRI,
        )
        if not self._visible:
            self.deiconify()
            self._visible = True
        self.lift()

    def hide(self) -> None:
        self._visible = False
        self.withdraw()

    def _source_title(self, source: str) -> str:
        labels = SOURCE_TITLES.get(source, SOURCE_TITLES["listen"])
        if self._ui_lang in labels:
            return labels[self._ui_lang]
        base_lang = self._ui_lang.split("-", 1)[0]
        for lang, label in labels.items():
            if lang.split("-", 1)[0] == base_lang:
                return label
        return labels.get("en", source)
