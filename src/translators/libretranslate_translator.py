from __future__ import annotations

import logging
import time

import requests

from src.utils.secure_http import validate_api_base_url
from src.utils.http_session_pool import ThreadLocalSessionPool
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_warmup import warmup_requests_session

from .base import BaseTranslator
from src.utils.input_validation import ValidationError, validate_translation_text

logger = logging.getLogger(__name__)


class LibreTranslateTranslator(BaseTranslator):
    """LibreTranslate translator.

    This backend is intended for a local/self-hosted LibreTranslate server or a
    trusted public instance. Self-hosting avoids provider-side quota limits.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "http://127.0.0.1:5000",
        timeout_s: float = 10.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self._api_key = str(api_key or "").strip()
        self._base_url = validate_api_base_url(
            base_url or "http://127.0.0.1:5000",
            label="LibreTranslate API",
            allow_private_http=not bool(self._api_key),
        )
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        def session_factory():
            session = requests.Session()
            session.headers.update({"User-Agent": "MioTranslator/1.3"})
            return session

        self._session_pool = ThreadLocalSessionPool(session_factory)
        self.model = "libretranslate"

    def prewarm(self) -> bool:
        """Warm this translation worker's LibreTranslate session."""

        result = warmup_requests_session(
            self._session_pool.get(),
            f"{self._base_url.rstrip('/')}/languages",
            method="HEAD",
            timeout_s=min(self._timeout_s, 3.0),
        )
        logger.log(
            logging.INFO if result.succeeded else logging.WARNING,
            "LibreTranslate prewarm %s "
            "(probe_status=%s probe_route_accepted=%s elapsed_ms=%.0f error_type=%s)",
            "transport reachable" if result.succeeded else "failed",
            result.status_code if result.status_code is not None else "unknown",
            bool(result.status_code is not None and 200 <= result.status_code < 400),
            result.elapsed_s * 1000.0,
            result.error_type or "none",
        )
        return result.succeeded

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        del context_source
        try:
            text = validate_translation_text(text)
        except ValidationError as exc:
            raise ValueError(f"Invalid translation input: {exc}") from exc
        if self._source_matches_target(src_lang, tgt_lang):
            return text

        source = self._language(src_lang, allow_auto=True)
        target = self._language(tgt_lang, allow_auto=False)
        cache_model = f"{self.model}:{self._base_url}:{source}:{target}"
        cached = self._get_cached_translation(text, src_lang, tgt_lang, cache_model)
        if cached is not None:
            return cached

        payload = {
            "q": text,
            "source": source,
            "target": target,
            "format": "text",
        }
        if self._api_key:
            payload["api_key"] = self._api_key

        translated = self._request_translation(payload)
        translated = self._finalize_translation_output_for_target(
            translated,
            source_text=text,
            target_language=tgt_lang,
        )
        if not translated:
            raise RuntimeError("LibreTranslate returned an empty translation")
        translated = self._store_cached_translation(
            text,
            src_lang,
            tgt_lang,
            cache_model,
            translated,
        )
        self._remember_context_turn(text, translated, src_lang, tgt_lang)
        return translated

    def _request_translation(self, payload: dict[str, str]) -> str:
        url = f"{self._base_url}/translate"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._session_pool.get().post(
                    url,
                    json=payload,
                    timeout=self._timeout_s,
                )
                response.raise_for_status()
                data = response.json()
                translated = data.get("translatedText", "")
                logger.info(
                    "LibreTranslate request finished (elapsed=%.2fs)",
                    time.perf_counter() - started,
                )
                return str(translated or "")
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LibreTranslate attempt failed: %s",
                    safe_exception_summary(exc),
                )
                if attempt < self._max_retries:
                    time.sleep(min(0.25 * (attempt + 1), 1.0))
        raise RuntimeError(f"LibreTranslate failed: {last_exc}") from last_exc

    def _language(self, code: str, *, allow_auto: bool) -> str:
        normalized = self._normalize_language_code(code)
        if not normalized or normalized == "auto":
            if allow_auto:
                return "auto"
            raise ValueError("LibreTranslate target language must be configured")
        if normalized == "yue":
            return "zh"
        return normalized
