from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from src.utils.i18n import tr
from .window_effects import apply_window_icon

WINDOW_BG = "#f6f8fb"
CARD_BG = "#ffffff"
CARD_BORDER = "#e4e9f0"
HISTORY_BG = "#f6f8fb"
HISTORY_ITEM_BG = "#ffffff"
HISTORY_ITEM_SELECTED_BG = "#f4f8fd"
HISTORY_INCOMING_BG = "#ffffff"
HISTORY_OUTGOING_BG = "#f6f9fe"
HISTORY_ERROR_BG = "#fff6f7"
HEADER_CHIP_BG = "#f1f4f8"
ACTION_BG = "#fafbfd"
TEXT_PRI = "#10243b"
TEXT_SEC = "#58708b"
TEXT_SOFT = "#95a5b7"
ACCENT = "#0a84ff"
ACCENT_HOVER = "#006ae6"
SOURCE_COLORS = {
    "listen": "#0f766e",
    "mic": "#1d4ed8",
    "error": "#b91c1c",
}
SOURCE_TITLES = {
    "listen": {
        "zh-CN": "反向翻译",
        "en": "VRC Listen",
        "ja": "逆翻訳",
        "ru": "Обратный перевод",
        "ko": "역방향 번역",
    },
    "mic": {
        "zh-CN": "麦克风",
        "en": "Microphone",
        "ja": "マイク",
        "ru": "Микрофон",
        "ko": "마이크",
    },
    "error": {
        "zh-CN": "错误",
        "en": "Error",
        "ja": "エラー",
        "ru": "Ошибка",
        "ko": "오류",
    },
}
WINDOW_COPY = {
    "history_subtitle": {
        "zh-CN": "左边显示收到的反向翻译，右边显示你这边的发送记录。",
        "en": "Incoming reverse translations stay on the left, and your side stays on the right.",
        "ja": "左に受信した逆翻訳、右にこちら側の送信履歴を表示します。",
        "ru": "Слева показывается входящий обратный перевод, справа — ваши отправленные записи.",
        "ko": "왼쪽은 받은 역번역, 오른쪽은 내 쪽 기록을 보여 줍니다.",
    },
    "history_count": {
        "zh-CN": "最近 {count}/{max} 条",
        "en": "Recent {count}/{max}",
        "ja": "直近 {count}/{max} 件",
        "ru": "Недавние {count}/{max}",
        "ko": "최근 {count}/{max}개",
    },
    "history_empty": {
        "zh-CN": "暂无聊天记录",
        "en": "No chat history yet",
        "ja": "履歴はまだありません",
        "ru": "История пока пуста",
        "ko": "아직 기록이 없습니다",
    },
    "history_resend_idle": {
        "zh-CN": "点选一条记录后，可重新发送到 VRC",
        "en": "Select a record to resend it to VRC",
        "ja": "履歴を選ぶと VRC へ再送できます",
        "ru": "Выберите запись, чтобы отправить ее в VRC еще раз",
        "ko": "기록을 선택하면 VRC로 다시 보낼 수 있습니다",
    },
    "history_resend_ready": {
        "zh-CN": "已选中，可发送到 VRC 进行确认",
        "en": "Selected and ready to resend to VRC",
        "ja": "選択済みです。VRC へ再送できます",
        "ru": "Запись выбрана и готова к повторной отправке в VRC",
        "ko": "선택되었습니다. VRC로 다시 보낼 수 있습니다",
    },
    "history_preview_empty": {
        "zh-CN": "选中一条记录后，这里会显示将要重新发送的内容。",
        "en": "Select a record to preview the text that will be resent.",
        "ja": "履歴を選ぶと、ここに再送する内容を表示します。",
        "ru": "Выберите запись, и здесь появится текст для повторной отправки.",
        "ko": "기록을 선택하면 여기에서 다시 보낼 내용을 미리 볼 수 있습니다.",
    },
}

MAX_HISTORY = 15


class FloatingWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        ui_language: str,
        on_resend: Callable[[str, str], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self._ui_lang = ui_language
        self._last_text = ""
        self._visible = False
        self._history: deque[dict[str, object]] = deque(maxlen=MAX_HISTORY)
        self._history_seq = 0
        self._selected_history_id: int | None = None
        self._on_resend = on_resend
        self._on_close = on_close
        self._last_layout_width = 0
        self._layout_refresh_after_id: str | None = None

        self.title(tr(self._ui_lang, "floating_window_title"))
        apply_window_icon(self)
        self.geometry("480x520+24+96")
        self._popup_size = (480, 520)
        self.resizable(True, True)
        self.minsize(340, 320)
        self.attributes("-topmost", True)
        self.configure(fg_color=WINDOW_BG)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        outer = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=24,
            border_width=1,
            border_color=CARD_BORDER,
        )
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        header_frame = ctk.CTkFrame(outer, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        header_frame.columnconfigure(0, weight=1)

        self._header_label = ctk.CTkLabel(
            header_frame,
            text=tr(self._ui_lang, "floating_window_header"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        self._header_label.grid(row=0, column=0, sticky="w")

        self._header_count_label = ctk.CTkLabel(
            header_frame,
            text=self._copy("history_count", count=0, max=MAX_HISTORY),
            text_color=TEXT_SEC,
            fg_color=HEADER_CHIP_BG,
            corner_radius=999,
            font=ctk.CTkFont(size=10, weight="bold"),
            padx=10,
            pady=4,
        )
        self._header_count_label.grid(row=0, column=1, sticky="e")

        self._header_subtitle_label = ctk.CTkLabel(
            header_frame,
            text=self._copy("history_subtitle"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
            wraplength=360,
        )
        self._header_subtitle_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self._scroll_frame = ctk.CTkScrollableFrame(
            outer,
            fg_color=HISTORY_BG,
            corner_radius=16,
        )
        self._scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._scroll_frame.columnconfigure(0, weight=1)

        self._actions_frame = ctk.CTkFrame(
            outer,
            fg_color=ACTION_BG,
            corner_radius=18,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self._actions_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._actions_frame.columnconfigure(0, weight=1)
        self._actions_frame.columnconfigure(1, weight=0)

        self._selection_preview_label = ctk.CTkLabel(
            self._actions_frame,
            text=self._copy("history_preview_empty"),
            text_color=TEXT_SOFT,
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
            wraplength=360,
        )
        self._selection_preview_label.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=12,
            pady=(10, 4),
        )

        self._selection_hint_label = ctk.CTkLabel(
            self._actions_frame,
            text=self._copy("history_resend_idle"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
            wraplength=280,
        )
        self._selection_hint_label.grid(row=1, column=0, sticky="ew", padx=(12, 10), pady=(0, 10))

        self._send_selected_button = ctk.CTkButton(
            self._actions_frame,
            text=tr(self._ui_lang, "send_to_vrc"),
            width=124,
            height=32,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            state="disabled",
            command=self._send_selected_history,
        )
        self._send_selected_button.grid(row=1, column=1, sticky="e", padx=(0, 12), pady=(0, 10))

        opacity_frame = ctk.CTkFrame(outer, fg_color="transparent")
        opacity_frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
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

        self.bind("<Configure>", self._on_window_configure)
        self.withdraw()
        self._refresh_history()

    def _copy(self, key: str, **kwargs) -> str:
        values = WINDOW_COPY.get(key, {})
        if self._ui_lang in values:
            template = values[self._ui_lang]
        else:
            base_lang = self._ui_lang.split("-", 1)[0]
            template = next(
                (
                    text
                    for lang, text in values.items()
                    if lang.split("-", 1)[0] == base_lang
                ),
                values.get("en", key),
            )
        if kwargs:
            return template.format(**kwargs)
        return template

    def _on_window_configure(self, event) -> None:
        if event.widget is not self:
            return
        width = int(getattr(event, "width", 0) or 0)
        if width <= 0 or abs(width - self._last_layout_width) < 24:
            return
        self._last_layout_width = width
        self._update_wraplengths()
        if self._layout_refresh_after_id is not None:
            try:
                self.after_cancel(self._layout_refresh_after_id)
            except Exception:
                pass
        self._layout_refresh_after_id = self.after(60, self._refresh_history)

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

    @staticmethod
    def _entry_payload(entry: dict[str, object]) -> str:
        return str(entry.get("payload", "") or "").strip()

    def _entry_can_resend(self, entry: dict[str, object]) -> bool:
        return bool(self._entry_payload(entry)) and str(entry.get("source", "")) != "error"

    @staticmethod
    def _entry_side(entry: dict[str, object]) -> str:
        source = str(entry.get("source", "listen"))
        if source == "mic":
            return "right"
        return "left"

    @staticmethod
    def _bubble_fill(source: str) -> str:
        if source == "mic":
            return HISTORY_OUTGOING_BG
        if source == "error":
            return HISTORY_ERROR_BG
        return HISTORY_INCOMING_BG

    def _selected_entry(self) -> dict[str, object] | None:
        if self._selected_history_id is None:
            return None
        for entry in self._history:
            if entry.get("id") == self._selected_history_id:
                return entry
        self._selected_history_id = None
        return None

    def _bubble_wraplength(self) -> int:
        width = max(int(self.winfo_width() or 0), self._popup_size[0])
        return max(220, min(int(width * 0.55), 320))

    @staticmethod
    def _preview_text(value: str, limit: int = 120) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _update_header(self) -> None:
        self._header_count_label.configure(
            text=self._copy("history_count", count=len(self._history), max=MAX_HISTORY)
        )

    def _update_wraplengths(self) -> None:
        width = max(int(self.winfo_width() or 0), self._popup_size[0])
        self._header_subtitle_label.configure(wraplength=max(260, width - 140))
        self._selection_preview_label.configure(wraplength=max(220, width - 160))

    def _update_actions(self) -> None:
        entry = self._selected_entry()
        if entry is None or not self._entry_can_resend(entry):
            self._selection_hint_label.configure(text=self._copy("history_resend_idle"))
            self._selection_preview_label.configure(
                text=self._copy("history_preview_empty"),
                text_color=TEXT_SOFT,
            )
            self._send_selected_button.configure(state="disabled")
            return
        self._selection_hint_label.configure(text=self._copy("history_resend_ready"))
        source_title = self._source_title(str(entry.get("source", "listen")))
        preview = self._preview_text(self._entry_payload(entry))
        self._selection_preview_label.configure(
            text=f"{source_title}  {preview}",
            text_color=TEXT_PRI,
        )
        self._send_selected_button.configure(state="normal")

    def _bind_select(self, widget, entry_id: int, can_resend: bool) -> None:
        if not can_resend:
            return
        widget.bind("<Button-1>", lambda _event, value=entry_id: self._select_history_entry(value))

    def _select_history_entry(self, entry_id: int) -> None:
        if self._selected_history_id == entry_id:
            self._selected_history_id = None
        else:
            self._selected_history_id = entry_id
        self._refresh_history()

    def _refresh_history(self) -> None:
        self._layout_refresh_after_id = None
        self._update_wraplengths()
        for widget in self._scroll_frame.winfo_children():
            widget.destroy()

        if self._selected_entry() is None:
            self._selected_history_id = None

        if not self._history:
            lbl = ctk.CTkLabel(
                self._scroll_frame,
                text=self._copy("history_empty"),
                text_color=TEXT_SEC,
                font=ctk.CTkFont(size=12),
            )
            lbl.grid(row=0, column=0, pady=20)
            self._update_header()
            self._update_actions()
            return

        wraplength = self._bubble_wraplength()
        for idx, entry in enumerate(self._history):
            source = str(entry.get("source", "listen"))
            color = SOURCE_COLORS.get(source, TEXT_PRI)
            source_title = self._source_title(source)
            time_label = str(entry.get("time_label", "") or "")
            can_resend = self._entry_can_resend(entry)
            is_selected = entry.get("id") == self._selected_history_id
            side = self._entry_side(entry)

            lane = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
            lane.grid(row=idx, column=0, sticky="ew", padx=8, pady=4)
            lane.columnconfigure(0, weight=1)
            lane.columnconfigure(1, weight=1)

            bubble = ctk.CTkFrame(
                lane,
                fg_color=HISTORY_ITEM_SELECTED_BG if is_selected else self._bubble_fill(source),
                corner_radius=20,
                border_width=1,
                border_color="#dbe4ef" if is_selected else CARD_BORDER,
            )
            bubble.columnconfigure(0, weight=1)
            bubble_width_pad = (4, 44) if side == "left" else (44, 4)
            bubble.grid(
                row=0,
                column=0 if side == "left" else 1,
                sticky="w" if side == "left" else "e",
                padx=bubble_width_pad,
            )

            meta_row = ctk.CTkFrame(bubble, fg_color="transparent")
            meta_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))
            meta_row.columnconfigure(0, weight=1)

            meta_label = ctk.CTkLabel(
                meta_row,
                text=source_title,
                text_color=color,
                font=ctk.CTkFont(size=10, weight="bold"),
                anchor="w" if side == "left" else "e",
                justify="left" if side == "left" else "right",
            )
            time_text = ctk.CTkLabel(
                meta_row,
                text=time_label,
                text_color=TEXT_SOFT,
                font=ctk.CTkFont(size=10),
                anchor="e" if side == "left" else "w",
            )
            if side == "left":
                meta_label.grid(row=0, column=0, sticky="w")
                time_text.grid(row=0, column=1, sticky="e", padx=(10, 0))
            else:
                time_text.grid(row=0, column=0, sticky="w")
                meta_label.grid(row=0, column=1, sticky="e", padx=(10, 0))

            text_label = ctk.CTkLabel(
                bubble,
                text=str(entry.get("text", "")),
                text_color=TEXT_PRI,
                font=ctk.CTkFont(size=13),
                justify="left",
                anchor="nw" if side == "left" else "ne",
                wraplength=wraplength,
            )
            text_label.grid(
                row=1,
                column=0,
                sticky="ew",
                padx=12,
                pady=(0, 10),
            )

            self._bind_select(lane, int(entry.get("id", 0)), can_resend)
            self._bind_select(bubble, int(entry.get("id", 0)), can_resend)
            self._bind_select(meta_label, int(entry.get("id", 0)), can_resend)
            self._bind_select(time_text, int(entry.get("id", 0)), can_resend)
            self._bind_select(text_label, int(entry.get("id", 0)), can_resend)

        self._update_header()
        self._update_actions()
        self._scroll_frame.after(60, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        try:
            self._scroll_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _send_selected_history(self) -> None:
        entry = self._selected_entry()
        if entry is None or not self._entry_can_resend(entry):
            self._update_actions()
            return
        if self._on_resend is None:
            return
        self._on_resend(self._entry_payload(entry), str(entry.get("source", "listen")))

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = ui_language
        self.title(tr(self._ui_lang, "floating_window_title"))
        self._header_label.configure(text=tr(self._ui_lang, "floating_window_header"))
        self._header_subtitle_label.configure(text=self._copy("history_subtitle"))
        self._opacity_title_label.configure(text=tr(self._ui_lang, "opacity") + ":")
        self._send_selected_button.configure(text=tr(self._ui_lang, "send_to_vrc"))
        self._refresh_history()

    def show_translation(
        self,
        text: str,
        *,
        source: str = "listen",
        payload: str | None = None,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._last_text = message
        self.add_history_entry(message, source=source, payload=payload)
        if not self._visible:
            self.deiconify()
            self._visible = True
        self.lift()

    def add_history_entry(
        self,
        text: str,
        *,
        source: str = "listen",
        payload: str | None = None,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._history_seq += 1
        if payload is None:
            payload = "" if source == "error" else message
        self._history.append(
            {
                "id": self._history_seq,
                "text": message,
                "source": str(source or "listen"),
                "payload": str(payload or "").strip(),
                "time_label": datetime.now().strftime("%H:%M"),
            }
        )
        if self._selected_entry() is None:
            self._selected_history_id = None
        self._refresh_history()

    def reveal(self) -> None:
        if not self._visible:
            self.deiconify()
            self._visible = True
        self.lift()

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self.withdraw()
        if self._on_close is not None:
            self._on_close()
