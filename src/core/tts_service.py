from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


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
        engine = str(tts_cfg.get("engine", "edge")).strip() or "edge"
        output_device = tts_cfg.get("output_device")
        output_device_name = tts_cfg.get("output_device_name", "")
        prefer_virtual = bool(tts_cfg.get("output_to_vrchat", False))
        monitor_output = bool(tts_cfg.get("monitor_enabled", False))
        perf_cfg = self._config.get("performance", {})
        if not isinstance(perf_cfg, dict):
            perf_cfg = {}

        try:
            from src.tts.manager import TTSManager
            self._manager = TTSManager(
                engine_name=engine,
                cache_enabled=True,
                allow_fallback=False,
                output_device=output_device,
                output_device_name=str(output_device_name or ""),
                prefer_virtual_output=prefer_virtual,
                monitor_output=monitor_output,
                sbv2_device=str(tts_cfg.get("style_bert_vits2", {}).get("device", "cpu")),
                sbv2_bert_language=str(tts_cfg.get("style_bert_vits2", {}).get("bert_language", "jp")),
                engine_config=tts_cfg.get(engine, {}),
                max_cache_size_mb=int(perf_cfg.get("tts_cache_max_mb", 24)),
                max_cache_items=int(perf_cfg.get("tts_cache_max_items", 60)),
            )
            if not self._manager.is_available():
                logger.warning("TTS engine '%s' is not available", engine)
                self._manager = None
                return False
            self._manager.start()
            return True
        except Exception as exc:
            logger.warning("Failed to initialize TTS manager: %s", exc)
            self._manager = None
            return False

    def speak(self, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        if not self._ensure_manager():
            return False
        tts_cfg = self._config.get("tts", {})
        engine_cfg = tts_cfg.get(tts_cfg.get("engine", "edge"), {})
        voice = engine_cfg.get("voice")
        rate = float(engine_cfg.get("rate", 1.0))
        volume = float(engine_cfg.get("volume", 0.8))
        try:
            self.speech_started.emit()
            ok = self._manager.speak(
                text,
                voice=str(voice) if voice else "",
                rate=rate,
                volume=volume,
            )
            self.speech_finished.emit()
            return ok
        except Exception as exc:
            logger.warning("TTS speak failed: %s", exc)
            self.error.emit(str(exc))
            return False

    def stop(self) -> None:
        if self._manager is not None:
            stop_playback = getattr(self._manager, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()

    def set_auto_read(self, enabled: bool) -> None:
        self._auto_read = bool(enabled)
        self._config.setdefault("tts", {})["auto_read"] = self._auto_read

    def is_echo_suppressed(self) -> bool:
        return self._echo_suppressed
