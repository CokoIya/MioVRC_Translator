from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import threading
import time
from collections.abc import Mapping
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.audio_encoding import wav_data_url
from src.asr.base import ASRProvider, ProgressCallback, close_runtime_resource
from src.asr.model_registry import (
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_DEFAULT_REGION,
    QWEN3_ASR_LEGACY_MODEL_IDS,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)
from src.asr.errors import (
    ASRConfigurationError,
    ASRMissingAPIKeyError,
    ASRNetworkError,
    ASRProviderError,
    ASRRateLimitError,
    ASRTemporaryUnavailableError,
)
from src.asr.text_corrections import LayeredASRCorrector
from src.utils.provider_network import (
    direct_connection_ssl_context,
    should_bypass_environment_proxies,
)
from src.utils.qwen_endpoints import (
    QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    require_qwen_tokyo_workspace_base_url,
)
from src.utils.secure_http import validate_api_base_url

logger = logging.getLogger(__name__)

DEFAULT_MODEL = QWEN3_ASR_DEFAULT_MODEL
DEFAULT_REGION = QWEN3_ASR_DEFAULT_REGION
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_HARD_TIMEOUT_SECONDS = 12.0
QWEN_HTTP_KEEPALIVE_EXPIRY_SECONDS = 120.0
QWEN_HTTP_CONNECT_TIMEOUT_SECONDS = 5.0
QWEN_HTTP_POOL_TIMEOUT_SECONDS = 1.0
QWEN_RECOVERY_BACKOFF_SECONDS = 0.75

_HTTP_REQUEST_CONTEXT: contextvars.ContextVar[dict[str, object] | None] = (
    contextvars.ContextVar("qwen_asr_http_request", default=None)
)

_LANGUAGE_ALIASES = {
    "ja-jp": "ja",
    "jp": "ja",
    "jpn": "ja",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "cn": "zh",
    "en-us": "en",
    "en-gb": "en",
    "ko-kr": "ko",
    "kr": "ko",
    "pt-br": "pt",
    "pt-pt": "pt",
}

_QWEN_LANGUAGE_HINTS = {
    "ja",
    "zh",
    "en",
    "ko",
    "ru",
    "fr",
    "de",
    "es",
    "pt",
    "it",
}


def _cfg(config: Mapping[str, object] | None) -> Mapping[str, object]:
    asr_cfg = (config or {}).get("asr", {}) if isinstance(config, Mapping) else {}
    if not isinstance(asr_cfg, Mapping):
        return {}
    provider_cfg = asr_cfg.get("qwen3_asr", {})
    return provider_cfg if isinstance(provider_cfg, Mapping) else {}


def _language_code(language: object) -> str:
    text = str(language or "").strip().lower().replace("_", "-")
    if not text or text == "auto":
        return ""
    text = _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])
    return text if text in _QWEN_LANGUAGE_HINTS else ""


def _request_language(language: object, configured_language: str) -> str:
    """Resolve an explicit request hint without turning Auto into Japanese."""

    text = str(language or "").strip().lower().replace("_", "-")
    if text in {"auto", "automatic", "detect"}:
        return ""
    if text:
        return _language_code(text)
    return configured_language


def _encode_wav_data_url(audio: np.ndarray, sample_rate: int) -> str:
    return wav_data_url(audio, sample_rate)


class _AsyncLoopRunner:
    """One persistent daemon event loop for cancellable Qwen HTTP requests."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="qwen-asr-event-loop",
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Qwen3-ASR event loop did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._closed.set()

    def submit(self, coroutine):
        loop = self._loop
        if loop is None or self._closed.is_set():
            close = getattr(coroutine, "close", None)
            if callable(close):
                close()
            raise RuntimeError("Qwen3-ASR event loop is closed")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def run(self, coroutine, *, timeout: float):
        future = self.submit(coroutine)
        try:
            return future.result(timeout=max(float(timeout), 0.1))
        except FutureTimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        loop = self._loop
        if loop is None or self._closed.is_set():
            return
        loop.call_soon_threadsafe(loop.stop)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)


class Qwen3ASRProvider(ASRProvider):
    provider_id = "qwen3-asr"
    display_name = "Qwen3-ASR"
    requires_api_key = True
    supports_partial = False

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        corrector: LayeredASRCorrector | None = None,
    ) -> None:
        provider_cfg = _cfg(config)
        self.api_key = str(provider_cfg.get("api_key", "") or "").strip()
        self.model = str(provider_cfg.get("model", DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        if self.model in QWEN3_ASR_LEGACY_MODEL_IDS:
            self.model = DEFAULT_MODEL
        self.region = normalize_qwen3_asr_region(provider_cfg.get("region", DEFAULT_REGION))
        self.base_url = str(provider_cfg.get("base_url", "") or "").strip()
        self.language = _language_code(provider_cfg.get("language", "ja")) or "ja"
        self.timeout_seconds = _float_value(
            provider_cfg.get("timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.hard_timeout_seconds = _float_range(
            provider_cfg.get("hard_timeout_seconds"),
            DEFAULT_HARD_TIMEOUT_SECONDS,
            2.0,
            30.0,
        )
        # Retrying an already-uploaded audio request can duplicate recognition
        # and extends head-of-line blocking. Realtime recovery is performed by
        # the next ordered sentence on a fresh transport generation instead.
        self.max_retries = 0
        self.max_concurrent_transcriptions = _int_range(
            provider_cfg.get("max_concurrent_transcriptions"),
            1,
            1,
            2,
        )
        # Separate persistent Qwen runtimes may serve microphone and reverse
        # audio concurrently. A shared instance still serializes itself through
        # this stable per-instance scheduler key and its internal semaphore.
        self.concurrency_key = (self.provider_id, id(self))
        self._corrector = corrector
        self._client = None
        self._http_client = None
        self._request_semaphore = None
        self._runner: _AsyncLoopRunner | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._runtime_generation = 0
        self._request_counter = 0
        self._active_requests: dict[int, tuple[int, object]] = {}
        self._seen_network_streams: set[int] = set()
        self._retry_not_before = 0.0
        self._successful_requests = 0
        self._failed_requests = 0
        self._timed_out_requests = 0

    def _resolved_base_url(self) -> str:
        if self.region == "japan":
            try:
                return require_qwen_tokyo_workspace_base_url(
                    self.base_url,
                    endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
                    label="Qwen3-ASR Tokyo workspace API",
                )
            except ValueError as exc:
                raise ASRConfigurationError(str(exc)) from exc
        if self.base_url:
            candidate = self.base_url
        elif self.region == "custom":
            raise ASRConfigurationError("Qwen3-ASR base_url is required when region is custom")
        else:
            candidate = get_qwen3_asr_base_url(self.region)
        try:
            return validate_api_base_url(
                candidate,
                label="Qwen3-ASR API",
                allow_private_http=should_bypass_environment_proxies(candidate),
            )
        except ValueError as exc:
            raise ASRConfigurationError(str(exc)) from exc

    def _uses_local_endpoint(self) -> bool:
        return should_bypass_environment_proxies(self._resolved_base_url())

    @staticmethod
    async def _remove_placeholder_authorization(request) -> None:
        """Keep the SDK-only key out of requests to keyless local servers."""

        authorization = str(
            request.headers.get("authorization", "") or ""
        ).strip()
        if authorization.casefold() == "bearer local-no-key":
            request.headers.pop("authorization", None)

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        with self._lock:
            if self._closed:
                raise ASRConfigurationError("Qwen3-ASR provider is closed")
            if self._client is not None:
                return
            if not self.api_key and not self._uses_local_endpoint():
                raise ASRMissingAPIKeyError("Qwen3-ASR API Key is not configured")
            try:
                import httpx
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ASRConfigurationError(
                    "openai and httpx packages are required for Qwen3-ASR"
                ) from exc

            runner = self._runner
            if runner is None:
                runner = _AsyncLoopRunner()
                self._runner = runner
            self._runtime_generation += 1
            generation = self._runtime_generation
            try:
                client, http_client, semaphore = runner.run(
                    self._create_runtime(
                        AsyncOpenAI,
                        httpx,
                        generation=generation,
                    ),
                    timeout=5.0,
                )
            except Exception:
                if self._runner is runner and self._client is None:
                    self._runner = None
                    runner.close()
                raise
            if self._closed:
                try:
                    runner.run(
                        _close_async_resources(client, http_client),
                        timeout=2.0,
                    )
                except Exception:
                    logger.debug(
                        "Qwen3-ASR runtime close after cancelled load failed",
                        exc_info=True,
                    )
                raise ASRConfigurationError("Qwen3-ASR provider is closed")
            self._client = client
            self._http_client = http_client
            self._request_semaphore = semaphore
            self._seen_network_streams.clear()
            logger.info(
                "Qwen3-ASR runtime ready generation=%d concurrency=%d "
                "hard_timeout_s=%.1f keepalive_s=%.1f retries=0",
                generation,
                self.max_concurrent_transcriptions,
                self.hard_timeout_seconds,
                QWEN_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            )
            if progress_callback is not None:
                progress_callback({"stage": "ready", "message": "Qwen3-ASR ready"})

    async def _create_runtime(self, async_openai, httpx, *, generation: int):
        resolved_base_url = self._resolved_base_url()
        bypass_environment_proxies = should_bypass_environment_proxies(
            resolved_base_url
        )
        phase_timeout = httpx.Timeout(
            self.timeout_seconds,
            connect=min(self.timeout_seconds, QWEN_HTTP_CONNECT_TIMEOUT_SECONDS),
            pool=min(self.timeout_seconds, QWEN_HTTP_POOL_TIMEOUT_SECONDS),
        )
        http_client_kwargs: dict[str, object] = {
            "timeout": phase_timeout,
            "trust_env": not bypass_environment_proxies,
            "limits": httpx.Limits(
                max_connections=self.max_concurrent_transcriptions,
                max_keepalive_connections=self.max_concurrent_transcriptions,
                keepalive_expiry=QWEN_HTTP_KEEPALIVE_EXPIRY_SECONDS,
            ),
            "event_hooks": {
                "request": [self._remove_placeholder_authorization]
                if not self.api_key and bypass_environment_proxies
                else [],
                "response": [self._on_http_response],
            },
        }
        direct_ssl_context = direct_connection_ssl_context(resolved_base_url)
        if direct_ssl_context is not None:
            http_client_kwargs["verify"] = direct_ssl_context
        http_client = httpx.AsyncClient(**http_client_kwargs)
        try:
            client = async_openai(
                # The OpenAI client requires a non-empty value even when a
                # loopback/private-LAN compatible server does not authenticate.
                api_key=self.api_key or "local-no-key",
                base_url=resolved_base_url,
                timeout=phase_timeout,
                max_retries=0,
                http_client=http_client,
            )
        except BaseException:
            await http_client.aclose()
            raise
        logger.debug("Created Qwen3-ASR HTTP runtime generation=%d", generation)
        return (
            client,
            http_client,
            asyncio.Semaphore(self.max_concurrent_transcriptions),
        )

    async def _on_http_response(self, response) -> None:
        context = _HTTP_REQUEST_CONTEXT.get() or {}
        request_id = context.get("request_id", "unknown")
        started_at = float(context.get("provider_started_at", 0.0) or 0.0)
        timing = context.get("timing")
        stream = getattr(response, "extensions", {}).get("network_stream")
        stream_id = id(stream) if stream is not None else 0
        with self._lock:
            reused = bool(stream_id and stream_id in self._seen_network_streams)
            if stream_id:
                self._seen_network_streams.add(stream_id)
        if isinstance(timing, dict):
            timing["connection_reused"] = reused
            timing["http_response_at"] = time.monotonic()
        provider_request_id = ""
        try:
            provider_request_id = str(
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
                or ""
            ).strip()
        except Exception:
            provider_request_id = ""
        logger.info(
            "Qwen3-ASR response headers request_id=%s status=%s header_ms=%.1f "
            "connection_reused=%s http_version=%s provider_request_id=%s",
            request_id,
            getattr(response, "status_code", "unknown"),
            max(0.0, time.monotonic() - started_at) * 1000.0
            if started_at > 0
            else 0.0,
            reused,
            getattr(response, "http_version", ""),
            provider_request_id or "none",
        )

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        return self._transcribe(
            audio,
            sample_rate=sample_rate,
            language=language,
            is_final=is_final,
            request_context=None,
            cancel_event=None,
        )

    def transcribe_realtime(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
        *,
        request_context: Mapping[str, object] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        return self._transcribe(
            audio,
            sample_rate=sample_rate,
            language=language,
            is_final=is_final,
            request_context=request_context,
            cancel_event=cancel_event,
        )

    def _transcribe(
        self,
        audio: np.ndarray,
        *,
        sample_rate: int,
        language: Optional[str],
        is_final: bool,
        request_context: Mapping[str, object] | None,
        cancel_event: threading.Event | None,
    ) -> str:
        if not is_final:
            return ""
        if cancel_event is not None and cancel_event.is_set():
            raise ASRTemporaryUnavailableError("Qwen3-ASR request was cancelled")
        data_url = _encode_wav_data_url(audio, sample_rate)
        if not data_url:
            return ""
        if cancel_event is not None and cancel_event.is_set():
            raise ASRTemporaryUnavailableError("Qwen3-ASR request was cancelled")
        if self._closed:
            raise ASRConfigurationError("Qwen3-ASR provider is closed")
        if self._client is None:
            self.load()
        with self._lock:
            client = self._client
            runner = self._runner
            semaphore = self._request_semaphore
            generation = self._runtime_generation
            now = time.monotonic()
            if self._closed or client is None or runner is None or semaphore is None:
                raise ASRConfigurationError("Qwen3-ASR provider is closed")
            if now < self._retry_not_before:
                remaining = self._retry_not_before - now
                raise ASRTemporaryUnavailableError(
                    f"Qwen3-ASR is recovering; retry in {remaining:.1f}s"
                )
            self._request_counter += 1
            request_number = self._request_counter
            context = dict(request_context or {})
            external_timing = context.get("timing")
            if not isinstance(external_timing, dict):
                external_timing = None
            source = str(context.get("source", "unknown") or "unknown")
            sequence = context.get("sequence", "unknown")
            session_id = context.get("session_id", "unknown")
            request_id = f"qwen-{generation}-{request_number}"
        lang = _request_language(language, self.language)
        extra_body: dict[str, object] = {"asr_options": {"enable_itn": False}}
        if lang:
            extra_body["asr_options"]["language"] = lang
        audio_array = np.asarray(audio)
        audio_samples = int(audio_array.size)
        audio_duration_s = (
            audio_samples / max(int(sample_rate), 1) if audio_samples else 0.0
        )
        submitted_at = time.monotonic()
        request_timing: dict[str, float] = {
            "scheduled_at": submitted_at,
        }
        with self._lock:
            if cancel_event is not None and cancel_event.is_set():
                raise ASRTemporaryUnavailableError("Qwen3-ASR request was cancelled")
            if (
                self._closed
                or generation != self._runtime_generation
                or client is not self._client
                or runner is not self._runner
                or semaphore is not self._request_semaphore
            ):
                raise ASRTemporaryUnavailableError("Qwen3-ASR request was cancelled")
            # Submission and registration must be atomic with runtime
            # retirement. Otherwise pipeline cancellation can miss a future
            # submitted to the loop but not yet visible in _active_requests.
            future = runner.submit(
                self._request_completion(
                    client,
                    semaphore,
                    request_id=request_id,
                    model=self.model,
                    data_url=data_url,
                    extra_body=extra_body,
                    timing=request_timing,
                )
            )
            self._active_requests[request_number] = (generation, future)
            active_estimate = len(self._active_requests)
        logger.info(
            "Qwen3-ASR request admitted request_id=%s source=%s sequence=%s "
            "session_id=%s generation=%d audio_ms=%.1f encoded_bytes=%d active=%d",
            request_id,
            source,
            sequence,
            session_id,
            generation,
            audio_duration_s * 1000.0,
            len(data_url.encode("utf-8")),
            active_estimate,
        )

        outcome = "failed"
        request_terminal_at = 0.0
        cleanup_duration_s = 0.0
        try:
            completion = self._wait_for_request(
                future,
                cancel_event=cancel_event,
                timeout=self.hard_timeout_seconds,
            )
            request_terminal_at = time.monotonic()
            outcome = "success"
            with self._lock:
                self._successful_requests += 1
                self._retry_not_before = 0.0
        except FutureTimeoutError as exc:
            request_terminal_at = time.monotonic()
            outcome = "timeout"
            with self._lock:
                self._timed_out_requests += 1
                self._failed_requests += 1
            cleanup_started_at = time.monotonic()
            self._retire_runtime(generation, reason="hard-timeout")
            cleanup_duration_s = max(0.0, time.monotonic() - cleanup_started_at)
            raise ASRTemporaryUnavailableError(
                f"Qwen3-ASR request exceeded {self.hard_timeout_seconds:.1f}s"
            ) from exc
        except FutureCancelledError as exc:
            request_terminal_at = time.monotonic()
            outcome = "cancelled"
            with self._lock:
                self._failed_requests += 1
            cleanup_started_at = time.monotonic()
            self._retire_runtime(generation, reason="cancelled")
            cleanup_duration_s = max(0.0, time.monotonic() - cleanup_started_at)
            raise ASRTemporaryUnavailableError("Qwen3-ASR request was cancelled") from exc
        except Exception as exc:
            request_terminal_at = time.monotonic()
            with self._lock:
                self._failed_requests += 1
            try:
                _raise_provider_error(exc)
            except ASRNetworkError:
                cleanup_started_at = time.monotonic()
                self._retire_runtime(generation, reason="network-error")
                cleanup_duration_s = max(
                    0.0,
                    time.monotonic() - cleanup_started_at,
                )
                raise
        finally:
            future.cancel() if outcome in {"timeout", "cancelled"} else None
            if request_terminal_at <= 0:
                request_terminal_at = time.monotonic()
            with self._lock:
                current = self._active_requests.get(request_number)
                if current is not None and current[1] is future:
                    self._active_requests.pop(request_number, None)
                active = len(self._active_requests)
                successes = self._successful_requests
                failures = self._failed_requests
                timeouts = self._timed_out_requests
            coroutine_started_at = float(
                request_timing.get("coroutine_started_at", 0.0) or 0.0
            )
            provider_queued_at = float(
                request_timing.get("provider_queued_at", 0.0) or 0.0
            )
            provider_started_at = float(
                request_timing.get("provider_started_at", 0.0) or 0.0
            )
            if provider_started_at > 0:
                phase = "provider"
                provider_queue_wait_s = max(
                    0.0,
                    provider_started_at - provider_queued_at,
                )
                provider_elapsed_s = max(
                    0.0,
                    request_terminal_at - provider_started_at,
                )
            elif provider_queued_at > 0:
                phase = "provider_queue"
                provider_queue_wait_s = max(
                    0.0,
                    request_terminal_at - provider_queued_at,
                )
                provider_elapsed_s = 0.0
            else:
                phase = "event_loop"
                provider_queue_wait_s = 0.0
                provider_elapsed_s = 0.0
            event_loop_queue_s = (
                max(0.0, coroutine_started_at - submitted_at)
                if coroutine_started_at > 0
                else max(0.0, request_terminal_at - submitted_at)
            )
            log_finished_at = time.monotonic()
            logger.info(
                "Qwen3-ASR request finished request_id=%s source=%s sequence=%s "
                "outcome=%s phase=%s event_loop_queue_ms=%.1f "
                "provider_queue_ms=%.1f provider_ms=%.1f request_ms=%.1f "
                "cleanup_ms=%.1f wall_ms=%.1f active=%d successes=%d "
                "failures=%d timeouts=%d",
                request_id,
                source,
                sequence,
                outcome,
                phase,
                event_loop_queue_s * 1000.0,
                provider_queue_wait_s * 1000.0,
                provider_elapsed_s * 1000.0,
                max(0.0, request_terminal_at - submitted_at) * 1000.0,
                cleanup_duration_s * 1000.0,
                max(0.0, log_finished_at - submitted_at) * 1000.0,
                active,
                successes,
                failures,
                timeouts,
            )
            if external_timing is not None:
                external_timing.update(
                    {
                        "asr_event_loop_queue_s": event_loop_queue_s,
                        "asr_provider_queue_s": provider_queue_wait_s,
                        "asr_provider_s": provider_elapsed_s,
                        "asr_request_s": max(
                            0.0,
                            request_terminal_at - submitted_at,
                        ),
                        "asr_cleanup_s": cleanup_duration_s,
                        "asr_outcome": outcome,
                        "asr_request_id": request_id,
                    }
                )
                if "connection_reused" in request_timing:
                    external_timing["asr_connection_reused"] = bool(
                        request_timing["connection_reused"]
                    )
        content = completion.choices[0].message.content if completion.choices else ""
        text = clean_asr_text(str(content or ""))
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=lang or None)
        return text

    async def _request_completion(
        self,
        client,
        semaphore,
        *,
        request_id: str,
        model: str,
        data_url: str,
        extra_body: dict[str, object],
        timing: dict[str, float],
    ):
        queued_at = time.monotonic()
        timing["coroutine_started_at"] = queued_at
        timing["provider_queued_at"] = queued_at
        async with semaphore:
            provider_started_at = time.monotonic()
            timing["provider_started_at"] = provider_started_at
            token = _HTTP_REQUEST_CONTEXT.set(
                {
                    "request_id": request_id,
                    "provider_started_at": provider_started_at,
                    "timing": timing,
                }
            )
            try:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": data_url},
                                }
                            ],
                        }
                    ],
                    extra_body=extra_body,
                )
                return completion
            finally:
                timing["provider_finished_at"] = time.monotonic()
                _HTTP_REQUEST_CONTEXT.reset(token)

    @staticmethod
    def _wait_for_request(
        future,
        *,
        cancel_event: threading.Event | None,
        timeout: float,
    ):
        deadline = time.monotonic() + max(float(timeout), 0.1)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                future.cancel()
                raise FutureCancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise FutureTimeoutError()
            try:
                return future.result(timeout=min(remaining, 0.1))
            except FutureTimeoutError:
                if time.monotonic() >= deadline:
                    future.cancel()
                    raise

    def _retire_runtime(self, generation: int, *, reason: str) -> None:
        cleanup_started_at = time.monotonic()
        with self._lock:
            if generation != self._runtime_generation or self._client is None:
                return
            client = self._client
            http_client = self._http_client
            runner = self._runner
            self._client = None
            self._http_client = None
            self._request_semaphore = None
            self._retry_not_before = time.monotonic() + QWEN_RECOVERY_BACKOFF_SECONDS
            active = [
                future
                for active_generation, future in self._active_requests.values()
                if active_generation == generation
            ]
        for future in active:
            try:
                future.cancel()
            except Exception:
                pass
        if runner is not None:
            try:
                runner.run(
                    _close_async_resources(client, http_client),
                    timeout=2.0,
                )
            except Exception:
                logger.debug(
                    "Qwen3-ASR retired runtime cleanup failed generation=%d reason=%s",
                    generation,
                    reason,
                    exc_info=True,
                )
        logger.warning(
            "Qwen3-ASR runtime retired generation=%d reason=%s "
            "cancelled_requests=%d cleanup_ms=%.1f",
            generation,
            reason,
            len(active),
            max(0.0, time.monotonic() - cleanup_started_at) * 1000.0,
        )

    def cancel_pending_requests(self) -> None:
        with self._lock:
            generation = self._runtime_generation
        self._retire_runtime(generation, reason="pipeline-cancel")

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return not self._closed and self._client is not None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            http_client = self._http_client
            runner = self._runner
            active = [future for _generation, future in self._active_requests.values()]
            self._client = None
            self._http_client = None
            self._request_semaphore = None
            self._runner = None
            self._active_requests.clear()
            self._corrector = None
        for future in active:
            try:
                future.cancel()
            except Exception:
                pass
        if runner is not None:
            try:
                runner.run(
                    _close_async_resources(client, http_client),
                    timeout=2.0,
                )
            except Exception:
                logger.debug("Qwen3-ASR async runtime close failed", exc_info=True)
            runner.close()
        else:
            close_runtime_resource(client)
            if http_client is not client:
                close_runtime_resource(http_client)


def _float_value(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _float_range(
    value: object,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _int_range(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _raise_provider_error(exc: Exception) -> None:
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
    except Exception:
        APIConnectionError = APITimeoutError = RateLimitError = AuthenticationError = APIStatusError = ()

    if APIConnectionError and isinstance(exc, (APIConnectionError, APITimeoutError)):
        raise ASRNetworkError(str(exc).strip() or "Qwen3-ASR network error") from exc
    if RateLimitError and isinstance(exc, RateLimitError):
        raise ASRRateLimitError(str(exc).strip() or "Qwen3-ASR rate limit") from exc
    if AuthenticationError and isinstance(exc, AuthenticationError):
        raise ASRMissingAPIKeyError("Qwen3-ASR authentication failed") from exc
    if APIStatusError and isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            raise ASRMissingAPIKeyError("Qwen3-ASR authentication failed") from exc
        if status_code == 429:
            raise ASRRateLimitError("Qwen3-ASR rate limit") from exc
        if status_code and int(status_code) >= 500:
            raise ASRNetworkError("Qwen3-ASR service unavailable") from exc
    raise ASRProviderError(str(exc).strip() or exc.__class__.__name__) from exc


async def _close_async_resources(client: object | None, http_client: object | None) -> None:
    resources = [client]
    if http_client is not None and http_client is not client:
        resources.append(http_client)
    for resource in resources:
        if resource is None:
            continue
        close = getattr(resource, "close", None)
        if not callable(close):
            close = getattr(resource, "aclose", None)
        if not callable(close):
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Qwen3-ASR async resource close failed", exc_info=True)
