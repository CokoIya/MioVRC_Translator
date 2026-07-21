from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping

import requests

from .base import BaseTranslator
from src.utils.http_session_pool import ThreadLocalSessionPool
from src.utils.input_validation import ValidationError, validate_translation_text
from src.utils.lang_detect import detect_language
from src.utils.provider_diagnostics import safe_exception_summary

logger = logging.getLogger(__name__)

_NUMERIC_PREFIX_RE = re.compile(r"^\s*\d+\s*(?::|\uff1a)\s*")


class MyMemoryTranslator(BaseTranslator):
    """MyMemory public translation API translator.

    The endpoint works without a player-provided API key. MyMemory requires an
    explicit source language, so "auto" is mapped through Mio's lightweight
    local script detector for common chat languages.
    """

    def __init__(
        self,
        base_url: str = "https://api.mymemory.translated.net/get",
        contact_email: str = "",
        timeout_s: float = 8.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self._base_url = str(base_url or "").strip() or (
            "https://api.mymemory.translated.net/get"
        )
        self._contact_email = str(contact_email or "").strip()
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        def session_factory():
            session = requests.Session()
            session.headers.update({"User-Agent": "MioTranslator/1.3"})
            return session

        self._session_pool = ThreadLocalSessionPool(session_factory)
        self.model = "mymemory"

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

        source = self._source_language(src_lang, text)
        target = self._language(tgt_lang, allow_auto=False)
        if source.lower() == target.lower():
            return text
        cache_model = f"{self.model}:{source}:{target}"
        cached = self._get_cached_translation(text, src_lang, tgt_lang, cache_model)
        if cached is not None:
            return cached

        payload = {
            "q": text,
            "langpair": f"{source}|{target}",
        }
        if self._contact_email:
            payload["de"] = self._contact_email

        translated = self._request_translation(payload)
        translated = self._finalize_translation_output(translated, source_text=text)
        if not translated:
            raise RuntimeError("MyMemory returned an empty translation")
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
                response = self._session_pool.get().get(
                    self._base_url,
                    params=payload,
                    timeout=self._timeout_s,
                )
                response.raise_for_status()
                data = response.json()
                status = str(data.get("responseStatus", "")).strip()
                if status != "200":
                    details = str(data.get("responseDetails") or "unknown error")
                    raise RuntimeError(f"MyMemory API error: {details}")
                if bool(data.get("quotaFinished")):
                    raise RuntimeError("MyMemory quota is exhausted")
                translated = self._extract_translation(data)
                logger.info(
                    "MyMemory translation finished (elapsed=%.2fs)",
                    time.perf_counter() - started,
                )
                return translated
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "MyMemory translation attempt failed: %s",
                    safe_exception_summary(exc),
                )
                if attempt < self._max_retries:
                    time.sleep(min(0.25 * (attempt + 1), 1.0))
        raise RuntimeError(f"MyMemory translation failed: {last_exc}") from last_exc

    def _source_language(self, code: str, text: str) -> str:
        raw = str(code or "").strip().lower().replace("_", "-")
        if not raw or raw == "auto":
            raw = detect_language(text)
        return self._language(raw, allow_auto=False)

    def _language(self, code: str, *, allow_auto: bool) -> str:
        raw = str(code or "").strip().lower().replace("_", "-")
        if not raw or raw == "auto":
            if allow_auto:
                return "auto"
            raise ValueError("MyMemory source and target languages must be configured")
        if raw in {"zh", "zh-cn", "zh-hans", "cn"}:
            return "zh-CN"
        if raw in {"zh-tw", "zh-hant", "yue"}:
            return "zh-TW"
        normalized = self._normalize_language_code(raw)
        if not normalized or normalized == "auto":
            if allow_auto:
                return "auto"
            raise ValueError("MyMemory source and target languages must be configured")
        return normalized

    def _extract_translation(self, data: Mapping[str, object]) -> str:
        response_data = data.get("responseData")
        if not isinstance(response_data, Mapping):
            raise RuntimeError("MyMemory response did not include responseData")

        raw_response = str(response_data.get("translatedText") or "")
        cleaned_response = self._clean_artifacts(raw_response)
        response_had_artifact = cleaned_response != raw_response.strip()
        if not response_had_artifact and cleaned_response:
            return cleaned_response

        response_match = self._float_value(response_data.get("match"), 0.0)
        candidates: list[tuple[float, float, int, str]] = []
        matches = data.get("matches")
        if isinstance(matches, list):
            for index, item in enumerate(matches):
                if not isinstance(item, Mapping):
                    continue
                raw_translation = str(item.get("translation") or "")
                cleaned = self._clean_artifacts(raw_translation)
                if not cleaned:
                    continue
                if cleaned != raw_translation.strip():
                    continue
                match = self._float_value(item.get("match"), 0.0)
                quality = self._float_value(item.get("quality"), 0.0)
                candidates.append((match, quality, -index, cleaned))

        minimum_match = max(0.8, response_match - 0.05)
        strong_candidates = [
            candidate for candidate in candidates if candidate[0] >= minimum_match
        ]
        if strong_candidates:
            strong_candidates.sort(reverse=True)
            return strong_candidates[0][3]
        if cleaned_response:
            return cleaned_response
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][3]
        return ""

    @staticmethod
    def _clean_artifacts(text: str) -> str:
        return _NUMERIC_PREFIX_RE.sub("", str(text or "").strip()).strip()

    @staticmethod
    def _float_value(value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
