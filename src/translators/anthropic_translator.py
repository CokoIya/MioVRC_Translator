from __future__ import annotations

from .base import BaseTranslator, _TRANSLATION_SYSTEM_PROMPT


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

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
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
                    ),
                }
            ],
        )
        translated = message.content[0].text.strip()
        if not translated:
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
