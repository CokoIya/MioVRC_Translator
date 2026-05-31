from __future__ import annotations

import base64

from src.tts.api_tts_engines import MimoTTS, QwenTTS


class _FakeResponse:
    def __init__(self, payload=None, content: bytes = b"", headers=None, status_code: int = 200):
        self._payload = payload
        self.content = content
        self.headers = headers or {"content-type": "application/json"}
        self.status_code = status_code
        self.text = str(payload or "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def mount(self, *_args, **_kwargs):
        pass

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, headers, json, timeout))
        if "xiaomimimo" in url:
            payload = {
                "choices": [
                    {
                        "message": {
                            "audio": {
                                "data": base64.b64encode(b"RIFF-mimo").decode("ascii")
                            }
                        }
                    }
                ]
            }
            return _FakeResponse(payload)
        return _FakeResponse({"output": {"audio": {"url": "https://audio.test/qwen.wav"}}})

    def get(self, url, timeout=None):
        self.gets.append((url, timeout))
        return _FakeResponse(content=b"RIFF-qwen", headers={"content-type": "audio/wav"})


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

    assert audio == b"RIFF-mimo"
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

    assert audio == b"RIFF-qwen"
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
