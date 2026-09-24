"""Free web backends back each other up, and a dead backend stops costing time."""

from __future__ import annotations

import pytest

from src.translators.base import BaseTranslator
from src.translators.factory import (
    FREE_WEB_BACKENDS,
    FallbackTranslator,
    _fallback_backends,
    create_translator,
)


class _Scripted(BaseTranslator):
    def __init__(self, name: str, outcomes: list[object]) -> None:
        super().__init__()
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else f"{self.name}:{text}"
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_free_web_primary_gets_the_other_free_backends_as_fallbacks():
    assert _fallback_backends({}, "microsoft_edge_web") == ["google_web", "mymemory"]
    assert _fallback_backends({}, "mymemory") == ["microsoft_edge_web", "google_web"]


def test_configured_fallbacks_win_and_the_default_can_be_switched_off():
    assert _fallback_backends({"fallback_backends": ["deepl"]}, "microsoft_edge_web") == [
        "deepl"
    ]
    assert _fallback_backends({"auto_free_fallback": False}, "microsoft_edge_web") == []


def test_keyed_backends_get_no_implicit_fallback():
    assert _fallback_backends({}, "openai") == []
    assert "openai" not in FREE_WEB_BACKENDS


def test_default_edge_translator_is_wrapped_in_the_free_chain():
    translator = create_translator({"translation": {"backend": "microsoft_edge_web"}})
    try:
        assert isinstance(translator, FallbackTranslator)
        assert translator._primary_name == "microsoft_edge_web"
        assert [name for name, _factory in translator._fallback_factories] == [
            "google_web",
            "mymemory",
        ]
    finally:
        translator.close()


def _chain(primary_outcomes, fallback_outcomes, clock):
    primary = _Scripted("edge", primary_outcomes)
    fallback = _Scripted("google", fallback_outcomes)
    chain = FallbackTranslator(
        primary,
        [("google_web", lambda: fallback)],
        primary_name="microsoft_edge_web",
        clock=clock,
    )
    return chain, primary, fallback


def test_a_backend_that_keeps_failing_is_skipped_for_the_cooldown():
    clock = _Clock()
    down = [RuntimeError("edge down")] * 10
    chain, primary, fallback = _chain(down, [], clock)

    for _ in range(FallbackTranslator.CIRCUIT_FAILURE_THRESHOLD):
        assert chain.translate("hi", "en", "ja").startswith("google:")
    assert primary.calls == FallbackTranslator.CIRCUIT_FAILURE_THRESHOLD

    # Open: the next sentences go straight to the fallback.
    chain.translate("hi", "en", "ja")
    chain.translate("hi", "en", "ja")
    assert primary.calls == FallbackTranslator.CIRCUIT_FAILURE_THRESHOLD

    # After the cooldown the primary gets another chance and, answering, stays.
    primary.outcomes = []
    clock.now += FallbackTranslator.CIRCUIT_COOLDOWN_S + 1
    assert chain.translate("hi", "en", "ja") == "edge:hi"
    assert chain.translate("hi", "en", "ja") == "edge:hi"


def test_a_single_failure_does_not_open_the_circuit():
    clock = _Clock()
    chain, primary, _fallback = _chain([RuntimeError("blip")], [], clock)

    assert chain.translate("a", "en", "ja") == "google:a"
    assert chain.translate("b", "en", "ja") == "edge:b"
    assert primary.calls == 2


def test_bad_input_does_not_count_against_a_backend():
    clock = _Clock()
    chain, primary, _fallback = _chain([ValueError("bad input")] * 5, [], clock)

    for _ in range(5):
        chain.translate("x", "en", "ja")

    assert primary.calls == 5


def test_everything_resting_still_tries_the_chain_in_order():
    clock = _Clock()
    chain, primary, fallback = _chain(
        [RuntimeError("down")] * 10, [RuntimeError("down")] * 3, clock
    )
    for _ in range(FallbackTranslator.CIRCUIT_FAILURE_THRESHOLD):
        with pytest.raises(RuntimeError):
            chain.translate("x", "en", "ja")

    fallback.outcomes = ["google:ok"]
    assert chain.translate("x", "en", "ja") == "google:ok"
