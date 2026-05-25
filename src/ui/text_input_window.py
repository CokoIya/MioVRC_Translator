from __future__ import annotations

from collections.abc import Callable, Mapping

import customtkinter as ctk

from src.utils.i18n import tr
from .window_effects import apply_window_icon, clamp_window_geometry

BG = "#f7f9fc"
PANEL_BG = "#ffffff"
HEADER_BG = "#edf3fb"
BORDER = "#cbd5e1"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#64748b"
ACCENT = "#0a84ff"
ACCENT_HOVER = "#006ae6"
BTN_BG = "#eef2f7"
BTN_HOVER = "#e2e8f0"

TEXT_INPUT_WINDOW_CONFIG_VERSION = 5
DEFAULT_GEOMETRY = "520x320"
DEFAULT_SIZE = (520, 320)
MIN_SIZE = (300, 180)
MIN_OPACITY = 0.45
MAX_OPACITY = 1.0
DEFAULT_OPACITY = 0.88
SHIFT_KEY_MASK = 0x0001

StateCallback = Callable[[dict[str, object]], None]
SendCallback = Callable[[str, Callable[[bool], None]], bool | None]


def _as_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_OPACITY, min(MAX_OPACITY, parsed))


class TextInputWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        *,
        config: Mapping[str, object] | None,
        ui_lang: str,
        initial_text: str,
        on_send: SendCallback,
        on_state_change: StateCallback,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._ui_lang = ui_lang
        self._on_send = on_send
        self._on_state_change = on_state_change
        self._on_close = on_close
        self._busy = False
        self._closed = False
        self._text_was_sent = False   # guard against show() re-inserting cleared text

        config = config or {}
        self._topmost = bool(config.get("topmost", True))
        self._opacity = _as_float(config.get("opacity"), DEFAULT_OPACITY)

        self.title(self._t("text_input_floating"))
        if config.get("size_version") != TEXT_INPUT_WINDOW_CONFIG_VERSION:
            geometry = DEFAULT_GEOMETRY
        else:
            geometry = str(config.get("geometry") or DEFAULT_GEOMETRY)
        self._has_saved_position = "+" in geometry
        self.geometry(geometry)
        # Parse saved size from geometry string
        try:
            size_part = geometry.split("+")[0] if "+" in geometry else geometry
            w, h = map(int, size_part.split("x"))
            self._popup_size = (max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1]))
        except (ValueError, IndexError):
            self._popup_size = DEFAULT_SIZE
        self.minsize(*MIN_SIZE)
        self.resizable(True, True)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.attributes("-topmost", self._topmost)
        self._apply_opacity()
        apply_window_icon(self)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_propagate(False)
        self._build(initial_text)
        self.bind("<Escape>", lambda _event: self.minimize())
        self.bind("<Control-Return>", lambda _event: self._send())
        # Bug fix: release I-beam cursor when app loses focus so it doesn't
        # interfere with game mouse management (especially in topmost mode).
        self.bind("<FocusOut>", self._on_app_focus_out, add="+")
        self.bind("<FocusIn>", self._on_app_focus_in, add="+")

        self.after(0, self._clamp_to_screen)
        self.after(60, self._focus_textbox)

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _build(self, initial_text: str) -> None:
        body = ctk.CTkFrame(
            self,
            fg_color=PANEL_BG,
            border_width=1,
            border_color=BORDER,
            corner_radius=0,
        )
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._textbox = ctk.CTkTextbox(
            body,
            font=ctk.CTkFont(size=14),
            wrap="word",
            fg_color="#fbfdff",
            text_color=TEXT_PRI,
            border_width=1,
            border_color="#dbe4ef",
            corner_radius=10,
        )
        self._textbox.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 5))
        if initial_text:
            self._textbox.insert("1.0", initial_text)
        self._textbox.bind("<Return>", self._on_textbox_return)
        self._textbox.bind("<KP_Enter>", self._on_textbox_return)

        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        footer.grid_columnconfigure(1, weight=1)

        self._opacity_label = ctk.CTkLabel(
            footer,
            text=self._opacity_label_text(),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=9),
        )
        self._opacity_label.grid(row=0, column=0, sticky="w", padx=(0, 4))

        self._opacity_slider = ctk.CTkSlider(
            footer,
            from_=int(MIN_OPACITY * 100),
            to=int(MAX_OPACITY * 100),
            number_of_steps=int((MAX_OPACITY - MIN_OPACITY) * 100),
            command=self._on_opacity_change,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
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
            fg_color=BTN_BG,
            hover_color=BTN_HOVER,
            corner_radius=9,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=13),
            command=self.toggle_topmost,
        )
        self._pin_button.grid(row=0, column=2, sticky="e", padx=(0, 6))

        self._send_button = ctk.CTkButton(
            footer,
            text=self._t("text_input_send_to_vrc"),
            width=86,
            height=28,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=9,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._send,
        )
        self._send_button.grid(row=0, column=3, sticky="e")

    def _pin_text(self) -> str:
        return self._t("text_input_pin_on" if self._topmost else "text_input_pin_off")

    def _opacity_label_text(self) -> str:
        return self._t("text_input_opacity", pct=int(round(self._opacity * 100)))

    def _apply_opacity(self) -> None:
        try:
            self.attributes("-alpha", self._opacity)
        except Exception:
            pass

    def _focus_textbox(self) -> None:
        if self._closed:
            return
        try:
            self._textbox.focus_set()
        except Exception:
            pass

    def _clamp_to_screen(self) -> None:
        if self._closed:
            return
        try:
            self.update_idletasks()
            # Use saved size to prevent growth, only read actual size if no saved size exists
            width, height = self._popup_size
            # Get current position
            x, y = self.winfo_x(), self.winfo_y()
            # Only center if no saved position AND this is the first positioning
            if not self._has_saved_position and (x, y) == (0, 0):
                parent = self.master
                parent.update_idletasks()
                x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
                y = parent.winfo_rooty() + 56
            safe_x, safe_y, safe_width, safe_height = clamp_window_geometry(
                self,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            self.geometry(f"{safe_width}x{safe_height}+{safe_x}+{safe_y}")
            # Update saved size to match clamped size
            self._popup_size = (safe_width, safe_height)
        except Exception:
            pass
        self._emit_state()

    def _on_opacity_change(self, value: float) -> None:
        self._opacity = _as_float(float(value) / 100.0, DEFAULT_OPACITY)
        self._apply_opacity()
        self._opacity_label.configure(text=self._opacity_label_text())
        self._emit_state()

    def _emit_state(self, *, minimized: bool | None = None) -> None:
        if self._closed:
            return
        state: dict[str, object] = {
            "topmost": self._topmost,
            "opacity": self._opacity,
            "geometry": self.geometry(),
            "size_version": TEXT_INPUT_WINDOW_CONFIG_VERSION,
        }
        if minimized is not None:
            state["minimized"] = minimized
        self._on_state_change(state)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send_button.configure(
            state="disabled" if busy else "normal",
            text=self._t("translating" if busy else "text_input_send_to_vrc"),
        )

    def _clear_text(self) -> None:
        self._textbox.delete("1.0", "end")
        self._text_was_sent = True   # prevent show() from re-inserting on next focus
        self._focus_textbox()

    def _finish_send(self, succeeded: bool = False) -> None:
        if self._closed:
            return
        self._set_busy(False)
        if succeeded:
            self._clear_text()

    def _on_textbox_return(self, event) -> str | None:
        if int(getattr(event, "state", 0) or 0) & SHIFT_KEY_MASK:
            return None
        self._send()
        return "break"

    def _send(self) -> None:
        if self._busy:
            return
        text = self._textbox.get("1.0", "end").strip()
        if not text:
            self._focus_textbox()
            return
        self._set_busy(True)
        accepted = False
        try:
            accepted = bool(self._on_send(text, self._finish_send))
        finally:
            if not accepted:
                self._finish_send(False)

    # ── cursor management ────────────────────────────────────────────────────

    def _on_app_focus_out(self, event) -> None:
        """App lost focus to another window/application."""
        if event.widget is not self:
            return
        try:
            # Switch to arrow so the I-beam doesn't bleed into game mouse mode
            self._textbox.configure(cursor="arrow")
        except Exception:
            pass

    def _on_app_focus_in(self, event) -> None:
        """App regained focus."""
        if event.widget is not self:
            return
        try:
            # Restore text cursor (empty string = widget default = xterm for Text)
            self._textbox.configure(cursor="")
        except Exception:
            pass

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        self.attributes("-topmost", self._topmost)
        self._pin_button.configure(
            text=self._pin_text(),
        )
        self._emit_state()

    def minimize(self) -> None:
        self._emit_state(minimized=True)
        self.withdraw()

    def show(self, initial_text: str = "") -> None:
        # Only pre-fill when the textbox is truly empty AND the user hasn't
        # already sent text this session.  Without the _text_was_sent guard,
        # pressing the hotkey after a send would re-insert the main-window's
        # last speech text because the cleared textbox looks "empty".
        if (initial_text
                and not self._text_was_sent
                and not self._textbox.get("1.0", "end").strip()):
            self._textbox.insert("1.0", initial_text)
        self.deiconify()
        self.lift()
        self.attributes("-topmost", self._topmost)
        self._emit_state(minimized=False)
        self.after(40, self._focus_textbox)

    def close(self) -> None:
        self._emit_state(minimized=False)
        self.destroy()

    def destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._on_close()
        finally:
            super().destroy()
