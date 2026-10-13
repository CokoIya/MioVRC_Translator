"""``list_models`` of the OpenAI- and Anthropic-protocol translators.

The real SDKs run against an in-process transport: every httpx client a
translator creates answers from the test's handler, so request URLs, query
parameters and headers are the ones the SDK would put on the wire. Nothing
leaves the process.
"""

from __future__ import annotations

import itertools
import threading
import types

import anthropic
import httpx
import openai
import pytest

from src.translators.anthropic_translator import AnthropicTranslator
from src.translators.base import ProviderWallTimeoutError
from src.translators.openai_translator import OpenAITranslator

RELAY = "https://relay.example.com/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
CLAUDE_RELAY = "https://claude-relay.example.com"
OPENAI_KEY = "sk-test-not-a-real-key-0000"
ANTHROPIC_KEY = "sk-ant-test-not-a-real-key-0000"


@pytest.fixture
def wire(monkeypatch):
    """Route the translators' httpx clients to ``wire.handler``."""

    state = types.SimpleNamespace(requests=[], handler=None)
    real_client = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        return state.handler(request)

    class InProcessClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            # No environment proxy may take the request elsewhere.
            kwargs["trust_env"] = False
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", InProcessClient)
    created: list[object] = []
    state.created = created
    yield state
    for translator in created:
        translator.close()


def _openai(wire, base_url: str = RELAY, **kwargs) -> OpenAITranslator:
    translator = OpenAITranslator(
        api_key=OPENAI_KEY,
        model="gpt-6-luna",
        base_url=base_url,
        provider_id="openai_compatible",
        **kwargs,
    )
    wire.created.append(translator)
    return translator


def _anthropic(wire, **kwargs) -> AnthropicTranslator:
    translator = AnthropicTranslator(
        api_key=ANTHROPIC_KEY,
        model="claude-opus-5",
        base_url=CLAUDE_RELAY,
        provider_id="anthropic_compatible",
        **kwargs,
    )
    wire.created.append(translator)
    return translator


def _anthropic_page(ids, *, has_more: bool, last_id: object = ...) -> dict:
    return {
        "data": [
            {
                "type": "model",
                "id": model_id,
                "display_name": model_id,
                "created_at": "2026-01-01T00:00:00Z",
            }
            for model_id in ids
        ],
        "has_more": has_more,
        "first_id": ids[0] if ids else None,
        "last_id": (ids[-1] if ids else None) if last_id is ... else last_id,
    }


def _short_deadline(monkeypatch, translator) -> dict:
    """Shrink the wall-clock deadline and record the one list_models asked for."""

    seen: dict = {}
    original = translator._run_with_wall_timeout

    def run(operation, *, timeout_s=None, operation_name="provider request"):
        seen["timeout_s"] = timeout_s
        seen["operation_name"] = operation_name
        return original(operation, timeout_s=0.2, operation_name=operation_name)

    monkeypatch.setattr(translator, "_run_with_wall_timeout", run)
    return seen


# --- OpenAI protocol ------------------------------------------------------------------


def test_openai_list_models_reads_the_relay_list_with_the_translation_settings(wire):
    wire.handler = lambda request: httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {
                    "id": "gpt-6-luna",
                    "object": "model",
                    "owned_by": "openai",
                    "supported_endpoint_types": ["openai"],
                },
                {"id": "models/gemini-3.6-flash", "object": "model"},
                {"id": "gpt-6-luna", "object": "model"},
                {"id": "", "object": "model"},
                "claude-haiku-5-5",
            ],
        },
    )
    translator = _openai(wire, custom_headers={"X-Relay-Token": "header-secret"})

    models = translator.list_models(timeout_s=5)

    assert [model.id for model in models] == [
        "gpt-6-luna",
        "gemini-3.6-flash",
        "claude-haiku-5-5",
    ]
    assert models[0].fields["supported_endpoint_types"] == ["openai"]
    (request,) = wire.requests
    assert request.method == "GET"
    assert str(request.url) == f"{RELAY}/models"
    assert request.headers["x-relay-token"] == "header-secret"
    assert request.headers["authorization"] == f"Bearer {OPENAI_KEY}"


def test_openai_list_models_asks_openrouter_for_its_largest_page(wire):
    wire.handler = lambda request: httpx.Response(
        200, json={"data": [{"id": "openai/gpt-6-luna"}]}
    )
    translator = _openai(wire, base_url=OPENROUTER)

    models = translator.list_models(timeout_s=5)

    assert [model.id for model in models] == ["openai/gpt-6-luna"]
    (request,) = wire.requests
    assert request.url.path == "/api/v1/models"
    assert request.url.params["limit"] == "1000"


def test_openai_list_models_sends_no_query_parameters_to_other_relays(wire):
    wire.handler = lambda request: httpx.Response(200, json={"data": []})
    translator = _openai(wire)

    assert translator.list_models(timeout_s=5) == []

    (request,) = wire.requests
    assert not request.url.query


def test_openai_list_models_rejects_a_relay_console_page(wire):
    # A Base URL without /v1 reaches the relay's web console instead.
    wire.handler = lambda request: httpx.Response(
        200,
        text="<!doctype html><html>relay console</html>",
        headers={"content-type": "text/html"},
    )
    translator = _openai(wire, base_url="https://relay.example.com")

    with pytest.raises(ValueError):
        translator.list_models(timeout_s=5)


def test_openai_list_models_raises_the_provider_error_without_retrying(wire):
    wire.handler = lambda request: httpx.Response(
        401, json={"error": {"message": "invalid token", "type": "new_api_error"}}
    )
    translator = _openai(wire, max_retries=2)

    with pytest.raises(openai.AuthenticationError) as info:
        translator.list_models(timeout_s=5)

    assert info.value.status_code == 401
    assert len(wire.requests) == 1


def test_openai_list_models_is_bounded_by_a_wall_clock_deadline(wire, monkeypatch):
    release = threading.Event()

    def stalled(request):
        release.wait(5)
        return httpx.Response(200, json={"data": []})

    wire.handler = stalled
    translator = _openai(wire)
    seen = _short_deadline(monkeypatch, translator)

    try:
        with pytest.raises(ProviderWallTimeoutError):
            translator.list_models(timeout_s=1)
    finally:
        release.set()

    # The deadline is the request timeout plus two seconds of slack.
    assert seen == {"timeout_s": 3.0, "operation_name": "model list request"}


# --- Anthropic protocol -----------------------------------------------------------------


def test_anthropic_list_models_follows_pagination_to_the_end(wire):
    all_ids = [f"claude-model-{index:04d}" for index in range(1500)]

    def paged(request):
        params = request.url.params
        limit = int(params["limit"])
        after = params.get("after_id")
        start = all_ids.index(after) + 1 if after else 0
        chunk = all_ids[start : start + limit]
        return httpx.Response(
            200, json=_anthropic_page(chunk, has_more=start + limit < len(all_ids))
        )

    wire.handler = paged
    translator = _anthropic(wire, custom_headers={"X-Relay-Token": "header-secret"})

    models = translator.list_models(timeout_s=5)

    # The raw list is not cut at the offered-list cap; filtering caps it later.
    assert [model.id for model in models] == all_ids
    assert [request.url.params.get("after_id") for request in wire.requests] == [
        None,
        "claude-model-0999",
    ]
    first = wire.requests[0]
    assert first.url.path == "/v1/models"
    assert first.url.params["limit"] == "1000"
    assert first.headers["x-api-key"] == ANTHROPIC_KEY
    assert first.headers["x-relay-token"] == "header-secret"
    assert "anthropic-version" in first.headers


def test_anthropic_list_models_stops_when_a_relay_repeats_the_page(wire):
    # A relay that ignores after_id answers the first page forever.
    wire.handler = lambda request: httpx.Response(
        200, json=_anthropic_page(["claude-sonnet-5", "claude-haiku-5-5"], has_more=True)
    )
    translator = _anthropic(wire)

    models = translator.list_models(timeout_s=5)

    assert [model.id for model in models] == ["claude-sonnet-5", "claude-haiku-5-5"]
    assert len(wire.requests) == 2
    assert wire.requests[1].url.params["after_id"] == "claude-haiku-5-5"


def test_anthropic_list_models_stops_when_a_page_adds_nothing_new(wire):
    pages = iter(
        [
            _anthropic_page(["claude-a", "claude-b"], has_more=True),
            _anthropic_page(["claude-a", "claude-b"], has_more=True, last_id="cursor-2"),
            _anthropic_page(["claude-c"], has_more=False),
        ]
    )
    wire.handler = lambda request: httpx.Response(200, json=next(pages))
    translator = _anthropic(wire)

    models = translator.list_models(timeout_s=5)

    assert [model.id for model in models] == ["claude-a", "claude-b"]
    assert len(wire.requests) == 2


def test_anthropic_list_models_stops_without_a_cursor(wire):
    wire.handler = lambda request: httpx.Response(
        200, json=_anthropic_page(["claude-opus-5"], has_more=True, last_id=None)
    )
    translator = _anthropic(wire)

    assert [model.id for model in translator.list_models(timeout_s=5)] == ["claude-opus-5"]
    assert len(wire.requests) == 1


def test_anthropic_list_models_reads_an_openai_shaped_relay_answer(wire):
    wire.handler = lambda request: httpx.Response(
        200,
        json={"object": "list", "data": [{"id": "claude-haiku-5-5", "object": "model"}]},
    )
    translator = _anthropic(wire)

    assert [model.id for model in translator.list_models(timeout_s=5)] == [
        "claude-haiku-5-5"
    ]
    assert len(wire.requests) == 1


def test_anthropic_list_models_reads_at_most_ten_pages(wire):
    counter = itertools.count()

    def endless(request):
        index = next(counter)
        return httpx.Response(
            200, json=_anthropic_page([f"claude-page-{index}"], has_more=True)
        )

    wire.handler = endless
    translator = _anthropic(wire)

    models = translator.list_models(timeout_s=5)

    assert [model.id for model in models] == [f"claude-page-{index}" for index in range(10)]
    assert len(wire.requests) == 10


def test_anthropic_list_models_raises_the_provider_error_without_retrying(wire):
    wire.handler = lambda request: httpx.Response(
        401,
        json={
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        },
    )
    translator = _anthropic(wire, max_retries=2)

    with pytest.raises(anthropic.AuthenticationError) as info:
        translator.list_models(timeout_s=5)

    assert info.value.status_code == 401
    assert len(wire.requests) == 1


def test_anthropic_list_models_rejects_a_non_json_page(wire):
    wire.handler = lambda request: httpx.Response(
        200, text="<html>gateway</html>", headers={"content-type": "text/html"}
    )
    translator = _anthropic(wire)

    with pytest.raises(ValueError):
        translator.list_models(timeout_s=5)


def test_anthropic_list_models_is_bounded_by_a_wall_clock_deadline(wire, monkeypatch):
    release = threading.Event()

    def stalled(request):
        release.wait(5)
        return httpx.Response(200, json=_anthropic_page([], has_more=False))

    wire.handler = stalled
    translator = _anthropic(wire)
    seen = _short_deadline(monkeypatch, translator)

    try:
        with pytest.raises(ProviderWallTimeoutError):
            translator.list_models(timeout_s=2)
    finally:
        release.set()

    assert seen == {"timeout_s": 4.0, "operation_name": "model list request"}
