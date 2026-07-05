from __future__ import annotations

import logging
import time

import requests

from .base import BaseTranslator
from src.utils.input_validation import ValidationError, validate_translation_text

logger = logging.getLogger(__name__)


class GoogleWebTranslator(BaseTranslator):
    """No-key Google Translate web endpoint translator.

    This uses the public web endpoint rather than Google Cloud Translation API.
    It is useful for simple setup where Google services are reachable, but it
    should not be presented as an official SLA-backed Google Cloud API.
    """

    def __init__(
        self,
        base_url: str = "https://translate.googleapis.com/translate_a/single",
        timeout_s: float = 8.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self._base_url = str(base_url or "").strip() or (
            "https://translate.googleapis.com/translate_a/single"
        )
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "MioTranslator/1.3"})
        self.model = "google-web"

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
        cache_model = f"{self.model}:{source}:{target}"
        cached = self._get_cached_translation(text, src_lang, tgt_lang, cache_model)
        if cached is not None:
            return cached

        payload = {
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "q": text,
        }
        translated = self._request_translation(payload)
        translated = self._finalize_translation_output(translated, source_text=text)
        if not translated:
            raise RuntimeError("Google Web returned an empty translation")
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
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._session.get(
                    self._base_url,
                    params=payload,
                    timeout=self._timeout_s,
                )
                if response.status_code == 429:
                    raise RuntimeError("Google Web rate limit reached")
                response.raise_for_status()
                translated = self._parse_response(response.json())
                logger.info(
                    "Google Web translation finished (elapsed=%.2fs)",
                    time.perf_counter() - started,
                )
                return translated
            except Exception as exc:
                last_exc = exc
                logger.warning("Google Web translation attempt failed: %s", exc)
                if attempt < self._max_retries:
                    time.sleep(min(0.2 * (attempt + 1), 0.8))
        raise RuntimeError(f"Google Web translation failed: {last_exc}") from last_exc

    @staticmethod
    def _parse_response(data: object) -> str:
        if not isinstance(data, list) or not data:
            raise RuntimeError("Google Web response was not a translation array")
        segments = data[0]
        if not isinstance(segments, list):
            raise RuntimeError("Google Web response did not include text segments")
        translated_parts: list[str] = []
        for segment in segments:
            if isinstance(segment, list) and segment:
                translated_parts.append(str(segment[0] or ""))
        return "".join(translated_parts).strip()

    def _language(self, code: str, *, allow_auto: bool) -> str:
        raw = str(code or "").strip().lower().replace("_", "-")
        if not raw or raw == "auto":
            if allow_auto:
                return "auto"
            raise ValueError("Google Web target language must be configured")
        if raw in {"zh", "zh-cn", "zh-hans", "cn"}:
            return "zh-CN"
        if raw in {"zh-tw", "zh-hant", "yue"}:
            return "zh-TW"
        normalized = self._normalize_language_code(raw)
        if not normalized or normalized == "auto":
            if allow_auto:
                return "auto"
            raise ValueError("Google Web target language must be configured")
        return normalized
