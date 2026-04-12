from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from threading import Lock
from time import monotonic

_TRANSLATION_SYSTEM_PROMPT = (
    "You are a real-time translator for VR social chat (VRChat). "
    "Translate only the current utterance, but use recent conversation context when it helps resolve "
    "pronouns, omitted subjects, slang, jokes, internet-native wording, and relationship tone. "
    "Produce natural, modern, colloquial translations that sound like something a real person would casually say. "
    "Prefer everyday spoken wording over formal written phrasing unless the source is clearly formal. "
    "Never sound stiff, word-for-word, or textbook-like. Preserve emotion, humor, slang, internet expressions, "
    "and gaming or VR-specific terms. Keep names, acronyms, product names, community jargon, and standard spellings "
    "in their modern commonly used forms. Return only the translated text with no explanations, notes, quotes, "
    "or repeated context. Do not add decorative punctuation, wrapping quotes, or unmatched brackets that are not required."
)
_CONTEXT_MAX_TURNS = 3
_CONTEXT_MAX_AGE_S = 75.0
_CONTEXT_TEXT_LIMIT = 160
_WRAP_PAIRS = {
    '"': '"',
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    "\u300c": "\u300d",
    "\u300e": "\u300f",
    "(": ")",
    "\uff08": "\uff09",
    "[": "]",
    "\u3010": "\u3011",
    "<": ">",
    "\u300a": "\u300b",
    "\u3008": "\u3009",
}
_TRAILING_ARTIFACT_OPENERS = "".join(ch for ch, closer in _WRAP_PAIRS.items() if ch != closer)
_SENTENCE_ENDING_PUNCT = "\u3002\uff0e.!?\uff01\uff1f\u2026"


class BaseTranslator(ABC):
    def __init__(
        self,
        cache_size: int = 256,
        prompt_profile: dict[str, object] | None = None,
    ):
        self._cache_size = max(int(cache_size), 0)
        self._cache: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()
        self._cache_lock = Lock()
        self._context_lock = Lock()
        self._recent_context: dict[tuple[str, str, str], deque[tuple[float, str, str]]] = {}
        self._prompt_profile = prompt_profile or {}
        self._prompt_signature = json.dumps(
            self._prompt_profile,
            ensure_ascii=False,
            sort_keys=True,
        )

    @abstractmethod
    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        pass

    def _language_name(self, code: str) -> str:
        lang_map = {
            "auto": "Auto-detect",
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

    def _source_language_label(self, code: str) -> str:
        normalized = str(code or "").strip()
        if normalized == "auto":
            return "Auto-detect from the current text"
        return self._language_name(normalized)

    def _build_prompt(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
    ) -> str:
        src = self._source_language_label(src_lang)
        tgt = self._language_name(tgt_lang)
        requirements = [
            "sound natural and colloquial, as a real person would casually say it",
            "prefer everyday spoken wording instead of stiff or bookish phrasing",
            "preserve emotion, tone, humor, slang, and gaming or VR terms",
            "prefer modern internet-native wording and community-standard names when appropriate",
            "keep meaning accurate but prioritize natural flow over word-for-word literalness",
            "use recent context only to disambiguate the current text when helpful",
            "translate only the current text and do not repeat previous lines",
            "do not add decorative quotes or extra punctuation",
            "output only the translation",
        ]
        profile_lines = self._prompt_profile_lines()
        if profile_lines:
            requirements.append("follow the social style instructions below")
        context_lines = self._context_lines(context_snapshot or ())
        return (
            "Translate the following text.\n"
            f"Source language: {src}\n"
            f"Target language: {tgt}\n"
            f"Requirements: {', '.join(requirements)}.\n"
            f"{context_lines}"
            f"{profile_lines}"
            f"Text:\n{text}"
        )

    def _build_messages(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _TRANSLATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._build_prompt(
                    text,
                    src_lang,
                    tgt_lang,
                    context_snapshot=context_snapshot,
                ),
            },
        ]

    def _cache_key(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        model: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> tuple[str, str, str, str]:
        signature = f"{model}|{self._prompt_signature}"
        normalized_source = str(context_source or "").strip() or "default"
        if normalized_source != "default":
            signature = f"{signature}|source={normalized_source}"
        if context_snapshot:
            signature = (
                f"{signature}|ctx="
                f"{json.dumps(context_snapshot, ensure_ascii=False, separators=(',', ':'))}"
            )
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

    @staticmethod
    def _trim_context_text(text: str) -> str:
        normalized = " ".join(str(text or "").split()).strip()
        if len(normalized) <= _CONTEXT_TEXT_LIMIT:
            return normalized
        return normalized[: _CONTEXT_TEXT_LIMIT - 3].rstrip() + "..."

    def _finalize_translation_output(self, text: str, *, source_text: str = "") -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""

        source = " ".join(str(source_text or "").split()).strip()
        source_wrapped = False
        for opener, closer in _WRAP_PAIRS.items():
            if len(source) >= 2 and source.startswith(opener) and source.endswith(closer):
                source_wrapped = True
                break

        if not source_wrapped:
            for opener, closer in _WRAP_PAIRS.items():
                if len(cleaned) >= 2 and cleaned.startswith(opener) and cleaned.endswith(closer):
                    inner = cleaned[1:-1].strip()
                    if inner:
                        cleaned = inner
                    break

        if _TRAILING_ARTIFACT_OPENERS:
            cleaned = re.sub(
                rf"[{re.escape(_TRAILING_ARTIFACT_OPENERS)}]+([{re.escape(_SENTENCE_ENDING_PUNCT)}]+)$",
                r"\1",
                cleaned,
            )
            cleaned = re.sub(
                rf"([{re.escape(_SENTENCE_ENDING_PUNCT)}]+)[{re.escape(_TRAILING_ARTIFACT_OPENERS)}]+$",
                r"\1",
                cleaned,
            )
            while cleaned and cleaned[-1] in _TRAILING_ARTIFACT_OPENERS:
                if source and source.rstrip().endswith(cleaned[-1]):
                    break
                cleaned = cleaned[:-1].rstrip()

        cleaned = re.sub(
            rf"([{re.escape(_SENTENCE_ENDING_PUNCT)}])(?:\1)+$",
            r"\1",
            cleaned,
        )
        return cleaned.strip()

    def _context_key(
        self,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> tuple[str, str, str]:
        return (
            str(src_lang).strip(),
            str(tgt_lang).strip(),
            str(context_source or "").strip() or "default",
        )

    def _prune_context_queue(
        self,
        queue: deque[tuple[float, str, str]],
        now: float | None = None,
    ) -> None:
        cutoff = (monotonic() if now is None else now) - _CONTEXT_MAX_AGE_S
        while queue and queue[0][0] < cutoff:
            queue.popleft()

    def _context_snapshot(
        self,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> tuple[tuple[str, str], ...]:
        key = self._context_key(src_lang, tgt_lang, context_source=context_source)
        now = monotonic()
        with self._context_lock:
            queue = self._recent_context.get(key)
            if not queue:
                return ()
            self._prune_context_queue(queue, now)
            if not queue:
                self._recent_context.pop(key, None)
                return ()
            return tuple((src, translated) for _, src, translated in queue)

    def _context_lines(self, context_snapshot: tuple[tuple[str, str], ...]) -> str:
        if not context_snapshot:
            return ""
        lines = [
            "Recent conversation context (reference only, do not translate these lines again):"
        ]
        for source_text, translated_text in context_snapshot:
            lines.append(f"- Source: {self._trim_context_text(source_text)}")
            lines.append(f"  Translation: {self._trim_context_text(translated_text)}")
        return "\n".join(lines) + "\n"

    def _remember_context_turn(
        self,
        text: str,
        translated: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> None:
        source_text = " ".join(str(text or "").split()).strip()
        translated_text = " ".join(str(translated or "").split()).strip()
        if not source_text or not translated_text:
            return

        key = self._context_key(src_lang, tgt_lang, context_source=context_source)
        now = monotonic()
        with self._context_lock:
            queue = self._recent_context.get(key)
            if queue is None:
                queue = deque(maxlen=_CONTEXT_MAX_TURNS)
                self._recent_context[key] = queue
            self._prune_context_queue(queue, now)
            if queue and queue[-1][1] == source_text and queue[-1][2] == translated_text:
                queue[-1] = (now, source_text, translated_text)
                return
            queue.append((now, source_text, translated_text))

    def _get_cached_translation(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        model: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str | None:
        if self._cache_size <= 0:
            return None
        key = self._cache_key(
            text,
            src_lang,
            tgt_lang,
            model,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
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
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        if self._cache_size <= 0:
            return translated
        key = self._cache_key(
            text,
            src_lang,
            tgt_lang,
            model,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        with self._cache_lock:
            self._cache[key] = translated
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return translated
