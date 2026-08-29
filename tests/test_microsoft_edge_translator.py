from __future__ import annotations

import pytest

from src.translators.factory import create_translator
from src.translators.google_web_translator import GoogleWebTranslator
from src.translators.microsoft_edge_translator import MicrosoftEdgeTranslator


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.headers: dict[str, str] = {}
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response

    def close(self) -> None:
        self.closed = True


def test_google_web_accepts_json_object_response(monkeypatch):
    session = _FakeSession(
        _FakeResponse({"sentences": [{"trans": "Bon"}, {"trans": "jour"}]})
    )
    session.get = session.post
    monkeypatch.setattr(
        "src.translators.google_web_translator.requests.Session",
        lambda: session,
    )

    assert (
        GoogleWebTranslator(max_retries=0).translate("Hello", "en", "fr") == "Bonjour"
    )


def test_edge_web_posts_kikitan_compatible_payload(monkeypatch):
    session = _FakeSession(
        _FakeResponse([{"translations": [{"text": "こんにちは", "to": "ja"}]}])
    )
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(max_retries=0)
    assert translator.translate("Hello", "auto", "ja") == "こんにちは"
    assert session.calls == [
        {
            "url": "https://edge.microsoft.com/translate/translatetext",
            "params": {"to": "ja", "isEnterpriseClient": "false"},
            "json": ["Hello"],
            "timeout": 8.0,
        }
    ]
    assert session.trust_env is True


def test_edge_web_maps_simplified_chinese_and_rejects_bad_response(monkeypatch):
    session = _FakeSession(_FakeResponse({"unexpected": True}))
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(max_retries=0)
    with pytest.raises(RuntimeError, match="failed"):
        translator.translate("hello", "en-US", "zh-CN")
    assert session.calls[0]["params"] == {
        "from": "en",
        "to": "zh-Hans",
        "isEnterpriseClient": "false",
    }


def test_edge_web_prewarm_only_uses_bounded_transport_probe(monkeypatch):
    session = _FakeSession(
        _FakeResponse([{"translations": [{"text": ".", "to": "en"}]}])
    )
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(timeout_s=4, max_retries=0)

    assert translator.prewarm() is True
    assert session.calls == [
        {
            "method": "HEAD",
            "url": "https://edge.microsoft.com/translate/translatetext",
            "headers": {},
            "timeout": 3.0,
            "allow_redirects": False,
            "stream": True,
        }
    ]


def test_factory_creates_edge_web_without_api_key():
    translator = create_translator(
        {
            "translation": {
                "backend": "microsoft_edge_web",
                "microsoft_edge_web": {"timeout_s": 5, "max_retries": 0},
            }
        }
    )
    try:
        assert isinstance(translator, MicrosoftEdgeTranslator)
        assert translator._timeout_s == 5.0
    finally:
        translator.close()


class _SequencedSession(_FakeSession):
    """Return a different response per call so a retry can be observed."""

    def __init__(self, responses):
        super().__init__(responses[0])
        self._responses = list(responses)

    def post(self, url: str, **kwargs: object):
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0) if self._responses else self.response


def test_edge_web_retries_with_detection_when_the_source_hint_is_wrong(monkeypatch):
    """Reverse translation hears whatever language the other player speaks.

    A stale `from` hint makes the endpoint echo the input back untranslated;
    that must cost one retry, not the whole utterance.
    """

    korean = "안녕하세요"
    session = _SequencedSession([
        _FakeResponse([{"translations": [{"text": korean, "to": "zh-Hans"}]}]),
        _FakeResponse([{"translations": [{"text": "你好", "to": "zh-Hans"}]}]),
    ])
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(max_retries=0)
    assert translator.translate(korean, "ja", "zh") == "你好"

    assert len(session.calls) == 2
    # The first attempt declares the configured source...
    assert session.calls[0]["params"]["from"] == "ja"
    # ...and the retry drops it so the endpoint detects the real language.
    assert "from" not in session.calls[1]["params"]
    assert session.calls[1]["params"]["to"] == "zh-Hans"


def test_edge_web_does_not_retry_when_detection_was_already_used(monkeypatch):
    """Without a source hint there is nothing left to relax, so fail fast."""

    korean = "안녕하세요"
    session = _FakeSession(
        _FakeResponse([{"translations": [{"text": korean, "to": "zh-Hans"}]}])
    )
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(max_retries=0)
    with pytest.raises(Exception) as excinfo:
        translator.translate(korean, "auto", "zh")

    assert "target_language_mismatch" in str(excinfo.value)
    assert len(session.calls) == 1


def test_edge_web_keeps_a_correct_first_answer_without_retrying(monkeypatch):
    session = _SequencedSession([
        _FakeResponse([{"translations": [{"text": "你好", "to": "zh-Hans"}]}]),
    ])
    monkeypatch.setattr(
        "src.translators.microsoft_edge_translator.requests.Session",
        lambda: session,
    )

    translator = MicrosoftEdgeTranslator(max_retries=0)
    assert translator.translate("こんにちは", "ja", "zh") == "你好"
    assert len(session.calls) == 1
