import pytest

from src.translators.factory import FallbackTranslator
from src.utils.latency_metrics import merge_translation_metrics


def test_multi_target_provider_metrics_sum_phases_and_keep_first_token_milestone():
    aggregate: dict[str, object] = {}

    merge_translation_metrics(
        aggregate,
        {
            "pool_wait_s": 0.01,
            "tcp_s": 0.02,
            "first_token_s": 0.08,
            "full_response_s": 0.20,
            "parse_s": 0.01,
            "connection_reused": False,
        },
    )
    merge_translation_metrics(
        aggregate,
        {
            "pool_wait_s": 0.03,
            "tcp_s": 0.0,
            "first_token_s": 0.04,
            "full_response_s": 0.10,
            "parse_s": 0.02,
            "connection_reused": True,
        },
    )

    assert aggregate["provider_calls"] == 2
    assert aggregate["pool_wait_s"] == 0.04
    assert aggregate["tcp_s"] == 0.02
    assert aggregate["first_token_s"] == 0.08
    assert aggregate["full_response_s"] == pytest.approx(0.30)
    assert aggregate["parse_s"] == 0.03
    assert aggregate["connection_reused"] is True
    assert aggregate["connection_reused_calls"] == 1
    assert aggregate["connection_reuse_observations"] == 2


def test_first_token_from_later_call_is_relative_to_whole_request():
    aggregate: dict[str, object] = {}

    merge_translation_metrics(
        aggregate,
        {
            "provider_s": 0.2,
            "full_response_s": 0.2,
            "total_s": 0.25,
            "first_token_s": None,
        },
    )
    merge_translation_metrics(
        aggregate,
        {
            "provider_s": 0.1,
            "full_response_s": 0.1,
            "total_s": 0.12,
            "first_token_s": 0.04,
        },
    )

    assert aggregate["first_token_s"] == pytest.approx(0.29)


def test_nested_provider_and_http_attempt_counts_are_preserved():
    aggregate: dict[str, object] = {}

    merge_translation_metrics(
        aggregate,
        {
            "provider_calls": 2,
            "http_request_count": 3,
            "connection_reuse_observations": 2,
            "connection_reused_calls": 1,
            "connection_reused": True,
            "cache_observations": 2,
            "cache_hits": 1,
            "cache_hit": True,
            "provider_s": 0.3,
            "total_s": 0.35,
        },
    )
    merge_translation_metrics(
        aggregate,
        {
            "provider_calls": 1,
            "http_request_count": 2,
            "connection_reuse_observations": 1,
            "connection_reused_calls": 0,
            "connection_reused": False,
            "cache_observations": 1,
            "cache_hits": 0,
            "cache_hit": False,
            "provider_s": 0.1,
            "total_s": 0.12,
        },
    )

    assert aggregate["provider_calls"] == 3
    assert aggregate["http_request_count"] == 5
    assert aggregate["connection_reuse_observations"] == 3
    assert aggregate["connection_reused_calls"] == 1
    assert aggregate["cache_observations"] == 3
    assert aggregate["cache_hits"] == 1


def test_cache_hit_does_not_count_as_provider_attempt():
    aggregate: dict[str, object] = {}

    merge_translation_metrics(
        aggregate,
        {
            "cache_hit": True,
            "provider_s": 0.0,
            "total_s": 0.001,
        },
    )

    assert aggregate["provider_calls"] == 0


def test_fallback_chain_aggregates_failed_and_successful_provider_attempts():
    class Translator:
        def __init__(self, *, fail: bool, metrics: dict[str, object]):
            self.fail = fail
            self.metrics = metrics

        def translate(self, text, _src, _tgt, context_source="default"):
            assert context_source == "manual"
            if self.fail:
                raise TimeoutError("primary timed out")
            return f"translated:{text}"

        def translation_metrics(self):
            return dict(self.metrics)

        def rewrite_asr(
            self,
            text,
            _style,
            *,
            language_hint="auto",
            context_source="mic",
        ):
            assert language_hint == "en"
            assert context_source == "manual"
            if self.fail:
                raise TimeoutError("primary rewrite timed out")
            return f"rewritten:{text}"

        def close(self):
            return None

    primary = Translator(
        fail=True,
        metrics={
            "pool_wait_s": 0.02,
            "full_response_s": 0.20,
            "connection_reused": False,
        },
    )
    fallback = Translator(
        fail=False,
        metrics={
            "pool_wait_s": 0.01,
            "full_response_s": 0.10,
            "first_token_s": 0.04,
            "connection_reused": True,
        },
    )
    translator = FallbackTranslator(
        primary,
        [("fallback", lambda: fallback)],
    )

    assert translator.translate(
        "hello",
        "en",
        "ja",
        context_source="manual",
    ) == "translated:hello"

    metrics = translator.translation_metrics()
    assert metrics["provider_calls"] == 2
    assert metrics["pool_wait_s"] == pytest.approx(0.03)
    assert metrics["full_response_s"] == pytest.approx(0.30)
    assert metrics["first_token_s"] == 0.04
    assert metrics["connection_reused_calls"] == 1

    assert translator.rewrite_asr(
        "hello",
        "catgirl",
        language_hint="en",
        context_source="manual",
    ) == "rewritten:hello"
    rewrite_metrics = translator.translation_metrics()
    assert rewrite_metrics["provider_calls"] == 2
    assert rewrite_metrics["pool_wait_s"] == pytest.approx(0.03)
    assert rewrite_metrics["full_response_s"] == pytest.approx(0.30)
