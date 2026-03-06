"""
OpenAI 互換翻訳バックエンド。
対応サービス: OpenAI, DeepSeek, Qianwen（互換モード）, Gemini（互換モード）,
             Ollama, その他 OpenAI API 互換サービス。
"""

from .base import BaseTranslator


class OpenAITranslator(BaseTranslator):
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1"):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai がインストールされていません。実行してください: pip install openai")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        prompt = self._build_prompt(text, src_lang, tgt_lang)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        return response.choices[0].message.content.strip()
