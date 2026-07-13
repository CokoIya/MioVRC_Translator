from __future__ import annotations

import os
import logging
import time

import httpx

from .base import (
    BaseTranslator,
    TranslationContextStore,
    _TRANSLATION_SYSTEM_PROMPT,
)
from .asr_rewriter import build_asr_rewrite_messages, normalize_asr_rewrite_style
from src.utils.input_validation import validate_translation_text, ValidationError
from src.utils.secure_http import validate_api_base_url

logger = logging.getLogger(__name__)

OPENAI_HTTP_KEEPALIVE_EXPIRY_S = 60.0

_QWEN_COLLOQUIAL_SYSTEM_ADDON = (
    "Qwen style calibration: use brief, fluent, natural live-VRChat speech; "
    "avoid literal machine translation, subtitle, essay, or dictionary wording."
)
_QWEN_ZH_COLLOQUIAL_GUIDE = (
    "Qwen colloquial Chinese guide:\n"
    "- 目标是中国大陆日常聊天口吻；避免书面腔和照词序硬翻，自然转换日语语气与委婉表达。\n"
    "- 示例：今日はちょっと眠いかも -> 今天有点困了。"
)
_QWEN_JA_COLLOQUIAL_GUIDE = (
    "Qwen colloquial Japanese guide:\n"
    "- Use short, natural spoken Japanese for casual VRChat conversation, not textbook phrasing.\n"
    "- Preserve politeness; avoid exaggerated anime speech unless present in the source."
)
_QWEN_EN_COLLOQUIAL_GUIDE = (
    "Qwen colloquial English guide:\n"
    "- Use a natural spoken line with short everyday phrasing and contractions when appropriate.\n"
    "- Avoid direct calques from Japanese, Chinese, or Korean; preserve tone and fix only clear ASR artifacts."
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
        context_store: TranslationContextStore | None = None,
        provider_id: str = "",
        allow_private_http: bool = False,
    ):
        super().__init__(prompt_profile=prompt_profile, context_store=context_store)
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请先执行: pip install openai")

        validated_base_url = validate_api_base_url(
            base_url,
            label="Translation API",
            allow_private_http=allow_private_http,
        )
        self._timeout_s = max(float(timeout_s), 1.0)
        self._max_retries = max(int(max_retries), 0)
        # The realtime scheduler owns several long-lived translation workers.
        # httpx otherwise expires idle connections after five seconds, so a
        # worker rotation can turn nearly every conversational request into a
        # fresh proxy/TCP/TLS handshake. Keep each worker's pool warm long
        # enough to be reused across normal pauses between spoken sentences.
        self._http_client = httpx.Client(
            timeout=httpx.Timeout(self._timeout_s),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=OPENAI_HTTP_KEEPALIVE_EXPIRY_S,
            ),
        )
        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=validated_base_url,
                timeout=self._timeout_s,
                max_retries=self._max_retries,
                http_client=self._http_client,
            )
        except BaseException:
            self._http_client.close()
            raise
        self.model = model
        self._provider_id = str(provider_id or "").strip().lower()
        self._base_url = validated_base_url.lower()
        model_name = str(model).lower()
        self._is_openai_api = "api.openai.com" in self._base_url
        self._is_reasoning_model = (
            "reasoner" in model_name
            or "reasoning" in model_name
            or "thinking" in model_name
        )
        self._uses_max_completion_tokens = (
            (
                (self._is_openai_api or self._provider_id == "openai_compatible")
                and model_name.startswith("gpt-5")
            )
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
            or (self._is_openai_api and model_name.startswith("gpt-5"))
            or self._use_responses_api
        )
        self._max_output_tokens = max(int(max_output_tokens), 32)
        self._managed_no_thinking_extra_keys: set[str] = set()
        self._no_thinking_request_supported = True
        self._extra_body = self._translation_extra_body(extra_body or {}, model_name)
        self._last_response_summary = ""

    def _translation_extra_body(self, extra_body: dict, model_name: str) -> dict:
        body = dict(extra_body)
        if (
            self._provider_id in {"qianwen", "hunyuan"}
            or "dashscope" in self._base_url
        ) and not model_name.startswith("qwen-mt-"):
            body["enable_thinking"] = False
            self._managed_no_thinking_extra_keys.add("enable_thinking")
        if (
            self._provider_id
            in {"deepseek", "xiaomi", "zhipu", "kimi", "doubao"}
            or "api.deepseek.com" in self._base_url
            or "api.moonshot.cn" in self._base_url
            or "open.bigmodel.cn" in self._base_url
        ):
            body["thinking"] = {"type": "disabled"}
            self._managed_no_thinking_extra_keys.add("thinking")
        return body

    def _estimate_max_tokens(self, text: str) -> int:
        compact = "".join(str(text or "").split())
        if not compact:
            return min(self._max_output_tokens, 32)

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
        estimated = cjk_chars + ((other_chars + 2) // 3) + 18
        return max(32, min(self._max_output_tokens, estimated))

    def _uses_reasoning_effort_control(self) -> bool:
        model_name = str(self.model or "").strip().lower()
        if model_name.startswith("gpt-5"):
            return bool(getattr(self, "_is_openai_api", False)) or (
                getattr(self, "_provider_id", "") == "openai_compatible"
            )
        provider = getattr(self, "_provider_id", "")
        if provider == "gemini":
            return True
        return provider == "xai" and (
            "reason" in model_name or "thinking" in model_name
        )

    def _request_extra_body(self) -> dict:
        body = dict(getattr(self, "_extra_body", {}) or {})
        if not getattr(self, "_no_thinking_request_supported", True):
            for key in getattr(self, "_managed_no_thinking_extra_keys", set()):
                body.pop(key, None)
        return body

    @staticmethod
    def _unsupported_request_parameter(
        exc: Exception,
        fields: tuple[str, ...],
    ) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        mentions_control = any(field in message for field in fields)
        unsupported = any(
            marker in message
            for marker in (
                "unsupported",
                "unknown",
                "unrecognized",
                "not permitted",
                "not allowed",
                "extra_forbidden",
                "unexpected",
                "invalid parameter",
            )
        )
        return mentions_control and unsupported

    @staticmethod
    def _unsupported_no_thinking_control(exc: Exception) -> bool:
        return OpenAITranslator._unsupported_request_parameter(
            exc,
            ("reasoning_effort", "enable_thinking", "thinking"),
        )

    def _without_no_thinking_controls(self, kwargs: dict) -> dict:
        fallback = dict(kwargs)
        fallback.pop("reasoning_effort", None)
        fallback.pop("reasoning", None)
        extra_body = fallback.get("extra_body")
        if isinstance(extra_body, dict):
            cleaned = dict(extra_body)
            for key in getattr(self, "_managed_no_thinking_extra_keys", set()):
                cleaned.pop(key, None)
            if cleaned:
                fallback["extra_body"] = cleaned
            else:
                fallback.pop("extra_body", None)
        return fallback

    def _create_with_control_fallback(self, create, kwargs: dict):
        current = dict(kwargs)
        removed_no_thinking = False
        swapped_token_parameter = False
        for _attempt in range(3):
            try:
                return create(**current)
            except Exception as exc:
                extra_body = current.get("extra_body")
                extra_keys = (
                    extra_body.keys() if isinstance(extra_body, dict) else ()
                )
                has_no_thinking_control = bool(
                    "reasoning_effort" in current
                    or "reasoning" in current
                    or getattr(
                        self,
                        "_managed_no_thinking_extra_keys",
                        set(),
                    ).intersection(extra_keys)
                )
                if (
                    not removed_no_thinking
                    and has_no_thinking_control
                    and self._unsupported_no_thinking_control(exc)
                ):
                    removed_no_thinking = True
                    self._no_thinking_request_supported = False
                    current = self._without_no_thinking_controls(current)
                    logger.warning(
                        "Translation provider rejected no-thinking controls; retrying "
                        "without unsupported fields (model=%s base_url=%s)",
                        self.model,
                        self._base_url,
                    )
                    continue

                token_parameter = next(
                    (
                        field
                        for field in ("max_completion_tokens", "max_tokens")
                        if field in current and field in str(exc or "").lower()
                    ),
                    "",
                )
                if (
                    not swapped_token_parameter
                    and token_parameter
                    and self._unsupported_request_parameter(
                        exc,
                        (token_parameter,),
                    )
                ):
                    swapped_token_parameter = True
                    replacement = (
                        "max_tokens"
                        if token_parameter == "max_completion_tokens"
                        else "max_completion_tokens"
                    )
                    current = dict(current)
                    current[replacement] = current.pop(token_parameter)
                    self._uses_max_completion_tokens = (
                        replacement == "max_completion_tokens"
                    )
                    logger.warning(
                        "Translation provider rejected %s; retrying with %s "
                        "(model=%s base_url=%s)",
                        token_parameter,
                        replacement,
                        self.model,
                        self._base_url,
                    )
                    continue
                raise
        raise RuntimeError("Translation request compatibility retry limit exceeded")

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
            current_text=text,
        )
        if (
            self._uses_qwen_mt_translation_options
            and context_snapshot
            and not TranslationContextStore.context_likely_needed(text)
        ):
            # Preserve the low-latency Qwen-MT endpoint for standalone lines;
            # switch to the contextual prompt only for likely references or
            # sentence fragments.
            context_snapshot = ()
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

    def rewrite_asr(
        self,
        text: str,
        style: str,
        *,
        language_hint: str = "auto",
        context_source: str = "mic",
    ) -> str:
        try:
            text = validate_translation_text(text)
        except ValidationError as exc:
            raise ValueError(f"Invalid ASR rewrite input: {exc}") from exc

        normalized_style = normalize_asr_rewrite_style(style)
        if normalized_style == "off":
            return text
        cache_source = f"rewrite:{normalized_style}"
        cached = self._get_cached_translation(
            text,
            cache_source,
            str(language_hint or "auto"),
            self.model,
            context_source=f"asr_rewrite:{context_source}",
        )
        if cached is not None:
            return cached

        messages = build_asr_rewrite_messages(
            text,
            normalized_style,
            language_hint=language_hint,
        )
        messages = self._chat_messages_for_backend(messages)
        output_tokens = min(
            self._max_output_tokens,
            max(32, self._estimate_max_tokens(text) + 12),
        )
        self._last_response_summary = ""
        if self._use_responses_api:
            prompt = "\n\n".join(
                str(message.get("content", "")) for message in messages
            )
            kwargs = {
                "model": self.model,
                "input": prompt,
                "max_output_tokens": output_tokens,
            }
            request_timeout = getattr(self, "_timeout_s", None)
            if request_timeout:
                kwargs["timeout"] = request_timeout
            if not self._omits_temperature:
                kwargs["temperature"] = 0.2
            if (
                getattr(self, "_no_thinking_request_supported", True)
                and self._uses_reasoning_effort_control()
            ):
                kwargs["reasoning"] = {"effort": "none"}
            extra_body = self._request_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = self._create_with_control_fallback(
                self._client.responses.create,
                kwargs,
            )
            output = str(getattr(response, "output_text", "") or "")
        else:
            kwargs = {
                "model": self.model,
                "messages": messages,
            }
            request_timeout = getattr(self, "_timeout_s", None)
            if request_timeout:
                kwargs["timeout"] = request_timeout
            if not self._omits_temperature:
                kwargs["temperature"] = 0.2
            if self._uses_max_completion_tokens:
                kwargs["max_completion_tokens"] = output_tokens
            else:
                kwargs["max_tokens"] = output_tokens
            if (
                getattr(self, "_no_thinking_request_supported", True)
                and self._uses_reasoning_effort_control()
            ):
                kwargs["reasoning_effort"] = "none"
            extra_body = self._request_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = self._create_with_control_fallback(
                self._client.chat.completions.create,
                kwargs,
            )
            output = self._chat_completion_output_text(response)

        rewritten = self._finalize_asr_rewrite_output(
            output,
            source_text=text,
        )
        if not rewritten:
            self._last_response_summary = self._response_debug_summary(response)
            raise RuntimeError(
                "ASR rewrite API returned an empty response"
                + (
                    f" ({self._last_response_summary})"
                    if self._last_response_summary
                    else ""
                )
            )
        return self._store_cached_translation(
            text,
            cache_source,
            str(language_hint or "auto"),
            self.model,
            rewritten,
            context_source=f"asr_rewrite:{context_source}",
        )

    def _translate_with_chat_completions(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        extra_body = self._request_extra_body()
        uses_translation_options = self._should_use_qwen_mt_translation_options(
            src_lang,
            tgt_lang,
            context_source=context_source,
        )
        if context_snapshot and TranslationContextStore.context_likely_needed(text):
            uses_translation_options = False
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
        if (
            not uses_translation_options
            and getattr(self, "_no_thinking_request_supported", True)
            and self._uses_reasoning_effort_control()
        ):
            kwargs["reasoning_effort"] = "none"
        if extra_body:
            kwargs["extra_body"] = extra_body

        started = time.perf_counter()
        try:
            response = self._create_with_control_fallback(
                self._client.chat.completions.create,
                kwargs,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            active_context = self._active_context()
            logger.warning(
                "Translation API request failed "
                "(model=%s base_url=%s elapsed=%.2fs source=%s sequence=%s error=%s)",
                self.model,
                self._base_url,
                elapsed,
                context_source,
                active_context.sequence,
                exc,
                exc_info=True,
            )
            raise
        elapsed = time.perf_counter() - started
        active_context = self._active_context()
        logger.info(
            "Translation API request finished "
            "(model=%s base_url=%s elapsed=%.2fs source=%s sequence=%s "
            "prompt_chars=%d context_turns=%d)",
            self.model,
            self._base_url,
            elapsed,
            context_source,
            active_context.sequence,
            sum(len(str(message.get("content", ""))) for message in messages),
            len(context_snapshot or ()),
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
        if getattr(
            self,
            "_no_thinking_request_supported",
            True,
        ) and self._uses_reasoning_effort_control():
            kwargs["reasoning"] = {"effort": "none"}
        extra_body = self._request_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        started = time.perf_counter()
        try:
            response = self._create_with_control_fallback(
                self._client.responses.create,
                kwargs,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            active_context = self._active_context()
            logger.warning(
                "Translation Responses API request failed "
                "(model=%s base_url=%s elapsed=%.2fs source=%s sequence=%s error=%s)",
                self.model,
                self._base_url,
                elapsed,
                context_source,
                active_context.sequence,
                exc,
                exc_info=True,
            )
            raise
        elapsed = time.perf_counter() - started
        active_context = self._active_context()
        logger.info(
            "Translation Responses API request finished "
            "(model=%s base_url=%s elapsed=%.2fs source=%s sequence=%s "
            "prompt_chars=%d context_turns=%d)",
            self.model,
            self._base_url,
            elapsed,
            context_source,
            active_context.sequence,
            len(prompt),
            len(context_snapshot or ()),
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

    def _should_use_qwen_mt_translation_options(
        self,
        src_lang: str,
        tgt_lang: str,
        *,
        context_source: str = "default",
    ) -> bool:
        del src_lang, context_source
        if not self._uses_qwen_mt_translation_options:
            return False
        if self._normalize_language_code(tgt_lang) == "en":
            # Player reports point to English needing the richer colloquial
            # prompt/context path instead of the literal MT fast path.
            return False
        return True
