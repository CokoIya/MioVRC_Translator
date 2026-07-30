from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

from src.asr.base import ASRProvider
from src.asr.fallback_asr import FallbackASR
from src.asr.qwen3_asr import Qwen3ASRProvider
from src.asr.webspeech_asr import WebSpeechASRProvider, _BridgeState
from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import BaseTranslator
from src.translators.deepl_translator import DeepLTranslator
from src.translators.factory import FallbackTranslator
from src.translators.google_web_translator import GoogleWebTranslator
from src.translators.libretranslate_translator import LibreTranslateTranslator
from src.translators.mymemory_translator import MyMemoryTranslator
from src.translators.openai_translator import OpenAITranslator
from src.tts.api_tts_engines import MimoTTS, QwenTTS
from src.tts.edge_tts_engine import EdgeTTS
from src.tts.gtts_engine import GoogleTTS
from src.tts.voicevox_compatible_engine import VoicevoxCompatibleTTS
from src.utils.provider_warmup import ProviderWarmupResult


_SUCCESS = ProviderWarmupResult(
    attempted=True,
    succeeded=True,
    elapsed_s=0.01,
    status_code=405,
)


class _Pool:
    def __init__(self, session: object) -> None:
        self.session = session
        self.calls = 0

    def get(self):
        self.calls += 1
        return self.session


def test_openai_and_anthropic_prewarm_use_exact_sdk_http_clients(monkeypatch):
    calls: list[tuple[object, str, dict[str, object]]] = []

    def fake_warmup(client, url, **kwargs):
        calls.append((client, url, kwargs))
        return _SUCCESS

    monkeypatch.setattr(
        "src.translators.openai_translator.warmup_httpx_client",
        fake_warmup,
    )
    monkeypatch.setattr(
        "src.translators.anthropic_translator.warmup_httpx_client",
        fake_warmup,
    )

    openai_http = object()
    openai = OpenAITranslator.__new__(OpenAITranslator)
    openai._client = type(
        "SDK",
        (),
        {"default_headers": {"Authorization": "Bearer openai-secret"}},
    )()
    openai._http_client = openai_http
    openai._base_url = "https://api.example/v1"
    openai._connect_timeout_s = 4.0
    openai._provider_id = "openai-compatible"
    openai._log_endpoint = "https://api.example/v1"
    openai._custom_headers = {}

    anthropic_http = object()
    anthropic = AnthropicTranslator.__new__(AnthropicTranslator)
    anthropic._client = type(
        "SDK",
        (),
        {
            "default_headers": {
                "x-api-key": "anthropic-secret",
                "anthropic-version": "2023-06-01",
            }
        },
    )()
    anthropic._http_client = anthropic_http
    anthropic._base_url = "https://anthropic.example/prefix"
    anthropic._connect_timeout_s = 2.0
    anthropic._log_endpoint = "https://anthropic.example/prefix"
    anthropic._custom_headers = {}

    assert openai.prewarm() is True
    assert anthropic.prewarm() is True
    assert calls == [
        (
            openai_http,
            "https://api.example/v1/models",
            {
                "method": "HEAD",
                "headers": {"Authorization": "Bearer openai-secret"},
                "timeout_s": 3.0,
            },
        ),
        (
            anthropic_http,
            "https://anthropic.example/prefix/v1/messages",
            {
                "method": "HEAD",
                "headers": {
                    "x-api-key": "anthropic-secret",
                    "anthropic-version": "2023-06-01",
                },
                "timeout_s": 2.0,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("module_name", "translator_type", "base_url", "expected_url"),
    [
        (
            "src.translators.deepl_translator",
            DeepLTranslator,
            "https://api-free.deepl.com/v2",
            "https://api-free.deepl.com/v2/usage",
        ),
        (
            "src.translators.google_web_translator",
            GoogleWebTranslator,
            "https://translate.googleapis.com/translate_a/single",
            "https://translate.googleapis.com/translate_a/single",
        ),
        (
            "src.translators.libretranslate_translator",
            LibreTranslateTranslator,
            "https://libre.example",
            "https://libre.example/languages",
        ),
        (
            "src.translators.mymemory_translator",
            MyMemoryTranslator,
            "https://api.mymemory.translated.net/get",
            "https://api.mymemory.translated.net/get",
        ),
    ],
)
def test_requests_translators_prewarm_current_worker_session(
    monkeypatch,
    module_name,
    translator_type,
    base_url,
    expected_url,
):
    session = object()
    pool = _Pool(session)
    calls = []

    def fake_warmup(client, url, **kwargs):
        calls.append((client, url, kwargs))
        return _SUCCESS

    monkeypatch.setattr(f"{module_name}.warmup_requests_session", fake_warmup)
    translator = translator_type.__new__(translator_type)
    translator._session_pool = pool
    translator._base_url = base_url
    translator._timeout_s = 8.0

    assert translator.prewarm() is True
    assert pool.calls == 1
    assert calls == [
        (
            session,
            expected_url,
            {"method": "HEAD", "timeout_s": 3.0},
        )
    ]


def test_fallback_wrappers_only_prewarm_primary():
    class Translator(BaseTranslator):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def prewarm(self):
            self.calls += 1
            return True

        def translate(self, text, src_lang, tgt_lang, context_source="default"):
            del src_lang, tgt_lang, context_source
            return text

    primary_translator = Translator()
    fallback_created = []
    translator = FallbackTranslator(
        primary_translator,
        [("fallback", lambda: fallback_created.append(True) or Translator())],
    )

    class ASR(ASRProvider):
        provider_id = "test"

        def __init__(self):
            self.calls = 0

        def prewarm(self):
            self.calls += 1
            return True

        def transcribe(
            self,
            audio: np.ndarray,
            sample_rate: int = 16000,
            language: str | None = None,
            is_final: bool = True,
        ) -> str:
            del audio, sample_rate, language, is_final
            return ""

    primary_asr = ASR()
    asr_fallback_created = []
    asr = FallbackASR(
        primary_asr,
        fallback_factory=lambda: asr_fallback_created.append(True) or ASR(),
    )

    assert translator.prewarm() is True
    assert asr.prewarm() is True
    assert primary_translator.calls == 1
    assert primary_asr.calls == 1
    assert fallback_created == []
    assert asr_fallback_created == []


def test_qwen_asr_prewarm_uses_exact_async_client_and_runner(monkeypatch):
    calls = []

    async def fake_warmup(client, url, **kwargs):
        calls.append((client, url, kwargs))
        return _SUCCESS

    class Runner:
        def __init__(self):
            self.timeouts = []

        def run(self, coroutine, *, timeout):
            self.timeouts.append(timeout)
            return asyncio.run(coroutine)

    client = object()
    runner = Runner()
    provider = Qwen3ASRProvider.__new__(Qwen3ASRProvider)
    provider._lock = threading.RLock()
    provider._closed = False
    provider._http_client = client
    provider._runner = runner
    provider._runtime_generation = 7
    provider.timeout_seconds = 10.0
    provider.api_key = "qwen-secret"
    provider.load = lambda: None
    provider._resolved_base_url = lambda: "https://qwen.example/compatible-mode/v1"
    monkeypatch.setattr(
        "src.asr.qwen3_asr.warmup_async_httpx_client",
        fake_warmup,
    )

    assert provider.prewarm() is True
    assert runner.timeouts == [3.5]
    assert calls == [
        (
            client,
            "https://qwen.example/compatible-mode/v1/models",
            {
                "method": "HEAD",
                "headers": {
                    "Accept": "application/json",
                    "Authorization": "Bearer qwen-secret",
                },
                "timeout_s": 3.0,
            },
        )
    ]


def test_webspeech_prewarm_never_opens_browser_or_capture():
    provider = WebSpeechASRProvider.__new__(WebSpeechASRProvider)
    provider._lock = threading.RLock()
    provider._closed = False
    provider._server = None
    provider._url = ""
    provider._browser_opened = False
    provider._bridge_prewarmed_paused = False
    provider._state = _BridgeState()
    provider.auto_open_browser = True
    opened = []

    def start_server():
        provider._server = object()
        provider._url = "http://127.0.0.1:12345/"

    provider._start_server = start_server
    def open_bridge_page():
        opened.append(provider._state.capture_status()["capture_enabled"])
        provider._browser_opened = True

    provider._open_bridge_page = open_bridge_page

    assert provider.prewarm() is True
    assert provider._server is not None
    assert provider._state.capture_status() == {"capture_enabled": False}
    assert provider._browser_opened is False
    assert opened == []

    provider.load()

    assert provider._state.capture_status() == {"capture_enabled": True}
    assert provider._browser_opened is True
    assert opened == [True]


@pytest.mark.parametrize(
    ("engine_type", "expected_url", "expected_auth"),
    [
        (
            MimoTTS,
            "https://mimo.example/v1/chat/completions",
            {"api-key": "tts-secret"},
        ),
        (
            QwenTTS,
            "https://qwen.example/api/v1/services/aigc/"
            "multimodal-generation/generation",
            {"Authorization": "Bearer tts-secret"},
        ),
    ],
)
def test_api_tts_prewarm_uses_non_inference_head_on_worker_session(
    monkeypatch,
    engine_type,
    expected_url,
    expected_auth,
):
    session = object()
    pool = _Pool(session)
    calls = []

    def fake_warmup(client, url, **kwargs):
        calls.append((client, url, kwargs))
        return _SUCCESS

    monkeypatch.setattr(
        "src.tts.api_tts_engines.warmup_requests_session",
        fake_warmup,
    )
    engine = engine_type.__new__(engine_type)
    engine.base_url = (
        "https://mimo.example/v1"
        if engine_type is MimoTTS
        else "https://qwen.example/api/v1"
    )
    engine.model = "tts-model"
    engine.api_key = "tts-secret"
    engine.connect_timeout_seconds = 9.0
    engine._close_requested = threading.Event()
    engine._request_state_local = threading.local()
    engine._session_pool = pool

    engine.prewarm("voice")

    assert pool.calls == 1
    assert len(calls) == 1
    client, url, kwargs = calls[0]
    assert client is session
    assert url == expected_url
    assert kwargs["method"] == "HEAD"
    assert kwargs["timeout_s"] == 3.0
    for header, value in expected_auth.items():
        assert kwargs["headers"][header] == value


def test_edge_gtts_and_voicevox_prewarm_never_synthesize(monkeypatch):
    edge_calls = []
    edge = EdgeTTS.__new__(EdgeTTS)
    edge._edge_tts = object()
    edge._thread_event_loop = lambda: edge_calls.append("loop")
    edge.get_available_voices = lambda: edge_calls.append("voices") or []
    edge.prewarm("voice")

    dns_calls = []
    monkeypatch.setattr(
        "src.tts.gtts_engine.warmup_dns_origin",
        lambda url, **kwargs: dns_calls.append((url, kwargs)) or _SUCCESS,
    )
    gtts = GoogleTTS.__new__(GoogleTTS)
    gtts._gtts = object()
    gtts.prewarm("ja")

    voicevox_calls = []
    voicevox = VoicevoxCompatibleTTS.__new__(VoicevoxCompatibleTTS)
    voicevox.ENGINE_LABEL = "VOICEVOX"
    voicevox.is_available = lambda: voicevox_calls.append("version") or True
    voicevox.prewarm("1")

    assert edge_calls == ["loop", "voices"]
    assert dns_calls == [
        ("https://translate.google.com", {"timeout_s": 2.0})
    ]
    assert voicevox_calls == ["version"]
