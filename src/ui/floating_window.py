from __future__ import annotations

from collections import deque
import logging
from typing import Callable

import customtkinter as ctk

from src.utils.i18n import tr
from .window_effects import apply_window_icon
from .text_input_window import (
    ACCENT as INPUT_ACCENT,
    ACCENT_HOVER as INPUT_ACCENT_HOVER,
    BG as INPUT_BG,
    BORDER as INPUT_BORDER,
    BTN_BG as INPUT_BTN_BG,
    BTN_HOVER as INPUT_BTN_HOVER,
    DEFAULT_OPACITY as INPUT_DEFAULT_OPACITY,
    MAX_OPACITY as INPUT_MAX_OPACITY,
    MIN_OPACITY as INPUT_MIN_OPACITY,
    PANEL_BG as INPUT_PANEL_BG,
    TEXT_PRI as INPUT_TEXT_PRI,
    TEXT_SEC as INPUT_TEXT_SEC,
)

logger = logging.getLogger(__name__)

HISTORY_ITEM_SELECTED_BG = "#f4f8fd"
HISTORY_INCOMING_BG = "#ffffff"
HISTORY_OUTGOING_BG = "#f6f9fe"
HISTORY_ERROR_BG = "#fff6f7"

MAX_HISTORY = 15
DEFAULT_SIZE = (520, 320)
MIN_SIZE = (300, 180)


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
        self._topmost = True
        self._opacity = INPUT_DEFAULT_OPACITY
        self._last_layout_width = 0
        self._layout_refresh_after_id: str | None = None
        self._history_widgets: dict[int, dict[str, object]] = {}
        self._pending_scroll_after_id: str | None = None
        self._full_refresh_count = 0
        self._append_update_count = 0
        self._selection_update_count = 0
        self._wrap_update_count = 0

        self.title(tr(self._ui_lang, "floating_window_title"))
        apply_window_icon(self)
        self.geometry(f"{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}+24+96")
        self._popup_size = DEFAULT_SIZE
        self.resizable(True, True)
        self.minsize(*MIN_SIZE)
        self.attributes("-topmost", self._topmost)
        self._apply_opacity()
        self.configure(fg_color=INPUT_BG)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        body = ctk.CTkFrame(
            self,
            fg_color=INPUT_PANEL_BG,
            corner_radius=0,
            border_width=1,
            border_color=INPUT_BORDER,
        )
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._scroll_frame = ctk.CTkScrollableFrame(
            body,
            fg_color="#fbfdff",
            corner_radius=10,
            border_width=1,
            border_color="#dbe4ef",
        )
        self._scroll_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 5))
        self._scroll_frame.columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        footer.columnconfigure(1, weight=1)

        self._opacity_label = ctk.CTkLabel(
            footer,
            text=self._opacity_label_text(),
            text_color=INPUT_TEXT_SEC,
            font=ctk.CTkFont(size=9),
        )
        self._opacity_label.grid(row=0, column=0, sticky="w", padx=(0, 4))

        self._opacity_var = ctk.DoubleVar(value=int(self._opacity * 100))
        self._opacity_slider = ctk.CTkSlider(
            footer,
            from_=int(INPUT_MIN_OPACITY * 100),
            to=int(INPUT_MAX_OPACITY * 100),
            number_of_steps=int((INPUT_MAX_OPACITY - INPUT_MIN_OPACITY) * 100),
            variable=self._opacity_var,
            command=self._on_opacity_change,
            progress_color=INPUT_ACCENT,
            button_color=INPUT_ACCENT,
            button_hover_color=INPUT_ACCENT_HOVER,
            fg_color="#dbe4ef",
            height=14,
            width=54,
        )
        self._opacity_slider.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._opacity_slider.set(int(self._opacity * 100))

        self._pin_button = ctk.CTkButton(
            footer,
            text=self._pin_text(),
            width=30,
            height=28,
            fg_color=INPUT_BTN_BG,
            hover_color=INPUT_BTN_HOVER,
            corner_radius=9,
            text_color=INPUT_TEXT_PRI,
            font=ctk.CTkFont(size=13),
            command=self.toggle_topmost,
        )
        self._pin_button.grid(row=0, column=2, sticky="e", padx=(0, 6))

        self._send_selected_button = ctk.CTkButton(
            footer,
            text=tr(self._ui_lang, "send_to_vrc"),
            width=86,
            height=28,
            fg_color=INPUT_ACCENT,
            hover_color=INPUT_ACCENT_HOVER,
            corner_radius=9,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            state="disabled",
            command=self._send_selected_history,
        )
        self._send_selected_button.grid(row=0, column=3, sticky="e")

        self.bind("<Configure>", self._on_window_configure)
        self.withdraw()
        self._refresh_history()

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
        self._layout_refresh_after_id = self.after(60, self._apply_layout_update)

    def _opacity_label_text(self) -> str:
        return tr(self._ui_lang, "text_input_opacity", pct=int(round(self._opacity * 100)))

    def _pin_text(self) -> str:
        return tr(self._ui_lang, "text_input_pin_on" if self._topmost else "text_input_pin_off")

    def _apply_opacity(self) -> None:
        try:
            self.attributes("-alpha", self._opacity)
        except Exception:
            pass

    def _on_opacity_change(self, value: float) -> None:
        try:
            self._opacity = max(INPUT_MIN_OPACITY, min(INPUT_MAX_OPACITY, float(value) / 100.0))
        except (TypeError, ValueError):
            self._opacity = INPUT_DEFAULT_OPACITY
        self._apply_opacity()
        self._opacity_label.configure(text=self._opacity_label_text())

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        self.attributes("-topmost", self._topmost)
        self._pin_button.configure(text=self._pin_text())

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
        return max(160, min(int(width * 0.72), 440))

    def _update_wraplengths(self) -> None:
        self._update_history_wraplengths()

    def _apply_layout_update(self) -> None:
        self._layout_refresh_after_id = None
        self._update_wraplengths()
        self._update_history_wraplengths()

    def _history_canvas(self):
        return getattr(self._scroll_frame, "_parent_canvas", None)

    def _is_near_bottom(self) -> bool:
        canvas = self._history_canvas()
        if canvas is None:
            return True
        try:
            first, last = canvas.yview()
        except Exception:
            return True
        return last >= 0.96 or (last - first) >= 0.99

    def _schedule_scroll_to_bottom(self) -> None:
        if self._pending_scroll_after_id is not None:
            try:
                self.after_cancel(self._pending_scroll_after_id)
            except Exception:
                pass
        self._pending_scroll_after_id = self.after(16, self._scroll_to_bottom)

    def _update_actions(self) -> None:
        entry = self._selected_entry()
        if entry is None or not self._entry_can_resend(entry):
            self._send_selected_button.configure(state="disabled")
            return
        self._send_selected_button.configure(state="normal")

    def _bind_select(self, widget, entry_id: int, can_resend: bool) -> None:
        if not can_resend:
            return
        widget.bind("<Button-1>", lambda _event, value=entry_id: self._select_history_entry(value))

    def _select_history_entry(self, entry_id: int) -> None:
        previous_id = self._selected_history_id
        if self._selected_history_id == entry_id:
            self._selected_history_id = None
        else:
            self._selected_history_id = entry_id
        self._update_selection_ui(previous_id, self._selected_history_id)

    def _refresh_history(self) -> None:
        self._layout_refresh_after_id = None
        self._update_wraplengths()
        should_scroll = (not self._visible) or self._is_near_bottom()
        self._full_refresh_count += 1
        logger.debug("FloatingWindow full refresh #%s", self._full_refresh_count)
        self._clear_history_widgets()

        if self._selected_entry() is None:
            self._selected_history_id = None

        if not self._history:
            self._update_actions()
            return

        for idx, entry in enumerate(self._history):
            self._append_history_entry_ui(entry, idx)

        self._update_actions()
        if should_scroll:
            self._schedule_scroll_to_bottom()

    def _clear_history_widgets(self) -> None:
        for widgets in self._history_widgets.values():
            lane = widgets.get("lane")
            if lane is not None:
                try:
                    lane.destroy()
                except Exception:
                    pass
        self._history_widgets.clear()

    def _append_history_entry_ui(self, entry: dict[str, object], row_index: int | None = None) -> None:
        entry_id = int(entry.get("id", 0))
        if row_index is None:
            row_index = max(len(self._history_widgets), 0)
        source = str(entry.get("source", "listen"))
        can_resend = self._entry_can_resend(entry)
        side = self._entry_side(entry)

        lane = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        lane.grid(row=row_index, column=0, sticky="ew", padx=8, pady=4)
        lane.columnconfigure(0, weight=1)
        lane.columnconfigure(1, weight=1)

        bubble = ctk.CTkFrame(
            lane,
            corner_radius=20,
            border_width=1,
        )
        bubble.columnconfigure(0, weight=1)
        bubble_width_pad = (4, 44) if side == "left" else (44, 4)
        bubble.grid(
            row=0,
            column=0 if side == "left" else 1,
            sticky="w" if side == "left" else "e",
            padx=bubble_width_pad,
        )

        text_label = ctk.CTkLabel(
            bubble,
            text=str(entry.get("text", "")),
            text_color=INPUT_TEXT_PRI,
            font=ctk.CTkFont(size=13),
            justify="left",
            anchor="nw" if side == "left" else "ne",
            wraplength=self._bubble_wraplength(),
        )
        text_label.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=10,
        )

        self._history_widgets[entry_id] = {
            "lane": lane,
            "bubble": bubble,
            "text_label": text_label,
            "source": source,
            "can_resend": can_resend,
            "row_index": row_index,
        }
        self._style_history_entry(entry_id)
        self._bind_select(lane, entry_id, can_resend)
        self._bind_select(bubble, entry_id, can_resend)
        self._bind_select(text_label, entry_id, can_resend)
        self._append_update_count += 1
        logger.debug("FloatingWindow append update #%s entry_id=%s", self._append_update_count, entry_id)

    def _style_history_entry(self, entry_id: int) -> None:
        widgets = self._history_widgets.get(entry_id)
        if not widgets:
            return
        bubble = widgets.get("bubble")
        source = str(widgets.get("source", "listen"))
        is_selected = entry_id == self._selected_history_id
        if bubble is not None:
            bubble.configure(
                fg_color=HISTORY_ITEM_SELECTED_BG if is_selected else self._bubble_fill(source),
                border_color="#dbe4ef" if is_selected else INPUT_BORDER,
            )

    def _update_selection_ui(self, previous_id: int | None, current_id: int | None) -> None:
        if previous_id is not None:
            self._style_history_entry(previous_id)
        if current_id is not None:
            self._style_history_entry(current_id)
        self._selection_update_count += 1
        logger.debug(
            "FloatingWindow selection update #%s previous=%s current=%s",
            self._selection_update_count,
            previous_id,
            current_id,
        )
        self._update_actions()

    def _update_history_wraplengths(self) -> None:
        wraplength = self._bubble_wraplength()
        for widgets in self._history_widgets.values():
            text_label = widgets.get("text_label")
            if text_label is not None:
                text_label.configure(wraplength=wraplength)
        self._wrap_update_count += 1
        logger.debug("FloatingWindow wrap update #%s wraplength=%s", self._wrap_update_count, wraplength)

    def _remove_history_entry_ui(self, entry_id: int) -> None:
        widgets = self._history_widgets.pop(entry_id, None)
        if not widgets:
            return
        lane = widgets.get("lane")
        if lane is not None:
            try:
                lane.destroy()
            except Exception:
                pass

    def _reindex_history_rows(self) -> None:
        for idx, entry in enumerate(self._history):
            entry_id = int(entry.get("id", 0))
            widgets = self._history_widgets.get(entry_id)
            if not widgets:
                continue
            lane = widgets.get("lane")
            if lane is not None:
                lane.grid_configure(row=idx)
            widgets["row_index"] = idx

    def _scroll_to_bottom(self) -> None:
        self._pending_scroll_after_id = None
        try:
            canvas = self._history_canvas()
            if canvas is not None:
                canvas.yview_moveto(1.0)
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
        self._opacity_label.configure(text=self._opacity_label_text())
        self._pin_button.configure(text=self._pin_text())
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
        should_scroll = (not self._visible) or self._is_near_bottom()
        evicted_id: int | None = None
        if len(self._history) == MAX_HISTORY and self._history:
            evicted_id = int(self._history[0].get("id", 0))
        self._history_seq += 1
        if payload is None:
            payload = "" if source == "error" else message
        entry = {
            "id": self._history_seq,
            "text": message,
            "source": str(source or "listen"),
            "payload": str(payload or "").strip(),
        }
        self._history.append(entry)
        if evicted_id is not None:
            self._remove_history_entry_ui(evicted_id)
            self._reindex_history_rows()
            if self._selected_history_id == evicted_id:
                self._selected_history_id = None
        self._append_history_entry_ui(entry, len(self._history) - 1)
        if self._selected_entry() is None:
            self._selected_history_id = None
        self._update_actions()
        if should_scroll:
            self._schedule_scroll_to_bottom()

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
