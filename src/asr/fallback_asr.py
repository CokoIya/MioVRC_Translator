from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Optional

import numpy as np

from src.asr.base import ASRProvider, ProgressCallback
from src.asr.errors import (
    ASRError,
    ASRConfigurationError,
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRPermissionError,
    ASRProviderError,
    ASRRateLimitError,
    ASRUnsupportedRuntimeError,
)

logger = logging.getLogger(__name__)

_FALLBACK_ERRORS = (
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRPermissionError,
    ASRConfigurationError,
    ASRProviderError,
    ASRRateLimitError,
    ASRUnsupportedRuntimeError,
)


class FallbackASR(ASRProvider):
    """Try a primary provider first, then SenseVoice-style fallback if enabled."""

    def __init__(
        self,
        primary: ASRProvider,
        fallback: ASRProvider | None = None,
        *,
        fallback_factory: Callable[[], ASRProvider] | None = None,
        auto_fallback: bool = True,
    ) -> None:
        if fallback is None and fallback_factory is None:
            raise ValueError("FallbackASR requires a fallback provider or factory")
        self.primary = primary
        self._fallback = fallback
        self._fallback_factory = fallback_factory
        self.auto_fallback = bool(auto_fallback)
        self._using_fallback = False
        self._lock = threading.RLock()

    @property
    def provider_id(self) -> str:
        return self.primary.provider_id

    @property
    def display_name(self) -> str:
        return self.primary.display_name

    @property
    def is_streaming(self) -> bool:
        return bool(getattr(self.primary, "is_streaming", False))

    @property
    def requires_api_key(self) -> bool:
        return bool(getattr(self.primary, "requires_api_key", False))

    @property
    def supports_partial(self) -> bool:
        if self._using_fallback:
            return bool(getattr(self._ensure_fallback(), "supports_partial", True))
        return bool(getattr(self.primary, "supports_partial", True))

    @property
    def is_loaded(self) -> bool:
        active = self._ensure_fallback() if self._using_fallback else self.primary
        return bool(getattr(active, "is_loaded", True))

    @property
    def device(self) -> str:
        active = self._ensure_fallback() if self._using_fallback else self.primary
        return str(getattr(active, "device", ""))

    @property
    def runtime_device(self) -> str:
        active = self._ensure_fallback() if self._using_fallback else self.primary
        return str(getattr(active, "runtime_device", getattr(active, "device", "")))

    def _ensure_fallback(self) -> ASRProvider:
        fallback = self._fallback
        if fallback is None:
            fallback = self._fallback_factory()
            self._fallback = fallback
        return fallback

    def _fallback_allowed(self, exc: BaseException) -> bool:
        return self.auto_fallback and isinstance(exc, _FALLBACK_ERRORS)

    def _activate_fallback(
        self,
        exc: BaseException,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        if not self._fallback_allowed(exc):
            raise exc
        if not self._using_fallback:
            logger.warning(
                "ASR provider %s failed; falling back to %s: %s",
                getattr(self.primary, "provider_id", "primary"),
                getattr(self._ensure_fallback(), "provider_id", "fallback"),
                exc,
            )
        self._using_fallback = True
        fallback = self._ensure_fallback()
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "fallback",
                    "message": str(exc).strip() or exc.__class__.__name__,
                }
            )
        try:
            fallback.load(progress_callback=progress_callback)
        except _FALLBACK_ERRORS:
            raise
        except Exception as load_exc:
            raise ASRProviderError(
                f"Fallback ASR ({getattr(fallback, 'provider_id', 'fallback')}) "
                f"failed to load: {load_exc}"
            ) from load_exc

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        with self._lock:
            try:
                self.primary.load(progress_callback=progress_callback)
            except _FALLBACK_ERRORS as exc:
                self._activate_fallback(exc, progress_callback)

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        with self._lock:
            active = self._ensure_fallback() if self._using_fallback else self.primary
            try:
                return active.transcribe(
                    audio,
                    sample_rate=sample_rate,
                    language=language,
                    is_final=is_final,
                )
            except _FALLBACK_ERRORS as exc:
                self._activate_fallback(exc)
                return self._ensure_fallback().transcribe(
                    audio,
                    sample_rate=sample_rate,
                    language=language,
                    is_final=is_final,
                )
            except ASRError:
                raise

    def close(self) -> None:
        self.primary.close()
        if self._fallback is not None:
            self._fallback.close()
