from __future__ import annotations

import base64
import io
import logging
import threading
import wave
from collections.abc import Mapping
from typing import Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.base import ASRProvider, ProgressCallback
from src.asr.model_registry import (
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_DEFAULT_REGION,
    QWEN3_ASR_LEGACY_MODEL_IDS,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)
from src.asr.errors import (
    ASRConfigurationError,
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRProviderError,
    ASRRateLimitError,
)
from src.asr.text_corrections import LayeredASRCorrector

logger = logging.getLogger(__name__)

DEFAULT_MODEL = QWEN3_ASR_DEFAULT_MODEL
DEFAULT_REGION = QWEN3_ASR_DEFAULT_REGION
DEFAULT_TIMEOUT_SECONDS = 15.0

_LANGUAGE_ALIASES = {
    "ja-jp": "ja",
    "jp": "ja",
    "jpn": "ja",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "cn": "zh",
    "en-us": "en",
    "en-gb": "en",
    "ko-kr": "ko",
    "kr": "ko",
}


def _cfg(config: Mapping[str, object] | None) -> Mapping[str, object]:
    asr_cfg = (config or {}).get("asr", {}) if isinstance(config, Mapping) else {}
    if not isinstance(asr_cfg, Mapping):
        return {}
    provider_cfg = asr_cfg.get("qwen3_asr", {})
    return provider_cfg if isinstance(provider_cfg, Mapping) else {}


def _language_code(language: object) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text or text == "auto":
        return ""
    text = _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])
    return text if text in {"ja", "zh", "en", "ko", "ru", "fr", "de", "es"} else ""


def _encode_wav_data_url(audio: np.ndarray, sample_rate: int) -> str:
    arr = np.asarray(audio)
    if arr.size == 0:
        return ""
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    arr = arr.astype(np.float32, copy=False).flatten()
    if arr.size == 0:
        return ""
    if np.nanmax(np.abs(arr)) > 1.5:
        arr = arr / 32768.0
    pcm16 = np.clip(arr, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype("<i2", copy=False)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(max(int(sample_rate or 16000), 1))
        wav_file.writeframes(pcm16.tobytes())
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:audio/wav;base64,{payload}"


class Qwen3ASRProvider(ASRProvider):
    provider_id = "qwen3-asr"
    display_name = "Qwen3-ASR"
    requires_api_key = True
    supports_partial = False

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        corrector: LayeredASRCorrector | None = None,
    ) -> None:
        provider_cfg = _cfg(config)
        self.api_key = str(provider_cfg.get("api_key", "") or "").strip()
        self.model = str(provider_cfg.get("model", DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        if self.model in QWEN3_ASR_LEGACY_MODEL_IDS:
            self.model = DEFAULT_MODEL
        self.region = normalize_qwen3_asr_region(provider_cfg.get("region", DEFAULT_REGION))
        self.base_url = str(provider_cfg.get("base_url", "") or "").strip()
        self.language = _language_code(provider_cfg.get("language", "ja")) or "ja"
        self.timeout_seconds = _float_value(
            provider_cfg.get("timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self._corrector = corrector
        self._client = None
        self._lock = threading.RLock()

    def _resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.region == "custom":
            raise ASRConfigurationError("Qwen3-ASR base_url is required when region is custom")
        return get_qwen3_asr_base_url(self.region)

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        with self._lock:
            if self._client is not None:
                return
            if not self.api_key:
                raise ASRMissingAPIKeyError("Qwen3-ASR API Key is not configured")
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ASRConfigurationError("openai package is required for Qwen3-ASR") from exc

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self._resolved_base_url(),
                timeout=self.timeout_seconds,
            )
            if progress_callback is not None:
                progress_callback({"stage": "ready", "message": "Qwen3-ASR ready"})

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        if not is_final:
            return ""
        data_url = _encode_wav_data_url(audio, sample_rate)
        if not data_url:
            return ""
        with self._lock:
            if self._client is None:
                self.load()
            lang = _language_code(language) or self.language
            extra_body: dict[str, object] = {"asr_options": {"enable_itn": False}}
            if lang:
                extra_body["asr_options"]["language"] = lang
            try:
                completion = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": data_url},
                                }
                            ],
                        }
                    ],
                    extra_body=extra_body,
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                _raise_provider_error(exc)
        content = completion.choices[0].message.content if completion.choices else ""
        text = clean_asr_text(str(content or ""))
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=lang or None)
        return text

    @property
    def is_loaded(self) -> bool:
        return self._client is not None


def _float_value(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _raise_provider_error(exc: Exception) -> None:
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
    except Exception:
        APIConnectionError = APITimeoutError = RateLimitError = AuthenticationError = APIStatusError = ()

    if APIConnectionError and isinstance(exc, (APIConnectionError, APITimeoutError)):
        raise ASRNetworkError(str(exc).strip() or "Qwen3-ASR network error") from exc
    if RateLimitError and isinstance(exc, RateLimitError):
        raise ASRRateLimitError(str(exc).strip() or "Qwen3-ASR rate limit") from exc
    if AuthenticationError and isinstance(exc, AuthenticationError):
        raise ASRMissingAPIKeyError("Qwen3-ASR authentication failed") from exc
    if APIStatusError and isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            raise ASRMissingAPIKeyError("Qwen3-ASR authentication failed") from exc
        if status_code == 429:
            raise ASRRateLimitError("Qwen3-ASR rate limit") from exc
        if status_code and int(status_code) >= 500:
            raise ASRNetworkError("Qwen3-ASR service unavailable") from exc
    raise ASRProviderError(str(exc).strip() or exc.__class__.__name__) from exc
