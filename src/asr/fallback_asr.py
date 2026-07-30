from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
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
        self._closed = False

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
        if self._closed:
            return False
        if self._using_fallback:
            return bool(getattr(self._ensure_fallback(), "supports_partial", True))
        return bool(getattr(self.primary, "supports_partial", True))

    @property
    def max_concurrent_transcriptions(self) -> int:
        if self._closed:
            return 1
        active = self._ensure_fallback() if self._using_fallback else self.primary
        try:
            return max(
                1,
                min(int(getattr(active, "max_concurrent_transcriptions", 1)), 4),
            )
        except (TypeError, ValueError):
            return 1

    @property
    def is_loaded(self) -> bool:
        if self._closed:
            return False
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
        with self._lock:
            if self._closed:
                raise RuntimeError("Fallback ASR provider is closed")
            fallback = self._fallback
            if fallback is None:
                factory = self._fallback_factory
                if factory is None:
                    raise RuntimeError("Fallback ASR factory is unavailable")
                fallback = factory()
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
        try:
            fallback = self._ensure_fallback()
        except Exception as fallback_exc:
            logger.warning(
                "ASR provider %s failed and the local fallback is unavailable: %s",
                getattr(self.primary, "provider_id", "primary"),
                fallback_exc,
            )
            raise exc from fallback_exc
        if not self._using_fallback:
            logger.warning(
                "ASR provider %s failed; falling back to %s: %s",
                getattr(self.primary, "provider_id", "primary"),
                getattr(fallback, "provider_id", "fallback"),
                exc,
            )
        self._using_fallback = True
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
            if self._closed:
                raise RuntimeError("Fallback ASR provider is closed")
            try:
                self.primary.load(progress_callback=progress_callback)
            except _FALLBACK_ERRORS as exc:
                self._activate_fallback(exc, progress_callback)

    def prewarm(self) -> bool:
        """Warm only the primary provider and keep the fallback lazy."""

        with self._lock:
            if self._closed:
                return False
            primary = self.primary
        return bool(primary.prewarm())

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Fallback ASR provider is closed")
            active = self._ensure_fallback() if self._using_fallback else self.primary
        try:
            return active.transcribe(
                audio,
                sample_rate=sample_rate,
                language=language,
                is_final=is_final,
            )
        except _FALLBACK_ERRORS as exc:
            with self._lock:
                self._activate_fallback(exc)
                fallback = self._ensure_fallback()
            return fallback.transcribe(
                audio,
                sample_rate=sample_rate,
                language=language,
                is_final=is_final,
            )
        except ASRError:
            raise

    def transcribe_realtime(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
        *,
        request_context: Mapping[str, object] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Fallback ASR provider is closed")
            active = self._ensure_fallback() if self._using_fallback else self.primary

        def recognize(provider: ASRProvider) -> str:
            method = getattr(provider, "transcribe_realtime", None)
            if callable(method):
                return method(
                    audio,
                    sample_rate=sample_rate,
                    language=language,
                    is_final=is_final,
                    request_context=request_context,
                    cancel_event=cancel_event,
                )
            return provider.transcribe(
                audio,
                sample_rate=sample_rate,
                language=language,
                is_final=is_final,
            )

        try:
            return recognize(active)
        except _FALLBACK_ERRORS as exc:
            with self._lock:
                self._activate_fallback(exc)
                fallback = self._ensure_fallback()
            return recognize(fallback)
        except ASRError:
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._fallback_factory = None
            providers = [self.primary]
            if self._fallback is not None and self._fallback is not self.primary:
                providers.append(self._fallback)
        for provider in providers:
            try:
                provider.close()
            except Exception:
                logger.debug("Failed to close nested ASR provider", exc_info=True)

    def set_capture_enabled(self, enabled: bool) -> None:
        """Forward optional capture control to browser-owned ASR providers."""

        if self._closed:
            return
        providers = [self.primary]
        if self._fallback is not None:
            providers.append(self._fallback)
        for provider in providers:
            setter = getattr(provider, "set_capture_enabled", None)
            if callable(setter):
                setter(bool(enabled))

    def cancel_pending_requests(self) -> None:
        """Interrupt network work before scheduler shutdown waits for workers."""

        if self._closed:
            return
        providers = [self.primary]
        if self._fallback is not None and self._fallback is not self.primary:
            providers.append(self._fallback)
        for provider in providers:
            cancel = getattr(provider, "cancel_pending_requests", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    logger.debug(
                        "Failed to cancel nested ASR provider requests",
                        exc_info=True,
                    )
