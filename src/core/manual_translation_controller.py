from __future__ import annotations

import _thread
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from src.core.output_dispatcher import OutputDispatcher
from src.core.rewrite_coordinator import (
    REWRITE_PRIORITY_TYPED,
    RewriteCallCoordinator,
)
from src.translators.asr_rewriter import (
    ASR_REWRITE_DISABLED,
    normalize_asr_rewrite_style,
)
from src.translators.base import translation_context_scope
from src.utils.lang_detect import detect_language
from src.utils.latency_metrics import (
    merge_translation_metrics,
    translation_metrics_snapshot,
)
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import get_backend_config_value, get_backend_spec, normalize_backend

logger = logging.getLogger(__name__)

_MAX_ACTIVE_WORKERS = 8
_MAX_TYPED_REWRITE_QUEUE_WAIT_S = 2.0


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
    rewrite_typed_text: bool = False
    rewrite_style: str = ASR_REWRITE_DISABLED


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
        rewrite_coordinator: RewriteCallCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._output_dispatcher = output_dispatcher
        self._translator_factory = translator_factory or _create_translator
        self._language_detector = language_detector
        self._error_formatter = error_formatter
        self._rewrite_coordinator = rewrite_coordinator
        self._translator = None
        self._generation = 0
        self._lock = threading.RLock()
        self._active_translator_uses: dict[int, int] = {}
        self._retired_translators: dict[int, Any] = {}
        self._threads: set[threading.Thread] = set()
        self._cleanup_threads: set[threading.Thread] = set()
        self._cleanup_translator_ids: set[int] = set()
        self._pending_cleanup_retries: dict[int, tuple[Any, bool, str]] = {}
        self._cleanup_sequence = 0
        self._state_changed = threading.Condition(self._lock)
        self._cancelled_through_generation = 0
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
        with self._lock:
            if value is not None and self._translator_cleanup_is_supervised_locked(
                value
            ):
                if self._translator is value:
                    self._translator = None
                    self._state_changed.notify_all()
                return
            if self._closed:
                if value is not None:
                    self._schedule_translator_cleanup_locked(
                        value,
                        cancel=True,
                        reason="closed-controller-assignment",
                    )
                value = None
            previous = self._translator
            if previous is value:
                return
            self._translator = value
            if previous is not None:
                identity = id(previous)
                if self._active_translator_uses.get(identity, 0) > 0:
                    self._retired_translators[identity] = previous
                else:
                    self._schedule_translator_cleanup_locked(
                        previous,
                        cancel=False,
                        reason="translator-replaced",
                    )

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
        rewrite_style = normalize_asr_rewrite_style(request.rewrite_style)
        needs_rewrite = bool(request.rewrite_typed_text) and (
            rewrite_style != ASR_REWRITE_DISABLED
        )

        output_original_only = (
            self._output_dispatcher.output_format() == "original_only"
        )
        if output_original_only and not needs_rewrite:
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

        if (
            source_language == target_language
            and not include_second_target
            and not include_third_target
            and not needs_rewrite
        ):
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

        needs_primary_translation = (
            not output_original_only and source_language != target_language
        )
        needs_second_translation = (
            not output_original_only
            and include_second_target
            and source_language != second_target_language
            and second_target_language != target_language
        )
        needs_third_translation = (
            not output_original_only
            and include_third_target
            and bool(third_target_language)
            and source_language != third_target_language
            and third_target_language != target_language
            and not (third_target_language == second_target_language and include_second_target)
        )

        needs_provider = bool(
            needs_rewrite
            or needs_primary_translation
            or needs_second_translation
            or needs_third_translation
        )
        if needs_provider:
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
        submitted_at = time.monotonic()

        def run() -> None:
            worker_started_at = time.monotonic()
            rewrite_duration_s = 0.0
            provider_duration_s = 0.0
            provider_calls = 0
            rewrite_metrics: dict[str, object] = {}
            provider_metrics: dict[str, object] = {}
            outcome = "failed"
            active_translator = translator
            active_translator_leased = translator_leased

            def translate_target(target_language: str) -> str:
                nonlocal provider_duration_s, provider_calls
                if self._request_destructively_cancelled(generation):
                    raise RuntimeError("Manual translation request was cancelled")
                if active_translator is None:
                    raise RuntimeError("Manual translation provider is unavailable")
                started_at = time.monotonic()
                try:
                    with translation_context_scope(
                        session_id="manual",
                        sequence=generation,
                    ):
                        return active_translator.translate(
                            processed_text,
                            source_language,
                            target_language,
                            context_source="manual",
                        )
                finally:
                    provider_duration_s += max(
                        0.0,
                        time.monotonic() - started_at,
                    )
                    provider_calls += 1
                    merge_translation_metrics(
                        provider_metrics,
                        translation_metrics_snapshot(active_translator),
                    )

            try:
                processed_text = src_text
                if needs_rewrite:
                    rewrite_started_at = time.monotonic()
                    try:
                        rewrite = getattr(active_translator, "rewrite_asr", None)
                        if not callable(rewrite):
                            raise RuntimeError(
                                "The selected translation provider cannot rewrite typed text"
                            )

                        def rewrite_operation() -> object:
                            try:
                                with translation_context_scope(
                                    session_id="typed-rewrite",
                                    sequence=generation,
                                ):
                                    return rewrite(
                                        src_text,
                                        rewrite_style,
                                        language_hint=source_language,
                                        context_source="manual",
                                    )
                            finally:
                                merge_translation_metrics(
                                    rewrite_metrics,
                                    translation_metrics_snapshot(active_translator),
                                )

                        coordinator = self._rewrite_coordinator
                        if coordinator is None:
                            rewritten = rewrite_operation()
                        else:
                            rewritten = coordinator.execute(
                                priority=REWRITE_PRIORITY_TYPED,
                                config=self._config,
                                style=rewrite_style,
                                language_hint=source_language,
                                text=src_text,
                                operation=rewrite_operation,
                                wait_timeout_s=min(
                                    self.timeout_seconds(),
                                    _MAX_TYPED_REWRITE_QUEUE_WAIT_S,
                                ),
                            )
                        processed_text = str(rewritten or "").strip() or src_text
                    except Exception as exc:
                        processed_text = src_text
                        logger.warning(
                            "Typed-text style rewrite failed open "
                            "(style=%s error=%s)",
                            rewrite_style,
                            safe_exception_summary(exc),
                        )
                    finally:
                        rewrite_duration_s = max(
                            0.0,
                            time.monotonic() - rewrite_started_at,
                        )
                    if self._translator_cleanup_is_supervised(
                        active_translator,
                        rewrite_metrics,
                    ):
                        if self._request_destructively_cancelled(generation):
                            raise RuntimeError(
                                "Manual translation request was cancelled"
                            )
                        if active_translator_leased:
                            self._release_translator(
                                active_translator,
                                metrics=rewrite_metrics,
                            )
                            active_translator_leased = False
                        active_translator = None
                        if (
                            needs_primary_translation
                            or needs_second_translation
                            or needs_third_translation
                        ):
                            active_translator = self._acquire_translator()
                            active_translator_leased = True

                result = processed_text
                if needs_primary_translation:
                    result = translate_target(target_language)
                result2 = ""
                if not output_original_only and include_second_target:
                    if second_target_language == source_language:
                        result2 = processed_text
                    elif second_target_language == target_language:
                        result2 = result
                    else:
                        result2 = translate_target(second_target_language)
                result3 = ""
                if (
                    not output_original_only
                    and include_third_target
                    and third_target_language
                ):
                    if third_target_language == source_language:
                        result3 = processed_text
                    elif third_target_language == target_language:
                        result3 = result
                    elif third_target_language == second_target_language and include_second_target:
                        result3 = result2
                    else:
                        result3 = translate_target(third_target_language)
                display = self._output_dispatcher.manual_display_text(result, result2, result3)
                outcome = "success"
                self._emit_if_open(
                    self.succeeded,
                    ManualTranslationResult(
                        generation=generation,
                        original_text=processed_text,
                        source_language=source_language,
                        translated_text=result,
                        translated_text_2=result2,
                        translated_text_3=result3,
                        display_text=display,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Manual translation failed (%s)",
                    safe_exception_summary(exc),
                )
                self._emit_if_open(
                    self.failed,
                    ManualTranslationError(generation, exc, self._format_error(exc)),
                )
            finally:
                finished_at = time.monotonic()
                with self._lock:
                    if not self._closed and generation != self._generation:
                        outcome = "stale"
                def metric_ms(metrics: dict[str, object], key: str) -> str:
                    value = metrics.get(key)
                    if value is None:
                        return "na"
                    try:
                        parsed = float(value)
                    except (TypeError, ValueError):
                        return "na"
                    return f"{max(0.0, parsed) * 1000.0:.1f}"

                def metric_count(
                    metrics: dict[str, object],
                    key: str,
                    default: int = 0,
                ) -> int:
                    try:
                        return max(0, int(metrics.get(key, default) or 0))
                    except (TypeError, ValueError):
                        return max(0, int(default))

                logger.info(
                    "Manual translation latency generation=%d outcome=%s "
                    "local_queue_ms=%.1f rewrite_ms=%.1f "
                    "rewrite_pool_wait_ms=%s rewrite_dns_ms=%s "
                    "rewrite_tcp_ms=%s rewrite_tls_ms=%s "
                    "rewrite_response_header_ms=%s "
                    "rewrite_provider_processing_ms=%s "
                    "rewrite_first_token_ms=%s rewrite_full_response_ms=%s "
                    "rewrite_parsing_postprocessing_ms=%.1f "
                    "rewrite_provider_calls=%d rewrite_http_requests=%d "
                    "provider_calls=%d http_requests=%d "
                    "connection_pool_wait_ms=%s dns_ms=%s tcp_ms=%s tls_ms=%s "
                    "response_header_ms=%s provider_processing_ms=%s "
                    "streaming_first_token_ms=%s full_response_ms=%s "
                    "parsing_postprocessing_ms=%.1f provider_wall_ms=%.1f "
                    "local_postprocess_ms=%.1f total_wall_ms=%.1f",
                    generation,
                    outcome,
                    max(0.0, worker_started_at - submitted_at) * 1000.0,
                    rewrite_duration_s * 1000.0,
                    metric_ms(rewrite_metrics, "pool_wait_s"),
                    metric_ms(rewrite_metrics, "dns_s"),
                    metric_ms(rewrite_metrics, "tcp_s"),
                    metric_ms(rewrite_metrics, "tls_s"),
                    metric_ms(rewrite_metrics, "response_headers_s"),
                    metric_ms(rewrite_metrics, "provider_processing_s"),
                    metric_ms(rewrite_metrics, "first_token_s"),
                    metric_ms(rewrite_metrics, "full_response_s"),
                    (
                        float(rewrite_metrics.get("parse_s", 0.0) or 0.0)
                        + float(rewrite_metrics.get("postprocess_s", 0.0) or 0.0)
                    )
                    * 1000.0,
                    metric_count(rewrite_metrics, "provider_calls"),
                    metric_count(rewrite_metrics, "http_request_count"),
                    metric_count(provider_metrics, "provider_calls", provider_calls),
                    metric_count(provider_metrics, "http_request_count"),
                    metric_ms(provider_metrics, "pool_wait_s"),
                    metric_ms(provider_metrics, "dns_s"),
                    metric_ms(provider_metrics, "tcp_s"),
                    metric_ms(provider_metrics, "tls_s"),
                    metric_ms(provider_metrics, "response_headers_s"),
                    metric_ms(provider_metrics, "provider_processing_s"),
                    metric_ms(provider_metrics, "first_token_s"),
                    metric_ms(provider_metrics, "full_response_s"),
                    (
                        float(provider_metrics.get("parse_s", 0.0) or 0.0)
                        + float(provider_metrics.get("postprocess_s", 0.0) or 0.0)
                    )
                    * 1000.0,
                    provider_duration_s * 1000.0,
                    max(
                        0.0,
                        finished_at
                        - worker_started_at
                        - rewrite_duration_s
                        - provider_duration_s,
                    )
                    * 1000.0,
                    max(0.0, finished_at - submitted_at) * 1000.0,
                )
                if active_translator_leased and active_translator is not None:
                    self._release_translator(
                        active_translator,
                        metrics=provider_metrics,
                    )
                self._emit_if_open(self.worker_finished, generation)
                with self._lock:
                    self._threads.discard(threading.current_thread())
                    self._state_changed.notify_all()

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
                self._state_changed.notify_all()
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
                self._state_changed.notify_all()
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

    def cancel_active_requests(self) -> int:
        """Cancel timed-out manual calls and rotate provider clients.

        A UI generation invalidation alone hides a late result but leaves the
        synchronous SDK call, worker slot, and persistent connection alive.
        Detaching the clients before cancellation lets the next request create
        a clean runtime while late workers remain signal-suppressed.
        """

        translators: list[Any] = []
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._cancelled_through_generation = max(
                self._cancelled_through_generation,
                generation,
            )
            for translator in (
                self._translator,
                *self._retired_translators.values(),
            ):
                if translator is not None and all(
                    translator is not existing for existing in translators
                ):
                    translators.append(translator)
            self._translator = None
            self._retired_translators.clear()
            self._active_translator_uses.clear()
            for translator in translators:
                self._schedule_translator_cleanup_locked(
                    translator,
                    cancel=True,
                    reason="manual-timeout",
                )
            self._state_changed.notify_all()
        logger.info(
            "Manual translation requests cancelled generation=%d clients=%d",
            generation,
            len(translators),
        )
        return generation

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

    def _request_destructively_cancelled(self, generation: int) -> bool:
        with self._lock:
            return bool(
                self._closed
                or generation <= self._cancelled_through_generation
            )

    def _translator_cleanup_is_supervised(
        self,
        translator: Any,
        metrics: Mapping[str, object] | None = None,
    ) -> bool:
        with self._lock:
            return self._translator_cleanup_is_supervised_locked(
                translator,
                metrics,
            )

    def _translator_cleanup_is_supervised_locked(
        self,
        translator: Any,
        metrics: Mapping[str, object] | None = None,
    ) -> bool:
        """Return true when destructive cleanup already owns this client."""

        if translator is None:
            return False
        if id(translator) in self._cleanup_translator_ids:
            return True
        retired = getattr(translator, "_pending_requests_retired", None)
        if callable(retired):
            try:
                if bool(retired()):
                    return True
            except Exception as exc:
                logger.debug(
                    "Failed to inspect manual translator retirement (%s)",
                    safe_exception_summary(exc),
                )
                return True
        snapshot: Mapping[str, object]
        if isinstance(metrics, Mapping):
            snapshot = metrics
        else:
            snapshot = translation_metrics_snapshot(translator)
        return bool(snapshot.get("wall_timeout_triggered"))

    def _create_usable_translator_locked(self) -> Any:
        translator = self._translator_factory(self._config)
        if translator is None:
            raise RuntimeError("Translation provider is unavailable")
        if self._translator_cleanup_is_supervised_locked(translator):
            raise RuntimeError("Translation provider returned a retired client")
        return translator

    def _ensure_translator(self) -> Any:
        with self._lock:
            if self._translator_cleanup_is_supervised_locked(self._translator):
                self._translator = None
            if self._translator is None:
                self._translator = self._create_usable_translator_locked()
            return self._translator

    def _acquire_translator(self) -> Any:
        """Lease an isolated client when an older manual request is in flight."""

        with self._lock:
            if self._closed:
                raise RuntimeError("Manual translation controller is closed")
            translator = self._translator
            if self._translator_cleanup_is_supervised_locked(translator):
                self._translator = None
                translator = None
            if translator is None:
                translator = self._create_usable_translator_locked()
                self._translator = translator
            elif self._active_translator_uses.get(id(translator), 0) > 0:
                previous = translator
                candidate = self._create_usable_translator_locked()
                if candidate is not previous:
                    translator = candidate
                    self._translator = candidate
                    self._retired_translators[id(previous)] = previous

            identity = id(translator)
            self._active_translator_uses[identity] = (
                self._active_translator_uses.get(identity, 0) + 1
            )
            return translator

    def _release_translator(
        self,
        translator: Any,
        *,
        metrics: Mapping[str, object] | None = None,
    ) -> None:
        identity = id(translator)
        with self._lock:
            remaining = self._active_translator_uses.get(identity, 0) - 1
            if remaining > 0:
                self._active_translator_uses[identity] = remaining
            else:
                self._active_translator_uses.pop(identity, None)
                retired = self._retired_translators.pop(identity, None)
                cleanup_supervised = self._translator_cleanup_is_supervised_locked(
                    translator,
                    metrics,
                )
                if self._translator is translator and cleanup_supervised:
                    self._translator = None
                if retired is not None and not cleanup_supervised:
                    self._schedule_translator_cleanup_locked(
                        retired,
                        cancel=False,
                        reason="retired-worker-release",
                    )
            self._state_changed.notify_all()

    def _schedule_translator_cleanup_locked(
        self,
        translator: Any,
        *,
        cancel: bool,
        reason: str,
    ) -> bool:
        """Register cleanup before start and never run SDK teardown inline."""

        if translator is None:
            return False
        identity = id(translator)
        if identity in self._cleanup_translator_ids:
            return False
        if self._translator_cleanup_is_supervised_locked(translator):
            return False
        self._cleanup_translator_ids.add(identity)
        return self._launch_translator_cleanup_locked(
            translator,
            cancel=cancel,
            reason=reason,
            identity=identity,
        )

    def _launch_translator_cleanup_locked(
        self,
        translator: Any,
        *,
        cancel: bool,
        reason: str,
        identity: int,
    ) -> bool:
        """Launch one already-registered cleanup job."""

        self._pending_cleanup_retries.pop(identity, None)
        self._cleanup_sequence += 1
        sequence = self._cleanup_sequence
        claim_lock = threading.Lock()
        claimed = False
        cleanup: threading.Thread

        def run_cleanup() -> None:
            nonlocal claimed
            with claim_lock:
                if claimed:
                    return
                claimed = True
            try:
                if cancel:
                    cancel_pending = getattr(
                        translator,
                        "cancel_pending_requests",
                        None,
                    )
                    if callable(cancel_pending):
                        try:
                            cancel_pending()
                        except Exception as exc:
                            logger.debug(
                                "Failed to cancel detached manual translator "
                                "reason=%s error=%s",
                                reason,
                                safe_exception_summary(exc),
                            )
                close = getattr(translator, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:
                        logger.debug(
                            "Failed to close detached manual translator "
                            "reason=%s error=%s",
                            reason,
                            safe_exception_summary(exc),
                        )
            finally:
                with self._lock:
                    self._cleanup_threads.discard(cleanup)
                    self._cleanup_translator_ids.discard(identity)
                    self._pending_cleanup_retries.pop(identity, None)
                    self._state_changed.notify_all()

        cleanup = threading.Thread(
            target=run_cleanup,
            daemon=True,
            name=f"manual-translator-cleanup-{sequence}",
        )
        self._cleanup_threads.add(cleanup)
        self._state_changed.notify_all()
        try:
            cleanup.start()
        except BaseException as exc:
            if cleanup.is_alive():
                return True
            logger.warning(
                "Failed to start manual translator cleanup thread; "
                "using emergency cleanup (%s)",
                safe_exception_summary(exc),
            )
            try:
                _thread.start_new_thread(run_cleanup, ())
            except BaseException as fallback_exc:
                self._cleanup_threads.discard(cleanup)
                self._pending_cleanup_retries[identity] = (
                    translator,
                    cancel,
                    reason,
                )
                self._state_changed.notify_all()
                logger.error(
                    "Failed to start emergency manual translator cleanup; "
                    "cleanup remains pending for retry (%s)",
                    safe_exception_summary(fallback_exc),
                )
        return True

    def _retry_pending_cleanup_locked(self) -> None:
        pending = tuple(self._pending_cleanup_retries.items())
        for identity, (translator, cancel, reason) in pending:
            if identity not in self._cleanup_translator_ids:
                self._cleanup_translator_ids.add(identity)
            self._launch_translator_cleanup_locked(
                translator,
                cancel=cancel,
                reason=reason,
                identity=identity,
            )

    def _quiescent_locked(self, current: threading.Thread) -> bool:
        return (
            not any(thread is not current for thread in self._threads)
            and not any(
                thread is not current for thread in self._cleanup_threads
            )
            and not self._pending_cleanup_retries
        )

    def close(self, *, wait_timeout_s: float | None = None) -> bool:
        """Cancel active client work and optionally wait for worker release.

        Normal UI disposal only needs the default non-blocking behavior.  The
        updater passes a bounded wait so a visible installer is never launched
        while a typed-text rewrite or translation request still owns a provider
        connection.
        """

        translators: list[Any] = []
        with self._lock:
            self._retry_pending_cleanup_locked()
            if not self._closed:
                self._closed = True
                self._generation += 1
                self._cancelled_through_generation = max(
                    self._cancelled_through_generation,
                    self._generation,
                )
                for translator in (self._translator, *self._retired_translators.values()):
                    if translator is not None and all(
                        translator is not existing for existing in translators
                    ):
                        translators.append(translator)
                self._translator = None
                self._retired_translators.clear()
                self._active_translator_uses.clear()
                for translator in translators:
                    self._schedule_translator_cleanup_locked(
                        translator,
                        cancel=True,
                        reason="controller-close",
                    )
                self._state_changed.notify_all()

        current = threading.current_thread()
        if wait_timeout_s is not None:
            deadline = time.monotonic() + max(0.0, float(wait_timeout_s))
            retry_delay_s = 0.05
            next_retry_at = time.monotonic() + retry_delay_s
            with self._state_changed:
                while not self._quiescent_locked(current):
                    now = time.monotonic()
                    if (
                        self._pending_cleanup_retries
                        and now >= next_retry_at
                    ):
                        self._retry_pending_cleanup_locked()
                        retry_delay_s = min(retry_delay_s * 2.0, 0.5)
                        next_retry_at = now + retry_delay_s
                        if self._quiescent_locked(current):
                            break
                    remaining = deadline - now
                    if remaining <= 0:
                        break
                    wait_s = min(remaining, 0.05)
                    if self._pending_cleanup_retries:
                        wait_s = min(
                            wait_s,
                            max(0.001, next_retry_at - now),
                        )
                    self._state_changed.wait(timeout=wait_s)

        with self._lock:
            return self._quiescent_locked(current)

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
