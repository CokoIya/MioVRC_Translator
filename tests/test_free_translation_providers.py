from __future__ import annotations

import pytest

from src.translators.deepl_translator import DeepLTranslator
from src.translators.factory import create_translator
from src.translators.google_web_translator import GoogleWebTranslator
from src.translators.libretranslate_translator import LibreTranslateTranslator
from src.translators.mymemory_translator import MyMemoryTranslator
from src.utils.translation_config_validation import missing_required_translation_api_key


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.headers: dict[str, str] = {}
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_deepl_translator_posts_form_payload(monkeypatch):
    session = _FakeSession(_FakeResponse({"translations": [{"text": "Hello!"}]}))
    monkeypatch.setattr(
        "src.translators.deepl_translator.requests.Session",
        lambda: session,
    )

    translator = DeepLTranslator("test-key", max_retries=0)
    result = translator.translate("こんにちは！", "ja", "en")

    assert result == "Hello!"
    assert session.headers["Authorization"] == "DeepL-Auth-Key test-key"
    assert session.calls == [
        {
            "url": "https://api-free.deepl.com/v2/translate",
            "data": {
                "text": "こんにちは！",
                "target_lang": "EN-US",
                "preserve_formatting": "1",
                "source_lang": "JA",
            },
            "timeout": 10.0,
        }
    ]


def test_libretranslate_translator_posts_json_payload(monkeypatch):
    session = _FakeSession(_FakeResponse({"translatedText": "Hello!"}))
    monkeypatch.setattr(
        "src.translators.libretranslate_translator.requests.Session",
        lambda: session,
    )

    translator = LibreTranslateTranslator(base_url="http://localhost:5000", max_retries=0)
    result = translator.translate("こんにちは！", "ja", "en")

    assert result == "Hello!"
    assert session.calls == [
        {
            "url": "http://localhost:5000/translate",
            "json": {
                "q": "こんにちは！",
                "source": "ja",
                "target": "en",
                "format": "text",
            },
            "timeout": 10.0,
        }
    ]


def test_google_web_translator_gets_public_endpoint(monkeypatch):
    session = _FakeSession(_FakeResponse([[["Bonjour", "Hello", None, None, 10]], None, "en"]))
    monkeypatch.setattr(
        "src.translators.google_web_translator.requests.Session",
        lambda: session,
    )

    translator = GoogleWebTranslator(max_retries=0)
    result = translator.translate("Hello", "auto", "fr")

    assert result == "Bonjour"
    assert session.calls == [
        {
            "url": "https://translate.googleapis.com/translate_a/single",
            "params": {
                "client": "gtx",
                "sl": "auto",
                "tl": "fr",
                "dt": "t",
                "q": "Hello",
            },
            "timeout": 8.0,
        }
    ]


def test_mymemory_translator_gets_no_key_endpoint_and_cleans_tm_artifacts(monkeypatch):
    session = _FakeSession(
        _FakeResponse(
            {
                "responseStatus": 200,
                "quotaFinished": False,
                "responseData": {"translatedText": "359: Bonjour", "match": 1},
                "matches": [
                    {"translation": "359: Bonjour", "match": 1, "quality": 74},
                    {"translation": "Bonjour", "match": 0.98, "quality": 100},
                ],
            }
        )
    )
    monkeypatch.setattr(
        "src.translators.mymemory_translator.requests.Session",
        lambda: session,
    )

    translator = MyMemoryTranslator(max_retries=0)
    result = translator.translate("Hello", "auto", "fr")

    assert result == "Bonjour"
    assert session.calls == [
        {
            "url": "https://api.mymemory.translated.net/get",
            "params": {
                "q": "Hello",
                "langpair": "en|fr",
            },
            "timeout": 8.0,
        }
    ]


def test_translation_factory_creates_free_translation_backends(monkeypatch):
    created: list[tuple[str, dict[str, object]]] = []

    class FakeDeepL:
        def __init__(self, **kwargs):
            created.append(("deepl", kwargs))

    class FakeLibre:
        def __init__(self, **kwargs):
            created.append(("libretranslate", kwargs))

    class FakeGoogle:
        def __init__(self, **kwargs):
            created.append(("google_web", kwargs))

    class FakeMyMemory:
        def __init__(self, **kwargs):
            created.append(("mymemory", kwargs))

    monkeypatch.setattr("src.translators.factory.DeepLTranslator", FakeDeepL)
    monkeypatch.setattr("src.translators.factory.LibreTranslateTranslator", FakeLibre)
    monkeypatch.setattr("src.translators.factory.GoogleWebTranslator", FakeGoogle)
    monkeypatch.setattr("src.translators.factory.MyMemoryTranslator", FakeMyMemory)

    assert isinstance(
        create_translator(
            {
                "translation": {
                    "backend": "deepl",
                    "deepl": {"api_key": "deepl-key", "timeout_s": 7, "max_retries": 0},
                }
            }
        ),
        FakeDeepL,
    )
    assert isinstance(
        create_translator(
            {
                "translation": {
                    "backend": "libretranslate",
                    "libretranslate": {
                        "api_key": "",
                        "base_url": "http://127.0.0.1:5000",
                        "timeout_s": 8,
                        "max_retries": 0,
                    },
                }
            }
        ),
        FakeLibre,
    )
    assert isinstance(
        create_translator(
            {
                "translation": {
                    "backend": "google_web",
                    "google_web": {"timeout_s": 6, "max_retries": 0},
                }
            }
        ),
        FakeGoogle,
    )
    assert isinstance(
        create_translator(
            {
                "translation": {
                    "backend": "mymemory",
                    "mymemory": {"contact_email": "", "timeout_s": 6, "max_retries": 0},
                }
            }
        ),
        FakeMyMemory,
    )

    assert created[0][0] == "deepl"
    assert created[0][1]["api_key"] == "deepl-key"
    assert created[1] == (
        "libretranslate",
        {
            "api_key": "",
            "base_url": "http://127.0.0.1:5000",
            "timeout_s": 8.0,
            "max_retries": 0,
        },
    )
    assert created[2] == (
        "google_web",
        {
            "base_url": "https://translate.googleapis.com/translate_a/single",
            "timeout_s": 6.0,
            "max_retries": 0,
        },
    )
    assert created[3] == (
        "mymemory",
        {
            "base_url": "https://api.mymemory.translated.net/get",
            "contact_email": "",
            "timeout_s": 6.0,
            "max_retries": 0,
        },
    )


def test_deepl_requires_api_key():
    with pytest.raises(ValueError, match="DeepL API Key"):
        DeepLTranslator("")


def test_no_key_translation_backends_do_not_require_api_keys():
    for backend in ("google_web", "mymemory"):
        missing, label = missing_required_translation_api_key(
            {"translation": {"backend": backend, backend: {}}}
        )
        assert (missing, label) == (False, "")
