from __future__ import annotations

import os
import contextlib
import logging
import math
import time
import urllib.parse

import httpx

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
from src.utils.provider_network import (
    direct_connection_ssl_context,
    is_special_local_provider_host,
    resolve_special_local_provider_addresses,
    should_bypass_environment_proxies,
)
from src.utils.qwen_endpoints import is_qwen_translation_api_host
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
        custom_headers: object = None,
        streaming: bool = False,
        connect_timeout_s: float | None = None,
        pool_timeout_s: float | None = None,
        read_timeout_s: float | None = None,
        write_timeout_s: float | None = None,
        wall_timeout_s: float | None = None,
        omit_placeholder_authorization: bool = False,
    ):
        super().__init__(prompt_profile=prompt_profile, context_store=context_store)
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Translation dependency is not installed: openai")

        validated_base_url = validate_api_base_url(
            base_url,
            label="Translation API",
            allow_private_http=allow_private_http,
        )
        parsed_base_url = urllib.parse.urlsplit(validated_base_url)
        base_hostname = (parsed_base_url.hostname or "").rstrip(".").casefold()
        self._pinned_local_http_host = ""
        self._pinned_local_http_address = ""
        self._pinned_local_http_authority = ""
        if (
            parsed_base_url.scheme.casefold() == "http"
            and is_special_local_provider_host(base_hostname)
        ):
            resolved_addresses = resolve_special_local_provider_addresses(
                base_hostname
            )
            if not resolved_addresses:
                raise ValueError(
                    "Translation API base URL must use HTTPS or an explicit "
                    "loopback/private local HTTP host"
                )
            self._pinned_local_http_host = base_hostname
            self._pinned_local_http_address = str(resolved_addresses[0])
            base_port = parsed_base_url.port
            self._pinned_local_http_authority = (
                base_hostname
                if base_port in (None, 80)
                else f"{base_hostname}:{base_port}"
            )
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
        self._max_retries = max(int(max_retries), 0)
        self._custom_headers = normalize_openai_custom_headers(custom_headers)
        self._http_timing = ProviderHttpTimingHooks()
        self._omit_placeholder_authorization = bool(
            omit_placeholder_authorization
        )
        self._trust_env = not (
            bool(self._pinned_local_http_address)
            or should_bypass_environment_proxies(validated_base_url)
        )
        # The realtime scheduler owns several long-lived translation workers.
        # httpx otherwise expires idle connections after five seconds, so a
        # worker rotation can turn nearly every conversational request into a
        # fresh proxy/TCP/TLS handshake. Keep each worker's pool warm long
        # enough to be reused across normal pauses between spoken sentences.
        request_hooks = []
        if self._pinned_local_http_address:
            request_hooks.append(self._pin_reserved_local_http_request)
        if self._omit_placeholder_authorization:
            request_hooks.append(self._remove_placeholder_authorization)
        request_hooks.append(self._http_timing.on_request)
        http_client_kwargs: dict[str, object] = {
            "timeout": self._request_timeout,
            "limits": httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=OPENAI_HTTP_KEEPALIVE_EXPIRY_S,
            ),
            "event_hooks": {
                "request": request_hooks,
                "response": [self._http_timing.on_response],
            },
            # Local model servers should never detour through a desktop,
            # corporate, or environment-configured proxy. Public providers
            # retain normal proxy support for users who require it.
            "trust_env": self._trust_env,
        }
        direct_ssl_context = direct_connection_ssl_context(validated_base_url)
        if direct_ssl_context is not None:
            http_client_kwargs["verify"] = direct_ssl_context
        self._http_client = httpx.Client(**http_client_kwargs)
        try:
            client_kwargs = dict(
                api_key=api_key,
                base_url=validated_base_url,
                # Keep the public SDK default backward-compatible while the
                # provided httpx client and per-call options carry granular
                # phase timeouts.
                timeout=self._timeout_s,
                max_retries=self._max_retries,
                http_client=self._http_client,
            )
            if self._custom_headers:
                client_kwargs["default_headers"] = dict(self._custom_headers)
            self._client = OpenAI(**client_kwargs)
        except BaseException:
            self._http_client.close()
            raise
        self.model = model
        self._provider_id = str(provider_id or "").strip().lower()
        self._base_url = validated_base_url
        self._log_endpoint, self._log_endpoint_id = provider_endpoint_diagnostics(
            validated_base_url
        )
        self._base_url_lower = validated_base_url.casefold()
        self._base_hostname = (
            urllib.parse.urlsplit(validated_base_url).hostname or ""
        ).rstrip(".").casefold()
        model_name = str(model).strip().casefold()
        self._is_qwen_api_endpoint = is_qwen_translation_api_host(
            self._base_hostname,
            provider_id=self._provider_id,
        )
        self._is_openai_api = self._base_hostname == "api.openai.com"
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
            self._is_qwen_api_endpoint and model_name.startswith("qwen-mt-")
        )
        self._is_qwen_backend = (
            self._is_qwen_api_endpoint
            or model_name.startswith("qwen")
        )
        # Responses API is opt-in via env var so model routing stays driven by
        # the provider/model table instead of a hardcoded release name.
        self._use_responses_api = (
            self._is_openai_api
            and not bool(streaming)
            and os.environ.get("MIO_TRANSLATOR_USE_RESPONSES_API", "").strip() == "1"
        )
        self._omits_temperature = (
            (
                self._base_hostname == "api.deepseek.com"
                and model_name == "deepseek-reasoner"
            )
            or (self._is_openai_api and model_name.startswith("gpt-5"))
            or self._use_responses_api
        )
        self._max_output_tokens = max(int(max_output_tokens), 32)
        self._managed_no_thinking_extra_keys: set[str] = set()
        self._no_thinking_request_supported = True
        self._extra_body = self._translation_extra_body(extra_body or {}, model_name)
        self._streaming_enabled = bool(streaming)
        self._streaming_supported = True
        self._last_response_summary = ""

    @staticmethod
    def _remove_placeholder_authorization(request: httpx.Request) -> None:
        """Do not send the SDK-only placeholder as a real bearer token."""

        if request.headers.get("authorization", "").strip() == "Bearer local-ai":
            request.headers.pop("authorization", None)

    def _pin_reserved_local_http_request(self, request: httpx.Request) -> None:
        """Connect a validated Docker gateway name to its pinned local address."""

        request_host = str(request.url.host or "").rstrip(".").casefold()
        allowed_hosts = {
            self._pinned_local_http_host,
            self._pinned_local_http_address.casefold(),
        }
        if request.url.scheme.casefold() != "http" or request_host not in allowed_hosts:
            raise RuntimeError("Local Translation API request target was rejected")
        request.url = request.url.copy_with(host=self._pinned_local_http_address)
        request.headers["host"] = self._pinned_local_http_authority

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

    def _translation_extra_body(self, extra_body: dict, model_name: str) -> dict:
        body = dict(extra_body)
        hostname = str(getattr(self, "_base_hostname", "") or "").casefold()
        if not hostname:
            hostname = str(getattr(self, "_base_url", "") or "").casefold()
        if (
            self._provider_id in {"qianwen", "hunyuan"}
            or is_qwen_translation_api_host(
                hostname,
                provider_id=self._provider_id,
            )
        ) and not model_name.startswith("qwen-mt-"):
            body["enable_thinking"] = False
            self._managed_no_thinking_extra_keys.add("enable_thinking")
        if (
            self._provider_id
            in {"deepseek", "xiaomi", "zhipu", "kimi", "doubao"}
            or hostname == "api.deepseek.com"
            or hostname == "api.moonshot.cn"
            or hostname == "open.bigmodel.cn"
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
        wall_started = time.perf_counter()
        for _attempt in range(3):
            self._raise_if_wall_timeout(wall_started)
            try:
                response = create(**current)
                self._raise_if_wall_timeout(wall_started)
                return response
            except Exception as exc:
                self._raise_if_wall_timeout(wall_started)
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
                        "without unsupported fields "
                        "(model=%s endpoint=%s endpoint_id=%s)",
                        self.model,
                        self._log_endpoint,
                        self._log_endpoint_id,
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
                        "(model=%s endpoint=%s endpoint_id=%s)",
                        token_parameter,
                        replacement,
                        self.model,
                        self._log_endpoint,
                        self._log_endpoint_id,
                    )
                    continue
                raise
        raise RuntimeError("Translation request compatibility retry limit exceeded")

    @staticmethod
    def _content_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            text = content.get("text") or content.get("content")
            return OpenAITranslator._content_text(text)
        if not isinstance(content, (list, tuple)):
            return ""
        fragments: list[str] = []
        for part in content:
            if isinstance(part, str):
                fragments.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
            else:
                text = getattr(part, "text", None) or getattr(part, "content", None)
            if text:
                fragments.append(OpenAITranslator._content_text(text) or str(text))
        return "".join(fragments)

    @staticmethod
    def _stream_delta_text(delta: object) -> str:
        if isinstance(delta, dict):
            content = delta.get("content")
            if content is None:
                content = delta.get("text")
        else:
            content = getattr(delta, "content", None)
            if content is None:
                content = getattr(delta, "text", None)
        return OpenAITranslator._content_text(content)

    def _chat_completion_request(self, kwargs: dict) -> tuple[str, object]:
        return self._run_with_wall_timeout(
            lambda: self._chat_completion_request_unbounded(kwargs),
            timeout_s=getattr(self, "_wall_timeout_s", None),
            operation_name="OpenAI-compatible request",
        )

    def _chat_completion_request_unbounded(
        self,
        kwargs: dict,
    ) -> tuple[str, object]:
        request_started = time.perf_counter()
        if not getattr(self, "_streaming_enabled", False) or not getattr(
            self,
            "_streaming_supported",
            True,
        ):
            response = self._create_with_control_fallback(
                self._client.chat.completions.create,
                kwargs,
            )
            parse_started = time.perf_counter()
            output = self._chat_completion_output_text(response)
            self._record_translation_metrics(
                first_token_s=None,
                parse_s=max(0.0, time.perf_counter() - parse_started),
            )
            return output, response

        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        try:
            stream = self._create_with_control_fallback(
                self._client.chat.completions.create,
                stream_kwargs,
            )
        except Exception as exc:
            if not self._unsupported_request_parameter(exc, ("stream", "streaming")):
                raise
            self._streaming_supported = False
            logger.warning(
                "Translation provider rejected streaming; retrying without it "
                "(model=%s endpoint=%s endpoint_id=%s)",
                self.model,
                self._log_endpoint,
                self._log_endpoint_id,
            )
            self._raise_if_wall_timeout(request_started)
            response = self._create_with_control_fallback(
                self._client.chat.completions.create,
                kwargs,
            )
            parse_started = time.perf_counter()
            output = self._chat_completion_output_text(response)
            self._record_translation_metrics(
                first_token_s=None,
                parse_s=max(0.0, time.perf_counter() - parse_started),
            )
            return output, response

        fragments: list[str] = []
        chunk_count = 0
        first_token_s: float | None = None
        parse_s = 0.0
        try:
            for chunk in stream:
                self._raise_if_wall_timeout(request_started)
                chunk_count += 1
                try:
                    if isinstance(chunk, dict):
                        raw_choices = chunk.get("choices", [])
                    else:
                        raw_choices = getattr(chunk, "choices", [])
                    choices = list(raw_choices or [])
                except TypeError:
                    choices = []
                if not choices:
                    continue
                choice = choices[0]
                if isinstance(choice, dict):
                    delta = choice.get("delta") or choice.get("message")
                else:
                    delta = getattr(choice, "delta", None) or getattr(
                        choice,
                        "message",
                        None,
                    )
                parse_started = time.perf_counter()
                text = self._stream_delta_text(delta)
                parse_s += max(0.0, time.perf_counter() - parse_started)
                if text:
                    if first_token_s is None:
                        first_token_s = max(
                            0.0,
                            time.perf_counter() - request_started,
                        )
                    fragments.append(text)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        parse_started = time.perf_counter()
        output = "".join(fragments)
        parse_s += max(0.0, time.perf_counter() - parse_started)
        self._record_translation_metrics(
            first_token_s=first_token_s,
            parse_s=parse_s,
        )
        if not output:
            self._last_response_summary = (
                f"model={self.model}, endpoint={self._log_endpoint}, "
                f"endpoint_id={self._log_endpoint_id}, "
                f"stream_chunks={chunk_count}"
            )
        return output, stream

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
            self._record_translation_metrics(
                cache_hit=True,
                total_s=max(0.0, time.perf_counter() - call_started),
                provider_s=0.0,
            )
            return cached

        self._last_response_summary = ""
        try:
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
                summary = str(
                    getattr(self, "_last_response_summary", "") or ""
                ).strip()
                if summary:
                    raise RuntimeError(
                        f"Translation API returned an empty response ({summary})"
                    )
                raise RuntimeError("Translation API returned an empty response")
        except Exception:
            self._record_translation_metrics(
                total_s=max(0.0, time.perf_counter() - call_started)
            )
            raise
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
        messages = self._chat_messages_for_backend(base_messages)
        output_tokens = min(
            self._max_output_tokens,
            max(32, self._estimate_max_tokens(text) + 12),
        )
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=sum(
                len(str(message.get("content", ""))) for message in messages
            ),
        )
        self._last_response_summary = ""
        provider_started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                if self._use_responses_api:
                    prompt = "\n\n".join(
                        str(message.get("content", "")) for message in messages
                    )
                    kwargs = {
                        "model": self.model,
                        "input": prompt,
                        "max_output_tokens": output_tokens,
                    }
                    request_timeout = self._sdk_timeout()
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
                    response = self._run_with_wall_timeout(
                        lambda: self._create_with_control_fallback(
                            self._client.responses.create,
                            kwargs,
                        ),
                        timeout_s=getattr(self, "_wall_timeout_s", None),
                        operation_name="OpenAI Responses ASR rewrite request",
                    )
                    parse_started = time.perf_counter()
                    output = str(getattr(response, "output_text", "") or "")
                    self._record_translation_metrics(
                        first_token_s=None,
                        parse_s=max(0.0, time.perf_counter() - parse_started),
                    )
                else:
                    kwargs = {
                        "model": self.model,
                        "messages": messages,
                    }
                    request_timeout = self._sdk_timeout()
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
                    output, response = self._chat_completion_request(kwargs)
        except Exception as exc:
            elapsed = max(0.0, time.perf_counter() - provider_started)
            transport_metrics = self._capture_metrics(http_capture)
            self._record_translation_metrics(
                provider_s=elapsed,
                full_response_s=elapsed,
                total_s=max(0.0, time.perf_counter() - call_started),
                **transport_metrics,
            )
            logger.warning(
                "ASR rewrite API request failed "
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
            if self._use_responses_api:
                rewritten = self._retry_responses_transformation(
                    messages=base_messages,
                    source_text=text,
                    operation="rewrite",
                    reason=exc.reason,
                    output_tokens=output_tokens,
                )
            else:
                rewritten = self._retry_chat_transformation(
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
            self._last_response_summary = (
                self._last_response_summary
                or self._response_debug_summary(response)
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

    def _translate_with_chat_completions(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        prompt_started = time.perf_counter()
        extra_body = self._request_extra_body()
        uses_translation_options = self._should_use_qwen_mt_translation_options(
            src_lang,
            tgt_lang,
            context_source=context_source,
        )
        if context_snapshot and TranslationContextStore.context_likely_needed(text):
            uses_translation_options = False
        output_tokens = self._estimate_max_tokens(text)
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
        request_timeout = self._sdk_timeout()
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
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=sum(
                len(str(message.get("content", ""))) for message in messages
            ),
        )

        started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                output, response = self._chat_completion_request(kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            transport_metrics = self._capture_metrics(http_capture)
            self._record_translation_metrics(
                provider_s=elapsed,
                full_response_s=elapsed,
                **transport_metrics,
            )
            active_context = self._active_context()
            logger.warning(
                "Translation API request failed "
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
        elapsed = time.perf_counter() - started
        transport_metrics = self._capture_metrics(http_capture)
        self._record_translation_metrics(
            provider_s=elapsed,
            full_response_s=elapsed,
            **transport_metrics,
        )
        active_context = self._active_context()
        logger.info(
            "Translation API request finished "
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
            sum(len(str(message.get("content", ""))) for message in messages),
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
            )
        except TransformationOutputRejected as exc:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
            retry_messages = self._build_messages(
                text,
                src_lang,
                tgt_lang,
                context_snapshot=context_snapshot,
                context_source=context_source,
            )
            translated = self._retry_chat_transformation(
                messages=retry_messages,
                source_text=text,
                operation="translation",
                reason=exc.reason,
                output_tokens=output_tokens,
            )
        else:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
        if not translated:
            self._last_response_summary = (
                self._last_response_summary
                or self._response_debug_summary(response)
            )
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
        hostname = str(getattr(self, "_base_hostname", "") or "").casefold()
        if not hostname:
            hostname = str(getattr(self, "_base_url", "") or "").casefold()
        if not is_qwen_translation_api_host(
            hostname,
            provider_id=getattr(self, "_provider_id", ""),
        ):
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

    def _record_structured_output_retry(
        self,
        *,
        elapsed: float,
        capture: object,
    ) -> None:
        metrics = self.translation_metrics()
        try:
            provider_s = float(metrics.get("provider_s") or 0.0)
        except (TypeError, ValueError):
            provider_s = 0.0
        try:
            full_response_s = float(metrics.get("full_response_s") or 0.0)
        except (TypeError, ValueError):
            full_response_s = 0.0
        self._record_translation_metrics(
            provider_s=provider_s + max(0.0, elapsed),
            full_response_s=full_response_s + max(0.0, elapsed),
            transformation_attempts=2,
            output_retries=1,
            **self._capture_metrics(capture),
        )

    def _retry_chat_transformation(
        self,
        *,
        messages: list[dict[str, str]],
        source_text: str,
        operation: str,
        reason: str,
        output_tokens: int,
    ) -> str:
        logger.warning(
            "Rejected non-transformational provider output; retrying with "
            "structured contract (provider=%s model=%s operation=%s reason=%s)",
            self._provider_id or "openai",
            self.model,
            operation,
            reason,
        )
        retry_messages = self._build_structured_retry_messages(
            messages,
            operation=operation,
            reason=reason,
        )
        retry_messages = self._chat_messages_for_backend(retry_messages)
        retry_tokens = min(
            self._max_output_tokens,
            max(64, int(output_tokens) + 24),
        )
        kwargs: dict[str, object] = {
            "model": self.model,
            "messages": retry_messages,
        }
        request_timeout = self._sdk_timeout()
        if request_timeout:
            kwargs["timeout"] = request_timeout
        if not self._omits_temperature:
            kwargs["temperature"] = 0.0
        if self._uses_max_completion_tokens:
            kwargs["max_completion_tokens"] = retry_tokens
        else:
            kwargs["max_tokens"] = retry_tokens
        if (
            getattr(self, "_no_thinking_request_supported", True)
            and self._uses_reasoning_effort_control()
        ):
            kwargs["reasoning_effort"] = "none"
        extra_body = self._request_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                output, _response = self._chat_completion_request(kwargs)
        except Exception:
            self._record_structured_output_retry(
                elapsed=max(0.0, time.perf_counter() - started),
                capture=http_capture,
            )
            raise
        elapsed = max(0.0, time.perf_counter() - started)
        self._record_structured_output_retry(
            elapsed=elapsed,
            capture=http_capture,
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
        )

    def _retry_responses_transformation(
        self,
        *,
        messages: list[dict[str, str]],
        source_text: str,
        operation: str,
        reason: str,
        output_tokens: int,
    ) -> str:
        logger.warning(
            "Rejected non-transformational provider output; retrying Responses API "
            "with structured contract (provider=%s model=%s operation=%s reason=%s)",
            self._provider_id or "openai",
            self.model,
            operation,
            reason,
        )
        retry_messages = self._build_structured_retry_messages(
            messages,
            operation=operation,
            reason=reason,
        )
        prompt = "\n\n".join(
            f"{str(message.get('role') or 'user').upper()}:\n{message.get('content', '')}"
            for message in retry_messages
        )
        retry_tokens = min(
            self._max_output_tokens,
            max(64, int(output_tokens) + 24),
        )
        kwargs: dict[str, object] = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": retry_tokens,
        }
        request_timeout = self._sdk_timeout()
        if request_timeout:
            kwargs["timeout"] = request_timeout
        if not self._omits_temperature:
            kwargs["temperature"] = 0.0
        if (
            getattr(self, "_no_thinking_request_supported", True)
            and self._uses_reasoning_effort_control()
        ):
            kwargs["reasoning"] = {"effort": "none"}
        extra_body = self._request_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                response = self._run_with_wall_timeout(
                    lambda: self._create_with_control_fallback(
                        self._client.responses.create,
                        kwargs,
                    ),
                    timeout_s=getattr(self, "_wall_timeout_s", None),
                    operation_name=f"OpenAI Responses {operation} structured retry",
                )
        except Exception:
            self._record_structured_output_retry(
                elapsed=max(0.0, time.perf_counter() - started),
                capture=http_capture,
            )
            raise
        elapsed = max(0.0, time.perf_counter() - started)
        self._record_structured_output_retry(
            elapsed=elapsed,
            capture=http_capture,
        )
        output = str(getattr(response, "output_text", "") or "")
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
        )

    def _translate_with_responses(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_snapshot: tuple[tuple[str, str], ...] | None = None,
        context_source: str = "default",
    ) -> str:
        prompt_started = time.perf_counter()
        base_messages = self._build_messages(
            text,
            src_lang,
            tgt_lang,
            context_snapshot=context_snapshot,
            context_source=context_source,
        )
        prompt = (
            "\n\n".join(
                str(message.get("content", ""))
                for message in base_messages
            )
        )
        output_tokens = self._estimate_max_tokens(text)
        kwargs = dict(
            model=self.model,
            input=prompt,
            max_output_tokens=output_tokens,
        )
        request_timeout = self._sdk_timeout()
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
        self._record_translation_metrics(
            prompt_build_s=max(0.0, time.perf_counter() - prompt_started),
            prompt_chars=len(prompt),
        )

        started = time.perf_counter()
        http_capture = None
        try:
            with self._http_timing_capture() as http_capture:
                response = self._run_with_wall_timeout(
                    lambda: self._create_with_control_fallback(
                        self._client.responses.create,
                        kwargs,
                    ),
                    timeout_s=getattr(self, "_wall_timeout_s", None),
                    operation_name="OpenAI Responses translation request",
                )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            transport_metrics = self._capture_metrics(http_capture)
            self._record_translation_metrics(
                provider_s=elapsed,
                full_response_s=elapsed,
                first_token_s=None,
                **transport_metrics,
            )
            active_context = self._active_context()
            logger.warning(
                "Translation Responses API request failed "
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
        elapsed = time.perf_counter() - started
        transport_metrics = self._capture_metrics(http_capture)
        parse_started = time.perf_counter()
        output_text = str(getattr(response, "output_text", "") or "")
        parse_s = max(0.0, time.perf_counter() - parse_started)
        self._record_translation_metrics(
            provider_s=elapsed,
            full_response_s=elapsed,
            first_token_s=None,
            parse_s=parse_s,
            **transport_metrics,
        )
        active_context = self._active_context()
        logger.info(
            "Translation Responses API request finished "
            "(model=%s endpoint=%s endpoint_id=%s elapsed=%.2fs "
            "source=%s sequence=%s "
            "prompt_chars=%d context_turns=%d pool_wait_s=%s tcp_s=%s "
            "tls_s=%s response_headers_s=%s reused=%s)",
            self.model,
            self._log_endpoint,
            self._log_endpoint_id,
            elapsed,
            context_source,
            active_context.sequence,
            len(prompt),
            len(context_snapshot or ()),
            transport_metrics.get("pool_wait_s"),
            transport_metrics.get("tcp_s"),
            transport_metrics.get("tls_s"),
            transport_metrics.get("response_headers_s"),
            transport_metrics.get("connection_reused"),
        )
        postprocess_started = time.perf_counter()
        try:
            translated = self._validated_translation_output(
                output_text,
                source_text=text,
            )
        except TransformationOutputRejected as exc:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
            translated = self._retry_responses_transformation(
                messages=base_messages,
                source_text=text,
                operation="translation",
                reason=exc.reason,
                output_tokens=output_tokens,
            )
        else:
            self._record_translation_metrics(
                postprocess_s=max(0.0, time.perf_counter() - postprocess_started)
            )
        if not translated:
            self._last_response_summary = (
                self._last_response_summary
                or self._response_debug_summary(response)
            )
            logger.warning(
                "Translation Responses API returned empty content (%s)",
                self._last_response_summary,
            )
        else:
            self._last_response_summary = ""
        return translated

    def _chat_completion_output_text(self, response) -> str:
        try:
            if isinstance(response, dict):
                raw_choices = response.get("choices", [])
            else:
                raw_choices = getattr(response, "choices", [])
            choices = list(raw_choices or [])
        except TypeError:
            choices = []
        if not choices:
            return ""
        choice = choices[0]
        if isinstance(choice, dict):
            message = choice.get("message")
            fallback_text = choice.get("text")
        else:
            message = getattr(choice, "message", None)
            fallback_text = getattr(choice, "text", None)
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        return self._content_text(content) or self._content_text(fallback_text)

    def _response_debug_summary(self, response) -> str:
        parts = [
            f"model={self.model}",
            f"endpoint={self._log_endpoint}",
            f"endpoint_id={self._log_endpoint_id}",
        ]
        response_status = getattr(response, "status", None)
        try:
            parsed_status = int(response_status)
        except (TypeError, ValueError):
            parsed_status = 0
        if 100 <= parsed_status <= 599:
            parts.append(f"status={parsed_status}")
        try:
            choices = list(getattr(response, "choices", []) or [])
        except TypeError:
            choices = []
        parts.append(f"choices={len(choices)}")
        if choices:
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason:
                normalized_finish_reason = str(finish_reason).strip().casefold()
                if normalized_finish_reason in {
                    "content_filter",
                    "function_call",
                    "length",
                    "stop",
                    "tool_calls",
                }:
                    parts.append(f"finish_reason={normalized_finish_reason}")
                else:
                    parts.append("finish_reason=present")
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
                try:
                    parsed_value = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed_value >= 0:
                    parts.append(f"{name}={parsed_value}")
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
