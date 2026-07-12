# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from src.translators.factory import create_translator
from src.utils.lang_detect import detect_language
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import normalize_output_format, normalize_output_format_2

logger = logging.getLogger(__name__)


class TranslationPipeline(QObject):
    translation_ready = Signal(str, str)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config
        self._translator = None
        self._busy = False
        self._lock = threading.RLock()
        self._closed = False
        self._generation = 0
        self._active_operations = 0
        self._threads: set[threading.Thread] = set()

    def set_output_format(self, output_format: str) -> None:
        trans_cfg = self._config.setdefault("translation", {})
        trans_cfg["output_format"] = normalize_output_format(output_format)

    def translate(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        trans_cfg = self._config.get("translation", {})
        src_lang = str(trans_cfg.get("source_language") or "auto")
        if src_lang == "auto":
            src_lang = detect_language(text)
        tgt_lang = str(trans_cfg.get("target_language") or "ja")
        if src_lang == tgt_lang or self.output_format() == "original_only":
            return text
        with self._lock:
            if self._closed:
                raise RuntimeError("Translation pipeline is closed")
            self._active_operations += 1
            try:
                if self._translator is None:
                    self._translator = create_translator(self._config)
                translator = self._translator
            except BaseException:
                self._active_operations = max(0, self._active_operations - 1)
                raise
        try:
            return translator.translate(text, src_lang, tgt_lang, context_source="manual")
        finally:
            self._finish_operation()

    def translate_async(self, text: str) -> None:
        with self._lock:
            if self._closed or self._busy:
                return
            self._busy = True
            self._generation += 1
            generation = self._generation
        self._emit_if_current(generation, self.busy_changed, True)

        def run() -> None:
            try:
                result = self.translate(text)
            except Exception as exc:
                trans_cfg = self._config.get("translation", {})
                friendly = format_translation_error(
                    exc,
                    backend=trans_cfg.get("backend"),
                    ui_language=self._config.get("ui", {}).get("language", "zh-CN"),
                )
                logger.warning("Manual translation failed: %s", exc)
                self._emit_if_current(generation, self.error, friendly.inline_message)
            else:
                self._emit_if_current(generation, self.translation_ready, result, text)
            finally:
                with self._lock:
                    current = threading.current_thread()
                    self._threads.discard(current)
                    should_emit = not self._closed and generation == self._generation
                    if should_emit:
                        self._busy = False
                if should_emit:
                    self._emit_if_current(generation, self.busy_changed, False)

        thread = threading.Thread(target=run, daemon=True, name="qt-manual-translate")
        with self._lock:
            if self._closed or generation != self._generation:
                self._busy = False
                return
            self._threads.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._threads.discard(thread)
                if generation == self._generation:
                    self._busy = False
            self._emit_if_current(generation, self.busy_changed, False)
            raise

    def _emit_if_current(self, generation: int, signal, *args: object) -> bool:
        with self._lock:
            if self._closed or generation != self._generation:
                return False
            try:
                signal.emit(*args)
                return True
            except RuntimeError:
                logger.debug("Suppressed signal delivery from a disposed translation pipeline")
                return False

    def _finish_operation(self) -> None:
        close_now = None
        with self._lock:
            self._active_operations = max(0, self._active_operations - 1)
            if self._closed and self._active_operations == 0:
                close_now = self._translator
                self._translator = None
        self._close_translator(close_now)

    @staticmethod
    def _close_translator(translator) -> None:
        close = getattr(translator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close translation pipeline client", exc_info=True)

    def close(self) -> None:
        close_now = None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._busy = False
            if self._active_operations == 0:
                close_now = self._translator
                self._translator = None
        self._close_translator(close_now)

    def format_output(self, translated: str, original: str, translated_2: str = "") -> str:
        fmt = normalize_output_format(self._config.get("translation", {}).get("output_format"))
        translated = str(translated or "")
        original = str(original or "")
        translated_2 = str(translated_2 or "")
        if fmt in ("original_with_translated1_translated2", "translated1_with_translated2", "translated1_with_translated2_original") and translated_2:
            if fmt == "original_with_translated1_translated2":
                return f"{original}（{translated}）（{translated_2}）" if original and translated else original or translated or translated_2
            if fmt == "translated1_with_translated2":
                return f"{translated}（{translated_2}）" if translated else translated_2
            if fmt == "translated1_with_translated2_original":
                return f"{translated}（{translated_2}）（{original}）" if original and translated else translated or translated_2 or original

        if fmt == "original_only":
            return original or translated
        if fmt == "translated_only":
            return translated or original
        if fmt == "original_with_translated":
            return f"{original}({translated})" if original and translated else original or translated
        return f"{translated}({original})" if original and translated else translated or original

    def send_to_vrc(self, text: str) -> str:
        return str(text or "")

    def output_format(self) -> str:
        trans_cfg = self._config.get("translation", {})
        return normalize_output_format(trans_cfg.get("output_format"))
