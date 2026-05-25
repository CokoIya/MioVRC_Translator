from __future__ import annotations

import logging

from .base import BaseTranslator, _TRANSLATION_SYSTEM_PROMPT
from src.utils.input_validation import validate_translation_text, ValidationError


logger = logging.getLogger(__name__)


class AnthropicTranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        timeout_s: float = 15.0,
        max_output_tokens: int = 192,
        prompt_profile: dict[str, object] | None = None,
    ):
        super().__init__(prompt_profile=prompt_profile)
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic 未安装，请先执行: pip install anthropic"
            )
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
        self.model = model
        self._max_output_tokens = max(int(max_output_tokens), 48)
        self._last_response_summary = ""

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        # Validate and sanitize input
        try:
            text = validate_translation_text(text)
        except ValidationError as e:
            raise ValueError(f"Invalid translation input: {e}")

        context_snapshot = self._context_snapshot(
            src_lang,
            tgt_lang,
            context_source=context_source,
        )
        cached = self._get_cached_translation(
            text,
            src_lang,
            tgt_lang,
            self.model,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        if cached is not None:
            return cached

        self._last_response_summary = ""
        message = self._client.messages.create(
            model=self.model,
            system=_TRANSLATION_SYSTEM_PROMPT,
            max_tokens=self._max_output_tokens,
            messages=[
                {
                    "role": "user",
                    "content": self._build_prompt(
                        text,
                        src_lang,
                        tgt_lang,
                        context_snapshot=context_snapshot,
                        context_source=context_source,
                    ),
                }
            ],
        )
        output = self._message_output_text(message)
        translated = self._finalize_translation_output(
            output,
            source_text=text,
        )
        if not translated:
            self._last_response_summary = self._response_debug_summary(message)
            logger.warning(
                "Translation API returned empty content (%s)",
                self._last_response_summary,
            )
            summary = str(getattr(self, "_last_response_summary", "") or "").strip()
            if summary:
                raise RuntimeError(f"Translation API returned an empty response ({summary})")
            raise RuntimeError("Translation API returned an empty response")
        translated = self._store_cached_translation(
            text,
            src_lang,
            tgt_lang,
            self.model,
            translated,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        self._remember_context_turn(
            text,
            translated,
            src_lang,
            tgt_lang,
            context_source=context_source,
        )
        return translated

    def _message_output_text(self, response) -> str:
        try:
            content = list(getattr(response, "content", []) or [])
        except TypeError:
            content = []
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "".join(parts)

    def _response_debug_summary(self, response) -> str:
        parts = [f"model={self.model}"]
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason:
            parts.append(f"stop_reason={stop_reason}")
        try:
            content = list(getattr(response, "content", []) or [])
        except TypeError:
            content = []
        parts.append(f"content_blocks={len(content)}")
        text_chars = 0
        for block in content:
            text = getattr(block, "text", None)
            if text:
                text_chars += len(str(text))
        parts.append(f"text_chars={text_chars}")
        usage = getattr(response, "usage", None)
        if usage is not None:
            for name in ("input_tokens", "output_tokens"):
                value = getattr(usage, name, None)
                if value is not None:
                    parts.append(f"{name}={value}")
        return ", ".join(parts)
