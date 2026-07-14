from __future__ import annotations


class ASRError(Exception):
    """Base error for ASR provider failures."""


class ASRMissingAPIKeyError(ASRError):
    """An online ASR provider needs an API key before it can run."""


class ASRRateLimitError(ASRError):
    """The provider rejected the request because of quota or rate limits."""


class ASRNetworkError(ASRError):
    """The provider could not be reached or timed out."""


class ASRTemporaryUnavailableError(ASRError):
    """A realtime provider request was cancelled or exceeded its hard deadline.

    This is intentionally separate from :class:`ASRNetworkError`: automatic
    local-model fallback can take many seconds to initialize and would turn a
    short realtime timeout into another queue stall.  Callers should surface a
    temporary failure and allow the next sentence to retry the provider.
    """


class ASRProviderError(ASRError):
    """The provider returned an unexpected error."""


class ASRUnsupportedRuntimeError(ASRError):
    """The current runtime cannot use this ASR provider."""


class ASRPermissionError(ASRError):
    """The provider cannot access the required microphone or browser permission."""


class ASRConfigurationError(ASRError):
    """The provider configuration is incomplete or invalid."""
