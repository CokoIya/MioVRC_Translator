from __future__ import annotations

import logging
import threading
from collections.abc import Mapping

from PySide6.QtCore import QObject, Signal
from src.tts.error_utils import tts_error_code, tts_error_token
from src.utils.provider_diagnostics import safe_exception_summary

logger = logging.getLogger(__name__)

_FAILED_MANAGER_CLEANUP_TIMEOUT_SECONDS = 0.25


class TtsService(QObject):
    speech_started = Signal()
    speech_finished = Signal()
    error = Signal(str)

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config
        self._manager = None
        self._auto_read = bool(config.get("tts", {}).get("auto_read", True))
        self._echo_suppressed = False

    def _ensure_manager(self) -> bool:
        if self._manager is not None:
            return True
        tts_cfg = self._config.get("tts", {})
        engine = str(tts_cfg.get("engine", "qwen_tts")).strip() or "qwen_tts"
        output_device = tts_cfg.get("output_device")
        output_device_name = tts_cfg.get("output_device_name", "")
        prefer_virtual = bool(tts_cfg.get("output_to_vrchat", False))
        monitor_output = bool(tts_cfg.get("monitor_enabled", False))
        perf_cfg = self._config.get("performance", {})
        if not isinstance(perf_cfg, dict):
            perf_cfg = {}

        manager = None
        try:
            from src.tts.manager import TTSManager
            engine_config = tts_cfg.get(engine, {})
            if isinstance(engine_config, dict):
                engine_config = dict(engine_config)
            else:
                engine_config = {}
            manager = TTSManager(
                engine_name=engine,
                cache_enabled=True,
                allow_fallback=False,
                output_device=output_device,
                output_device_name=str(output_device_name or ""),
                prefer_virtual_output=prefer_virtual,
                monitor_output=monitor_output,
                sbv2_device=str(tts_cfg.get("style_bert_vits2", {}).get("device", "cpu")),
                sbv2_bert_language=str(tts_cfg.get("style_bert_vits2", {}).get("bert_language", "jp")),
                engine_config=engine_config,
                max_cache_size_mb=int(perf_cfg.get("tts_cache_max_mb", 24)),
                max_cache_items=int(perf_cfg.get("tts_cache_max_items", 60)),
            )
            if not manager.is_available():
                logger.warning("TTS engine '%s' is not available", engine)
                self._cleanup_unsuccessful_manager(manager)
                return False
            manager.start()
            self._manager = manager
            return True
        except Exception as exc:
            logger.warning(
                "Failed to initialize TTS manager: %s",
                safe_exception_summary(exc),
            )
            self._cleanup_unsuccessful_manager(manager)
            self._manager = None
            return False

    @staticmethod
    def _cleanup_unsuccessful_manager(manager) -> None:
        """Bound cleanup of a manager that was never published to the service."""

        if manager is None:
            return
        try:
            cleanup = getattr(manager, "close", None)
            if not callable(cleanup):
                cleanup = getattr(manager, "stop", None)
            if not callable(cleanup):
                return
            cleanup(timeout_seconds=_FAILED_MANAGER_CLEANUP_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning(
                "Failed to clean up unsuccessful TTS manager (%s)",
                safe_exception_summary(exc),
            )

    def speak(
        self,
        text: str,
        *,
        request_context: Mapping[str, object] | None = None,
    ) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        if not self._ensure_manager():
            self.error.emit(tts_error_token("unavailable"))
            return False
        tts_cfg = self._config.get("tts", {})
        engine_cfg = tts_cfg.get(tts_cfg.get("engine", "qwen_tts"), {})
        if not isinstance(engine_cfg, Mapping):
            engine_cfg = {}
        voice = engine_cfg.get("voice")
        try:
            rate = max(0.5, min(float(engine_cfg.get("rate", 1.0)), 2.0))
        except (TypeError, ValueError):
            rate = 1.0
        try:
            volume = max(0.0, min(float(engine_cfg.get("volume", 0.8)), 1.0))
        except (TypeError, ValueError):
            volume = 0.8
        callback_lock = threading.Lock()
        callback_state: dict[str, object] = {
            "decision_made": False,
            "accepted": False,
            "started_emitted": False,
            "terminal_delivered": False,
            "pending": None,
        }

        def deliver_terminal(success: bool, message: str) -> None:
            if not success:
                self.error.emit(
                    message
                    if str(message or "").startswith("tts_error:")
                    else tts_error_token(tts_error_code(message))
                )
            self.speech_finished.emit()

        def on_done(success: bool, message: str) -> None:
            should_deliver = False
            with callback_lock:
                if bool(callback_state["terminal_delivered"]):
                    return
                if (
                    not bool(callback_state["decision_made"])
                    or (
                        bool(callback_state["accepted"])
                        and not bool(callback_state["started_emitted"])
                    )
                ):
                    callback_state["pending"] = (bool(success), str(message or ""))
                    return
                accepted = bool(callback_state["accepted"])
                if accepted:
                    callback_state["terminal_delivered"] = True
                    should_deliver = True
            if should_deliver:
                deliver_terminal(bool(success), str(message or ""))

        try:
            ok = self._manager.speak(
                text,
                voice=str(voice) if voice else "",
                rate=rate,
                volume=volume,
                callback=on_done,
                request_context=request_context,
            )
            with callback_lock:
                callback_state["decision_made"] = True
                callback_state["accepted"] = bool(ok)
            if ok:
                self.speech_started.emit()
                with callback_lock:
                    callback_state["started_emitted"] = True
                    pending = callback_state["pending"]
                    callback_state["pending"] = None
                    should_deliver = bool(
                        isinstance(pending, tuple)
                        and len(pending) == 2
                        and not callback_state["terminal_delivered"]
                    )
                    if should_deliver:
                        callback_state["terminal_delivered"] = True
                if should_deliver and isinstance(pending, tuple):
                    deliver_terminal(bool(pending[0]), str(pending[1] or ""))
            else:
                with callback_lock:
                    pending = callback_state["pending"]
                    callback_state["pending"] = None
                    should_report = bool(
                        isinstance(pending, tuple)
                        and len(pending) == 2
                        and not bool(pending[0])
                        and not callback_state["terminal_delivered"]
                    )
                    if should_report:
                        callback_state["terminal_delivered"] = True
                if should_report and isinstance(pending, tuple):
                    message = str(pending[1] or "")
                    self.error.emit(
                        message
                        if message.startswith("tts_error:")
                        else tts_error_token(tts_error_code(message))
                    )
            return ok
        except Exception as exc:
            logger.warning(
                "TTS speak failed: %s",
                safe_exception_summary(exc),
            )
            self.error.emit(tts_error_token(tts_error_code(exc)))
            return False

    def stop(self) -> None:
        if self._manager is not None:
            clear_queue = getattr(self._manager, "clear_queue", None)
            if callable(clear_queue):
                clear_queue(
                    message="TTS service stopped.",
                    error_code="stopped",
                )
            stop_playback = getattr(self._manager, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()

    def close(self, timeout_seconds: float | None = None):
        manager = self._manager
        self._manager = None
        if manager is None:
            return None
        close = getattr(manager, "close", None)
        if callable(close):
            if timeout_seconds is None:
                return close()
            return close(timeout_seconds=timeout_seconds)
        stop = getattr(manager, "stop", None)
        if callable(stop):
            if timeout_seconds is None:
                return stop()
            return stop(timeout_seconds=timeout_seconds)
        return None

    def set_auto_read(self, enabled: bool) -> None:
        self._auto_read = bool(enabled)
        self._config.setdefault("tts", {})["auto_read"] = self._auto_read

    def is_echo_suppressed(self) -> bool:
        return self._echo_suppressed
