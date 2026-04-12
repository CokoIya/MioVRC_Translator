from __future__ import annotations

from .base import BaseTranslator, _TRANSLATION_SYSTEM_PROMPT


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
        prompt_profile: dict[str, object] | None = None,
    ):
        super().__init__(prompt_profile=prompt_profile)
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
        self._base_url = str(base_url or "").strip().lower()
        model_name = str(model).lower()
        self._is_reasoning_model = (
            model_name.startswith("gpt-5")
            or "reasoner" in model_name
            or "thinking" in model_name
        )
        self._uses_max_completion_tokens = (
            "api.openai.com" in self._base_url and model_name.startswith("gpt-5")
        )
        self._uses_qwen_mt_translation_options = (
            "dashscope" in self._base_url and model_name.startswith("qwen-mt-")
        )
        self._omits_temperature = (
            "api.deepseek.com" in self._base_url and model_name == "deepseek-reasoner"
        )
        self._use_responses_api = "gpt-5.4-pro" in model_name
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

        if self._use_responses_api:
            translated = self._translate_with_responses(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
            )
        else:
            translated = self._translate_with_chat_completions(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
            )
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

    def _translate_with_chat_completions(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
    ) -> str:
        output_tokens = self._estimate_max_tokens(text)
        messages = self._build_messages(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
        )
        extra_body = dict(self._extra_body)
        if self._uses_qwen_mt_translation_options:
            extra_body["translation_options"] = {
                "source_lang": self._translation_option_language(src_lang),
                "target_lang": self._translation_option_language(tgt_lang),
            }
        kwargs = dict(
            model=self.model,
            messages=messages,
        )
        if not self._omits_temperature:
            kwargs["temperature"] = 0.0
        if self._uses_max_completion_tokens:
            kwargs["max_completion_tokens"] = output_tokens
        else:
            kwargs["max_tokens"] = output_tokens
        if extra_body:
            kwargs["extra_body"] = extra_body

        response = self._client.chat.completions.create(**kwargs)
        return self._finalize_translation_output(
            response.choices[0].message.content or "",
            source_text=text,
        )

    def _translate_with_responses(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
    ) -> str:
        prompt = (
            f"{_TRANSLATION_SYSTEM_PROMPT}\n\n"
            f"{self._build_prompt(text, src_lang, tgt_lang, context_snapshot=context_snapshot)}"
        )
        kwargs = dict(
            model=self.model,
            input=prompt,
            temperature=0.0,
            max_output_tokens=self._estimate_max_tokens(text),
        )
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        response = self._client.responses.create(**kwargs)
        return self._finalize_translation_output(
            str(response.output_text or ""),
            source_text=text,
        )

    def _translation_option_language(self, code: str) -> str:
        normalized = str(code or "").strip().lower()
        if not normalized or normalized == "auto":
            return "auto"
        return self._language_name(normalized)
