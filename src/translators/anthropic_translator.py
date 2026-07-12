from __future__ import annotations

import logging
import urllib.parse

from .base import (
    BaseTranslator,
    TranslationContextStore,
    _TRANSLATION_SYSTEM_PROMPT,
)
from .asr_rewriter import build_asr_rewrite_messages, normalize_asr_rewrite_style
from src.utils.input_validation import validate_translation_text, ValidationError
from src.utils.secure_http import validate_api_base_url

logger = logging.getLogger(__name__)


def normalize_anthropic_base_url(base_url: str) -> str:
    """Return the SDK base URL for an Anthropic-compatible endpoint.

    The Anthropic SDK appends ``/v1/messages`` itself.  Relay providers often
    document their URL as ``https://host/v1`` (or copy the complete messages
    endpoint), which otherwise produces ``/v1/v1/messages``.  Remove only that
    well-known terminal API suffix after the normal credential-safe URL
    validation; custom proxy path prefixes remain intact.
    """

    validated = validate_api_base_url(
        base_url,
        label="Anthropic translation API",
    )
    parsed = urllib.parse.urlsplit(validated)
    path = parsed.path.rstrip("/")
    folded_path = path.casefold()
    for suffix in ("/v1/messages", "/v1"):
        if folded_path == suffix or folded_path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    normalized = urllib.parse.urlunsplit(parsed._replace(path=path))
    return validate_api_base_url(
        normalized,
        label="Anthropic translation API",
    )


class AnthropicTranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        base_url: str = "https://api.anthropic.com",
        timeout_s: float = 15.0,
        max_retries: int = 0,
        max_output_tokens: int = 192,
        prompt_profile: dict[str, object] | None = None,
        context_store: TranslationContextStore | None = None,
    ):
        super().__init__(prompt_profile=prompt_profile, context_store=context_store)
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic 未安装，请先执行: pip install anthropic")
        validated_base_url = normalize_anthropic_base_url(base_url)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=validated_base_url,
            timeout=timeout_s,
            max_retries=max(int(max_retries), 0),
        )
        self.model = model
        self._base_url = validated_base_url
        self._max_output_tokens = max(int(max_output_tokens), 32)
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
        if self._source_matches_target(src_lang, tgt_lang):
            return text

        context_snapshot = self._context_snapshot(
            src_lang,
            tgt_lang,
            context_source=context_source,
            current_text=text,
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
            max_tokens=self._estimate_max_tokens(text),
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
                raise RuntimeError(
                    f"Translation API returned an empty response ({summary})"
                )
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
        system = str(messages[0]["content"])
        user = str(messages[1]["content"])
        output_tokens = min(
            self._max_output_tokens,
            max(48, self._estimate_max_tokens(text) * 2),
        )
        response = self._client.messages.create(
            model=self.model,
            system=system,
            max_tokens=output_tokens,
            messages=[{"role": "user", "content": user}],
        )
        rewritten = self._finalize_asr_rewrite_output(
            self._message_output_text(response),
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
