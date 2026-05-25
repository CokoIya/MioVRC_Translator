from __future__ import annotations


class ASRError(Exception):
    """Base error for ASR provider failures."""


class ASRMissingAPIKeyError(ASRError):
    """An online ASR provider needs an API key before it can run."""


class ASRRateLimitError(ASRError):
    """The provider rejected the request because of quota or rate limits."""


class ASRNetworkError(ASRError):
    """The provider could not be reached or timed out."""


class ASRProviderError(ASRError):
    """The provider returned an unexpected error."""


class ASRUnsupportedRuntimeError(ASRError):
    """The current runtime cannot use this ASR provider."""


class ASRPermissionError(ASRError):
    """The provider cannot access the required microphone or browser permission."""


class ASRConfigurationError(ASRError):
    """The provider configuration is incomplete or invalid."""
