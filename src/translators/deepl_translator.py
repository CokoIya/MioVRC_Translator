from __future__ import annotations

import logging
import time

import requests

from src.utils.secure_http import validate_api_base_url
from src.utils.http_session_pool import ThreadLocalSessionPool

from .base import BaseTranslator
from src.utils.input_validation import ValidationError, validate_translation_text

logger = logging.getLogger(__name__)

_DEEPL_SOURCE_LANGS = {
    "ar",
    "bg",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "hu",
    "id",
    "it",
    "ja",
    "ko",
    "lt",
    "lv",
    "nb",
    "nl",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sv",
    "tr",
    "uk",
    "zh",
}

_DEEPL_TARGET_LANGS = {
    **{code: code.upper() for code in _DEEPL_SOURCE_LANGS if code != "en"},
    "en": "EN-US",
    "pt": "PT-PT",
    "zh": "ZH-HANS",
    "zh-cn": "ZH-HANS",
    "zh-hans": "ZH-HANS",
    "zh-tw": "ZH-HANT",
    "zh-hant": "ZH-HANT",
    "yue": "ZH-HANT",
}


class DeepLTranslator(BaseTranslator):
    """DeepL API Free / Pro translator."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api-free.deepl.com/v2",
        timeout_s: float = 10.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self._api_key = str(api_key or "").strip()
        if not self._api_key:
            raise ValueError("DeepL API Key is not configured")
        self._base_url = validate_api_base_url(
            base_url or "https://api-free.deepl.com/v2",
            label="DeepL API",
        )
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        session_headers = {
            "Authorization": f"DeepL-Auth-Key {self._api_key}",
            "User-Agent": "MioTranslator/1.3",
        }

        def session_factory():
            session = requests.Session()
            session.headers.update(session_headers)
            return session

        self._session_pool = ThreadLocalSessionPool(session_factory)
        self.model = "deepl"

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

        target = self._target_language(tgt_lang)
        source = self._source_language(src_lang)
        cache_model = f"{self.model}:{target}:{source or 'auto'}"
        cached = self._get_cached_translation(text, src_lang, tgt_lang, cache_model)
        if cached is not None:
            return cached

        payload = {
            "text": text,
            "target_lang": target,
            "preserve_formatting": "1",
        }
        if source:
            payload["source_lang"] = source

        translated = self._request_translation(payload)
        translated = self._finalize_translation_output(translated, source_text=text)
        if not translated:
            raise RuntimeError("DeepL returned an empty translation")
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
                    data=payload,
                    timeout=self._timeout_s,
                )
                if response.status_code == 456:
                    raise RuntimeError("DeepL quota exceeded")
                response.raise_for_status()
                data = response.json()
                translations = data.get("translations", [])
                if not translations:
                    raise RuntimeError("DeepL response did not include translations")
                text = translations[0].get("text", "")
                logger.info(
                    "DeepL translation finished (elapsed=%.2fs)",
                    time.perf_counter() - started,
                )
                return str(text or "")
            except Exception as exc:
                last_exc = exc
                logger.warning("DeepL translation attempt failed: %s", exc)
                if attempt < self._max_retries:
                    time.sleep(min(0.25 * (attempt + 1), 1.0))
        raise RuntimeError(f"DeepL translation failed: {last_exc}") from last_exc

    def _source_language(self, code: str) -> str:
        normalized = self._normalize_language_code(code)
        if not normalized or normalized == "auto":
            return ""
        if normalized == "yue":
            normalized = "zh"
        if normalized in _DEEPL_SOURCE_LANGS:
            return normalized.upper()
        return normalized.upper()

    def _target_language(self, code: str) -> str:
        raw = str(code or "").strip().lower().replace("_", "-")
        if raw in _DEEPL_TARGET_LANGS:
            return _DEEPL_TARGET_LANGS[raw]
        normalized = self._normalize_language_code(raw)
        if not normalized or normalized == "auto":
            raise ValueError("DeepL target language must be configured")
        return _DEEPL_TARGET_LANGS.get(normalized, normalized.upper())
