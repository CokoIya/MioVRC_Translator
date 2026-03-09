"""Floating window used to display incoming reverse-translated chat."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import customtkinter as ctk

from src.utils.i18n import tr

BG_FLOAT = "#f7f5f0"
BG_TITLEBAR = "#e5e1d8"
GLASS_BG = "#daeaf8"
TEXT_PRI = "#252535"
TEXT_SEC = "#686880"
MAX_ENTRIES = 5


class FloatingWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        ui_language: str = "zh-CN",
        opacity: float = 0.92,
        on_opacity_change: Callable[[float], None] | None = None,
    ):
        super().__init__(parent)
        self._ui_lang = ui_language
        self._on_opacity_change = on_opacity_change
        self.title(tr(self._ui_lang, "floating_window_title"))
        self.geometry("420x300")
        self.attributes("-topmost", True)
        self.attributes("-alpha", float(opacity))
        self.overrideredirect(True)
        self.configure(fg_color=BG_FLOAT)

        self._entries: deque[tuple[str, str | None]] = deque(maxlen=MAX_ENTRIES)
        self._build(opacity=float(opacity))
        self._start_drag()

    def _build(self, *, opacity: float) -> None:
        self._title_bar = ctk.CTkFrame(
            self,
            height=32,
            corner_radius=0,
            fg_color=BG_TITLEBAR,
        )
        self._title_bar.pack(fill="x")

        ctk.CTkLabel(
            self._title_bar,
            text=tr(self._ui_lang, "floating_window_header"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            self._title_bar,
            text="x",
            width=30,
            height=24,
            fg_color="transparent",
            hover_color="#e05060",
            text_color=TEXT_SEC,
            corner_radius=6,
            command=self.hide,
        ).pack(side="right", padx=4)

        slider_frame = ctk.CTkFrame(self, fg_color=GLASS_BG, corner_radius=0, height=28)
        slider_frame.pack(fill="x")

        ctk.CTkLabel(
            slider_frame,
            text=tr(self._ui_lang, "opacity"),
            font=ctk.CTkFont(size=10),
            text_color=TEXT_SEC,
        ).pack(side="left", padx=8)

        self._slider = ctk.CTkSlider(
            slider_frame,
            from_=0.3,
            to=1.0,
            command=self._on_opacity,
            width=130,
        )
        self._slider.set(opacity)
        self._slider.pack(side="left", padx=4, pady=4)

        self._text = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(size=12),
            wrap="word",
            state="disabled",
            fg_color=BG_FLOAT,
            corner_radius=0,
            text_color=TEXT_PRI,
        )
        self._text.pack(fill="both", expand=True, padx=4, pady=4)

    def _on_opacity(self, value) -> None:
        opacity = float(value)
        self.attributes("-alpha", opacity)
        if self._on_opacity_change is not None:
            self._on_opacity_change(opacity)

    def _start_drag(self) -> None:
        self._title_bar.bind("<ButtonPress-1>", self._drag_start)
        self._title_bar.bind("<B1-Motion>", self._drag_motion)

    def _drag_start(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_motion(self, event) -> None:
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")

    def add_message(self, original: str, translated: str | None) -> None:
        self._entries.append((original, translated))
        self._refresh()

    def _refresh(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for original, translated in self._entries:
            self._text.insert("end", f"{original}\n")
            if translated:
                self._text.insert("end", f"  -> {translated}\n\n")
            else:
                self._text.insert("end", "\n")
        self._text.configure(state="disabled")
        self._text.see("end")

    def show(self) -> None:
        self.deiconify()
        self.lift()

    def hide(self) -> None:
        self.withdraw()
