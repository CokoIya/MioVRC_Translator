from __future__ import annotations

import logging

from .base import TransformationOutputRejected
from .web_translator_base import WebTranslatorBase
from src.utils.input_validation import ValidationError, validate_translation_text

logger = logging.getLogger(__name__)


class MicrosoftEdgeTranslator(WebTranslatorBase):
    """No-key translator backed by Microsoft Edge's web translation endpoint.

    The endpoint is an undocumented Edge browser service, not the supported
    Azure AI Translator API. It is intentionally presented as a web provider
    without an availability guarantee.
    """

    PROVIDER_LABEL = "Microsoft Edge Web"
    RETRY_BASE_DELAY_S = 0.2
    RETRY_MAX_DELAY_S = 0.8

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

        self._init_session_pool(self._base_url, configure_network=True)
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

        return self._prewarm_session(self._base_url)

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
        return self._send_with_retries(
            lambda session: session.post(
                self._base_url,
                params=params,
                json=[text],
                timeout=self._timeout_s,
            ),
            lambda response: self._parse_response(response.json()),
        )

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
