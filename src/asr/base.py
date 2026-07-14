from __future__ import annotations

import gc
import logging
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Optional

import numpy as np


ProgressCallback = Callable[[dict[str, object]], None]
logger = logging.getLogger(__name__)


def close_runtime_resource(resource: object | None) -> None:
    """Best-effort close for a detached provider-owned native resource.

    ASR providers are replaced while the application is running, and closed
    provider instances can remain referenced by lifecycle bookkeeping.  Merely
    dropping the provider's model/client attribute is therefore not enough when
    the resource exposes an explicit ``close`` method.
    """

    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("ASR runtime resource close failed", exc_info=True)


def release_runtime_memory(*, device: object = "") -> None:
    """Collect detached model cycles and release an existing CUDA cache.

    This intentionally does not import torch during teardown; it only asks an
    already-loaded runtime to return unused CUDA allocator blocks.
    """

    gc.collect()

    if not str(device or "").strip().lower().startswith("cuda"):
        return
    torch = sys.modules.get("torch")
    cuda = getattr(torch, "cuda", None) if torch is not None else None
    empty_cache = getattr(cuda, "empty_cache", None)
    if callable(empty_cache):
        try:
            empty_cache()
        except Exception:
            logger.debug("CUDA cache release after ASR shutdown failed", exc_info=True)


class ASRProvider(ABC):
    """Common interface for local and online speech-to-text providers."""

    provider_id = "base"
    display_name = "ASR"
    is_streaming = False
    requires_api_key = False
    supports_partial = True
    max_concurrent_transcriptions = 1

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        del progress_callback

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass

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
        """Optional cancellable final-recognition entry point.

        Providers with asynchronous network transports can override this to
        honor the scheduler cancellation event and attach request diagnostics.
        Local and legacy providers keep the historical synchronous behavior.
        """

        del request_context, cancel_event
        return self.transcribe(
            audio,
            sample_rate=sample_rate,
            language=language,
            is_final=is_final,
        )

    def cancel_pending_requests(self) -> None:
        """Best-effort interruption hook used before realtime worker joins."""

        return None

    @property
    def is_loaded(self) -> bool:
        return True


class StreamingASRProvider(ASRProvider):
    """Interface for future providers that receive audio frames continuously."""

    is_streaming = True

    def start(
        self,
        language: Optional[str] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        raise NotImplementedError

    def push_audio(self, pcm16: bytes, sample_rate: int = 16000) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.stop()
