from __future__ import annotations

import contextlib
import httpx

import logging
import math
import re
import time
import urllib.parse
from collections.abc import Mapping

from .base import (
    BaseTranslator,
    TransformationOutputRejected,
    TranslationContextStore,
)
from .asr_rewriter import build_asr_rewrite_messages, normalize_asr_rewrite_style
from src.utils.input_validation import validate_translation_text, ValidationError
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.provider_diagnostics import (
    provider_endpoint_diagnostics,
    safe_exception_summary,
)
from src.utils.provider_http_timing import ProviderHttpTimingHooks
from src.utils.provider_warmup import warmup_httpx_client
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


ANTHROPIC_OFFICIAL_HOST = "api.anthropic.com"
# Thinking cannot be switched off on these; they run at low effort instead.
_ALWAYS_THINKING_MODEL_PREFIXES = ("claude-opus-5-5", "claude-fable-", "claude-mythos-")
# Room for a short thought plus the subtitle on the always-thinking models.
_ALWAYS_THINKING_MIN_MAX_TOKENS = 2048
# Think by default but accept ``thinking: disabled`` (at the default effort).
_THINKING_DEFAULT_ON_MODEL_PREFIXES = ("claude-opus-5", "claude-sonnet-5")
# With thinking switched off these models occasionally echo internal tags;
# this is the wording Anthropic recommends against it.
_NO_INTERNAL_TAGS_INSTRUCTION = (
    "Do not include internal or system XML tags in your response."
)
# Server-side refusal fallback: "default" routes a declined request to the
# model Anthropic recommends for the refusal category.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_REFUSAL_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-")
_INTERNAL_TAG_BLOCK_RE = re.compile(
    r"<(thinking|reasoning|analysis|scratchpad)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_TAG_RE = re.compile(
    r"</?(?:thinking|reasoning|analysis|scratchpad)\b[^>]*>",
    re.IGNORECASE,
)


def _strip_internal_tags(text: str) -> str:
    """Drop internal reasoning markup a model leaked into its visible text."""

    if not text or "<" not in text:
        return text
    cleaned = _INTERNAL_TAG_BLOCK_RE.sub("", text)
    cleaned = _INTERNAL_TAG_RE.sub("", cleaned)
    return cleaned.strip()


class AnthropicTranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
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
        refusal_fallbacks: bool = True,
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
        self._refusal_fallbacks = bool(refusal_fallbacks)
        self._official_endpoint = (
            (urllib.parse.urlsplit(validated_base_url).hostname or "").casefold()
            == ANTHROPIC_OFFICIAL_HOST
        )
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

    def prewarm(self) -> bool:
        """Open the exact HTTP pool used by the Anthropic SDK."""

        default_headers = getattr(self._client, "default_headers", {})
        headers = (
            dict(default_headers)
            if isinstance(default_headers, Mapping)
            else dict(getattr(self, "_custom_headers", {}) or {})
        )
        result = warmup_httpx_client(
            self._http_client,
            f"{self._base_url.rstrip('/')}/v1/messages",
            method="HEAD",
            headers=headers,
            timeout_s=min(float(getattr(self, "_connect_timeout_s", 2.0)), 3.0),
        )
        if result.succeeded:
            probe_status = result.status_code
            route_accepted = bool(
                probe_status is not None and 200 <= probe_status < 400
            )
            logger.info(
                "Anthropic translation transport reachable "
                "(endpoint=%s probe_status=%s probe_route_accepted=%s "
                "elapsed_ms=%.0f)",
                self._log_endpoint,
                probe_status if probe_status is not None else "unknown",
                route_accepted,
                result.elapsed_s * 1000.0,
            )
        else:
            logger.warning(
                "Anthropic translation prewarm failed "
                "(endpoint=%s elapsed_ms=%.0f error_type=%s)",
                self._log_endpoint,
                result.elapsed_s * 1000.0,
                result.error_type or "unknown",
            )
        return result.succeeded

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
                self._raise_if_refused(final_response)
                parse_started = time.perf_counter()
                output = "".join(fragments)
                if not output and final_response is not None:
                    output = self._message_output_text(final_response)
                output = _strip_internal_tags(output)
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
        self._raise_if_refused(response)
        parse_started = time.perf_counter()
        output = _strip_internal_tags(self._message_output_text(response))
        self._record_translation_metrics(
            first_token_s=None,
            parse_s=max(0.0, time.perf_counter() - parse_started),
        )
        return output, response

    def _retry_message_transformation(
        self,
        *,
        messages: list[dict[str, str]],
        source_text: str,
        operation: str,
        reason: str,
        output_tokens: int,
        target_language: str = "",
    ) -> str:
        logger.warning(
            "Rejected non-transformational Anthropic output; retrying with "
            "structured contract (provider=%s model=%s operation=%s reason=%s)",
            self._provider_id or "anthropic",
            self.model,
            operation,
            reason,
        )
        retry_messages = self._build_structured_retry_messages(
            messages,
            operation=operation,
            reason=reason,
        )
        system_parts = [
            str(message.get("content", ""))
            for message in retry_messages
            if str(message.get("role") or "").strip() == "system"
        ]
        user_parts = [
            str(message.get("content", ""))
            for message in retry_messages
            if str(message.get("role") or "").strip() != "system"
        ]
        retry_tokens = min(
            self._max_output_tokens,
            max(64, int(output_tokens) + 24),
        )
        kwargs: dict[str, object] = {
            "model": self.model,
            "system": "\n\n".join(system_parts),
            "max_tokens": retry_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": "\n\n".join(user_parts),
                }
            ],
        }
        self._apply_model_request_defaults(kwargs)
        request_timeout = self._sdk_timeout()
        if request_timeout:
            kwargs["timeout"] = request_timeout

        started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                output, _response = self._message_request(kwargs)
        except Exception:
            elapsed = max(0.0, time.perf_counter() - started)
            metrics = self.translation_metrics()
            self._record_translation_metrics(
                provider_s=float(metrics.get("provider_s") or 0.0) + elapsed,
                full_response_s=float(metrics.get("full_response_s") or 0.0)
                + elapsed,
                transformation_attempts=2,
                output_retries=1,
                **self._capture_metrics(http_capture),
            )
            raise
        elapsed = max(0.0, time.perf_counter() - started)
        metrics = self.translation_metrics()
        self._record_translation_metrics(
            provider_s=float(metrics.get("provider_s") or 0.0) + elapsed,
            full_response_s=float(metrics.get("full_response_s") or 0.0) + elapsed,
            transformation_attempts=2,
            output_retries=1,
            **self._capture_metrics(http_capture),
        )
        if operation == "rewrite":
            return self._validated_asr_rewrite_output(
                output,
                source_text=source_text,
                structured=True,
            )
        return self._validated_translation_output(
            output,
            source_text=source_text,
            structured=True,
            target_language=target_language,
        )

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
        base_messages = self._build_messages(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        system = str(base_messages[0]["content"])
        prompt = str(base_messages[1]["content"])
        output_tokens = self._estimate_max_tokens(text)
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=len(prompt) + len(system),
        )
        kwargs = {
            "model": self.model,
            "system": system,
            "max_tokens": output_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        self._apply_model_request_defaults(kwargs)
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
            len(prompt) + len(system),
            len(context_snapshot or ()),
            transport_metrics.get("pool_wait_s"),
            transport_metrics.get("tcp_s"),
            transport_metrics.get("tls_s"),
            transport_metrics.get("response_headers_s"),
            self.translation_metrics().get("first_token_s"),
            transport_metrics.get("connection_reused"),
        )
        postprocess_started = time.perf_counter()
        try:
            translated = self._validated_translation_output(
                output,
                source_text=text,
                target_language=tgt_lang,
            )
        except TransformationOutputRejected as exc:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
            translated = self._retry_message_transformation(
                messages=base_messages,
                source_text=text,
                operation="translation",
                reason=exc.reason,
                output_tokens=output_tokens,
                target_language=tgt_lang,
            )
        else:
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
        base_messages = build_asr_rewrite_messages(
            text,
            normalized_style,
            language_hint=language_hint,
        )
        system = str(base_messages[0]["content"])
        user = str(base_messages[1]["content"])
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
        self._apply_model_request_defaults(kwargs)
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
        try:
            rewritten = self._validated_asr_rewrite_output(
                output,
                source_text=text,
            )
        except TransformationOutputRejected as exc:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
            rewritten = self._retry_message_transformation(
                messages=base_messages,
                source_text=text,
                operation="rewrite",
                reason=exc.reason,
                output_tokens=output_tokens,
            )
        else:
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

    def _apply_model_request_defaults(self, kwargs: dict[str, object]) -> None:
        """Fit the request to the model family.

        A subtitle call has a small ``max_tokens``, and thinking tokens count
        against it. Claude Opus 5 and Sonnet 5 think unless told not to, so
        thinking is switched off there, with the instruction Anthropic
        recommends against leaked internal tags. Opus 5.5 and the Fable tier
        cannot switch thinking off (the request would be rejected), so they run
        at low effort with room for a little thinking. The 4.6-4.8 and Haiku
        models do not think unless asked and need nothing.

        On Anthropic's own endpoint the Opus 5 / Opus 5.5 / Fable families also
        opt into the server-side refusal fallback, which re-runs a request their
        safety classifiers declined on the model Anthropic recommends for that
        category instead of losing the sentence. Relays are left alone: they may
        not know the parameter.
        """

        model = str(self.model or "").strip().lower()
        if model.startswith(_ALWAYS_THINKING_MODEL_PREFIXES):
            kwargs.pop("thinking", None)
            output_config = dict(kwargs.get("output_config") or {})
            output_config.setdefault("effort", "low")
            kwargs["output_config"] = output_config
            kwargs["max_tokens"] = max(
                int(kwargs.get("max_tokens") or 0),
                _ALWAYS_THINKING_MIN_MAX_TOKENS,
            )
        elif model.startswith(_THINKING_DEFAULT_ON_MODEL_PREFIXES):
            kwargs["thinking"] = {"type": "disabled"}
            system = str(kwargs.get("system") or "")
            if _NO_INTERNAL_TAGS_INSTRUCTION not in system:
                kwargs["system"] = (
                    f"{system}\n\n{_NO_INTERNAL_TAGS_INSTRUCTION}"
                    if system
                    else _NO_INTERNAL_TAGS_INSTRUCTION
                )
        if (
            getattr(self, "_refusal_fallbacks", False)
            and getattr(self, "_official_endpoint", False)
            and model.startswith(_REFUSAL_FALLBACK_MODEL_PREFIXES)
        ):
            headers = dict(kwargs.get("extra_headers") or {})
            configured = (getattr(self, "_custom_headers", None) or {}).get(
                "anthropic-beta"
            )
            betas = [
                part.strip()
                for source in (configured, headers.get("anthropic-beta"))
                for part in str(source or "").split(",")
                if part.strip()
            ]
            betas.append(REFUSAL_FALLBACK_BETA)
            headers["anthropic-beta"] = ",".join(dict.fromkeys(betas))
            kwargs["extra_headers"] = headers
            body = dict(kwargs.get("extra_body") or {})
            body.setdefault("fallbacks", "default")
            kwargs["extra_body"] = body

    def _raise_if_refused(self, response: object) -> None:
        """A declined request is an HTTP 200 with ``stop_reason: refusal``.

        Whatever text came with it (a mid-stream decline keeps the partial
        output) must not reach a subtitle, so it becomes a safety failure the
        error formatter shows as such.
        """

        if response is None:
            return
        if isinstance(response, dict):
            stop_reason = response.get("stop_reason")
            details = response.get("stop_details")
        else:
            stop_reason = getattr(response, "stop_reason", None)
            details = getattr(response, "stop_details", None)
        if str(stop_reason or "").strip().casefold() != "refusal":
            return
        if isinstance(details, dict):
            category = details.get("category")
        else:
            category = getattr(details, "category", None)
        raise RuntimeError(
            "Anthropic safety policy declined the request "
            f"(stop_reason=refusal category={str(category or 'unspecified')[:40]})"
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
