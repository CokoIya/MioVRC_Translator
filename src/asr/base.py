from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Optional

import numpy as np


ProgressCallback = Callable[[dict[str, object]], None]


class ASRProvider(ABC):
    """Common interface for local and online speech-to-text providers."""

    provider_id = "base"
    display_name = "ASR"
    is_streaming = False
    requires_api_key = False
    supports_partial = True

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
