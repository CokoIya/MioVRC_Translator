from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

from src.utils import config_manager
from src.utils.i18n import tr
from src.utils.ui_config import (
    BACKEND_ORDER,
    DEFAULT_ASR_ENGINE,
    OUTPUT_FORMAT_OPTIONS,
    TARGET_LANGUAGE_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    get_backend_label,
    get_backend_value,
    get_ui_language,
    normalize_backend,
    normalize_output_format,
)

BG_PRIMARY = "#f7f5f0"
BG_SECONDARY = "#edeae2"
GLASS_BG = "#daeaf8"
GLASS_BORDER = "#8ab8d8"
GLASS_HOVER = "#c4dcf2"
ACCENT = "#3a9fd8"
ACCENT_HOVER = "#2882bc"
TEXT_PRI = "#252535"
TEXT_SEC = "#686880"

ASR_ENGINES = [("SenseVoice Small", DEFAULT_ASR_ENGINE)]


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: dict, on_save=None):
        super().__init__(parent)
        self._config = config
        self._on_save = on_save
        self._ui_lang = get_ui_language(config)

        self.title(tr(self._ui_lang, "settings_window_title"))
        self.geometry("560x460")
        self.resizable(False, False)
        self.grab_set()
        self.configure(fg_color=BG_PRIMARY)

        self._field_vars: dict[str, ctk.StringVar] = {}
        self._editable_backend_entries: list[ctk.CTkEntry] = []
        self._readonly_backend_entries: list[ctk.CTkEntry] = []
        self._build()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

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

        section_label(self._t("app_language"))
        ui_lang_labels = [label for label, _ in UI_LANGUAGE_OPTIONS]
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_var = ctk.StringVar(
            value=self._ui_lang_reverse.get(self._ui_lang, ui_lang_labels[0])
        )
        ctk.CTkOptionMenu(
            scroll,
            values=ui_lang_labels,
            variable=self._ui_lang_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
        ).pack(**pad, fill="x")

        section_label(self._t("translation_backend"))
        backend = normalize_backend(trans_cfg.get("backend"))
        backend_labels = [get_backend_label(code) for code in BACKEND_ORDER]
        self._backend_codes = {
            get_backend_label(code): code for code in BACKEND_ORDER
        }
        self._backend_reverse = {code: get_backend_label(code) for code in BACKEND_ORDER}
        self._backend_var = ctk.StringVar(value=self._backend_reverse.get(backend, backend_labels[0]))
        self._backend_menu = ctk.CTkOptionMenu(
            scroll,
            values=backend_labels,
            variable=self._backend_var,
            command=self._on_backend_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
        )
        self._backend_menu.pack(**pad, fill="x")

        section_label(self._t("target_language"))
        lang_labels = [label for label, _ in TARGET_LANGUAGE_OPTIONS]
        self._lang_codes = {label: code for label, code in TARGET_LANGUAGE_OPTIONS}
        self._lang_reverse = {code: label for label, code in TARGET_LANGUAGE_OPTIONS}
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

        section_label(self._t("output_format"))
        format_labels = [label for label, _ in OUTPUT_FORMAT_OPTIONS]
        self._fmt_codes = {label: code for label, code in OUTPUT_FORMAT_OPTIONS}
        self._fmt_reverse = {code: label for label, code in OUTPUT_FORMAT_OPTIONS}
        current_format = normalize_output_format(trans_cfg.get("output_format"))
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
        self._build_hint_box(scroll, self._t("output_hint"))

        section_label(self._t("translation_backend_params"))
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

        section_label(self._t("asr_backend"))
        asr_labels = [label for label, _ in ASR_ENGINES]
        self._asr_codes = {label: code for label, code in ASR_ENGINES}
        self._asr_reverse = {code: label for label, code in ASR_ENGINES}
        current_engine = asr_cfg.get("engine", DEFAULT_ASR_ENGINE)
        self._asr_var = ctk.StringVar(
            value=self._asr_reverse.get(current_engine, asr_labels[0])
        )
        self._asr_menu = ctk.CTkOptionMenu(
            scroll,
            values=asr_labels,
            variable=self._asr_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            corner_radius=10,
            text_color=TEXT_PRI,
            width=440,
            state="disabled",
        )
        self._asr_menu.pack(**pad, fill="x")

        self._asr_hint_label = ctk.CTkLabel(
            self._build_hint_box(scroll, ""),
            text=self._t("asr_hint_sensevoice"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=500,
        )
        self._asr_hint_label.pack(padx=10, pady=8, anchor="w")

        section_label(self._t("streaming_params"))
        self._chunk_interval_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_interval_ms", 250))
        )
        self._chunk_window_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_window_s", 1.6))
        )
        self._partial_hits_var = ctk.StringVar(
            value=str(streaming_cfg.get("partial_stability_hits", 2))
        )

        self._build_entry(scroll, self._t("partial_refresh_interval"), self._chunk_interval_var, **pad)
        self._build_entry(scroll, self._t("recognition_window_length"), self._chunk_window_var, **pad)
        self._build_entry(scroll, self._t("partial_hits"), self._partial_hits_var, **pad)
        self._build_hint_box(scroll, self._t("streaming_hint"))

        section_label(self._t("vad_silence_threshold"))
        self._vad_var = ctk.StringVar(
            value=str(self._config.get("audio", {}).get("vad_silence_threshold", 0.8))
        )
        self._build_entry(scroll, self._t("vad_silence_label"), self._vad_var, **pad)

        btn_frame = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)

        ctk.CTkButton(
            btn_frame,
            text=self._t("save"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=12,
            text_color="#ffffff",
            command=self._save,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_frame,
            text=self._t("cancel"),
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

    def _translation_locked(self) -> bool:
        return self._fmt_codes.get(self._fmt_var.get(), OUTPUT_FORMAT_OPTIONS[0][1]) == "original_only"

    def _add_backend_field(
        self,
        label_text: str,
        value: str,
        *,
        secret: bool = False,
        readonly: bool = False,
        bind_key: str | None = None,
    ) -> None:
        ctk.CTkLabel(
            self._fields_frame,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=4, pady=(6, 0))

        variable = ctk.StringVar(value=value)
        entry = ctk.CTkEntry(
            self._fields_frame,
            textvariable=variable,
            show="*" if secret else "",
            fg_color=BG_SECONDARY if readonly else GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_SEC if readonly else TEXT_PRI,
            state="disabled" if readonly else "normal",
        )
        entry.pack(fill="x", padx=4, pady=(0, 2))
        if bind_key is not None:
            self._field_vars[bind_key] = variable
            self._editable_backend_entries.append(entry)
        else:
            self._readonly_backend_entries.append(entry)

    def _on_backend_change(self, selected_label: str):
        for widget in self._fields_frame.winfo_children():
            widget.destroy()

        self._field_vars = {}
        self._editable_backend_entries = []
        self._readonly_backend_entries = []

        backend = self._backend_codes.get(selected_label, BACKEND_ORDER[0])
        trans_cfg = self._config.get("translation", {})
        backend_cfg = trans_cfg.get(backend, {})

        self._add_backend_field(
            self._t("api_key"),
            str(backend_cfg.get("api_key", "")),
            secret=True,
            bind_key="api_key",
        )
        self._add_backend_field(
            self._t("base_url"),
            get_backend_value(backend, "base_url"),
            readonly=True,
        )
        self._add_backend_field(
            self._t("model"),
            get_backend_value(backend, "model"),
            readonly=True,
        )
        self._build_hint_box(self._fields_frame, self._t("fixed_by_backend"))
        self._apply_translation_mode_state()

    def _on_output_format_change(self, _selected_label: str):
        self._apply_translation_mode_state()

    def _apply_translation_mode_state(self):
        locked = self._translation_locked()
        state = "disabled" if locked else "normal"

        self._backend_menu.configure(state=state)
        for entry in self._editable_backend_entries:
            entry.configure(
                state=state,
                fg_color=BG_SECONDARY if locked else GLASS_BG,
                text_color=TEXT_SEC if locked else TEXT_PRI,
            )

        if hasattr(self, "_translation_lock_label"):
            self._translation_lock_label.configure(
                text=self._t("translation_lock_on") if locked else self._t("translation_lock_off")
            )

    def _parse_positive_float(self, value: str, field_name: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_number", field=field_name)) from exc
        if parsed <= 0:
            raise ValueError(self._t("must_be_positive", field=field_name))
        return parsed

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_integer", field=field_name)) from exc
        if parsed <= 0:
            raise ValueError(self._t("must_be_positive", field=field_name))
        return parsed

    def _save(self):
        backend = self._backend_codes.get(self._backend_var.get(), BACKEND_ORDER[0])
        target_lang = self._lang_codes.get(
            self._lang_var.get(), TARGET_LANGUAGE_OPTIONS[0][1]
        )
        output_format = self._fmt_codes.get(
            self._fmt_var.get(), OUTPUT_FORMAT_OPTIONS[0][1]
        )
        asr_engine = self._asr_codes.get(self._asr_var.get(), DEFAULT_ASR_ENGINE)
        ui_language = self._ui_lang_codes.get(
            self._ui_lang_var.get(), UI_LANGUAGE_OPTIONS[0][1]
        )

        try:
            vad_threshold = self._parse_positive_float(
                self._vad_var.get(), self._t("vad_silence_label")
            )
            chunk_interval_ms = self._parse_positive_int(
                self._chunk_interval_var.get(),
                self._t("partial_refresh_interval"),
            )
            chunk_window_s = self._parse_positive_float(
                self._chunk_window_var.get(),
                self._t("recognition_window_length"),
            )
            partial_hits = self._parse_positive_int(
                self._partial_hits_var.get(),
                self._t("partial_hits"),
            )
        except ValueError as exc:
            messagebox.showerror(self._t("error_title"), str(exc))
            return

        if chunk_window_s * 1000 < chunk_interval_ms:
            messagebox.showerror(
                self._t("error_title"),
                self._t("window_must_not_be_less_than_interval"),
            )
            return

        cfg = self._config
        translation_cfg = cfg.setdefault("translation", {})
        translation_cfg["backend"] = backend
        translation_cfg["target_language"] = target_lang
        translation_cfg["output_format"] = normalize_output_format(output_format)
        translation_cfg.setdefault(backend, {})
        translation_cfg[backend]["api_key"] = self._field_vars["api_key"].get().strip()
        translation_cfg[backend]["base_url"] = get_backend_value(backend, "base_url")
        translation_cfg[backend]["model"] = get_backend_value(backend, "model")

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

        ui_cfg = cfg.setdefault("ui", {})
        ui_cfg["language"] = ui_language
        ui_cfg["language_source"] = "manual"
        ui_cfg.setdefault("osc_guide_seen", False)

        config_manager.save_config(cfg)
        if self._on_save:
            self._on_save(cfg)
        self.destroy()

