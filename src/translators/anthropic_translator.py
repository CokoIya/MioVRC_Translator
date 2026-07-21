from __future__ import annotations

import contextlib
import httpx

import logging
import math
import time
import urllib.parse

from .base import (
    BaseTranslator,
    TranslationContextStore,
    _TRANSLATION_SYSTEM_PROMPT,
)
from .asr_rewriter import build_asr_rewrite_messages, normalize_asr_rewrite_style
from src.utils.input_validation import validate_translation_text, ValidationError
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.provider_diagnostics import (
    provider_endpoint_diagnostics,
    safe_exception_summary,
)
from src.utils.provider_http_timing import ProviderHttpTimingHooks
from src.utils.secure_http import validate_api_base_url


ANTHROPIC_HTTP_KEEPALIVE_EXPIRY_S = 60.0

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
        provider_id: str = "anthropic",
        custom_headers: object = None,
        streaming: bool = False,
        connect_timeout_s: float | None = None,
        pool_timeout_s: float | None = None,
        read_timeout_s: float | None = None,
        write_timeout_s: float | None = None,
        wall_timeout_s: float | None = None,
    ):
        super().__init__(prompt_profile=prompt_profile, context_store=context_store)
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("Translation dependency is not installed: anthropic")
        validated_base_url = normalize_anthropic_base_url(base_url)
        self._timeout_s = self._positive_timeout(timeout_s, 15.0, minimum=1.0)
        self._connect_timeout_s = self._positive_timeout(
            connect_timeout_s,
            self._timeout_s,
            minimum=0.1,
        )
        self._pool_timeout_s = self._positive_timeout(
            pool_timeout_s,
            self._timeout_s,
            minimum=0.1,
        )
        self._read_timeout_s = self._positive_timeout(
            read_timeout_s,
            self._timeout_s,
            minimum=0.1,
        )
        self._write_timeout_s = self._positive_timeout(
            write_timeout_s,
            self._timeout_s,
            minimum=0.1,
        )
        self._wall_timeout_s = self._positive_timeout(
            wall_timeout_s,
            self._timeout_s,
            minimum=0.1,
        )
        self._request_timeout = httpx.Timeout(
            connect=self._connect_timeout_s,
            pool=self._pool_timeout_s,
            read=self._read_timeout_s,
            write=self._write_timeout_s,
        )
        self._custom_headers = normalize_openai_custom_headers(custom_headers)
        self._http_timing = ProviderHttpTimingHooks()
        self._http_client = httpx.Client(
            timeout=self._request_timeout,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=ANTHROPIC_HTTP_KEEPALIVE_EXPIRY_S,
            ),
            event_hooks={
                "request": [self._http_timing.on_request],
                "response": [self._http_timing.on_response],
            },
            trust_env=True,
        )
        try:
            client_kwargs = dict(
                api_key=api_key,
                base_url=validated_base_url,
                timeout=self._timeout_s,
                max_retries=max(int(max_retries), 0),
                http_client=self._http_client,
            )
            if self._custom_headers:
                client_kwargs["default_headers"] = dict(self._custom_headers)
            self._client = anthropic.Anthropic(**client_kwargs)
        except BaseException:
            self._http_client.close()
            raise
        self.model = model
        self._base_url = validated_base_url
        self._log_endpoint, self._log_endpoint_id = provider_endpoint_diagnostics(
            validated_base_url
        )
        self._provider_id = str(provider_id or "anthropic").strip().casefold()
        self._max_output_tokens = max(int(max_output_tokens), 32)
        self._streaming_enabled = bool(streaming)
        self._streaming_supported = True
        self._last_response_summary = ""

    @staticmethod
    def _positive_timeout(
        value: object,
        fallback: float,
        *,
        minimum: float,
    ) -> float:
        try:
            parsed = float(value) if value is not None else float(fallback)
        except (TypeError, ValueError):
            parsed = float(fallback)
        if not math.isfinite(parsed):
            parsed = float(fallback)
        return max(parsed, minimum)

    def _sdk_timeout(self) -> object:
        return getattr(self, "_request_timeout", getattr(self, "_timeout_s", None))

    def _raise_if_wall_timeout(self, started_at: float) -> None:
        wall_timeout = getattr(
            self,
            "_wall_timeout_s",
            getattr(self, "_timeout_s", None),
        )
        if wall_timeout is None:
            return
        elapsed = max(0.0, time.perf_counter() - started_at)
        if elapsed > float(wall_timeout):
            raise httpx.TimeoutException(
                "Translation request exceeded the wall-clock timeout "
                f"({float(wall_timeout):.2f}s)"
            )

    @staticmethod
    def _capture_metrics(capture: object) -> dict[str, object]:
        metrics = getattr(capture, "metrics", None)
        if not callable(metrics):
            return {}
        try:
            result = metrics()
        except Exception:
            return {}
        return dict(result) if isinstance(result, dict) else {}

    def _http_timing_capture(self):
        timing = getattr(self, "_http_timing", None)
        capture = getattr(timing, "capture", None)
        if callable(capture):
            return capture()
        return contextlib.nullcontext(None)

    @staticmethod
    def _unsupported_streaming(exc: Exception) -> bool:
        message = str(exc or "").strip().casefold()
        if not message or not any(
            marker in message for marker in ("stream", "streaming")
        ):
            return False
        return any(
            marker in message
            for marker in (
                "unsupported",
                "not supported",
                "unknown",
                "unrecognized",
                "not allowed",
                "not permitted",
                "invalid parameter",
                "extra inputs are not permitted",
            )
        )

    @staticmethod
    def _stream_event_text(event: object) -> str:
        if isinstance(event, str):
            return event
        if isinstance(event, dict):
            delta = event.get("delta")
            if isinstance(delta, dict):
                return str(delta.get("text") or "")
            return str(event.get("text") or "")
        delta = getattr(event, "delta", None)
        text = getattr(delta, "text", None) if delta is not None else None
        if text:
            return str(text)
        return str(getattr(event, "text", "") or "")

    def _message_request(self, kwargs: dict) -> tuple[str, object]:
        return self._run_with_wall_timeout(
            lambda: self._message_request_unbounded(kwargs),
            timeout_s=getattr(self, "_wall_timeout_s", None),
            operation_name="Anthropic-compatible request",
        )

    def _message_request_unbounded(self, kwargs: dict) -> tuple[str, object]:
        request_started = time.perf_counter()
        messages = self._client.messages
        stream_method = getattr(messages, "stream", None)
        if (
            getattr(self, "_streaming_enabled", False)
            and getattr(self, "_streaming_supported", True)
            and callable(stream_method)
        ):
            try:
                fragments: list[str] = []
                first_token_s: float | None = None
                parse_s = 0.0
                final_response: object | None = None
                with stream_method(**kwargs) as stream:
                    text_stream = getattr(stream, "text_stream", None)
                    events = text_stream if text_stream is not None else stream
                    for event in events:
                        self._raise_if_wall_timeout(request_started)
                        parse_started = time.perf_counter()
                        text = self._stream_event_text(event)
                        parse_s += max(0.0, time.perf_counter() - parse_started)
                        if not text:
                            continue
                        if first_token_s is None:
                            first_token_s = max(
                                0.0,
                                time.perf_counter() - request_started,
                            )
                        fragments.append(text)
                    get_final_message = getattr(stream, "get_final_message", None)
                    if callable(get_final_message):
                        final_response = get_final_message()
                self._raise_if_wall_timeout(request_started)
                parse_started = time.perf_counter()
                output = "".join(fragments)
                if not output and final_response is not None:
                    output = self._message_output_text(final_response)
                parse_s += max(0.0, time.perf_counter() - parse_started)
                self._record_translation_metrics(
                    first_token_s=first_token_s,
                    parse_s=parse_s,
                )
                return output, final_response if final_response is not None else stream
            except Exception as exc:
                if not self._unsupported_streaming(exc):
                    raise
                self._streaming_supported = False
                logger.warning(
                    "Anthropic-compatible provider rejected streaming; retrying "
                    "without it (model=%s endpoint=%s endpoint_id=%s)",
                    self.model,
                    self._log_endpoint,
                    self._log_endpoint_id,
                )
        elif getattr(self, "_streaming_enabled", False) and not callable(stream_method):
            self._streaming_supported = False

        self._raise_if_wall_timeout(request_started)
        response = messages.create(**kwargs)
        self._raise_if_wall_timeout(request_started)
        parse_started = time.perf_counter()
        output = self._message_output_text(response)
        self._record_translation_metrics(
            first_token_s=None,
            parse_s=max(0.0, time.perf_counter() - parse_started),
        )
        return output, response

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        call_started = time.perf_counter()
        self._reset_translation_metrics()
        # Validate and sanitize input
        try:
            text = validate_translation_text(text)
        except ValidationError as e:
            self._record_translation_metrics(
                total_s=max(0.0, time.perf_counter() - call_started)
            )
            raise ValueError(f"Invalid translation input: {e}")
        if self._source_matches_target(src_lang, tgt_lang):
            self._record_translation_metrics(
                cache_hit=False,
                provider_s=0.0,
                total_s=max(0.0, time.perf_counter() - call_started),
            )
            return text

        context_started = time.perf_counter()
        context_snapshot = self._context_snapshot(
            src_lang,
            tgt_lang,
            context_source=context_source,
            current_text=text,
        )
        self._record_translation_metrics(
            context_lookup_s=max(0.0, time.perf_counter() - context_started),
            context_turns=len(context_snapshot),
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
            self._record_translation_metrics(
                cache_hit=True,
                total_s=max(0.0, time.perf_counter() - call_started),
                provider_s=0.0,
            )
            return cached

        self._last_response_summary = ""
        prompt_started = time.perf_counter()
        prompt = self._build_prompt(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=len(prompt) + len(_TRANSLATION_SYSTEM_PROMPT),
        )
        kwargs = {
            "model": self.model,
            "system": _TRANSLATION_SYSTEM_PROMPT,
            "max_tokens": self._estimate_max_tokens(text),
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        request_timeout = self._sdk_timeout()
        if request_timeout:
            kwargs["timeout"] = request_timeout
        provider_started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                output, message = self._message_request(kwargs)
        except Exception as exc:
            elapsed = max(0.0, time.perf_counter() - provider_started)
            transport_metrics = self._capture_metrics(http_capture)
            self._record_translation_metrics(
                provider_s=elapsed,
                full_response_s=elapsed,
                total_s=max(0.0, time.perf_counter() - call_started),
                **transport_metrics,
            )
            active_context = self._active_context()
            logger.warning(
                "Anthropic translation API request failed "
                "(model=%s endpoint=%s endpoint_id=%s elapsed=%.2fs "
                "source=%s sequence=%s "
                "pool_wait_s=%s tcp_s=%s tls_s=%s response_headers_s=%s "
                "error=%s)",
                self.model,
                self._log_endpoint,
                self._log_endpoint_id,
                elapsed,
                context_source,
                active_context.sequence,
                transport_metrics.get("pool_wait_s"),
                transport_metrics.get("tcp_s"),
                transport_metrics.get("tls_s"),
                transport_metrics.get("response_headers_s"),
                safe_exception_summary(exc),
            )
            raise
        elapsed = max(0.0, time.perf_counter() - provider_started)
        transport_metrics = self._capture_metrics(http_capture)
        self._record_translation_metrics(
            provider_s=elapsed,
            full_response_s=elapsed,
            **transport_metrics,
        )
        active_context = self._active_context()
        logger.info(
            "Anthropic translation API request finished "
            "(model=%s endpoint=%s endpoint_id=%s elapsed=%.2fs "
            "source=%s sequence=%s "
            "prompt_chars=%d context_turns=%d pool_wait_s=%s tcp_s=%s "
            "tls_s=%s response_headers_s=%s first_token_s=%s reused=%s)",
            self.model,
            self._log_endpoint,
            self._log_endpoint_id,
            elapsed,
            context_source,
            active_context.sequence,
            len(prompt) + len(_TRANSLATION_SYSTEM_PROMPT),
            len(context_snapshot or ()),
            transport_metrics.get("pool_wait_s"),
            transport_metrics.get("tcp_s"),
            transport_metrics.get("tls_s"),
            transport_metrics.get("response_headers_s"),
            self.translation_metrics().get("first_token_s"),
            transport_metrics.get("connection_reused"),
        )
        postprocess_started = time.perf_counter()
        translated = self._finalize_translation_output(
            output,
            source_text=text,
        )
        self._record_translation_metrics(
            postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
        )
        if not translated:
            self._last_response_summary = self._response_debug_summary(message)
            logger.warning(
                "Translation API returned empty content (%s)",
                self._last_response_summary,
            )
            summary = str(getattr(self, "_last_response_summary", "") or "").strip()
            if summary:
                self._record_translation_metrics(
                    total_s=max(0.0, time.perf_counter() - call_started)
                )
                raise RuntimeError(
                    f"Translation API returned an empty response ({summary})"
                )
            self._record_translation_metrics(
                total_s=max(0.0, time.perf_counter() - call_started)
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
        self._record_translation_metrics(
            cache_hit=False,
            total_s=max(0.0, time.perf_counter() - call_started),
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
        call_started = time.perf_counter()
        self._reset_translation_metrics()
        try:
            text = validate_translation_text(text)
        except ValidationError as exc:
            self._record_translation_metrics(
                total_s=max(0.0, time.perf_counter() - call_started)
            )
            raise ValueError(f"Invalid ASR rewrite input: {exc}") from exc

        normalized_style = normalize_asr_rewrite_style(style)
        if normalized_style == "off":
            self._record_translation_metrics(
                provider_s=0.0,
                total_s=max(0.0, time.perf_counter() - call_started),
            )
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
            self._record_translation_metrics(
                cache_hit=True,
                provider_s=0.0,
                total_s=max(0.0, time.perf_counter() - call_started),
            )
            return cached

        prompt_started = time.perf_counter()
        messages = build_asr_rewrite_messages(
            text,
            normalized_style,
            language_hint=language_hint,
        )
        system = str(messages[0]["content"])
        user = str(messages[1]["content"])
        output_tokens = min(
            self._max_output_tokens,
            max(32, self._estimate_max_tokens(text) + 12),
        )
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=len(system) + len(user),
        )
        kwargs = {
            "model": self.model,
            "system": system,
            "max_tokens": output_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        request_timeout = self._sdk_timeout()
        if request_timeout:
            kwargs["timeout"] = request_timeout
        provider_started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                output, response = self._message_request(kwargs)
        except Exception as exc:
            elapsed = max(0.0, time.perf_counter() - provider_started)
            self._record_translation_metrics(
                provider_s=elapsed,
                full_response_s=elapsed,
                total_s=max(0.0, time.perf_counter() - call_started),
                **self._capture_metrics(http_capture),
            )
            logger.warning(
                "Anthropic ASR rewrite API request failed "
                "(model=%s endpoint=%s endpoint_id=%s elapsed=%.2fs "
                "source=%s error=%s)",
                self.model,
                self._log_endpoint,
                self._log_endpoint_id,
                elapsed,
                context_source,
                safe_exception_summary(exc),
            )
            raise
        elapsed = max(0.0, time.perf_counter() - provider_started)
        self._record_translation_metrics(
            provider_s=elapsed,
            full_response_s=elapsed,
            **self._capture_metrics(http_capture),
        )
        postprocess_started = time.perf_counter()
        rewritten = self._finalize_asr_rewrite_output(
            output,
            source_text=text,
        )
        self._record_translation_metrics(
            postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
        )
        if not rewritten:
            self._last_response_summary = self._response_debug_summary(response)
            self._record_translation_metrics(
                total_s=max(0.0, time.perf_counter() - call_started)
            )
            raise RuntimeError(
                "ASR rewrite API returned an empty response"
                + (
                    f" ({self._last_response_summary})"
                    if self._last_response_summary
                    else ""
                )
            )
        rewritten = self._store_cached_translation(
            text,
            cache_source,
            str(language_hint or "auto"),
            self.model,
            rewritten,
            context_source=f"asr_rewrite:{context_source}",
        )
        self._record_translation_metrics(
            cache_hit=False,
            total_s=max(0.0, time.perf_counter() - call_started),
        )
        return rewritten

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
            if isinstance(response, dict):
                raw_content = response.get("content", [])
            else:
                raw_content = getattr(response, "content", [])
            content = list(raw_content or [])
        except TypeError:
            content = []
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
            else:
                text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "".join(parts)

    def _response_debug_summary(self, response) -> str:
        parts = [f"model={self.model}"]
        if isinstance(response, dict):
            stop_reason = response.get("stop_reason")
            raw_content = response.get("content", [])
            usage = response.get("usage")
        else:
            stop_reason = getattr(response, "stop_reason", None)
            raw_content = getattr(response, "content", [])
            usage = getattr(response, "usage", None)
        if stop_reason:
            normalized_stop_reason = str(stop_reason).strip().casefold()
            if normalized_stop_reason in {
                "end_turn",
                "max_tokens",
                "pause_turn",
                "refusal",
                "stop_sequence",
                "tool_use",
            }:
                parts.append(f"stop_reason={normalized_stop_reason}")
            else:
                parts.append("stop_reason=present")
        try:
            content = list(raw_content or [])
        except TypeError:
            content = []
        parts.append(f"content_blocks={len(content)}")
        text_chars = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
            else:
                text = getattr(block, "text", None)
            if text:
                text_chars += len(str(text))
        parts.append(f"text_chars={text_chars}")
        if usage is not None:
            for name in ("input_tokens", "output_tokens"):
                if isinstance(usage, dict):
                    value = usage.get(name)
                else:
                    value = getattr(usage, name, None)
                try:
                    parsed_value = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed_value >= 0:
                    parts.append(f"{name}={parsed_value}")
        return ", ".join(parts)
