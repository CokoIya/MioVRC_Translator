"""逆翻訳結果を表示する常時最前面のフローティングウィンドウ  """

from collections import deque

import customtkinter as ctk
from src.utils.i18n import tr

# ── カラーパレット ────────────────────────────────────────────────────────
BG_FLOAT = "#f7f5f0"      # フローティングウィンドウの背景色
BG_TITLEBAR = "#e5e1d8"   # タイトルバーの背景色
GLASS_BG = "#daeaf8"      # 補助 UI 要素の背景色
TEXT_PRI = "#252535"      # 主テキスト色
TEXT_SEC = "#686880"      # 補助テキスト色

MAX_ENTRIES = 5


class FloatingWindow(ctk.CTkToplevel):
    def __init__(self, parent, ui_language: str = "zh-CN"):
        super().__init__(parent)
        self._ui_lang = ui_language
        self.title(tr(self._ui_lang, "floating_window_title"))
        self.geometry("420x300")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.92)
        self.overrideredirect(True)  # ボーダーを非表示にする  
        self.configure(fg_color=BG_FLOAT)

        self._entries: deque = deque(maxlen=MAX_ENTRIES)
        self._build()
        self._start_drag()

    def _build(self):
        # ドラッグ用タイトルバー  
        self._title_bar = ctk.CTkFrame(
            self, height=32, corner_radius=0, fg_color=BG_TITLEBAR,
        )
        self._title_bar.pack(fill="x")
        ctk.CTkLabel(
            self._title_bar, text=tr(self._ui_lang, "floating_window_header"),
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
        ).pack(side="left", padx=10)
        ctk.CTkButton(
            self._title_bar, text="✕", width=30, height=24,
            fg_color="transparent", hover_color="#e05060",
            text_color=TEXT_SEC, corner_radius=6,
            command=self.hide,
        ).pack(side="right", padx=4)

        # 透明度スライダー  
        slider_frame = ctk.CTkFrame(self, fg_color=GLASS_BG, corner_radius=0, height=28)
        slider_frame.pack(fill="x")
        ctk.CTkLabel(
            slider_frame, text=tr(self._ui_lang, "opacity"),
            font=ctk.CTkFont(size=10), text_color=TEXT_SEC,
        ).pack(side="left", padx=8)
        self._slider = ctk.CTkSlider(
            slider_frame, from_=0.3, to=1.0,
            command=self._on_opacity, width=130,
        )
        self._slider.set(0.92)
        self._slider.pack(side="left", padx=4, pady=4)

        # メッセージ表示エリア  
        self._text = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=12), wrap="word",
            state="disabled", fg_color=BG_FLOAT,
            corner_radius=0, text_color=TEXT_PRI,
        )
        self._text.pack(fill="both", expand=True, padx=4, pady=4)

    def _on_opacity(self, val):
        self.attributes("-alpha", float(val))

    def _start_drag(self):
        self._title_bar.bind("<ButtonPress-1>", self._drag_start)
        self._title_bar.bind("<B1-Motion>", self._drag_motion)

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_motion(self, event):
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")

    def add_message(self, original: str, translated: str | None):
        """受信メッセージと翻訳結果を追加する  """
        self._entries.append((original, translated))
        self._refresh()

    def _refresh(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for orig, trans in self._entries:
            self._text.insert("end", f"{orig}\n")
            if trans:
                self._text.insert("end", f"  -> {trans}\n\n")
            else:
                self._text.insert("end", "\n")
        self._text.configure(state="disabled")
        self._text.see("end")

    def show(self):
        self.deiconify()
        self.lift()

    def hide(self):
        self.withdraw()
