"""Anthropic の翻訳バックエンド。"""

from .base import BaseTranslator


class AnthropicTranslator(BaseTranslator):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic がインストールされていません。  実行してください: pip install anthropic")
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        prompt = self._build_prompt(text, src_lang, tgt_lang)
        message = self._client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
