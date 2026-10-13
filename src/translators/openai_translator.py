from __future__ import annotations

import os
import contextlib
import logging
import math
import re
import time
import urllib.parse
from collections.abc import Mapping

import httpx

from .anthropic_translator import (
    ALWAYS_THINKING_CLAUDE_PREFIXES,
    ALWAYS_THINKING_MIN_MAX_TOKENS,
    provider_error_status,
)
from .base import (
    BaseTranslator,
    TransformationOutputRejected,
    TranslationContextStore,
)
from .asr_rewriter import build_asr_rewrite_messages, normalize_asr_rewrite_style
from src.utils.input_validation import validate_translation_text, ValidationError
from src.utils.model_family import model_family_id
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.provider_diagnostics import (
    provider_endpoint_diagnostics,
    safe_exception_summary,
)
from src.utils.provider_http_timing import ProviderHttpTimingHooks
from src.utils.provider_network import (
    direct_connection_ssl_context,
    is_special_local_provider_host,
    resolve_pinnable_local_provider_addresses,
    should_bypass_environment_proxies,
)
from src.utils.provider_warmup import warmup_httpx_client
from src.utils.qwen_endpoints import is_qwen_translation_api_host
from src.utils.secure_http import validate_api_base_url

logger = logging.getLogger(__name__)

# OpenAI families that take ``reasoning_effort`` (GPT-5 and later).
_OPENAI_REASONING_FAMILY_PREFIXES = ("gpt-5", "gpt-6", "gpt-7")
# Models whose lowest effort is "low" (they cannot run without reasoning).
_OPENAI_LOW_FLOOR_RE = re.compile(r"^gpt-\d+(\.\d+)?-astra|^gpt-6\.1-sol")
# The original GPT-5 snapshots stop at "minimal".
_OPENAI_MINIMAL_FLOOR_RE = re.compile(r"^gpt-5(-mini|-nano)?(-\d{4}-\d{2}-\d{2})?$")
# Grok flagships after 4.3 always reason; "low" is their floor.
_REASONING_ONLY_GROK_RE = re.compile(r"^grok-(4\.(5|6|7|8|9)|[5-9])(?!.*non-reasoning)")
_MISTRAL_REASONING_RE = re.compile(
    r"^(mistral-small-(2603|latest|4)|mistral-medium-(3|latest)|mistral-large-4)"
)
# Output headroom for models that think a little even at their lowest effort.
_REASONING_OUTPUT_FLOORS = {"low": 1024, "minimal": 512}
_TOKEN_LIMIT_FIELDS = ("max_completion_tokens", "max_tokens", "max_output_tokens")
# The single relay backend ("Relay Compatible", OpenAI protocol).
_RELAY_PROVIDER_ID = "openai_compatible"
_ZHIPU_HOST = "open.bigmodel.cn"
_MOONSHOT_HOSTS = frozenset({"api.moonshot.cn", "api.moonshot.ai"})
_OPENROUTER_HOST = "openrouter.ai"
# OpenRouter offers no "none" effort for Claude. These always reason there
# (lowest effort "low"); every other Claude model takes reasoning.enabled false.
_OPENROUTER_REASONING_CLAUDE_PREFIXES = (
    *ALWAYS_THINKING_CLAUDE_PREFIXES,
    "claude-sonnet-5-5",
)
# Kimi K3 and the Kimi coding models always think and take no thinking switch.
_KIMI_ALWAYS_THINKING_RE = re.compile(r"^kimi-k3|^kimi-.*-code")
# OpenRouter's ``~vendor/<family>-latest`` aliases always point at the newest
# model of a family, so no versioned rule matches them. Each one follows the
# rules of the model that matches the alias's ``reasoning`` metadata in
# OpenRouter's /models list: "none" accepted (Luna, Terra, Mini), or reasoning
# mandatory with "low" as the lowest effort (the rest). Aliases whose own
# family id already matches a rule (Claude Fable/Haiku, DeepSeek, Kimi,
# Gemini) need no entry.
_RELAY_ALIAS_RULE_MODELS = {
    "gpt-luna-latest": "gpt-6-luna",
    "gpt-terra-latest": "gpt-5.6-terra",
    "gpt-mini-latest": "gpt-5.4-mini",
    "gpt-sol-latest": "gpt-6.1-sol",
    "gpt-astra-latest": "gpt-6-astra",
    "grok-latest": "grok-4.7",
    "claude-sonnet-latest": "claude-sonnet-5-5",
    "claude-opus-latest": "claude-opus-5-5",
    "glm-latest": "glm-5.3",
    "glm-flash-latest": "glm-5.3-flash",
}
# Words that mark an error as a refused or unknown request parameter.
_UNSUPPORTED_PARAMETER_MARKERS = (
    "unsupported",
    "unknown",
    "unrecognized",
    "not permitted",
    "not allowed",
    "extra_forbidden",
    "unexpected",
    "invalid parameter",
    # xAI: "Argument not supported on this model: reasoningEffort".
    "argument not supported",
    "not supported on this model",
)
# OpenRouter's ``reasoning`` object named as a parameter (``reasoning``,
# ``'reasoning.enabled'``), not a model id part (``-non-reasoning``) or
# ``reasoning_effort`` (which the generic matcher already knows).
_OPENROUTER_REASONING_PARAMETER_RE = re.compile(r"(?<![\w/-])reasoning(?![\w-])")
# Words that mark an error as a refused effort VALUE (a higher one would be
# accepted), as opposed to a refused or unknown parameter.
_REJECTED_EFFORT_VALUE_MARKERS = ("unsupported_value", "supported values", "must be one of")
# SDK errors repeat the provider's JSON as a Python repr, where a null field
# reads ``'param': None``. That is not the effort value "none".
_PY_NULL_FIELD_RE = re.compile(r"(:\s*)None\b")
# OpenRouter answers 500 entries per page unless asked for more (max 1000).
_OPENROUTER_MODEL_LIST_LIMIT = 1000

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
        resolved_addresses = (
            resolve_pinnable_local_provider_addresses(base_hostname)
            if parsed_base_url.scheme.casefold() == "http"
            else ()
        )
        if (
            parsed_base_url.scheme.casefold() == "http"
            and is_special_local_provider_host(base_hostname)
            and not resolved_addresses
        ):
            raise ValueError(
                "Translation API base URL must use HTTPS or an explicit "
                "loopback/private local HTTP host"
            )
        if resolved_addresses:
            self._pinned_local_http_host = base_hostname
            self._pinned_local_http_address = str(resolved_addresses[0])
            base_port = parsed_base_url.port
            authority_host = (
                f"[{base_hostname}]" if ":" in base_hostname else base_hostname
            )
            self._pinned_local_http_authority = (
                authority_host
                if base_port in (None, 80)
                else f"{authority_host}:{base_port}"
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
        relay = self._provider_id == _RELAY_PROVIDER_ID
        # Relays decorate ids (``openai/gpt-6-luna``, ``anthropic/claude-haiku-5.5``);
        # their per-family rules match the bare family id.
        family = self._family_model_id(model_name)
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
                (self._is_openai_api or relay)
                and family.startswith(_OPENAI_REASONING_FAMILY_PREFIXES)
            )
            or bool(prefer_max_completion_tokens)
            or family.startswith("mimo-")
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
            or (
                (self._is_openai_api or relay)
                and family.startswith(_OPENAI_REASONING_FAMILY_PREFIXES)
            )
            or self._use_responses_api
            # Kimi fixes its sampling parameters, Gemini 3 deprecated them,
            # and Claude 5 / reasoning-only Grok reject non-default values.
            or self._provider_id in {"kimi", "gemini"}
            or (relay and family.startswith(("kimi-", "gemini-3", "claude-")))
            or _REASONING_ONLY_GROK_RE.match(family) is not None
        )
        # Set once a raised effort was accepted after the lowest one was refused.
        self._reasoning_effort_override: str | None = None
        # Set once Zhipu or OpenRouter answered that the model cannot stop
        # thinking (and a request at low effort then succeeded).
        self._thinking_required = False
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
        """Connect a validated local hostname to its pinned local address."""

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

    def prewarm(self) -> bool:
        """Open the exact HTTP pool used by the OpenAI-compatible SDK."""

        default_headers = getattr(self._client, "default_headers", {})
        headers = (
            dict(default_headers)
            if isinstance(default_headers, Mapping)
            else dict(getattr(self, "_custom_headers", {}) or {})
        )
        result = warmup_httpx_client(
            self._http_client,
            f"{self._base_url.rstrip('/')}/models",
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
                "Translation provider transport reachable "
                "(provider=%s endpoint=%s probe_status=%s "
                "probe_route_accepted=%s elapsed_ms=%.0f)",
                self._provider_id or "openai",
                self._log_endpoint,
                probe_status if probe_status is not None else "unknown",
                route_accepted,
                result.elapsed_s * 1000.0,
            )
        else:
            logger.warning(
                "Translation provider prewarm failed "
                "(provider=%s endpoint=%s elapsed_ms=%.0f error_type=%s)",
                self._provider_id or "openai",
                self._log_endpoint,
                result.elapsed_s * 1000.0,
                result.error_type or "unknown",
            )
        return result.succeeded

    def list_models(self, *, timeout_s: float = 12.0) -> list:
        """Return the provider's model list (``GET {base_url}/models``).

        Uses the same SDK client, headers, proxy and TLS settings as the
        translation requests, so a relay that needs custom headers answers the
        same way it does for chat. OpenRouter is asked for its largest page;
        other relays get the plain request because unknown query parameters
        are not safe on every relay.
        """

        from .model_listing import parse_model_list_payload

        timeout = max(1.0, float(timeout_s))
        list_options: dict[str, object] = {}
        if self._is_openrouter_endpoint():
            list_options["extra_query"] = {"limit": _OPENROUTER_MODEL_LIST_LIMIT}

        def request():
            client = self._client.with_options(timeout=timeout, max_retries=0)
            raw = client.models.with_raw_response.list(**list_options)
            http_response = getattr(raw, "http_response", None)
            content = getattr(http_response, "content", None)
            if content is None:
                content = raw.parse().model_dump_json()
            return parse_model_list_payload(content)

        return self._run_with_wall_timeout(
            request,
            timeout_s=timeout + 2.0,
            operation_name="model list request",
        )

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
        """Provider/model specific switches that keep thinking off.

        Every model is read from the provider's own list now, so the switch is
        chosen per model: some families accept ``thinking: disabled``, some
        must be told ``enabled`` (GLM-5.3 cannot think less than "low"),
        translation-only models take no switch at all. OpenRouter takes one
        ``reasoning`` object for every vendor instead of the vendors' switches.
        """

        body = dict(extra_body)
        body.pop("enable_enhancement", None)
        hostname = self._endpoint_hostname()
        provider = self._provider_id
        family = self._family_model_id(model_name)
        if (
            provider == "qianwen"
            or is_qwen_translation_api_host(
                hostname,
                provider_id=provider,
            )
        ) and not family.startswith("qwen-mt-"):
            body["enable_thinking"] = False
            self._managed_no_thinking_extra_keys.add("enable_thinking")

        thinking_type = self._thinking_switch_type(family, hostname)
        if self._is_openrouter_endpoint():
            # The effort itself goes out per request (_apply_reasoning_effort);
            # the key is registered so the no-thinking fallback can drop it.
            # Models without a "none" effort there switch reasoning off instead
            # (DeepSeek flash, Kimi K2.6, GLM, MiMo, most Claude models).
            self._managed_no_thinking_extra_keys.add("reasoning")
            if thinking_type == "disabled" or (
                family.startswith("claude-")
                and self._reasoning_effort_for(family) is None
            ):
                body["reasoning"] = {"enabled": False}
        elif thinking_type is not None:
            body["thinking"] = {"type": thinking_type}
            self._managed_no_thinking_extra_keys.add("thinking")

        if provider == "nvidia" and self._reasoning_effort_for(family) is None:
            # NIM chat templates (Nemotron 3.5, Gemma 4, DeepSeek) switch
            # thinking off through template kwargs; templates ignore unknown keys.
            body["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
            self._managed_no_thinking_extra_keys.add("chat_template_kwargs")
        return body

    def _thinking_switch_type(self, family: str, hostname: str) -> str | None:
        """``thinking.type`` to send for a model, or None to send no switch."""

        provider = self._provider_id
        if provider == "zhipu" or hostname == _ZHIPU_HOST:
            return self._glm_thinking_type(family)
        if provider == "kimi" or hostname in _MOONSHOT_HOSTS:
            return self._kimi_thinking_type(family)
        if provider == "hunyuan":
            # hy-mt2 translation models have no thinking mode at all.
            return None if family.startswith("hy-mt") else "disabled"
        if provider in {"deepseek", "xiaomi", "doubao"} or hostname == "api.deepseek.com":
            return "disabled"
        if provider == _RELAY_PROVIDER_ID:
            # A relay serves every vendor; the family picks the vendor's switch.
            if family.startswith("glm-"):
                return self._glm_thinking_type(family)
            if family.startswith("kimi-"):
                return self._kimi_thinking_type(family)
            if family.startswith(("deepseek", "mimo-")):
                return "disabled"
        return None

    @staticmethod
    def _glm_thinking_type(family: str) -> str:
        # GLM-5.3 cannot stop thinking; it runs enabled at low effort instead.
        return "enabled" if family.startswith("glm-5.3") else "disabled"

    @staticmethod
    def _kimi_thinking_type(family: str) -> str | None:
        # kimi-k3 always thinks and is steered with reasoning_effort; the
        # coding models always think and must not be sent a switch at all.
        return None if _KIMI_ALWAYS_THINKING_RE.match(family) else "disabled"

    def _family_model_id(self, model_name: object) -> str:
        """Lower-cased model id the per-family rules match.

        On the relay, vendor namespaces, OpenRouter's ``~`` alias marker and
        ``:variant`` suffixes are removed (``anthropic/claude-haiku-5.5`` ->
        ``claude-haiku-5-5``), and a ``<family>-latest`` alias takes the rules
        of the model its OpenRouter metadata matches
        (``~openai/gpt-sol-latest`` -> ``gpt-6.1-sol``). NVIDIA NIM, local
        servers and vendor APIs keep the id as given: there the namespace or
        tag is part of the model.
        """

        model = str(model_name or "").strip().lower()
        if getattr(self, "_provider_id", "") == _RELAY_PROVIDER_ID:
            family = model_family_id(model)
            return _RELAY_ALIAS_RULE_MODELS.get(family, family)
        return model

    def _endpoint_hostname(self) -> str:
        hostname = str(getattr(self, "_base_hostname", "") or "")
        if not hostname:
            base_url = str(getattr(self, "_base_url", "") or "")
            hostname = urllib.parse.urlsplit(base_url).hostname or base_url
        return hostname.rstrip(".").casefold()

    def _is_openrouter_endpoint(self) -> bool:
        """The relay pointed at OpenRouter (``https://openrouter.ai/api/v1``)."""

        if getattr(self, "_provider_id", "") != _RELAY_PROVIDER_ID:
            return False
        host = self._endpoint_hostname()
        return host == _OPENROUTER_HOST or host.endswith(f".{_OPENROUTER_HOST}")

    def _is_zhipu_endpoint(self) -> bool:
        return (
            getattr(self, "_provider_id", "") == "zhipu"
            or self._endpoint_hostname() == _ZHIPU_HOST
        )

    def _reasoning_effort_for(self, model_name: str) -> str | None:
        """Lowest reasoning effort a model accepts, or None to send none at all."""

        model = self._family_model_id(model_name)
        provider = getattr(self, "_provider_id", "")
        relay = provider == _RELAY_PROVIDER_ID
        openai_like = bool(getattr(self, "_is_openai_api", False)) or relay
        if openai_like and model.startswith(_OPENAI_REASONING_FAMILY_PREFIXES):
            if _OPENAI_LOW_FLOOR_RE.match(model):
                return "low"
            if _OPENAI_MINIMAL_FLOOR_RE.match(model):
                return "minimal"
            return "none"
        if provider == "gemini" or (relay and model.startswith("gemini-")):
            if model.startswith("gemini-2.5") and "-pro" not in model:
                return "none"
            if model.startswith("gemini-3.8") or "-pro" in model or model.endswith("-latest"):
                return "low"
            return "minimal"
        if provider == "xai" or (relay and model.startswith("grok-")):
            if "non-reasoning" in model:
                return None
            if model.startswith("grok-4.3"):
                return "none"
            if _REASONING_ONLY_GROK_RE.match(model):
                return "low"
            if "reason" in model or "thinking" in model:
                return "none"
            return None
        if provider == "kimi" or (relay and model.startswith("kimi-")):
            return "low" if model.startswith("kimi-k3") else None
        if provider == "zhipu" or (relay and model.startswith("glm-")):
            return "low" if model.startswith("glm-5.3") else None
        if provider == "mistral":
            return "none" if _MISTRAL_REASONING_RE.match(model) else None
        if provider == "nvidia":
            if model.startswith(("nvidia/nemotron-3-super", "nvidia/nemotron-3-ultra")):
                return "none"
            if model.startswith("openai/gpt-oss"):
                return "low"
            return None
        if relay and model.startswith("claude-"):
            if self._is_openrouter_endpoint():
                # OpenRouter has no "none" effort for Claude; the models that
                # can stop thinking get reasoning.enabled false instead.
                return (
                    "low"
                    if model.startswith(_OPENROUTER_REASONING_CLAUDE_PREFIXES)
                    else None
                )
            # new-api style relays map this to Claude's thinking switch, and
            # to the lowest setting on models that always think.
            return "none"
        return None

    def _reasoning_effort_value(self) -> str | None:
        override = getattr(self, "_reasoning_effort_override", None)
        if override:
            return override
        if getattr(self, "_thinking_required", False):
            return "low"
        return self._reasoning_effort_for(str(getattr(self, "model", "") or ""))

    def _estimated_output_tokens(self, text: str) -> int:
        """Output tokens a reply to ``text`` needs, within max_output_tokens."""

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

    def _estimate_max_tokens(self, text: str) -> int:
        return self._with_output_floor(self._estimated_output_tokens(text))

    def _with_output_floor(self, tokens: int) -> int:
        """Raise a token cap to the headroom a thinking model needs.

        Thinking tokens count against the cap on models that cannot stop
        thinking; without headroom the reply would come back empty. Every
        request applies this after clamping to max_output_tokens.
        """

        return max(int(tokens), self._output_token_floor())

    def _output_token_floor(self) -> int:
        floor = _REASONING_OUTPUT_FLOORS.get(self._reasoning_effort_value() or "", 0)
        if self._thinking_cannot_be_disabled():
            floor = max(floor, _REASONING_OUTPUT_FLOORS["low"])
        if self._relay_model_always_thinks():
            # Same headroom the Anthropic path gives these models.
            floor = max(floor, ALWAYS_THINKING_MIN_MAX_TOKENS)
        return floor

    def _thinking_cannot_be_disabled(self) -> bool:
        if getattr(self, "_thinking_required", False):
            return True
        provider = getattr(self, "_provider_id", "")
        model = self._family_model_id(getattr(self, "model", ""))
        return provider in {"zhipu", _RELAY_PROVIDER_ID} and model.startswith("glm-5.3")

    def _relay_model_always_thinks(self) -> bool:
        """A Claude model that cannot stop thinking, called over the OpenAI protocol."""

        if getattr(self, "_provider_id", "") != _RELAY_PROVIDER_ID:
            return False
        return self._family_model_id(getattr(self, "model", "")).startswith(
            ALWAYS_THINKING_CLAUDE_PREFIXES
        )

    def _uses_reasoning_effort_control(self) -> bool:
        return self._reasoning_effort_value() is not None

    def _apply_reasoning_effort(self, kwargs: dict) -> None:
        """Add the model's lowest reasoning effort to a chat completions request.

        OpenRouter reads the effort from its ``reasoning`` object (sent in
        ``extra_body``); every other endpoint takes ``reasoning_effort``.
        """

        if not getattr(self, "_no_thinking_request_supported", True):
            return
        effort = self._reasoning_effort_value()
        if effort is None:
            return
        if self._is_openrouter_endpoint():
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body["reasoning"] = {"effort": effort}
            kwargs["extra_body"] = extra_body
        else:
            kwargs["reasoning_effort"] = effort

    def _request_extra_body(self) -> dict:
        body = dict(getattr(self, "_extra_body", {}) or {})
        if getattr(self, "_thinking_required", False):
            # The provider answered that this model cannot stop thinking.
            if "thinking" in body:
                body["thinking"] = {"type": "enabled"}
            reasoning = body.get("reasoning")
            if isinstance(reasoning, dict) and reasoning.get("enabled") is False:
                # OpenRouter: run at low effort instead of switching it off.
                body["reasoning"] = {"effort": "low"}
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
            marker in message for marker in _UNSUPPORTED_PARAMETER_MARKERS
        )
        return mentions_control and unsupported

    @staticmethod
    def _unsupported_no_thinking_control(exc: Exception) -> bool:
        return OpenAITranslator._unsupported_request_parameter(
            exc,
            (
                "reasoning_effort",
                # camelCase spelling some providers echo back (xAI).
                "reasoningeffort",
                "enable_thinking",
                "thinking",
                "chat_template_kwargs",
            ),
        )

    def _openrouter_rejected_reasoning(self, exc: Exception) -> bool:
        """OpenRouter refused its ``reasoning`` object as an unsupported parameter.

        Only a 400 that names ``reasoning`` itself (``Unsupported parameter:
        reasoning``, ``'reasoning.enabled' ... invalid parameter``) counts; a
        model id such as ``...-non-reasoning`` in an unrelated error does not.
        """

        if not self._is_openrouter_endpoint() or provider_error_status(exc) != 400:
            return False
        message = str(exc or "").lower()
        return bool(_OPENROUTER_REASONING_PARAMETER_RE.search(message)) and any(
            marker in message for marker in _UNSUPPORTED_PARAMETER_MARKERS
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

    @staticmethod
    def _sent_effort(current: dict) -> str:
        """The reasoning effort a request carries, wherever the endpoint wants it."""

        effort = current.get("reasoning_effort")
        if effort is None:
            reasoning = current.get("reasoning")
            if not isinstance(reasoning, dict):
                extra_body = current.get("extra_body")
                reasoning = (
                    extra_body.get("reasoning") if isinstance(extra_body, dict) else None
                )
            if isinstance(reasoning, dict):
                effort = reasoning.get("effort")
        return str(effort or "")

    @staticmethod
    def _rejected_effort_value(exc: Exception, current: dict) -> str:
        """Return the effort value the provider refused, if that is the error.

        Only a refused VALUE counts (the message names the value as a word or
        lists the supported values); a refused or unknown parameter is left to
        the no-thinking fallback, which drops the control instead.
        """

        effort = OpenAITranslator._sent_effort(current)
        if effort not in {"none", "minimal"}:
            return ""
        message = _PY_NULL_FIELD_RE.sub(r"\1null", str(exc or "")).lower()
        if "reasoning" not in message and "effort" not in message and "thinking" not in message:
            return ""
        if re.search(rf"\b{re.escape(effort)}\b", message) or any(
            marker in message for marker in _REJECTED_EFFORT_VALUE_MARKERS
        ):
            return effort
        return ""

    def _with_effort(self, current: dict, effort: str) -> dict:
        updated = dict(current)
        if "reasoning_effort" in updated:
            updated["reasoning_effort"] = effort
        if isinstance(updated.get("reasoning"), dict):
            updated["reasoning"] = dict(updated["reasoning"], effort=effort)
        extra_body = updated.get("extra_body")
        if isinstance(extra_body, dict) and isinstance(extra_body.get("reasoning"), dict):
            reasoning = extra_body["reasoning"]
            if "effort" in reasoning:
                updated["extra_body"] = dict(
                    extra_body,
                    reasoning=dict(reasoning, effort=effort),
                )
        return self._with_token_floor(updated, _REASONING_OUTPUT_FLOORS.get(effort, 0))

    @staticmethod
    def _with_token_floor(current: dict, floor: int) -> dict:
        if not floor:
            return current
        updated = dict(current)
        for field in _TOKEN_LIMIT_FIELDS:
            if field in updated:
                updated[field] = max(int(updated[field] or 0), floor)
        return updated

    def _without_raised_effort_headroom(self, current: dict, original: dict) -> dict:
        """Token limits once a raised effort is dropped.

        Starts from ``original`` (the request before this call raised the
        effort), brings it back within max_output_tokens and adds only the
        headroom the model itself still needs. A renamed token field keeps its
        new name.
        """

        cap = int(getattr(self, "_max_output_tokens", 0) or 0)
        limit = next(
            (original[field] for field in _TOKEN_LIMIT_FIELDS if field in original),
            None,
        )
        updated = dict(current)
        for field in _TOKEN_LIMIT_FIELDS:
            if field not in updated:
                continue
            value = int((updated[field] if limit is None else limit) or 0)
            if cap:
                value = min(value, cap)
            updated[field] = self._with_output_floor(value)
        return updated

    def _model_requires_thinking(self, exc: Exception, current: dict) -> bool:
        """The provider refused to switch thinking off: the model always thinks.

        Zhipu reports this as a 400 whose message talks about thinking (思考);
        its error code 1210 alone also covers max_tokens range errors.
        OpenRouter answers ``reasoning: {"enabled": false}`` (or effort
        "none") with a 400 "Reasoning is mandatory for this endpoint and
        cannot be disabled".
        """

        extra_body = current.get("extra_body")
        if not isinstance(extra_body, dict):
            return False
        if self._is_openrouter_endpoint():
            reasoning = extra_body.get("reasoning")
            if not isinstance(reasoning, dict) or not (
                reasoning.get("enabled") is False or reasoning.get("effort") == "none"
            ):
                return False
            if provider_error_status(exc) != 400:
                return False
            message = str(exc or "").lower()
            return "reasoning" in message and (
                "mandatory" in message or "cannot be disabled" in message
            )
        if not self._is_zhipu_endpoint():
            return False
        thinking = extra_body.get("thinking")
        if not isinstance(thinking, dict) or thinking.get("type") != "disabled":
            return False
        if provider_error_status(exc) != 400:
            return False
        message = str(exc or "")
        return "思考" in message or "thinking" in message.lower()

    def _with_required_thinking(self, current: dict) -> dict:
        updated = dict(current)
        if self._is_openrouter_endpoint():
            # OpenRouter reads the effort from its reasoning object.
            updated["extra_body"] = dict(
                updated.get("extra_body") or {},
                reasoning={"effort": "low"},
            )
        else:
            updated["extra_body"] = dict(
                updated.get("extra_body") or {},
                thinking={"type": "enabled"},
            )
            updated["reasoning_effort"] = "low"
        return self._with_token_floor(updated, _REASONING_OUTPUT_FLOORS["low"])

    def _create_with_control_fallback(self, create, kwargs: dict):
        current = dict(kwargs)
        removed_no_thinking = False
        swapped_token_parameter = False
        escalated_effort = False
        removed_temperature = False
        required_thinking = False
        # What a retry learned is kept only once a request with it succeeds.
        accepted_effort: str | None = None
        accepted_token_parameter = ""
        wall_started = time.perf_counter()
        for _attempt in range(6):
            self._raise_if_wall_timeout(wall_started)
            try:
                response = create(**current)
                self._raise_if_wall_timeout(wall_started)
            except Exception as exc:
                self._raise_if_wall_timeout(wall_started)
                if not required_thinking and self._model_requires_thinking(exc, current):
                    required_thinking = True
                    current = self._with_required_thinking(current)
                    logger.warning(
                        "Translation provider requires thinking for this model; "
                        "retrying with thinking enabled at low effort "
                        "(model=%s endpoint=%s endpoint_id=%s)",
                        self.model,
                        self._log_endpoint,
                        self._log_endpoint_id,
                    )
                    continue
                rejected_effort = self._rejected_effort_value(exc, current)
                if not escalated_effort and rejected_effort:
                    escalated_effort = True
                    accepted_effort = "low"
                    current = self._with_effort(current, "low")
                    logger.warning(
                        "Translation provider rejected reasoning effort %s; retrying "
                        "with low (model=%s endpoint=%s endpoint_id=%s)",
                        rejected_effort,
                        self.model,
                        self._log_endpoint,
                        self._log_endpoint_id,
                    )
                    continue
                if (
                    not removed_temperature
                    and "temperature" in current
                    and self._unsupported_request_parameter(exc, ("temperature",))
                ):
                    removed_temperature = True
                    current = dict(current)
                    current.pop("temperature", None)
                    logger.warning(
                        "Translation provider rejected temperature; retrying without "
                        "it (model=%s endpoint=%s endpoint_id=%s)",
                        self.model,
                        self._log_endpoint,
                        self._log_endpoint_id,
                    )
                    continue
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
                    and (
                        self._unsupported_no_thinking_control(exc)
                        or self._openrouter_rejected_reasoning(exc)
                    )
                ):
                    removed_no_thinking = True
                    # Without the controls there is no effort to raise: drop a
                    # raised effort (this call's or an earlier one's) and the
                    # token headroom it added.
                    raised_effort = bool(
                        escalated_effort
                        or getattr(self, "_reasoning_effort_override", None)
                    )
                    self._reasoning_effort_override = None
                    accepted_effort = None
                    current = self._without_no_thinking_controls(current)
                    if raised_effort:
                        current = self._without_raised_effort_headroom(
                            current,
                            kwargs if escalated_effort else current,
                        )
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
                    accepted_token_parameter = replacement
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
            if removed_no_thinking:
                self._no_thinking_request_supported = False
            if accepted_effort:
                self._reasoning_effort_override = accepted_effort
            if required_thinking:
                self._thinking_required = True
            if removed_temperature:
                self._omits_temperature = True
            if accepted_token_parameter:
                self._uses_max_completion_tokens = (
                    accepted_token_parameter == "max_completion_tokens"
                )
            return response
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
        output_tokens = self._with_output_floor(
            min(
                self._max_output_tokens,
                max(32, self._estimated_output_tokens(text) + 12),
            )
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
                        kwargs["reasoning"] = {"effort": self._reasoning_effort_value()}
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
                    extra_body = self._request_extra_body()
                    if extra_body:
                        kwargs["extra_body"] = extra_body
                    self._apply_reasoning_effort(kwargs)
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
        if extra_body:
            kwargs["extra_body"] = extra_body
        if not uses_translation_options:
            self._apply_reasoning_effort(kwargs)
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
                target_language=tgt_lang,
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
                target_language=tgt_lang,
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
        target_language: str = "",
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
        retry_tokens = self._with_output_floor(
            min(
                self._max_output_tokens,
                max(64, int(output_tokens) + 24),
            )
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
        extra_body = self._request_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        self._apply_reasoning_effort(kwargs)

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
            target_language=target_language,
        )

    def _retry_responses_transformation(
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
        retry_tokens = self._with_output_floor(
            min(
                self._max_output_tokens,
                max(64, int(output_tokens) + 24),
            )
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
            kwargs["reasoning"] = {"effort": self._reasoning_effort_value()}
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
            target_language=target_language,
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
            kwargs["reasoning"] = {"effort": self._reasoning_effort_value()}
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
                target_language=tgt_lang,
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
                target_language=tgt_lang,
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
