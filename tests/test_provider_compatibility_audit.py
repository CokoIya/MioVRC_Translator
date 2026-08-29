from __future__ import annotations

import logging
import sys
import threading
import time
import types

import httpx
import pytest

from src.translators import factory as translator_factory
from src.translators.factory import create_translator
from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import ProviderWallTimeoutError
from src.translators.openai_translator import OpenAITranslator
from src.utils import config_manager
from src.utils.provider_http_timing import ProviderHttpTimingHooks
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import backend_model_is_editable, get_backend_label


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_kwargs: None)
        )
        self.responses = types.SimpleNamespace(create=lambda **_kwargs: None)
        self.instances.append(self)


def test_connection_test_cleanup_failure_preserves_result_and_hides_prose(
    monkeypatch,
    caplog,
):
    cleanup_secret = "raw cleanup failure with relay/private-path"

    class Translator:
        def translate(self, *_args, **_kwargs):
            return "接続成功"

        def close(self):
            raise RuntimeError(cleanup_secret)

    monkeypatch.setattr(
        translator_factory,
        "create_translator",
        lambda _config: Translator(),
    )
    caplog.set_level("WARNING", logger="src.translators.factory")

    assert translator_factory.test_translation_connection({}) == "接続成功"
    assert cleanup_secret not in caplog.text
    assert "type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_connection_test_cleanup_failure_does_not_replace_provider_error(
    monkeypatch,
):
    provider_error = ValueError("provider authentication failed")

    class Translator:
        def translate(self, *_args, **_kwargs):
            raise provider_error

        def close(self):
            raise RuntimeError("cleanup also failed")

    monkeypatch.setattr(
        translator_factory,
        "create_translator",
        lambda _config: Translator(),
    )

    with pytest.raises(ValueError) as captured:
        translator_factory.test_translation_connection({})

    assert captured.value is provider_error


def test_openai_compatible_preserves_exact_relay_settings_and_phase_timeouts(
    monkeypatch,
):
    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    translator = create_translator(
        {
            "translation": {
                "backend": "openai_compatible",
                "openai_compatible": {
                    "api_key": "relay-key",
                    "base_url": "https://Relay.Example.com/OpenAI/V1",
                    "model": "Vendor/Custom-GPT:router-001",
                    "timeout_s": 17,
                    "connect_timeout_s": 2.5,
                    "pool_timeout_s": 1.25,
                    "read_timeout_s": 12,
                    "write_timeout_s": 4,
                    "wall_timeout_s": 14,
                    "custom_headers": {"X-Tenant": "player-one"},
                    "streaming": True,
                },
            }
        }
    )
    try:
        client = _FakeOpenAI.instances[-1]
        assert client.kwargs["base_url"] == "https://Relay.Example.com/OpenAI/V1"
        assert client.kwargs["default_headers"] == {"X-Tenant": "player-one"}
        assert translator.model == "Vendor/Custom-GPT:router-001"
        assert translator._base_url == "https://Relay.Example.com/OpenAI/V1"
        assert translator._request_timeout.connect == 2.5
        assert translator._request_timeout.pool == 1.25
        assert translator._request_timeout.read == 12.0
        assert translator._request_timeout.write == 4.0
        assert translator._wall_timeout_s == 14.0
        assert translator._streaming_enabled is True
        assert backend_model_is_editable("openai_compatible") is True
    finally:
        translator.close()


def test_openai_official_detection_uses_exact_hostname(monkeypatch):
    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    translator = OpenAITranslator(
        api_key="relay-key",
        model="gpt-5-custom",
        base_url="https://api.openai.com.relay.example/v1",
        provider_id="openai_compatible",
    )
    try:
        assert translator._is_openai_api is False
        assert translator._use_responses_api is False
    finally:
        translator.close()


def test_openai_provider_logs_hide_relay_path_and_raw_error(
    monkeypatch,
    caplog,
):
    class SensitiveProviderError(RuntimeError):
        status_code = 503
        code = "provider_failure"

    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    translator = OpenAITranslator(
        api_key="relay-key",
        model="custom-model",
        base_url="https://relay.example/tenant/opaque-secret/v1",
        provider_id="openai_compatible",
    )

    def fail(**_kwargs):
        raise SensitiveProviderError("raw-provider-secret and echoed player text")

    _FakeOpenAI.instances[-1].chat.completions.create = fail
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(SensitiveProviderError):
                translator.translate("hello", "en", "ja")
    finally:
        translator.close()

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "tenant/opaque-secret" not in rendered
    assert "raw-provider-secret" not in rendered
    assert "echoed player text" not in rendered
    assert "endpoint=https://relay.example" in rendered
    assert "endpoint_id=" in rendered
    assert "type=SensitiveProviderError status=503 code=provider_failure" in rendered


def test_openai_streaming_setting_keeps_chat_stream_path_when_responses_opted_in(
    monkeypatch,
):
    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    monkeypatch.setenv("MIO_TRANSLATOR_USE_RESPONSES_API", "1")
    translator = OpenAITranslator(
        api_key="official-key",
        model="gpt-5-custom",
        base_url="https://api.openai.com/v1",
        provider_id="openai",
        streaming=True,
    )
    try:
        assert translator._is_openai_api is True
        assert translator._use_responses_api is False
        assert translator._streaming_enabled is True
    finally:
        translator.close()


def test_provider_credentials_reject_control_characters_without_prefix_rewriting(
    monkeypatch,
):
    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    config = {
        "translation": {
            "backend": "openai_compatible",
            "openai_compatible": {
                "api_key": "relay-key\nsmuggled-header",
                "base_url": "https://relay.example/v1",
                "model": "custom-model",
            },
        }
    }

    with pytest.raises(ValueError, match="invalid control characters"):
        create_translator(config)


def test_openai_response_parsing_accepts_relay_content_blocks_and_legacy_text():
    translator = OpenAITranslator.__new__(OpenAITranslator)
    blocked = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            }
        ]
    }
    legacy = {"choices": [{"text": "legacy relay output"}]}

    assert translator._chat_completion_output_text(blocked) == "hello world"
    assert translator._chat_completion_output_text(legacy) == "legacy relay output"


class _AnthropicStream:
    def __init__(self, fragments: tuple[str, ...]) -> None:
        self.text_stream = iter(fragments)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    @staticmethod
    def get_final_message():
        return {"content": [{"type": "text", "text": "streamed"}]}


class _AnthropicMessages:
    def __init__(self, *, reject_streaming: bool = False) -> None:
        self.reject_streaming = reject_streaming
        self.stream_kwargs = None
        self.create_kwargs = None

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs
        if self.reject_streaming:
            raise RuntimeError("streaming is unsupported by this relay")
        return _AnthropicStream(("こん", "にちは"))

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return {"content": [{"type": "text", "text": "fallback"}]}


def _install_fake_anthropic(monkeypatch, *, reject_streaming: bool = False):
    instances = []

    class _FakeAnthropic:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.messages = _AnthropicMessages(reject_streaming=reject_streaming)
            instances.append(self)

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=_FakeAnthropic),
    )
    return instances


def _anthropic_config() -> dict:
    return {
        "translation": {
            "backend": "anthropic_compatible",
            "anthropic_compatible": {
                "api_key": "relay-key",
                "base_url": "https://claude-relay.example/gateway/v1",
                "model": "relay/Claude-Custom-Sonnet:beta",
                "timeout_s": 19,
                "connect_timeout_s": 3,
                "pool_timeout_s": 1,
                "read_timeout_s": 13,
                "write_timeout_s": 5,
                "wall_timeout_s": 16,
                "custom_headers": {"X-Relay-Tenant": "mio"},
                "streaming": True,
            },
        }
    }


@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_compatible_streams_with_custom_headers_and_exact_model(monkeypatch):
    instances = _install_fake_anthropic(monkeypatch)
    translator = create_translator(_anthropic_config())
    try:
        assert translator.translate("hello", "en", "ja") == "こんにちは"
        client = instances[-1]
        assert client.kwargs["base_url"] == "https://claude-relay.example/gateway"
        assert client.kwargs["default_headers"] == {"X-Relay-Tenant": "mio"}
        assert translator.model == "relay/Claude-Custom-Sonnet:beta"
        assert client.messages.stream_kwargs["model"] == translator.model
        assert isinstance(client.messages.stream_kwargs["timeout"], httpx.Timeout)
        metrics = translator.translation_metrics()
        assert metrics["first_token_s"] is not None
        assert metrics["full_response_s"] >= 0.0
        assert metrics["parse_s"] >= 0.0
        assert metrics["postprocess_s"] >= 0.0
        assert metrics["total_s"] >= metrics["full_response_s"]
        assert backend_model_is_editable("anthropic_compatible") is True
    finally:
        translator.close()


@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_compatible_falls_back_only_for_explicit_stream_rejection(
    monkeypatch,
):
    instances = _install_fake_anthropic(monkeypatch, reject_streaming=True)
    translator = create_translator(_anthropic_config())
    try:
        assert translator.translate("hello", "en", "ja") == "fallback"
        assert translator._streaming_supported is False
        assert instances[-1].messages.create_kwargs is not None
    finally:
        translator.close()


@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_provider_logs_hide_relay_path_and_raw_error(
    monkeypatch,
    caplog,
):
    class SensitiveProviderError(RuntimeError):
        status_code = 429
        code = "rate_limit"

    instances = _install_fake_anthropic(monkeypatch)
    translator = AnthropicTranslator(
        api_key="relay-key",
        model="custom-claude",
        base_url="https://claude-relay.example/tenant/opaque-secret/v1",
        provider_id="anthropic_compatible",
        streaming=False,
    )

    def fail(**_kwargs):
        raise SensitiveProviderError("raw-provider-secret and echoed player text")

    instances[-1].messages.create = fail
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(SensitiveProviderError):
                translator.translate("hello", "en", "ja")
    finally:
        translator.close()

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "tenant/opaque-secret" not in rendered
    assert "raw-provider-secret" not in rendered
    assert "echoed player text" not in rendered
    assert "endpoint=https://claude-relay.example" in rendered
    assert "endpoint_id=" in rendered
    assert "type=SensitiveProviderError status=429 code=rate_limit" in rendered


@pytest.mark.parametrize("language", ("zh-CN", "en", "ja", "ru", "ko"))
def test_safety_filter_error_is_localized_without_raw_provider_prose(language):
    raw_message = "Input data may contain inappropriate content."
    error = (
        "Error code: 400 - {'error': {'message': '"
        + raw_message
        + "', 'type': 'data_inspection_failed', 'code': 'data_inspection_failed'}}"
    )

    friendly = format_translation_error(error, backend="qianwen", ui_language=language)

    assert friendly.category == "safety"
    assert raw_message not in friendly.short_message
    assert raw_message not in friendly.inline_message
    assert raw_message not in friendly.detailed_message
    assert raw_message in friendly.detail


@pytest.mark.parametrize(
    ("error", "category"),
    (
        ("Error code: 401 - credential rejected", "auth"),
        ("Error code: 429 - retry later", "quota"),
        ("Error code: 404 - Not Found", "endpoint"),
        ("Error code: 503 - Service Unavailable", "provider"),
        ("request was cancelled", "cancelled"),
    ),
)
def test_provider_status_and_cancellation_categories_do_not_leak_raw_text(
    error,
    category,
):
    friendly = format_translation_error(
        error,
        backend="openai_compatible",
        ui_language="ja",
    )

    assert friendly.category == category
    assert error not in friendly.detailed_message


@pytest.mark.parametrize(
    "error",
    (
        "HTTP 401 Unauthorized",
        "status code: 401",
        "Client error '401 Unauthorized' for url",
    ),
)
def test_common_relay_http_status_text_is_classified(error):
    friendly = format_translation_error(
        error,
        backend="openai_compatible",
        ui_language="en",
    )

    assert friendly.category == "auth"


@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_overload_is_provider_failure_not_billing_quota():
    friendly = format_translation_error(
        "HTTP 529 - {'type': 'overloaded_error', 'message': 'Overloaded'}",
        backend="anthropic_compatible",
        ui_language="en",
    )

    assert friendly.category == "provider"


@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_model_not_found_404_is_model_error():
    friendly = format_translation_error(
        "HTTP 404 Not Found: not_found_error model: claude-custom-preview",
        backend="anthropic_compatible",
        ui_language="en",
    )

    assert friendly.category == "model"


@pytest.mark.parametrize(
    "backend",
    ("openai_compatible", "xai"),
)
def test_compatible_and_xai_auth_errors_use_provider_specific_guidance(backend):
    friendly = format_translation_error(
        "HTTP 401 Unauthorized",
        backend=backend,
        ui_language="en",
    )

    assert friendly.category == "auth"
    assert "Base URL" in friendly.detailed_message or "official xAI" in friendly.detailed_message


def test_compatible_provider_names_use_localized_backend_labels():
    for language in ("zh-CN", "en", "ja", "ru", "ko"):
        friendly = format_translation_error(
            "Error code: 401 - rejected",
            backend="openai_compatible",
            ui_language=language,
        )
        assert get_backend_label("openai_compatible", language) in friendly.short_message


def test_config_normalization_preserves_relay_model_and_bounds_phase_timeouts():
    custom_model = "relay/custom-gpt:2026-07"
    config = {
        "translation": {
            "backend": "openai_compatible",
            "openai_compatible": {
                "model": custom_model,
                "timeout_s": 12,
                "connect_timeout_s": "2.5",
                "pool_timeout_s": -1,
                "read_timeout_s": "nan",
                "write_timeout_s": 4,
                "wall_timeout_s": 999,
                "streaming": "true",
                "custom_headers": {"X-Tenant": "one"},
            },
        }
    }

    config_manager._ensure_translation_config(
        config,
        loaded={"translation": dict(config["translation"])},
    )
    provider = config["translation"]["openai_compatible"]

    assert provider["model"] == custom_model
    assert provider["connect_timeout_s"] == 2.5
    assert provider["pool_timeout_s"] == 12.0
    assert provider["read_timeout_s"] == 12.0
    assert provider["write_timeout_s"] == 4.0
    assert provider["wall_timeout_s"] == 12.0
    assert provider["streaming"] is True
    assert provider["custom_headers"] == {"X-Tenant": "one"}


def test_http_timing_hooks_report_only_measurable_transport_phases():
    hooks = ProviderHttpTimingHooks()
    request = httpx.Request("POST", "https://relay.example/v1/messages")

    with hooks.capture() as capture:
        hooks.on_request(request)
        trace = request.extensions["trace"]
        for event in (
            "connection.connect_tcp.started",
            "connection.connect_tcp.complete",
            "connection.start_tls.started",
            "connection.start_tls.complete",
            "http11.send_request_headers.started",
            "http11.send_request_headers.complete",
            "http11.receive_response_headers.started",
            "http11.receive_response_headers.complete",
        ):
            trace(event, {})
        hooks.on_response(httpx.Response(200, request=request))

    metrics = capture.metrics()
    assert metrics["http_request_count"] == 1
    assert metrics["dns_s"] is None
    assert metrics["tcp_s"] >= 0.0
    assert metrics["tls_s"] >= 0.0
    assert metrics["response_headers_s"] >= 0.0
    assert metrics["provider_processing_s"] >= 0.0
    assert metrics["connection_reused"] is False


class _CloseReleasedCall:
    def __init__(self, *, compatibility_retry: bool = False) -> None:
        self.compatibility_retry = compatibility_retry
        self.started = threading.Event()
        self.released = threading.Event()
        self.finished = threading.Event()
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.compatibility_retry and self.calls == 1:
            token_field = (
                "max_completion_tokens"
                if "max_completion_tokens" in kwargs
                else "max_tokens"
            )
            raise ValueError(f"Unsupported parameter: {token_field}")
        self.started.set()
        self.released.wait(timeout=2.0)
        self.finished.set()
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content="late result"),
                )
            ],
            output_text="late result",
            usage=None,
        )


def _install_close_released_openai(
    monkeypatch,
    *,
    compatibility_retry: bool = False,
):
    calls = _CloseReleasedCall(compatibility_retry=compatibility_retry)
    instances = []

    class _Client:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.chat = types.SimpleNamespace(completions=calls)
            self.responses = calls
            self.close_calls = 0
            instances.append(self)

        def close(self) -> None:
            self.close_calls += 1
            calls.released.set()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_Client))
    return calls, instances


def _assert_hard_timeout_cleanup(translator, call, client) -> None:
    assert call.started.is_set()
    assert call.finished.wait(timeout=0.5)
    assert translator._active_wall_call_count() == 0
    assert translator._resources_closed is True
    assert translator._http_client.is_closed is True
    assert client.close_calls == 1
    metrics = translator.translation_metrics()
    assert metrics["wall_timeout_triggered"] is True


def test_openai_nonstreaming_hard_wall_timeout_closes_and_releases_sdk_call(
    monkeypatch,
):
    call, instances = _install_close_released_openai(monkeypatch)
    wall_timeout_s = 0.2
    translator = OpenAITranslator(
        api_key="relay-key",
        model="relay-model",
        base_url="https://relay.example/v1",
        provider_id="openai_compatible",
        wall_timeout_s=wall_timeout_s,
    )

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.05
    _assert_hard_timeout_cleanup(translator, call, instances[-1])
    with pytest.raises(RuntimeError, match="client is closed"):
        translator.translate("again", "en", "ja")


def test_openai_hard_wall_timeout_covers_compatibility_retry_fallback(
    monkeypatch,
):
    call, instances = _install_close_released_openai(
        monkeypatch,
        compatibility_retry=True,
    )
    wall_timeout_s = 0.2
    translator = OpenAITranslator(
        api_key="relay-key",
        model="relay-custom-model",
        base_url="https://relay.example/v1",
        provider_id="openai_compatible",
        wall_timeout_s=wall_timeout_s,
    )

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.05
    assert call.calls == 2
    _assert_hard_timeout_cleanup(translator, call, instances[-1])


def test_openai_responses_hard_wall_timeout_covers_sdk_internal_work(
    monkeypatch,
):
    call, instances = _install_close_released_openai(monkeypatch)
    monkeypatch.setenv("MIO_TRANSLATOR_USE_RESPONSES_API", "1")
    wall_timeout_s = 0.2
    translator = OpenAITranslator(
        api_key="official-key",
        model="gpt-5-custom",
        base_url="https://api.openai.com/v1",
        provider_id="openai",
        wall_timeout_s=wall_timeout_s,
    )
    assert translator._use_responses_api is True

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.05
    assert call.calls == 1
    _assert_hard_timeout_cleanup(translator, call, instances[-1])


class _CloseReleasedAnthropicMessages:
    def __init__(self, call: _CloseReleasedCall, *, reject_streaming: bool) -> None:
        self._call = call
        self._reject_streaming = reject_streaming
        self.stream_calls = 0

    def stream(self, **_kwargs):
        self.stream_calls += 1
        if self._reject_streaming:
            raise RuntimeError("streaming is unsupported by this relay")
        raise AssertionError("stream path was not expected")

    def create(self, **kwargs):
        response = self._call.create(**kwargs)
        return {
            "content": [{"type": "text", "text": response.output_text}],
        }


def _install_close_released_anthropic(monkeypatch, *, reject_streaming: bool):
    call = _CloseReleasedCall()
    instances = []

    class _Client:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.messages = _CloseReleasedAnthropicMessages(
                call,
                reject_streaming=reject_streaming,
            )
            self.close_calls = 0
            instances.append(self)

        def close(self) -> None:
            self.close_calls += 1
            call.released.set()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=_Client),
    )
    return call, instances


@pytest.mark.parametrize("streaming", (False, True))
@pytest.mark.skip(reason="Claude/Anthropic translation backends are disabled")
def test_anthropic_hard_wall_timeout_covers_nonstream_and_stream_fallback(
    monkeypatch,
    streaming,
):
    call, instances = _install_close_released_anthropic(
        monkeypatch,
        reject_streaming=streaming,
    )
    wall_timeout_s = 0.2
    translator = AnthropicTranslator(
        api_key="relay-key",
        model="relay-claude-model",
        base_url="https://claude-relay.example/v1",
        provider_id="anthropic_compatible",
        streaming=streaming,
        wall_timeout_s=wall_timeout_s,
    )

    started_at = time.perf_counter()
    with pytest.raises(ProviderWallTimeoutError):
        translator.translate("hello", "en", "ja")
    elapsed = time.perf_counter() - started_at

    assert elapsed <= wall_timeout_s + 0.05
    if streaming:
        assert instances[-1].messages.stream_calls == 1
    _assert_hard_timeout_cleanup(translator, call, instances[-1])
