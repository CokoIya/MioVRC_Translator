from __future__ import annotations

import logging
import time

import requests

from .base import BaseTranslator, TransformationOutputRejected
from src.utils.http_session_pool import ThreadLocalSessionPool
from src.utils.input_validation import ValidationError, validate_translation_text
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_network import configure_requests_session_for_url
from src.utils.provider_warmup import warmup_requests_session

logger = logging.getLogger(__name__)

class MicrosoftEdgeTranslator(BaseTranslator):
    """No-key translator backed by Microsoft Edge's web translation endpoint.

    The endpoint is an undocumented Edge browser service, not the supported
    Azure AI Translator API. It is intentionally presented as a web provider
    without an availability guarantee.
    """

    def __init__(
        self,
        base_url: str = "https://edge.microsoft.com/translate/translatetext",
        timeout_s: float = 8.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self._base_url = str(base_url or "").strip() or (
            "https://edge.microsoft.com/translate/translatetext"
        )
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)

        def session_factory():
            session = requests.Session()
            session.headers.update({"User-Agent": "MioTranslator/1.3"})
            return configure_requests_session_for_url(session, self._base_url)

        self._session_pool = ThreadLocalSessionPool(session_factory)
        # Matches the catalog id shown in settings; it also prefixes the
        # translation cache key, so it must stay in step with that catalog.
        self.model = "bing"

    def prewarm(self) -> bool:
        """Warm DNS/TCP/TLS without occupying Edge's translation route.

        A real translation POST can remain in flight for tens of seconds and
        competes with the player's first request when several realtime clients
        start together.  A short, non-redirecting HEAD keeps startup bounded
        and retains the transport session without sending synthetic text.
        """

        result = warmup_requests_session(
            self._session_pool.get(),
            self._base_url,
            method="HEAD",
            timeout_s=min(self._timeout_s, 3.0),
        )
        logger.log(
            logging.INFO if result.succeeded else logging.WARNING,
            "Microsoft Edge Web translation prewarm %s "
            "(probe_status=%s probe_route_accepted=%s elapsed_ms=%.0f error_type=%s method=HEAD)",
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
        cache_model = f"{self.model}:{source or 'auto'}:{target}"
        cached = self._get_cached_translation(text, src_lang, tgt_lang, cache_model)
        if cached is not None:
            return cached

        params = {
            "to": target,
            "isEnterpriseClient": "false",
        }
        if source:
            params["from"] = source
        translated = self._translate_with_source_recovery(
            params,
            text,
            source=source,
            target_language=tgt_lang,
        )
        if not translated:
            raise RuntimeError("Microsoft Edge Web returned an empty translation")
        translated = self._store_cached_translation(
            text,
            src_lang,
            tgt_lang,
            cache_model,
            translated,
        )
        self._remember_context_turn(text, translated, src_lang, tgt_lang)
        return translated

    def _translate_with_source_recovery(
        self,
        params: dict[str, str],
        text: str,
        *,
        source: str,
        target_language: str,
    ) -> str:
        """Translate, retrying once with language detection on a wrong result.

        A declared source language that does not match the audio makes the
        endpoint echo the input back untranslated. Reverse translation hears
        whichever language the other player happens to speak, so a stale hint
        must not cost the whole utterance: drop it and let the endpoint detect.
        """

        translated = self._request_translation(params, text)
        try:
            return self._finalize_translation_output_for_target(
                translated,
                source_text=text,
                target_language=target_language,
            )
        except TransformationOutputRejected as exc:
            if not source:
                # Detection was already in use; there is nothing left to relax.
                raise
            logger.info(
                "Microsoft Edge Web output was not in the target language "
                "(reason=%s); retrying with language detection instead of the "
                "configured source",
                exc.reason,
            )

        retry_params = dict(params)
        retry_params.pop("from", None)
        retried = self._request_translation(retry_params, text)
        return self._finalize_translation_output_for_target(
            retried,
            source_text=text,
            target_language=target_language,
        )

    def _request_translation(self, params: dict[str, str], text: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._session_pool.get().post(
                    self._base_url,
                    params=params,
                    json=[text],
                    timeout=self._timeout_s,
                )
                if response.status_code == 429:
                    raise RuntimeError("Microsoft Edge Web rate limit reached")
                response.raise_for_status()
                translated = self._parse_response(response.json())
                logger.info(
                    "Microsoft Edge Web translation finished (elapsed=%.2fs)",
                    time.perf_counter() - started,
                )
                return translated
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Microsoft Edge Web translation attempt failed: %s",
                    safe_exception_summary(exc),
                )
                if attempt < self._max_retries:
                    time.sleep(min(0.2 * (attempt + 1), 0.8))
        raise RuntimeError(
            f"Microsoft Edge Web translation failed: {last_exc}"
        ) from last_exc

    @staticmethod
    def _parse_response(data: object) -> str:
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise RuntimeError(
                "Microsoft Edge Web response was not a translation array"
            )
        translations = data[0].get("translations")
        if not isinstance(translations, list) or not translations:
            raise RuntimeError(
                "Microsoft Edge Web response did not include translations"
            )
        first = translations[0]
        if not isinstance(first, dict) or not isinstance(first.get("text"), str):
            raise RuntimeError(
                "Microsoft Edge Web response did not include translated text"
            )
        return str(first["text"]).strip()

    def _language(self, code: str, *, allow_auto: bool) -> str:
        raw = str(code or "").strip().lower().replace("_", "-")
        if not raw or raw == "auto":
            if allow_auto:
                # Omitting `from` enables Edge endpoint language detection.
                return ""
            raise ValueError("Microsoft Edge Web target language must be configured")
        if raw in {"zh", "zh-cn", "zh-hans", "cn"}:
            return "zh-Hans"
        if raw in {"zh-tw", "zh-hant"}:
            return "zh-Hant"
        normalized = self._normalize_language_code(raw)
        if not normalized or normalized == "auto":
            if allow_auto:
                return ""
            raise ValueError("Microsoft Edge Web target language must be configured")
        return normalized
