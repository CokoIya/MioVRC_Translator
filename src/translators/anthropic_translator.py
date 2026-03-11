from __future__ import annotations

from .base import BaseTranslator


class AnthropicTranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        timeout_s: float = 15.0,
        max_output_tokens: int = 192,
    ):
        super().__init__()
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic がインストールされていません。実行してください: pip install anthropic"
            )
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
        self.model = model
        self._max_output_tokens = max(int(max_output_tokens), 48)

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        cached = self._get_cached_translation(text, src_lang, tgt_lang, self.model)
        if cached is not None:
            return cached

        message = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_output_tokens,
            messages=[{"role": "user", "content": self._build_prompt(text, src_lang, tgt_lang)}],
        )
        translated = message.content[0].text.strip()
        if not translated:
            raise RuntimeError("Translation API returned an empty response")
        return self._store_cached_translation(
            text,
            src_lang,
            tgt_lang,
            self.model,
            translated,
        )
