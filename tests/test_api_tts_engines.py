from __future__ import annotations

import base64

import pytest

import src.tts.api_tts_engines as api_tts_engines
from src.tts.api_tts_engines import MimoTTS, QwenTTS


class _FakeResponse:
    def __init__(
        self,
        payload=None,
        content: bytes = b"",
        headers=None,
        status_code: int = 200,
        url: str = "https://audio.test/qwen.wav",
    ):
        self._payload = payload
        if payload is not None and not content:
            import json

            content = json.dumps(payload).encode("utf-8")
        self.content = content
        self.headers = headers or {"content-type": "application/json"}
        self.status_code = status_code
        self.url = url
        self.text = str(payload or "")
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        del chunk_size
        if self.content:
            yield self.content

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def mount(self, *_args, **_kwargs):
        pass

    def post(
        self,
        url,
        headers=None,
        json=None,
        timeout=None,
        stream=None,
        allow_redirects=None,
    ):
        assert stream is True
        assert allow_redirects is False
        self.posts.append((url, headers, json, timeout))
        if "xiaomimimo" in url:
            payload = {
                "choices": [
                    {
                        "message": {
                            "audio": {
                                "data": base64.b64encode(
                                    b"RIFF\x04\x00\x00\x00WAVE"
                                ).decode("ascii")
                            }
                        }
                    }
                ]
            }
            return _FakeResponse(payload)
        return _FakeResponse({"output": {"audio": {"url": "https://audio.test/qwen.wav"}}})

    def get(
        self,
        url,
        headers=None,
        timeout=None,
        stream=None,
        allow_redirects=None,
    ):
        assert stream is True
        assert allow_redirects is False
        self.gets.append((url, timeout))
        return _FakeResponse(
            content=b"RIFF\x04\x00\x00\x00WAVE",
            headers={"content-type": "audio/wav"},
            url=url,
        )


@pytest.fixture(autouse=True)
def _allow_fake_audio_host(monkeypatch):
    monkeypatch.setattr(
        api_tts_engines._APITTSBase,
        "_is_safe_audio_url",
        lambda _self, _url: True,
    )


def test_mimo_tts_posts_openai_compatible_audio_request_with_api_key_header(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = MimoTTS(
        {
            "api_key": "mimo-key",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5-tts",
        }
    )

    audio = engine.synthesize("こんにちは", "mimo_default")

    assert audio == b"RIFF\x04\x00\x00\x00WAVE"
    url, headers, payload, _timeout = fake.posts[0]
    assert url == "https://api.xiaomimimo.com/v1/chat/completions"
    assert headers["api-key"] == "mimo-key"
    assert payload["model"] == "mimo-v2.5-tts"
    assert payload["audio"] == {"voice": "mimo_default", "format": "wav"}
    assert payload["messages"][-1] == {"role": "assistant", "content": "こんにちは"}


def test_qwen_tts_posts_dashscope_request_and_downloads_audio(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "qwen-key",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
        }
    )

    audio = engine.synthesize("Hello there", "Cherry")

    assert audio == b"RIFF\x04\x00\x00\x00WAVE"
    url, headers, payload, _timeout = fake.posts[0]
    assert url == (
        "https://dashscope-intl.aliyuncs.com/api/v1/"
        "services/aigc/multimodal-generation/generation"
    )
    assert headers["Authorization"] == "Bearer qwen-key"
    assert payload["model"] == "qwen3-tts-flash"
    assert payload["input"] == {
        "text": "Hello there",
        "voice": "Cherry",
        "language_type": "English",
    }
    assert fake.gets == [("https://audio.test/qwen.wav", 30.0)]


def test_qwen_tts_instruct_model_sends_style_instructions(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "qwen-key",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-instruct-flash",
            "instructions": "Read with a warm playful tone.",
            "optimize_instructions": True,
        }
    )

    engine.synthesize("Hello there", "Cherry")

    _url, _headers, payload, _timeout = fake.posts[0]
    assert payload["input"]["instructions"] == "Read with a warm playful tone."
    assert payload["input"]["optimize_instructions"] is True


def test_qwen_tts_plain_model_omits_style_instructions(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "qwen-key",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
            "instructions": "Read with a warm playful tone.",
        }
    )

    engine.synthesize("Hello there", "Cherry")

    _url, _headers, payload, _timeout = fake.posts[0]
    assert "instructions" not in payload["input"]
    assert "optimize_instructions" not in payload["input"]


def test_qwen_tts_uses_auto_language_type_for_uncertain_latin_text(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "qwen-key",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
        }
    )

    engine.synthesize("Bonjour, ça va ?", "Serena")

    _url, _headers, payload, _timeout = fake.posts[0]
    assert payload["input"]["language_type"] == "Auto"


def test_qwen_tts_uses_target_language_hint_for_kanji_only_japanese(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "qwen-key",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
            "language_type": "Japanese",
        }
    )

    engine.synthesize("東京", "Cherry")

    _url, _headers, payload, _timeout = fake.posts[0]
    assert payload["input"]["language_type"] == "Japanese"


def test_qwen_tts_exposes_official_system_voices():
    engine = QwenTTS(
        {
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
        }
    )

    voices = engine.get_available_voices()
    voice_ids = {voice.id for voice in voices}

    assert len(voices) >= 48
    assert {"Cherry", "Eldric Sage", "Ono Anna", "Radio Gol", "Kiki"}.issubset(voice_ids)
    assert next(voice for voice in voices if voice.id == "Cherry").name == "Cherry / 芊悦"


def test_qwen_tts_filters_hidden_voices(monkeypatch, tmp_path):
    # Patch writable_app_dir to point to our temp directory
    monkeypatch.setattr("src.tts.api_tts_engines.writable_app_dir", lambda: tmp_path)

    # Write a seren.json that hides Seren
    (tmp_path / "seren.json").write_text(
        '{"hidden_voices": ["Seren"]}', encoding="utf-8"
    )

    # Reset the module-level cache so the new file is picked up
    import src.tts.api_tts_engines as api_engines

    api_engines._HIDDEN_VOICES = None

    engine = QwenTTS(
        {
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "model": "qwen3-tts-flash",
        }
    )

    voices = engine.get_available_voices()
    voice_ids = {v.id for v in voices}

    assert "Seren" not in voice_ids
    assert "Cherry" in voice_ids
    assert len(voices) >= 47

    # Re-enable Seren
    (tmp_path / "seren.json").write_text(
        '{"hidden_voices": []}', encoding="utf-8"
    )
    api_engines._HIDDEN_VOICES = None

    voices_after = engine.get_available_voices()
    voice_ids_after = {v.id for v in voices_after}

    assert "Seren" in voice_ids_after


def test_audio_url_validation_rejects_ssrf_and_insecure_public_urls(monkeypatch):
    def resolve(host, port, **_kwargs):
        addresses = {
            "api.example": "93.184.216.34",
            "cdn.example": "93.184.216.35",
        }
        address = addresses[host]
        return [(2, 1, 6, "", (address, port))]

    monkeypatch.setattr(api_tts_engines.socket, "getaddrinfo", resolve)

    assert api_tts_engines._is_safe_audio_download_url(
        "https://cdn.example/audio.wav",
        api_base_url="https://api.example/v1",
    )
    assert not api_tts_engines._is_safe_audio_download_url(
        "http://cdn.example/audio.wav",
        api_base_url="https://api.example/v1",
    )
    assert not api_tts_engines._is_safe_audio_download_url(
        "https://127.0.0.1/audio.wav",
        api_base_url="https://api.example/v1",
    )
    assert not api_tts_engines._is_safe_audio_download_url(
        "https://169.254.169.254/latest/meta-data",
        api_base_url="https://api.example/v1",
    )
    assert not api_tts_engines._is_safe_audio_download_url(
        "https://user:password@cdn.example/audio.wav",
        api_base_url="https://api.example/v1",
    )


def test_audio_url_validation_allows_only_same_port_loopback_for_local_api():
    assert api_tts_engines._is_safe_audio_download_url(
        "http://127.0.0.1:8080/audio.wav",
        api_base_url="http://127.0.0.1:8080/v1",
    )
    assert not api_tts_engines._is_safe_audio_download_url(
        "http://127.0.0.1:9090/audio.wav",
        api_base_url="http://127.0.0.1:8080/v1",
    )


def test_base64_audio_decode_is_size_bounded(monkeypatch):
    monkeypatch.setattr(api_tts_engines, "_MAX_AUDIO_BYTES", 4)
    encoded = base64.b64encode(b"12345").decode("ascii")

    with pytest.raises(RuntimeError, match="maximum allowed size"):
        api_tts_engines._decode_audio_data(encoded)


def test_api_response_is_streamed_with_a_hard_size_limit(monkeypatch):
    response = _FakeResponse(
        content=b"{}",
        headers={
            "content-type": "application/json",
            "Content-Length": "1024",
        },
        url="https://api.xiaomimimo.com/v1/chat/completions",
    )

    class Session(_FakeSession):
        def post(self, *_args, **_kwargs):
            return response

    session = Session()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: session)
    monkeypatch.setattr(api_tts_engines, "_MAX_JSON_AUDIO_RESPONSE_BYTES", 16)
    engine = MimoTTS(
        {
            "api_key": "key",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5-tts",
        }
    )

    with pytest.raises(RuntimeError, match="maximum allowed size"):
        engine._request_json_audio("https://api.xiaomimimo.com/v1/test", {})

    assert response.closed is True


def test_api_post_redirect_is_rejected_without_following(monkeypatch):
    response = _FakeResponse(
        content=b"",
        headers={"Location": "https://evil.example/steal"},
        status_code=302,
        url="https://api.xiaomimimo.com/v1/test",
    )

    class Session(_FakeSession):
        def post(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", Session)
    engine = MimoTTS(
        {
            "api_key": "secret",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5-tts",
        }
    )

    with pytest.raises(RuntimeError, match="redirects are not allowed"):
        engine._request_json_audio("https://api.xiaomimimo.com/v1/test", {})

    assert response.closed is True


def test_api_key_is_never_sent_over_public_plain_http(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: session)
    with pytest.raises(ValueError, match="HTTPS"):
        MimoTTS(
            {
                "api_key": "secret",
                "base_url": "http://api.example/v1",
                "model": "mimo-v2.5-tts",
            }
        )

    assert session.posts == []


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@api.example/v1",
        "https://api.example/v1?redirect=evil",
        "https://api.example/v1#fragment",
    ],
)
def test_api_tts_rejects_ambiguous_or_credentialed_base_urls(base_url):
    with pytest.raises(ValueError):
        QwenTTS(
            {
                "api_key": "secret",
                "base_url": base_url,
                "model": "qwen3-tts-flash",
            }
        )


def test_api_tts_allows_loopback_http_for_local_compatibility(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: fake)

    engine = QwenTTS(
        {
            "api_key": "local-secret",
            "base_url": "http://127.0.0.1:8080/v1/",
            "model": "qwen3-tts-flash",
        }
    )

    assert engine.base_url == "http://127.0.0.1:8080/v1"
