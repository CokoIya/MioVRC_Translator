from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from threading import Lock

_TRANSLATION_SYSTEM_PROMPT = (
    "You translate short VR chat messages between languages. "
    "Return only the translated text. Do not add explanations, notes, or quotes."
)


class BaseTranslator(ABC):
    def __init__(self, cache_size: int = 256):
        self._cache_size = max(int(cache_size), 0)
        self._cache: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()
        self._cache_lock = Lock()

    @abstractmethod
    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        pass

    def _language_name(self, code: str) -> str:
        lang_map = {
            "zh": "Chinese",
            "ja": "Japanese",
            "en": "English",
            "ko": "Korean",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "ru": "Russian",
        }
        return lang_map.get(str(code or "").strip(), str(code or "").strip() or "Unknown")

    def _build_prompt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        src = self._language_name(src_lang)
        tgt = self._language_name(tgt_lang)
        return (
            "Translate the following text.\n"
            f"Source language: {src}\n"
            f"Target language: {tgt}\n"
            "Requirements: keep the meaning and tone accurate, and output only the translation.\n"
            f"Text:\n{text}"
        )

    def _build_messages(self, text: str, src_lang: str, tgt_lang: str) -> list[dict[str, str]]:
        content = (
            f"{_TRANSLATION_SYSTEM_PROMPT}\n\n"
            f"{self._build_prompt(text, src_lang, tgt_lang)}"
        )
        return [
            {"role": "user", "content": content},
        ]

    def _cache_key(self, text: str, src_lang: str, tgt_lang: str, model: str) -> tuple[str, str, str, str]:
        return (str(model), str(src_lang), str(tgt_lang), str(text))

    def _get_cached_translation(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        model: str,
    ) -> str | None:
        if self._cache_size <= 0:
            return None
        key = self._cache_key(text, src_lang, tgt_lang, model)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            self._cache.move_to_end(key)
            return cached

    def _store_cached_translation(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        model: str,
        translated: str,
    ) -> str:
        if self._cache_size <= 0:
            return translated
        key = self._cache_key(text, src_lang, tgt_lang, model)
        with self._cache_lock:
            self._cache[key] = translated
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return translated
