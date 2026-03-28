from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import OrderedDict
from threading import Lock

_TRANSLATION_SYSTEM_PROMPT = (
    "You are a real-time translator for VR social chat (VRChat). "
    "Produce natural, colloquial translations that sound like something a real person would casually say — "
    "never stiff, word-for-word, or textbook-like. "
    "Preserve emotion, humor, slang, internet expressions, and gaming/VR-specific terms. "
    "Return only the translated text with no explanations, notes, or quotes."
)


class BaseTranslator(ABC):
    def __init__(
        self,
        cache_size: int = 256,
        prompt_profile: dict[str, object] | None = None,
    ):
        self._cache_size = max(int(cache_size), 0)
        self._cache: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()
        self._cache_lock = Lock()
        self._prompt_profile = prompt_profile or {}
        self._prompt_signature = json.dumps(
            self._prompt_profile,
            ensure_ascii=False,
            sort_keys=True,
        )

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
            "pt": "Portuguese",
            "it": "Italian",
            "th": "Thai",
            "vi": "Vietnamese",
            "id": "Indonesian",
            "ms": "Malay",
            "ru": "Russian",
        }
        return lang_map.get(str(code or "").strip(), str(code or "").strip() or "Unknown")

    def _build_prompt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        src = self._language_name(src_lang)
        tgt = self._language_name(tgt_lang)
        requirements = [
            "sound natural and colloquial, as a real person would casually say it",
            "preserve emotion, tone, humor, slang, and gaming/VR terms",
            "keep meaning accurate but prioritize natural flow over word-for-word literalness",
            "output only the translation",
        ]
        profile_lines = self._prompt_profile_lines()
        if profile_lines:
            requirements.append("follow the social style instructions below")
        return (
            "Translate the following text.\n"
            f"Source language: {src}\n"
            f"Target language: {tgt}\n"
            f"Requirements: {', '.join(requirements)}.\n"
            f"{profile_lines}"
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
        signature = f"{model}|{self._prompt_signature}"
        return (signature, str(src_lang), str(tgt_lang), str(text))

    def _prompt_profile_lines(self) -> str:
        if not self._prompt_profile:
            return ""

        lines: list[str] = []
        social_mode = str(self._prompt_profile.get("mode", "standard")).strip()
        if social_mode == "language_exchange":
            lines.append(
                "- Social mode: language exchange. Prefer easy-to-understand, friendly wording "
                "that helps cross-language conversation."
            )
        elif social_mode == "roleplay":
            lines.append(
                "- Social mode: roleplay. Preserve in-character phrasing and roleplay flavor "
                "without changing the original meaning."
            )

        politeness = str(self._prompt_profile.get("politeness", "neutral")).strip()
        politeness_map = {
            "casual": "Use a casual register unless the source is explicitly formal.",
            "polite": "Use a polite register suitable for friendly VR social conversation.",
            "very_polite": "Use a very polite and respectful register.",
        }
        if politeness in politeness_map:
            lines.append(f"- Politeness: {politeness_map[politeness]}")

        tone = str(self._prompt_profile.get("tone", "natural")).strip()
        tone_map = {
            "natural": "Keep the translation natural and conversational.",
            "cute": "Use a lightly cute and playful tone when it fits.",
            "cool": "Use a concise, cool, composed tone when it fits.",
            "host": "Use a clear, welcoming host or guide tone when it fits.",
        }
        if tone in tone_map:
            lines.append(f"- Tone: {tone_map[tone]}")

        persona_name = str(self._prompt_profile.get("persona_name", "")).strip()
        if persona_name:
            lines.append(f"- Persona name: {persona_name}")

        persona_prompt = str(self._prompt_profile.get("persona_prompt", "")).strip()
        if persona_prompt:
            lines.append(f"- Persona notes: {persona_prompt}")

        glossary = self._prompt_profile.get("glossary", ())
        if isinstance(glossary, (list, tuple)) and glossary:
            glossary_items = [
                str(item).strip()
                for item in glossary
                if str(item).strip()
            ]
            if glossary_items:
                lines.append("- Preferred glossary:")
                lines.extend(f"  * {item}" for item in glossary_items)

        if not lines:
            return ""
        return "\n".join(lines) + "\n"

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
