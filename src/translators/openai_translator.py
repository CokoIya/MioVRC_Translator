from __future__ import annotations

import os
import logging
import time

from .base import BaseTranslator, _TRANSLATION_SYSTEM_PROMPT
from src.utils.input_validation import validate_translation_text, ValidationError

logger = logging.getLogger(__name__)

_QWEN_COLLOQUIAL_SYSTEM_ADDON = (
    "Qwen style calibration: be especially careful to avoid literal, machine-translation wording. "
    "Make the result sound like a normal line in live VRChat conversation, not a subtitle, essay, or dictionary gloss. "
    "Keep it short, fluent, and natural while preserving meaning."
)
_QWEN_ZH_COLLOQUIAL_GUIDE = (
    "Qwen colloquial Chinese guide:\n"
    "- 目标是中国大陆日常聊天口吻，像真人顺嘴说出来的话。\n"
    "- 少用“我认为、由于、因此、进行、是否能够、非常感谢你”等书面表达，除非原文真的很正式。\n"
    "- 日语的语气词、犹豫、撒娇、吐槽、委婉说法，要转成中文里自然的语气，不要照着词序硬翻。\n"
    "- 可自然使用“有点、挺、吧、嘛、啦、诶、救命、笑死、懂了、可以啊、没事没事”等口语词；不要硬塞网络梗。\n"
    "- 示例风格：今日はちょっと眠いかも -> 今天有点困了；助かった -> 帮大忙了；行けたら行く -> 有空我就去。"
)
_QWEN_JA_COLLOQUIAL_GUIDE = (
    "Qwen colloquial Japanese guide:\n"
    "- Target Japanese should sound like natural spoken Japanese in a casual VRChat conversation.\n"
    "- Prefer short conversational phrasing over textbook translations.\n"
    "- Use casual particles and sentence endings when appropriate, but do not overdo anime-like speech unless the source has that flavor.\n"
    "- Preserve politeness if the source is clearly polite."
)
_QWEN_EN_COLLOQUIAL_GUIDE = (
    "Qwen colloquial English guide:\n"
    "- Target English should sound like a natural spoken line in a casual VRChat conversation.\n"
    "- Avoid direct calques from Japanese, Chinese, or Korean word order.\n"
    "- Correct obvious ASR wording or punctuation artifacts only when the intended meaning is clear.\n"
    "- Prefer short everyday phrasing and contractions when they fit, while preserving tone."
)


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
        prefer_max_completion_tokens: bool = False,
        prompt_profile: dict[str, object] | None = None,
    ):
        super().__init__(prompt_profile=prompt_profile)
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请先执行: pip install openai")

        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self._timeout_s,
            max_retries=self._max_retries,
        )
        self.model = model
        self._base_url = str(base_url or "").strip().lower()
        model_name = str(model).lower()
        self._is_openai_api = "api.openai.com" in self._base_url
        self._is_reasoning_model = (
            model_name.startswith("gpt-5")
            or "reasoner" in model_name
            or "thinking" in model_name
        )
        self._uses_max_completion_tokens = (
            (self._is_openai_api and model_name.startswith("gpt-5"))
            or bool(prefer_max_completion_tokens)
            or model_name.startswith("mimo-")
        )
        self._uses_qwen_mt_translation_options = (
            "dashscope" in self._base_url and model_name.startswith("qwen-mt-")
        )
        self._is_qwen_backend = (
            "dashscope" in self._base_url
            or model_name.startswith("qwen")
        )
        # Responses API is opt-in via env var so model routing stays driven by
        # the provider/model table instead of a hardcoded release name.
        self._use_responses_api = (
            self._is_openai_api
            and os.environ.get("MIO_TRANSLATOR_USE_RESPONSES_API", "").strip() == "1"
        )
        self._omits_temperature = (
            ("api.deepseek.com" in self._base_url and model_name == "deepseek-reasoner")
            or self._use_responses_api
        )
        min_output_tokens = 512 if self._is_reasoning_model else 48
        self._max_output_tokens = max(int(max_output_tokens), min_output_tokens)
        self._extra_body = self._translation_extra_body(extra_body or {}, model_name)
        self._last_response_summary = ""

    def _translation_extra_body(self, extra_body: dict, model_name: str) -> dict:
        body = dict(extra_body)
        if (
            "api.deepseek.com" in self._base_url
            and model_name.startswith("deepseek-v4-")
            and "thinking" not in body
        ):
            # DeepSeek V4 enables thinking mode by default. Live translation
            # needs the final text quickly, and short max_tokens budgets can
            # otherwise be consumed by reasoning_content while content stays
            # empty.
            body["thinking"] = {"type": "disabled"}
        return body

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
        # Validate and sanitize input
        try:
            text = validate_translation_text(text)
        except ValidationError as e:
            raise ValueError(f"Invalid translation input: {e}")
        if self._source_matches_target(src_lang, tgt_lang):
            return text

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
        if self._use_responses_api:
            translated = self._translate_with_responses(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
                context_source=context_source,
            )
        else:
            translated = self._translate_with_chat_completions(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
                context_source=context_source,
            )
        if not translated:
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

    def _translate_with_chat_completions(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        extra_body = dict(self._extra_body)
        uses_translation_options = self._should_use_qwen_mt_translation_options(
            src_lang,
            tgt_lang,
            context_source=context_source,
        )
        if uses_translation_options:
            extra_body["translation_options"] = {
                "source_lang": self._translation_option_language(src_lang),
                "target_lang": self._translation_option_language(tgt_lang),
            }
            # Qwen-MT translates the user content literally when
            # translation_options are present, so keep this fast path only for
            # plain translation requests with no active social style prompt.
            messages = [
                {
                    "role": "user",
                    "content": text,
                }
            ]
        else:
            output_tokens = self._estimate_max_tokens(text)
            messages = self._build_messages(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
                context_source=context_source,
            )
            messages = self._chat_messages_for_backend(messages)
        kwargs = dict(
            model=self.model,
            messages=messages,
        )
        request_timeout = getattr(self, "_timeout_s", None)
        if request_timeout:
            kwargs["timeout"] = request_timeout
        if not uses_translation_options and not self._omits_temperature:
            kwargs["temperature"] = self._translation_temperature(
                tgt_lang,
                uses_translation_options=uses_translation_options,
            )
        if not uses_translation_options:
            if self._uses_max_completion_tokens:
                kwargs["max_completion_tokens"] = output_tokens
            else:
                kwargs["max_tokens"] = output_tokens
        if extra_body:
            kwargs["extra_body"] = extra_body

        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            logger.warning(
                "Translation API request failed (model=%s base_url=%s elapsed=%.2fs error=%s)",
                self.model,
                self._base_url,
                elapsed,
                exc,
                exc_info=True,
            )
            raise
        elapsed = time.perf_counter() - started
        logger.info(
            "Translation API request finished (model=%s base_url=%s elapsed=%.2fs)",
            self.model,
            self._base_url,
            elapsed,
        )
        output = self._chat_completion_output_text(response)
        translated = self._finalize_translation_output(
            output,
            source_text=text,
        )
        if not translated:
            self._last_response_summary = self._response_debug_summary(response)
            logger.warning(
                "Translation API returned empty content (%s)",
                self._last_response_summary,
            )
        else:
            self._last_response_summary = ""
        return translated

    def _build_messages(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> list[dict[str, str]]:
        messages = super()._build_messages(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        qwen_guide = self._qwen_colloquial_guide(tgt_lang)
        if not qwen_guide:
            return messages

        messages[0]["content"] = (
            f"{messages[0]['content']} {_QWEN_COLLOQUIAL_SYSTEM_ADDON}"
        )
        messages[1]["content"] = f"{messages[1]['content']}\n\n{qwen_guide}"
        return messages

    def _chat_messages_for_backend(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        if "dashscope" not in str(getattr(self, "_base_url", "") or ""):
            return messages

        system_parts: list[str] = []
        converted: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "")
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            converted.append({"role": role, "content": content})

        if system_parts:
            system_text = "\n\n".join(system_parts)
            if converted and converted[0]["role"] == "user":
                converted[0] = {
                    **converted[0],
                    "content": f"{system_text}\n\n{converted[0]['content']}",
                }
            else:
                converted.insert(0, {"role": "user", "content": system_text})

        return converted or [{"role": "user", "content": ""}]

    def _translate_with_responses(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        prompt_body = self._build_prompt(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        prompt = (
            f"{_TRANSLATION_SYSTEM_PROMPT}\n\n"
            f"{prompt_body}"
        )
        kwargs = dict(
            model=self.model,
            input=prompt,
            max_output_tokens=self._estimate_max_tokens(text),
        )
        request_timeout = getattr(self, "_timeout_s", None)
        if request_timeout:
            kwargs["timeout"] = request_timeout
        if not self._omits_temperature:
            kwargs["temperature"] = 0.0
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        started = time.perf_counter()
        try:
            response = self._client.responses.create(**kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            logger.warning(
                "Translation Responses API request failed (model=%s base_url=%s elapsed=%.2fs error=%s)",
                self.model,
                self._base_url,
                elapsed,
                exc,
                exc_info=True,
            )
            raise
        elapsed = time.perf_counter() - started
        logger.info(
            "Translation Responses API request finished (model=%s base_url=%s elapsed=%.2fs)",
            self.model,
            self._base_url,
            elapsed,
        )
        translated = self._finalize_translation_output(
            str(response.output_text or ""),
            source_text=text,
        )
        if not translated:
            self._last_response_summary = self._response_debug_summary(response)
            logger.warning(
                "Translation Responses API returned empty content (%s)",
                self._last_response_summary,
            )
        else:
            self._last_response_summary = ""
        return translated

    def _chat_completion_output_text(self, response) -> str:
        try:
            choices = list(getattr(response, "choices", []) or [])
        except TypeError:
            choices = []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "")

    def _response_debug_summary(self, response) -> str:
        parts = [f"model={self.model}", f"base_url={self._base_url}"]
        response_status = getattr(response, "status", None)
        if response_status:
            parts.append(f"status={response_status}")
        try:
            choices = list(getattr(response, "choices", []) or [])
        except TypeError:
            choices = []
        parts.append(f"choices={len(choices)}")
        if choices:
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason:
                parts.append(f"finish_reason={finish_reason}")
            message = getattr(choice, "message", None)
            if message is not None:
                reasoning = getattr(message, "reasoning_content", None)
                if reasoning:
                    parts.append(f"reasoning_chars={len(str(reasoning))}")
                refusal = getattr(message, "refusal", None)
                if refusal:
                    parts.append("refusal=present")
        usage = getattr(response, "usage", None)
        if usage is not None:
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage, name, None)
                if value is not None:
                    parts.append(f"{name}={value}")
        return ", ".join(parts)

    def _translation_option_language(self, code: str) -> str:
        normalized = self._normalize_language_code(code)
        if not normalized or normalized == "auto":
            return "auto"
        if normalized == "zh":
            return "Chinese"
        return self._language_name(normalized)

    def _qwen_colloquial_guide(self, tgt_lang: str) -> str:
        if not self._is_qwen_backend:
            return ""
        target = self._normalize_language_code(tgt_lang)
        if target == "zh":
            return _QWEN_ZH_COLLOQUIAL_GUIDE
        if target == "ja":
            return _QWEN_JA_COLLOQUIAL_GUIDE
        if target == "en":
            return _QWEN_EN_COLLOQUIAL_GUIDE
        return ""

    def _translation_temperature(
        self,
        tgt_lang: str,
        *,
        uses_translation_options: bool = False,
    ) -> float:
        if uses_translation_options:
            return 0.0
        if self._is_qwen_backend and self._normalize_language_code(tgt_lang) in {"zh", "ja"}:
            return 0.2
        return 0.0

    def _has_custom_social_style(self, *, context_source: str = "default") -> bool:
        if context_source == "listen" or not self._prompt_profile:
            return False

        profile = self._prompt_profile
        social_mode = str(profile.get("mode", "standard")).strip()
        if social_mode in {"", "standard"}:
            return False
        if social_mode not in {"language_exchange", "roleplay"}:
            return False
        return True

    def _should_use_qwen_mt_translation_options(
        self,
        src_lang: str,
        tgt_lang: str,
        *,
        context_source: str = "default",
    ) -> bool:
        del src_lang
        if not self._uses_qwen_mt_translation_options:
            return False
        if self._normalize_language_code(tgt_lang) == "en":
            # Player reports point to English needing the richer colloquial
            # prompt/context path instead of the literal MT fast path.
            return False
        return not self._has_custom_social_style(context_source=context_source)
