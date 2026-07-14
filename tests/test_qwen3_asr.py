from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.asr import qwen3_asr
from src.asr.errors import (
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRProviderError,
    ASRTemporaryUnavailableError,
)
from src.asr.qwen3_asr import Qwen3ASRProvider


def _completion(content: str):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _install_fake_runtime(monkeypatch, behaviors=()):
    """Install isolated AsyncOpenAI/httpx fakes for one provider test."""

    state = SimpleNamespace(
        behaviors=list(behaviors),
        calls=[],
        openai_clients=[],
        http_clients=[],
        timeouts=[],
        limits=[],
    )

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(Exception):
        def __init__(self, message: str, *, status_code: int = 400) -> None:
            super().__init__(message)
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class Timeout:
        def __init__(self, timeout, **kwargs) -> None:
            self.timeout = timeout
            self.connect = kwargs.get("connect")
            self.pool = kwargs.get("pool")
            state.timeouts.append(self)

    class Limits:
        def __init__(self, **kwargs) -> None:
            self.max_connections = kwargs.get("max_connections")
            self.max_keepalive_connections = kwargs.get(
                "max_keepalive_connections"
            )
            self.keepalive_expiry = kwargs.get("keepalive_expiry")
            state.limits.append(self)

    class AsyncClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.close_calls = 0
            state.http_clients.append(self)

        async def aclose(self) -> None:
            self.close_calls += 1

    class AsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.close_calls = 0
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )
            state.openai_clients.append(self)

        async def _create(self, **kwargs):
            state.calls.append(kwargs)
            behavior = (
                state.behaviors.pop(0)
                if state.behaviors
                else "Transcription: default"
            )
            if isinstance(behavior, BaseException):
                raise behavior
            if callable(behavior):
                result = behavior(kwargs, state)
                if inspect.isawaitable(result):
                    return await result
                return result
            return _completion(str(behavior))

        async def close(self) -> None:
            self.close_calls += 1

    state.APIConnectionError = APIConnectionError
    state.APITimeoutError = APITimeoutError
    state.APIStatusError = APIStatusError
    state.AuthenticationError = AuthenticationError
    state.RateLimitError = RateLimitError
    state.AsyncOpenAI = AsyncOpenAI
    state.AsyncClient = AsyncClient
    state.Timeout = Timeout
    state.Limits = Limits

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            AsyncOpenAI=AsyncOpenAI,
            APIConnectionError=APIConnectionError,
            APITimeoutError=APITimeoutError,
            APIStatusError=APIStatusError,
            AuthenticationError=AuthenticationError,
            RateLimitError=RateLimitError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            AsyncClient=AsyncClient,
            Timeout=Timeout,
            Limits=Limits,
        ),
    )
    return state


def _provider_config(**overrides):
    provider_config = {"api_key": "test-key", **overrides}
    return {"asr": {"qwen3_asr": provider_config}}


def test_qwen3_asr_requires_api_key():
    provider = Qwen3ASRProvider({"asr": {"qwen3_asr": {"api_key": ""}}})

    with pytest.raises(ASRMissingAPIKeyError):
        provider.load()


def test_qwen3_asr_sends_audio_cleans_text_and_logs_request_context(
    monkeypatch,
    caplog,
):
    state = _install_fake_runtime(
        monkeypatch,
        ["  Transcription: hello   world  "],
    )
    provider = Qwen3ASRProvider(
        _provider_config(
            region="singapore",
            model="qwen3-asr-flash-2026-02-10",
            language="ja-JP",
        )
    )
    caplog.set_level(logging.INFO, logger="src.asr.qwen3_asr")

    try:
        text = provider.transcribe_realtime(
            np.zeros(1600, dtype=np.float32),
            sample_rate=16000,
            request_context={
                "source": "mic",
                "sequence": 42,
                "session_id": "session-a",
            },
        )

        assert text == "hello world"
        init_kwargs = state.openai_clients[0].kwargs
        assert init_kwargs["base_url"] == (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        assert init_kwargs["max_retries"] == 0
        request = state.calls[0]
        assert request["model"] == "qwen3-asr-flash-2026-02-10"
        assert "timeout" not in request
        content = request["messages"][0]["content"][0]
        assert content["type"] == "input_audio"
        assert content["input_audio"]["data"].startswith(
            "data:audio/wav;base64,"
        )
        assert request["extra_body"] == {
            "asr_options": {"enable_itn": False, "language": "ja"}
        }
        assert "request_id=qwen-1-1" in caplog.text
        assert "source=mic sequence=42 session_id=session-a" in caplog.text
        assert "outcome=success" in caplog.text
        assert "phase=provider" in caplog.text
        assert "event_loop_queue_ms=" in caplog.text
        assert "provider_queue_ms=" in caplog.text
        assert "provider_ms=" in caplog.text
        assert "request_ms=" in caplog.text
        assert "cleanup_ms=" in caplog.text
    finally:
        provider.close()


def test_qwen3_asr_reuses_persistent_async_clients_with_bounded_keepalive(
    monkeypatch,
):
    state = _install_fake_runtime(
        monkeypatch,
        ["Transcription: first", "Transcript: second"],
    )
    provider = Qwen3ASRProvider(_provider_config())

    try:
        assert provider.max_concurrent_transcriptions == 1
        assert provider.transcribe(np.ones(800, dtype=np.float32)) == "first"
        assert provider.transcribe(np.ones(800, dtype=np.float32)) == "second"

        assert len(state.openai_clients) == 1
        assert len(state.http_clients) == 1
        assert len(state.calls) == 2
        assert state.openai_clients[0].kwargs["http_client"] is state.http_clients[0]

        phase_timeout = state.timeouts[0]
        assert phase_timeout.timeout == 25.0
        assert phase_timeout.connect == 5.0
        assert phase_timeout.pool == 1.0

        limits = state.limits[0]
        assert limits.max_connections == 1
        assert limits.max_keepalive_connections == 1
        assert limits.keepalive_expiry == 120.0
        assert state.http_clients[0].kwargs["limits"] is limits
        assert state.http_clients[0].kwargs["timeout"] is phase_timeout
        assert state.http_clients[0].kwargs["event_hooks"]["response"] == [
            provider._on_http_response
        ]
    finally:
        provider.close()

    assert state.openai_clients[0].close_calls == 1
    assert state.http_clients[0].close_calls == 1


def test_qwen3_asr_uses_selected_latest_flash_alias(monkeypatch):
    state = _install_fake_runtime(monkeypatch, ["Transcription: latest"])
    provider = Qwen3ASRProvider(
        _provider_config(model="qwen3-asr-flash")
    )

    try:
        assert provider.transcribe(np.zeros(1600, dtype=np.float32)) == "latest"
        assert state.calls[0]["model"] == "qwen3-asr-flash"
    finally:
        provider.close()


def test_qwen3_asr_hard_timeout_releases_request_and_recycles_runtime(
    monkeypatch,
):
    started = threading.Event()
    cancelled = threading.Event()

    async def hang_forever(_kwargs, _state):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    state = _install_fake_runtime(
        monkeypatch,
        [hang_forever, "Transcription: recovered"],
    )
    monkeypatch.setattr(qwen3_asr, "QWEN_RECOVERY_BACKOFF_SECONDS", 0.0)
    provider = Qwen3ASRProvider(_provider_config())
    provider.hard_timeout_seconds = 0.1

    try:
        before = time.monotonic()
        with pytest.raises(ASRTemporaryUnavailableError, match="exceeded"):
            provider.transcribe(np.ones(1600, dtype=np.float32))
        elapsed = time.monotonic() - before

        assert elapsed < 1.0
        assert started.wait(timeout=1.0)
        assert cancelled.wait(timeout=1.0)
        assert provider._active_requests == {}
        assert provider._client is None
        assert provider._http_client is None
        assert provider._request_semaphore is None
        assert state.openai_clients[0].close_calls == 1
        assert state.http_clients[0].close_calls == 1

        assert provider.transcribe(np.ones(1600, dtype=np.float32)) == "recovered"
        assert provider._runtime_generation == 2
        assert len(state.openai_clients) == 2
        assert len(state.http_clients) == 2
        assert provider._active_requests == {}
    finally:
        provider.close()


def test_qwen3_asr_cancellation_releases_request_and_close_is_idempotent(
    monkeypatch,
):
    started = threading.Event()
    cancelled = threading.Event()
    cancel_request = threading.Event()

    async def hang_forever(_kwargs, _state):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    state = _install_fake_runtime(monkeypatch, [hang_forever])
    monkeypatch.setattr(qwen3_asr, "QWEN_RECOVERY_BACKOFF_SECONDS", 0.0)
    provider = Qwen3ASRProvider(_provider_config())
    result = {}

    def invoke() -> None:
        try:
            provider.transcribe_realtime(
                np.ones(1600, dtype=np.float32),
                cancel_event=cancel_request,
            )
        except BaseException as exc:  # Captured for the calling test thread.
            result["error"] = exc

    worker = threading.Thread(target=invoke, name="qwen-test-caller")
    worker.start()
    assert started.wait(timeout=1.0)
    runner = provider._runner
    cancel_request.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ASRTemporaryUnavailableError)
    assert cancelled.wait(timeout=1.0)
    assert provider._active_requests == {}
    assert state.openai_clients[0].close_calls == 1
    assert state.http_clients[0].close_calls == 1

    provider.cancel_pending_requests()
    provider.cancel_pending_requests()
    provider.close()
    provider.close()

    assert runner is not None
    assert not runner._thread.is_alive()
    assert provider._runner is None
    assert state.openai_clients[0].close_calls == 1
    assert state.http_clients[0].close_calls == 1


def test_qwen3_asr_pre_cancel_does_not_load_or_submit_provider(monkeypatch):
    state = _install_fake_runtime(monkeypatch, ["Transcription: must not run"])
    provider = Qwen3ASRProvider(_provider_config())
    cancel_request = threading.Event()
    cancel_request.set()

    try:
        with pytest.raises(ASRTemporaryUnavailableError, match="cancelled"):
            provider.transcribe_realtime(
                np.ones(1600, dtype=np.float32),
                cancel_event=cancel_request,
            )

        assert state.calls == []
        assert state.openai_clients == []
        assert state.http_clients == []
        assert provider._runner is None
        assert provider._active_requests == {}
    finally:
        provider.close()


def test_qwen3_asr_pipeline_cancel_cannot_miss_submitted_future(
    monkeypatch,
):
    provider_started = threading.Event()
    provider_cancelled = threading.Event()

    async def hang_forever(_kwargs, _state):
        provider_started.set()
        try:
            await asyncio.Future()
        finally:
            provider_cancelled.set()

    _install_fake_runtime(monkeypatch, [hang_forever])
    monkeypatch.setattr(qwen3_asr, "QWEN_RECOVERY_BACKOFF_SECONDS", 0.0)
    provider = Qwen3ASRProvider(_provider_config())
    provider.load()
    runner = provider._runner
    assert runner is not None
    original_submit = runner.submit
    submit_entered = threading.Event()
    release_submit = threading.Event()

    def gated_submit(coroutine):
        future = original_submit(coroutine)
        submit_entered.set()
        assert release_submit.wait(timeout=2.0)
        return future

    monkeypatch.setattr(runner, "submit", gated_submit)
    result = {}

    def invoke() -> None:
        try:
            provider.transcribe_realtime(np.ones(1600, dtype=np.float32))
        except BaseException as exc:
            result["error"] = exc

    caller = threading.Thread(target=invoke, name="qwen-race-caller")
    canceller = threading.Thread(
        target=provider.cancel_pending_requests,
        name="qwen-race-canceller",
    )
    caller.start()
    assert submit_entered.wait(timeout=1.0)
    canceller.start()
    assert canceller.is_alive()

    release_submit.set()
    canceller.join(timeout=2.0)
    caller.join(timeout=2.0)

    assert not canceller.is_alive()
    assert not caller.is_alive()
    assert isinstance(result.get("error"), ASRTemporaryUnavailableError)
    if provider_started.is_set():
        assert provider_cancelled.wait(timeout=1.0)
    assert provider._active_requests == {}
    assert provider._client is None
    assert provider._http_client is None

    provider.close()
    assert not runner._thread.is_alive()
    assert provider._runner is None


def test_qwen3_asr_provider_error_releases_semaphore_for_next_request(
    monkeypatch,
):
    state = _install_fake_runtime(monkeypatch)
    state.behaviors.extend(
        [
            state.APIStatusError("bad request", status_code=400),
            "Transcription: accepted next",
        ]
    )
    provider = Qwen3ASRProvider(_provider_config())

    try:
        with pytest.raises(ASRProviderError, match="bad request"):
            provider.transcribe(np.ones(1600, dtype=np.float32))

        assert provider._active_requests == {}
        assert provider.is_loaded is True
        assert state.openai_clients[0].close_calls == 0
        assert provider.transcribe(np.ones(1600, dtype=np.float32)) == (
            "accepted next"
        )
        assert len(state.openai_clients) == 1
        assert len(state.calls) == 2
    finally:
        provider.close()


def test_qwen3_asr_network_error_recycles_client_and_next_request_succeeds(
    monkeypatch,
):
    state = _install_fake_runtime(monkeypatch)
    state.behaviors.extend(
        [
            state.APIConnectionError("connection dropped"),
            "Transcription: network recovered",
        ]
    )
    monkeypatch.setattr(qwen3_asr, "QWEN_RECOVERY_BACKOFF_SECONDS", 0.0)
    provider = Qwen3ASRProvider(_provider_config())

    try:
        with pytest.raises(ASRNetworkError, match="connection dropped"):
            provider.transcribe(np.ones(1600, dtype=np.float32))

        assert provider._active_requests == {}
        assert provider.is_loaded is False
        assert state.openai_clients[0].close_calls == 1
        assert state.http_clients[0].close_calls == 1

        assert provider.transcribe(np.ones(1600, dtype=np.float32)) == (
            "network recovered"
        )
        assert provider._runtime_generation == 2
        assert len(state.openai_clients) == 2
        assert len(state.http_clients) == 2
    finally:
        provider.close()
