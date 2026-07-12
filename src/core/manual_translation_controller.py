from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from src.core.output_dispatcher import OutputDispatcher
from src.utils.lang_detect import detect_language
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import get_backend_config_value, get_backend_spec, normalize_backend

logger = logging.getLogger(__name__)

_MAX_ACTIVE_WORKERS = 8


def _create_translator(config: dict):
    from src.translators.factory import create_translator

    return create_translator(config)


@dataclass(frozen=True)
class ManualTranslationRequest:
    text: str
    source_language: str | None
    target_language: str
    second_target_language: str = "en"
    third_target_language: str = ""


@dataclass(frozen=True)
class ManualTranslationResult:
    generation: int
    original_text: str
    source_language: str
    translated_text: str
    translated_text_2: str = ""
    translated_text_3: str = ""
    display_text: str = ""


@dataclass(frozen=True)
class ManualTranslationError:
    generation: int
    error: object
    friendly_error: object


class ManualTranslationController(QObject):
    """Runs manual text translation while keeping MainWindow as the UI adapter."""

    started = Signal(int)
    succeeded = Signal(object)
    failed = Signal(object)
    worker_finished = Signal(int)

    def __init__(
        self,
        config: dict,
        output_dispatcher: OutputDispatcher,
        *,
        translator_factory: Callable[[dict], Any] | None = None,
        language_detector: Callable[[str], str] = detect_language,
        error_formatter: Callable[[object], object] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._output_dispatcher = output_dispatcher
        self._translator_factory = translator_factory or _create_translator
        self._language_detector = language_detector
        self._error_formatter = error_formatter
        self._translator = None
        self._generation = 0
        self._lock = threading.RLock()
        self._active_translator_uses: dict[int, int] = {}
        self._retired_translators: dict[int, Any] = {}
        self._threads: set[threading.Thread] = set()
        self._closed = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def translator(self) -> Any:
        with self._lock:
            return self._translator

    @translator.setter
    def translator(self, value: Any) -> None:
        close_now = None
        with self._lock:
            if self._closed:
                close_now = value
                value = None
            previous = self._translator
            if previous is value:
                if close_now is None:
                    return
            self._translator = value
            if previous is not None:
                identity = id(previous)
                if self._active_translator_uses.get(identity, 0) > 0:
                    self._retired_translators[identity] = previous
                else:
                    close_now = previous
        self._close_translator(close_now)

    def start(self, request: ManualTranslationRequest) -> int | None:
        src_text = str(request.text or "").strip()
        if not src_text:
            return None
        with self._lock:
            if self._closed:
                return None

        source_language = request.source_language or self._language_detector(src_text)
        target_language = request.target_language or "ja"
        second_target_language = request.second_target_language or "en"
        third_target_language = request.third_target_language or ""

        if self._output_dispatcher.output_format() == "original_only":
            generation = self._next_generation()
            self._emit_if_open(
                self.succeeded,
                ManualTranslationResult(
                    generation=generation,
                    original_text=src_text,
                    source_language=source_language,
                    translated_text=src_text,
                    display_text=src_text,
                )
            )
            self._emit_if_open(self.worker_finished, generation)
            return generation

        include_second_target = (
            self._output_dispatcher.output_format_uses_second_target()
            or self._output_dispatcher.chatbox_template_uses_second_target()
        )
        include_third_target = bool(third_target_language) and self._output_dispatcher.chatbox_template_uses_third_target()

        if source_language == target_language and not include_second_target and not include_third_target:
            generation = self._next_generation()
            self._emit_if_open(
                self.succeeded,
                ManualTranslationResult(
                    generation=generation,
                    original_text=src_text,
                    source_language=source_language,
                    translated_text=src_text,
                    display_text=src_text,
                )
            )
            self._emit_if_open(self.worker_finished, generation)
            return generation

        needs_primary_translation = source_language != target_language
        needs_second_translation = (
            include_second_target
            and source_language != second_target_language
            and second_target_language != target_language
        )
        needs_third_translation = (
            include_third_target
            and bool(third_target_language)
            and source_language != third_target_language
            and third_target_language != target_language
            and not (third_target_language == second_target_language and include_second_target)
        )

        if needs_primary_translation or needs_second_translation or needs_third_translation:
            with self._lock:
                at_capacity = len(self._threads) >= _MAX_ACTIVE_WORKERS
            if at_capacity:
                exc = RuntimeError("Too many manual translation requests are still active")
                generation = self._next_generation()
                self._emit_if_open(
                    self.failed,
                    ManualTranslationError(generation, exc, self._format_error(exc)),
                )
                self._emit_if_open(self.worker_finished, generation)
                logger.warning(
                    "Manual translation worker limit reached (%d)",
                    _MAX_ACTIVE_WORKERS,
                )
                return generation
            try:
                translator = self._acquire_translator()
            except Exception as exc:
                with self._lock:
                    if self._closed:
                        return None
                generation = self._next_generation()
                self._emit_if_open(
                    self.failed,
                    ManualTranslationError(generation, exc, self._format_error(exc)),
                )
                self._emit_if_open(self.worker_finished, generation)
                return generation
            translator_leased = True
        else:
            translator = self.translator
            translator_leased = False

        generation = self._next_generation()
        self._emit_if_open(self.started, generation)

        def run() -> None:
            try:
                result = src_text
                if needs_primary_translation:
                    result = translator.translate(
                        src_text,
                        source_language,
                        target_language,
                        context_source="manual",
                    )
                result2 = ""
                if include_second_target:
                    if second_target_language == source_language:
                        result2 = src_text
                    elif second_target_language == target_language:
                        result2 = result
                    else:
                        result2 = translator.translate(
                            src_text,
                            source_language,
                            second_target_language,
                            context_source="manual",
                        )
                result3 = ""
                if include_third_target and third_target_language:
                    if third_target_language == source_language:
                        result3 = src_text
                    elif third_target_language == target_language:
                        result3 = result
                    elif third_target_language == second_target_language and include_second_target:
                        result3 = result2
                    else:
                        result3 = translator.translate(
                            src_text,
                            source_language,
                            third_target_language,
                            context_source="manual",
                        )
                display = self._output_dispatcher.manual_display_text(result, result2, result3)
                self._emit_if_open(
                    self.succeeded,
                    ManualTranslationResult(
                        generation=generation,
                        original_text=src_text,
                        source_language=source_language,
                        translated_text=result,
                        translated_text_2=result2,
                        translated_text_3=result3,
                        display_text=display,
                    )
                )
            except Exception as exc:
                logger.warning("Manual translation failed: %s", exc)
                self._emit_if_open(
                    self.failed,
                    ManualTranslationError(generation, exc, self._format_error(exc)),
                )
            finally:
                if translator_leased:
                    self._release_translator(translator)
                with self._lock:
                    self._threads.discard(threading.current_thread())
                self._emit_if_open(self.worker_finished, generation)

        thread = threading.Thread(target=run, daemon=True, name="manual-translate")
        rejected_at_capacity = False
        with self._lock:
            if self._closed:
                if translator_leased:
                    self._release_translator(translator)
                return None
            if len(self._threads) >= _MAX_ACTIVE_WORKERS:
                rejected_at_capacity = True
            else:
                self._threads.add(thread)
        if rejected_at_capacity:
            if translator_leased:
                self._release_translator(translator)
            exc = RuntimeError("Too many manual translation requests are still active")
            self._emit_if_open(
                self.failed,
                ManualTranslationError(generation, exc, self._format_error(exc)),
            )
            self._emit_if_open(self.worker_finished, generation)
            logger.warning(
                "Manual translation worker limit reached (%d)",
                _MAX_ACTIVE_WORKERS,
            )
            return generation
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._threads.discard(thread)
            if translator_leased:
                self._release_translator(translator)
            raise
        return generation

    def timeout_seconds(self) -> float:
        trans_cfg = self._config.get("translation", {})
        if not isinstance(trans_cfg, dict):
            trans_cfg = {}
        backend = normalize_backend(trans_cfg.get("backend"))
        backend_cfg = trans_cfg.get(backend, {})
        if not isinstance(backend_cfg, dict):
            backend_cfg = {}
        spec = get_backend_spec(backend)
        timeout_text = backend_cfg.get("timeout_s")
        if timeout_text is None:
            timeout_text = get_backend_config_value(trans_cfg, backend, "timeout_s")
        try:
            timeout_s = float(timeout_text)
        except (TypeError, ValueError):
            timeout_s = float(spec.get("timeout_s", 15.0))
        try:
            retries = int(backend_cfg.get("max_retries", spec.get("max_retries", 0)))
        except (TypeError, ValueError):
            retries = int(spec.get("max_retries", 0) or 0)
        return max(8.0, min(timeout_s * (max(retries, 0) + 1) + 5.0, 120.0))

    def invalidate(self) -> int:
        return self._next_generation()

    def _next_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def _emit_if_open(self, signal, *args: object) -> bool:
        with self._lock:
            if self._closed:
                return False
            try:
                signal.emit(*args)
                return True
            except RuntimeError:
                logger.debug("Suppressed signal delivery from a disposed manual translator")
                return False

    def _ensure_translator(self) -> Any:
        with self._lock:
            if self._translator is None:
                self._translator = self._translator_factory(self._config)
            return self._translator

    def _acquire_translator(self) -> Any:
        """Lease an isolated client when an older manual request is in flight."""

        with self._lock:
            if self._closed:
                raise RuntimeError("Manual translation controller is closed")
            translator = self._translator
            if translator is None:
                translator = self._translator_factory(self._config)
                self._translator = translator
            elif self._active_translator_uses.get(id(translator), 0) > 0:
                candidate = self._translator_factory(self._config)
                if candidate is not translator:
                    translator = candidate
                    self._retired_translators[id(translator)] = translator

            identity = id(translator)
            self._active_translator_uses[identity] = (
                self._active_translator_uses.get(identity, 0) + 1
            )
            return translator

    def _release_translator(self, translator: Any) -> None:
        close_now = None
        identity = id(translator)
        with self._lock:
            remaining = self._active_translator_uses.get(identity, 0) - 1
            if remaining > 0:
                self._active_translator_uses[identity] = remaining
            else:
                self._active_translator_uses.pop(identity, None)
                close_now = self._retired_translators.pop(identity, None)
        self._close_translator(close_now)

    @staticmethod
    def _close_translator(translator: Any) -> None:
        close = getattr(translator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close manual translator client", exc_info=True)

    def close(self) -> None:
        close_now = None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            previous = self._translator
            self._translator = None
            if previous is not None:
                identity = id(previous)
                if self._active_translator_uses.get(identity, 0) > 0:
                    self._retired_translators[identity] = previous
                else:
                    close_now = previous
        self._close_translator(close_now)

    def _format_error(self, error: object) -> object:
        if self._error_formatter is not None:
            return self._error_formatter(error)
        trans_cfg = self._config.get("translation", {}) if isinstance(self._config, dict) else {}
        if not isinstance(trans_cfg, dict):
            trans_cfg = {}
        return format_translation_error(
            error,
            backend=trans_cfg.get("backend"),
            ui_language=self._config.get("ui", {}).get("language", "zh-CN"),
        )
