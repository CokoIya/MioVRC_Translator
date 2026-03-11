from __future__ import annotations

from .base import BaseTranslator


class OpenAITranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 15.0,
        max_output_tokens: int = 192,
        max_retries: int = 0,
        extra_body: dict | None = None,
    ):
        super().__init__()
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请先执行: pip install openai")

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=max(int(max_retries), 0),
        )
        self.model = model
        model_name = str(model).lower()
        self._is_reasoning_model = "reasoner" in model_name or "thinking" in model_name
        min_output_tokens = 512 if self._is_reasoning_model else 48
        self._max_output_tokens = max(int(max_output_tokens), min_output_tokens)
        self._extra_body = extra_body or {}

    def _estimate_max_tokens(self, text: str) -> int:
        compact = "".join(str(text or "").split())
        if not compact:
            return min(self._max_output_tokens, 48)

        cjk_chars = sum(
            1
            for char in compact
            if (
                "\u3400" <= char <= "\u4dbf"
                or "\u4e00" <= char <= "\u9fff"
                or "\u3040" <= char <= "\u30ff"
                or "\uac00" <= char <= "\ud7af"
            )
        )
        other_chars = max(len(compact) - cjk_chars, 0)
        estimated = cjk_chars + ((other_chars + 2) // 3) + 24
        if self._is_reasoning_model:
            return max(256, min(self._max_output_tokens, estimated + 160))
        return max(48, min(self._max_output_tokens, estimated))

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        cached = self._get_cached_translation(text, src_lang, tgt_lang, self.model)
        if cached is not None:
            return cached

        kwargs = dict(
            model=self.model,
            messages=self._build_messages(text, src_lang, tgt_lang),
            temperature=0.0,
            max_tokens=self._estimate_max_tokens(text),
        )
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        response = self._client.chat.completions.create(**kwargs)
        translated = (response.choices[0].message.content or "").strip()
        if not translated:
            raise RuntimeError("Translation API returned an empty response")
        return self._store_cached_translation(
            text,
            src_lang,
            tgt_lang,
            self.model,
            translated,
        )
