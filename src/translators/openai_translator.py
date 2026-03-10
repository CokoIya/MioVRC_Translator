from .base import BaseTranslator


class OpenAITranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 20.0,
        extra_body: dict | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请先执行: pip install openai")

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
        self.model = model
        self._extra_body = extra_body or {}

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        prompt = self._build_prompt(text, src_lang, tgt_lang)
        kwargs = dict(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=192,
        )
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        response = self._client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()
