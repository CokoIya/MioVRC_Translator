"""Model lists read from the provider: parsing, filtering and ordering."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.translators import model_listing as ml
from src.translators.model_listing import (
    MAX_LISTED_MODELS,
    ListedModel,
    backend_supports_model_listing,
    builtin_model_list,
    filter_listed_models,
    parse_model_list_payload,
)
from src.utils.model_family import model_family_id, same_model_family
from src.utils.ui_config import (
    DEEPSEEK_PRO_MODEL_PATTERN,
    RELAY_BACKEND,
    TRANSLATION_BACKENDS,
    TRANSLATION_MODEL_PRESETS,
)

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
RELAY = RELAY_BACKEND


def _filter(backend, models, protocol="openai"):
    return filter_listed_models(backend, models, protocol=protocol, now=NOW)


def _kept(backend, models, protocol="openai"):
    return list(_filter(backend, models, protocol)[0])


def _model(model_id, **fields):
    return ListedModel(model_id, fields)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_openai_shape_keeps_ids_and_fields():
    payload = {
        "object": "list",
        "data": [
            {"id": "gpt-6-luna", "object": "model", "owned_by": "openai"},
            {"id": "gpt-5.4-mini", "object": "model", "supported_endpoint_types": ["openai"]},
        ],
    }

    models = parse_model_list_payload(json.dumps(payload).encode())

    assert [model.id for model in models] == ["gpt-6-luna", "gpt-5.4-mini"]
    assert models[0].fields["owned_by"] == "openai"
    assert models[1].fields["supported_endpoint_types"] == ["openai"]


def test_parse_anthropic_page_and_bare_arrays():
    page = {
        "data": [{"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5"}],
        "has_more": False,
        "first_id": "claude-opus-5",
        "last_id": "claude-opus-5",
    }

    assert [m.id for m in parse_model_list_payload(json.dumps(page))] == ["claude-opus-5"]
    assert [m.id for m in parse_model_list_payload(["qwen3:4b-instruct", "gemma3:4b"])] == [
        "qwen3:4b-instruct",
        "gemma3:4b",
    ]


def test_parse_gemini_and_ollama_native_shapes():
    gemini = {
        "models": [
            {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
        ]
    }
    compat = {"data": [{"id": "models/gemini-3.5-flash-lite"}]}
    ollama = {"models": [{"name": "qwen3:4b-instruct", "model": "qwen3:4b-instruct", "size": 1}]}
    nested = {"result": {"data": [{"id": "hy3"}]}}

    assert [m.id for m in parse_model_list_payload(gemini)] == ["gemini-3.6-flash"]
    assert [m.id for m in parse_model_list_payload(compat)] == ["gemini-3.5-flash-lite"]
    assert [m.id for m in parse_model_list_payload(ollama)] == ["qwen3:4b-instruct"]
    assert [m.id for m in parse_model_list_payload(nested)] == ["hy3"]


def test_parse_accepts_a_utf8_bom_and_skips_junk_entries():
    body = json.dumps(
        {
            "data": [
                {"id": "gpt-6-luna"},
                {"id": "gpt-6-luna"},
                {"id": "bad\x00id"},
                {"id": "x" * 201},
                {"id": ""},
                {"object": "model"},
                42,
                None,
                "  openai/gpt-6-luna  ",
            ]
        }
    ).encode("utf-8")

    models = parse_model_list_payload(b"\xef\xbb\xbf" + body)

    # Exact duplicates collapse; decorated spellings stay verbatim.
    assert [m.id for m in models] == ["gpt-6-luna", "openai/gpt-6-luna"]


def test_parse_errors(monkeypatch):
    with pytest.raises(json.JSONDecodeError):
        # A relay console's HTML page (Base URL without /v1).
        parse_model_list_payload(b"<!doctype html><html><body>new-api</body></html>")
    for body in (b"", b"null", b"42", b'"models"'):
        with pytest.raises(ValueError, match="unexpected shape"):
            parse_model_list_payload(body)
    monkeypatch.setattr(ml, "MAX_MODEL_LIST_RESPONSE_BYTES", 16)
    with pytest.raises(ValueError, match="too large"):
        parse_model_list_payload(b'{"data": ["gpt-6-luna"]}')
    with pytest.raises(ValueError, match="too large"):
        parse_model_list_payload('{"data": ["gpt-6-luna"]}')


def test_parse_reports_a_json_error_answer_instead_of_an_empty_list():
    with pytest.raises(ValueError, match="rejected.*get user group failed"):
        parse_model_list_payload({"success": False, "message": "get user group failed"})
    with pytest.raises(ValueError, match="rejected.*Invalid token"):
        parse_model_list_payload({"error": {"message": "Invalid token", "type": "new_api_error"}})
    # An answer without a list and without an error is just empty.
    assert parse_model_list_payload({}) == []
    assert parse_model_list_payload({"success": True, "data": []}) == []


def test_parse_error_answers_never_carry_an_echoed_key():
    key = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

    with pytest.raises(ValueError) as caught:
        parse_model_list_payload({"error": {"message": f"Incorrect API key provided: {key}"}})

    assert key not in str(caught.value)
    assert "Incorrect API key provided" in str(caught.value)


def test_parse_reads_far_more_raw_entries_than_are_offered(monkeypatch):
    payload = {"data": [{"id": f"vendor/image-model-{index}"} for index in range(2100)]}
    payload["data"].append({"id": "gpt-6-luna"})

    models = parse_model_list_payload(json.dumps(payload).encode())

    assert len(models) == 2101
    assert models[-1].id == "gpt-6-luna"
    monkeypatch.setattr(ml, "MAX_PARSED_MODEL_ENTRIES", 5)
    assert len(parse_model_list_payload(json.dumps(payload).encode())) == 5


# --------------------------------------------------------------------------
# Non-chat name markers (L1)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id",
    [
        # embeddings / rerank
        "text-embedding-3-small",
        "bge-m3",
        "BAAI/bge-large-zh-v1.5",
        "bge_reranker_v2",
        "m3e-base",
        "multilingual-e5-large",
        "intfloat/e5-large-v2",
        "thenlper/gte-large",
        "sentence-transformers/all-minilm-l6-v2",
        "all-minilm:33m",
        "paraphrase-multilingual:278m",
        "colbert-v2",
        "voyage-3",
        "voyage-3-large",
        "voyage-code-3",
        "voyageai/voyage-multilingual-2",
        # image
        "gpt-image-1",
        "dall-e-3",
        "doubao-seedream-4-0-250828",
        "seedream-4.0",
        "seededit-3.0",
        "jimeng-4.0",
        "kolors",
        "Kwai-Kolors/Kolors",
        "cogview-4",
        "ideogram-v3",
        "recraft-v3",
        "nano-banana",
        "google/gemini-nano-banana-2.1",
        "sd3.5-large",
        "sdxl-turbo",
        "sd-turbo",
        "stabilityai/sd-turbo",
        "flux.1-kontext-pro",
        "midjourney",
        "mj_imagine",
        "mj_swap_face",
        "real-esrgan-upscale",
        "wanx2.1-t2i-turbo",
        # video
        "sora-2",
        "veo3",
        "veo3.1-fast",
        "veo-3.0-generate-001",
        "kling-v2-1",
        "doubao-seedance-1-0-pro-250528",
        "hailuo-02",
        "MiniMax-Hailuo-02",
        "cogvideox-3",
        "vidu-q1",
        "vidu2.0",
        # Vidu's q-series names glue the series to the brand.
        "viduq1",
        "viduq1-classic",
        "viduq2-pro",
        "viduq2-turbo",
        "viduq2-pro_img2video",
        "vidu/viduq3-mix_reference2video",
        "ViduQ3-Turbo",
        "S2V-01",
        "minimax/s2v-01",
        "wan2.2-t2v-plus",
        "alibaba/wan-3.0",
        "pika-2.2",
        "runway-gen4",
        # speech / music
        "tts-1",
        "gpt-4o-mini-tts",
        "whisper-1",
        "speech-02-hd",
        "cosyvoice-v2",
        "fish-speech-1.5",
        "FunAudioLLM/SenseVoiceSmall",
        "sensevoice-small",
        "eleven_multilingual_v2",
        "elevenlabs/scribe-v2",
        "minimax-music-1.5",
        "suno_music",
        "lyria-2",
        "google/chirp-3",
        # other non-chat services
        "omni-moderation-latest",
        "jina-reranker-m0",
        "gemini-live-2.5-flash",
        "meta-llama/llama-guard-4-12b",
    ],
)
def test_relay_hides_non_chat_models_by_name(model_id):
    assert _kept(RELAY, [model_id, "gpt-6-luna"]) == ["gpt-6-luna"]


@pytest.mark.parametrize(
    "model_id",
    [
        # "kling" is a whole-segment marker: these are chat models.
        "thinkingmachines/inkling",
        "inkling-small:free",
        "qwen3-235b-a22b-instruct-2507",
        "deepseek/deepseek-v4.1-flash",
        "meta-llama/llama-4-maverick",
        "nousresearch/hermes-4-405b",
        "bytedance-seed/seed-1.6-flash",
        "amazon/nova-pro-v1",
        "openrouter/auto",
        "mistralai/mistral-medium-3-5",
        "z-ai/glm-5.3",
        "moonshotai/kimi-k2.6",
        "swan-chat-7b",
        "sdk-assistant",
        # "vidu" must start a segment, "voyage" must be one, "sd" must be
        # followed by a version or "-turbo".
        "individual-assistant-7b",
        "voyager-chat-7b",
        "sd-chat-v2",
    ],
)
def test_relay_keeps_chat_models_whose_names_resemble_markers(model_id):
    assert model_id in _kept(RELAY, [model_id])


def test_local_server_list_keeps_installed_chat_models_only():
    kept = _kept(
        "local_ai",
        [
            "gemma3:4b",
            "nomic-embed-text:latest",
            "all-minilm",
            "paraphrase-multilingual",
            "qwen3:4b-instruct",
            "deepseek-v4-pro:cloud",
            "bge-m3:latest",
        ],
    )

    # The default model leads; embeddings and DeepSeek pro tags are hidden.
    assert kept == ["qwen3:4b-instruct", "gemma3:4b"]


def test_name_markers_hide_no_text_model_of_a_realistic_openrouter_list():
    text_models = [
        "openai/gpt-6-luna",
        "anthropic/claude-haiku-5.5",
        "google/gemini-3.6-flash",
        "x-ai/grok-4.3",
        "thinkingmachines/inkling",
        "thinkingmachines/inkling-small:free",
        "stepfun/step-5-preview",
        "aion-labs/aion-3.5",
        "liquid/lfm-2.5-2.6b",
        "ai21/jamba-large-1.7",
    ]

    assert sorted(_kept(RELAY, text_models)) == sorted(text_models)


# --------------------------------------------------------------------------
# Structured fields (L1, L2, L10)
# --------------------------------------------------------------------------


def test_new_api_endpoint_types_follow_the_relay_protocol():
    entries = [
        _model("gpt-5.4-mini", supported_endpoint_types=["openai"]),
        _model("claude-sonnet-5", supported_endpoint_types=["anthropic", "openai"]),
        _model("claude-only", supported_endpoint_types=["anthropic"]),
        _model("gemini-3.6-flash", supported_endpoint_types=["gemini", "openai"]),
        _model("chat-type", supported_endpoint_types=["chat"]),
        _model("gpt-5-codex-like", supported_endpoint_types=["openai-response"]),
        _model("responses-only", supported_endpoint_types=["openai-response", "openai-response-compact"]),
        _model("gemini-only", supported_endpoint_types=["gemini"]),
        _model("seed-image", supported_endpoint_types=["image-generation", "openai"]),
        _model("my-video", supported_endpoint_types=["openai-video"]),
        _model("my-embedder", supported_endpoint_types=["embeddings"]),
        _model("my-reranker", supported_endpoint_types=["jina-rerank"]),
        _model("no-types-field"),
        _model("empty-types", supported_endpoint_types=[]),
    ]

    openai_kept = set(_kept(RELAY, entries, "openai"))
    anthropic_kept = set(_kept(RELAY, entries, "anthropic"))

    assert openai_kept == {
        "gpt-5.4-mini",
        "claude-sonnet-5",
        "gemini-3.6-flash",
        "chat-type",
        "no-types-field",
        "empty-types",
    }
    assert anthropic_kept == {"claude-sonnet-5", "claude-only", "no-types-field", "empty-types"}


def test_structured_capability_fields_hide_non_chat_models():
    entries = [
        _model("mistral-small-2603", capabilities={"completion_chat": True}),
        _model("mistral-small-embedder", capabilities={"completion_chat": False}),
        _model("vendor/text-model", architecture={"output_modalities": ["text"]}),
        _model("openrouter/auto", architecture={"output_modalities": ["text", "image"]}),
        _model("vendor/painter", architecture={"output_modalities": ["image"]}),
        _model("vendor/flat-modalities", output_modalities=["embeddings"]),
        _model("gemini-3.6-flash", supportedGenerationMethods=["generateContent", "countTokens"]),
        _model("gemini-embedder-x", supportedGenerationMethods=["embedContent"]),
    ]

    assert set(_kept(RELAY, entries)) == {
        "mistral-small-2603",
        "vendor/text-model",
        "openrouter/auto",
        "gemini-3.6-flash",
    }


def test_openrouter_models_whose_reasoning_cannot_be_lowered_are_hidden():
    def reasoning(mandatory, efforts):
        return {"mandatory": mandatory, "supported_efforts": efforts, "default_effort": "high"}

    entries = [
        _model("openai/o4-mini-high", reasoning=reasoning(True, ["high"])),
        _model("openai/gpt-5.4-pro-like", reasoning=reasoning(True, ["xhigh", "high", "medium"])),
        _model("minimax/minimax-m2", reasoning=reasoning(True, [])),
        _model("deepseek/deepseek-r1", reasoning={"mandatory": True}),
        _model("google/gemini-3.6-flash", reasoning=reasoning(True, ["high", "medium", "low", "minimal"])),
        _model("google/gemini-3.5-flash-lite", reasoning=reasoning(True, ["minimal"])),
        _model("deepseek/deepseek-v4.1-flash", reasoning=reasoning(False, ["max", "high", "low"])),
        _model("x/thinks-but-can-stop", reasoning=reasoning(False, ["xhigh", "high"])),
        _model("openai/gpt-6-luna", reasoning=reasoning(False, ["high", "low", "none"])),
        _model("vendor/flag-only", reasoning=True),
    ]

    assert set(_kept(RELAY, entries)) == {
        "google/gemini-3.6-flash",
        "google/gemini-3.5-flash-lite",
        "deepseek/deepseek-v4.1-flash",
        "x/thinks-but-can-stop",
        "openai/gpt-6-luna",
        "vendor/flag-only",
    }


# --------------------------------------------------------------------------
# Lifecycle fields (L5)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"deprecated": True},
        {"deprecated": "true"},
        {"archived": True},
        {"status": "Deprecated "},
        {"status": "pre-offline"},
        {"state": "retired"},
        {"lifecycle": "deprecated"},
        {"lifecycle_status": "sunset"},
        {"lifecycle": {"status": "deprecated", "retires_at": None}},
        {"lifecycle": {"status": "active", "retires_at": "2026-10-20T00:00:00Z"}},
        {"retires_at": "2026-10-20T00:00:00Z"},
        {"deprecation": "2026-11-01T12:00:00Z"},
        {"shutdown_date": "2026-09-30"},
        {"expiration_date": "2026-10-31"},
        {"inference_offline_info": {"offline_time": "2026-10-31 00:00:00"}},
        {"deprecation": {"date": "2026-10-20"}},
        {"expires_at": "soon"},
        {"expires_at": "Dec 31, 2026"},
        {"expires_at": True},
    ],
)
def test_lifecycle_fields_that_hide_a_model(fields):
    assert ml._field_says_retiring(fields, now=NOW) is True


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"deprecated": False, "archived": False},
        {"status": "active"},
        {"lifecycle": "active"},
        {"lifecycle": {"status": "active", "retires_at": None}},
        {"lifecycle": {"status": "legacy", "retires_at": "2027-06-01T00:00:00+09:00"}},
        {"retires_at": "2027-06-01T00:00:00+09:00"},
        {"deprecation": None},
        {"expiration_date": "2026-12-31"},
        {"inference_offline_info": {"offline_time": None}},
        {"inference_offline_info": {"offline_time": "2027-06-30T00:00:00+08:00"}},
        {"deprecation": {"replacement": "mistral-medium-3-5"}},
        {"expires_at": ""},
    ],
)
def test_lifecycle_fields_that_keep_a_model(fields):
    assert ml._field_says_retiring(fields, now=NOW) is False


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        -1_700_000_000,
        0.0,
        "0",
        "-1",
        "never",
        "None",
        "null",
        "NULL",
        253_402_300_799,  # 9999-12-31T23:59:59Z in seconds
        253_402_300_799_000,  # the same in milliseconds
        "253402300799",
        "9999-12-31",
        "9999-12-31T23:59:59Z",
        "10000-01-01",
        float("inf"),
    ],
)
def test_lifecycle_date_sentinels_mean_no_retirement(value):
    assert ml._field_says_retiring({"expires_at": value}, now=NOW) is False
    assert ml._field_says_retiring({"retires_at": value}, now=NOW) is False


@pytest.mark.parametrize(
    "value",
    [1_798_761_600, 1_798_761_600_000, 1_798_761_600_000_000, 1_798_761_600_000_000_000, "1798761600"],
)
def test_lifecycle_timestamp_units_are_picked_by_size(value):
    assert ml._parse_lifecycle_date(value) == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert ml._field_says_retiring({"expires_at": value}, now=NOW) is False


@pytest.mark.parametrize(
    "value",
    [1_761_955_200, 1_761_955_200_000, 1_761_955_200_000_000, 1_761_955_200_000_000_000],
)
def test_past_timestamps_in_any_unit_hide_the_model(value):
    # 2025-11-01T00:00:00Z: already past.
    assert ml._parse_lifecycle_date(value) == datetime(2025, 11, 1, tzinfo=timezone.utc)
    assert ml._field_says_retiring({"shutdown_date": value}, now=NOW) is True


def test_lifecycle_date_parsing_details():
    assert ml._parse_lifecycle_date("2026-11-30T12:00:00+09:00") == datetime(
        2026, 11, 30, 3, 0, tzinfo=timezone.utc
    )
    assert ml._parse_lifecycle_date("2026-11-30 12:00:00") == datetime(
        2026, 11, 30, 12, 0, tzinfo=timezone.utc
    )
    assert ml._parse_lifecycle_date("2026/12/31") == datetime(2026, 12, 31, tzinfo=timezone.utc)
    assert ml._parse_lifecycle_date("garbage") is None
    assert ml._parse_lifecycle_date(None) is None


def test_lifecycle_fields_reach_the_filter():
    entries = [
        _model("claude-opus-5", lifecycle={"status": "deprecated", "retires_at": "2026-10-20T00:00:00Z"}),
        _model("claude-sonnet-5", retires_at="2026-10-20T00:00:00Z"),
        _model("claude-sonnet-5-5", retires_at=None, lifecycle="active"),
        _model("claude-haiku-5-5", retires_at="2027-06-01T00:00:00+09:00"),
        _model("claude-haiku-4-5-20251001", lifecycle="deprecated"),
    ]

    assert _kept("anthropic", entries) == ["claude-sonnet-5-5", "claude-haiku-5-5"]


# --------------------------------------------------------------------------
# Spec rules and retirement
# --------------------------------------------------------------------------


def test_spec_include_exclude_and_require_rules():
    openai = _kept(
        "openai",
        ["gpt-6-luna", "o3", "chatgpt-4o-latest", "gpt-5-search-api", "gpt-6-sol-pro", "gpt-oss-120b", "gpt-5.4"],
    )
    qwen = _kept("qianwen", ["qwen-mt-flash", "qwen-plus", "qwen-mt-image", "qwen3-livetranslate-flash"])
    deepseek = _kept("deepseek", ["deepseek-flash", "deepseek-chat", "deepseek-v4.1-flash", "deepseek-flash-math"])

    assert openai == ["gpt-6-luna", "gpt-5.4"]
    assert qwen == ["qwen-mt-flash"]
    assert deepseek == ["deepseek-flash", "deepseek-v4.1-flash"]


def test_zhipu_merges_the_builtin_list_into_the_fetched_one():
    kept = _kept("zhipu", ["glm-5.2", "glm-4.6", "glm-4.6v", "glm-4-voice"])

    for preset in TRANSLATION_MODEL_PRESETS["zhipu"]:
        assert preset in kept
    assert "glm-4.6" in kept
    assert "glm-4.6v" not in kept
    assert "glm-4-voice" not in kept


@pytest.mark.parametrize(
    ("backend", "model_id"),
    [
        ("openai", "gpt-5.1"),
        ("openai", "gpt-4.1-nano"),
        ("openai", "o4-mini"),
        ("gemini", "gemini-2.5-flash"),
        ("xai", "grok-3"),
        ("kimi", "kimi-k2.5"),
        ("anthropic", "claude-3-haiku-20240307"),
        ("anthropic", "claude-opus-4-1"),
        ("deepseek", "deepseek-reasoner"),
        ("nvidia", "meta/llama-3.3-70b-instruct"),
    ],
)
def test_retired_ids_are_hidden_for_provider_backends_but_kept_on_the_relay(backend, model_id):
    assert model_id not in _kept(backend, [model_id])
    # A relay owns its ids: the retirement tables never judge them.
    assert model_id in _kept(RELAY, [model_id])


@pytest.mark.parametrize("backend", [RELAY, "nvidia", "local_ai", "deepseek"])
def test_deepseek_pro_is_hidden_everywhere(backend):
    spec = TRANSLATION_BACKENDS[backend]
    assert DEEPSEEK_PRO_MODEL_PATTERN in spec["model_list_exclude_patterns"]
    pros = [
        "deepseek-pro",
        "deepseek-v4-pro",
        "deepseek-v4.1-pro",
        "deepseek-v4-pro:cloud",
        "deepseek-v4-pro@20260425",
        "deepseek-v4-pro[1m]",
        "deepseek-v4-pro(thinking)",
        "DeepSeek V4 Pro",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-v4-pro-260425",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-pro:free",
        "~deepseek/deepseek-pro-latest",
        "deepseek-v4-flash-pro",
    ]

    assert _kept(backend, pros) == []


@pytest.mark.parametrize("backend", [RELAY, "nvidia", "local_ai"])
def test_deepseek_flash_and_prover_stay_where_offered(backend):
    kept = _kept(backend, ["deepseek-v4.1-flash", "deepseek-prover-v2", "deepseek-ai/deepseek-v4.1-flash"])

    assert set(kept) == {"deepseek-v4.1-flash", "deepseek-prover-v2", "deepseek-ai/deepseek-v4.1-flash"}


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-5-codex",
        "gpt-5.1-codex-max",
        "openai/gpt-5.3-codex",
        "~openai/gpt-5.2-codex",
        "codex-mini-latest",
        "openai/codex-mini",
        "o3-pro",
        "openai/o1-pro",
        "gpt-6-sol-pro",
        "openai/gpt-5.5-pro:free",
        "gpt-4o-search-preview",
        "gpt-3.5-turbo-instruct",
        "grok-4.20-multi-agent",
        "google/gemini-3.6-flash:batch",
    ],
)
def test_relay_hides_slow_or_non_chat_completion_variants(model_id):
    assert _kept(RELAY, [model_id, "gpt-5.4-mini"]) == ["gpt-5.4-mini"]


# --------------------------------------------------------------------------
# Relay on the Anthropic protocol (L2)
# --------------------------------------------------------------------------


def test_anthropic_protocol_relay_keeps_other_models_and_lists_claude_first():
    models = [
        "gpt-5.4-mini",
        "deepseek-flash",
        "claude-sonnet-5",
        "anthropic/claude-haiku-5.5",
        "claude-opus-5",
        "deepseek-v4-pro",
        "bge-m3",
    ]

    kept = _kept(RELAY, models, "anthropic")

    # Default (claude-opus-5), the priority family match, the other Claude
    # ids, then the rest; DeepSeek pro and embeddings stay hidden.
    assert kept == [
        "claude-opus-5",
        "anthropic/claude-haiku-5.5",
        "claude-sonnet-5",
        "deepseek-flash",
        "gpt-5.4-mini",
    ]


def test_relay_never_rewrites_dated_claude_ids():
    assert _kept(RELAY, ["claude-haiku-4-5-20251001"], "anthropic") == ["claude-haiku-4-5-20251001"]
    assert _kept(RELAY, ["claude-haiku-4-5-20251001"], "openai") == ["claude-haiku-4-5-20251001"]


# --------------------------------------------------------------------------
# Dated snapshots (L3) and Claude aliases (L4)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dated", ["gpt-4o-2024-08-06", "gpt-4o-20240806", "gpt-4o@20240806"])
def test_full_date_snapshots_collapse_onto_their_alias_on_every_backend(dated):
    assert _filter(RELAY, ["gpt-4o", dated]) == (("gpt-4o",), 1)
    assert _filter("openai", ["gpt-4.1", dated.replace("gpt-4o", "gpt-4.1")]) == (("gpt-4.1",), 1)
    # Without the alias the snapshot stays.
    assert _kept(RELAY, [dated]) == [dated]


@pytest.mark.parametrize(
    ("base", "numbered"),
    [
        ("qwen3-235b-a22b", "qwen3-235b-a22b-2507"),
        ("qwen/qwen3-235b-a22b-instruct", "qwen/qwen3-235b-a22b-instruct-2507"),
        ("deepseek-r1", "deepseek-r1-0528"),
        ("moonshotai/kimi-k2", "moonshotai/kimi-k2-0905"),
        ("mistral-large", "mistral-large-2411"),
        ("deepseek-v3", "deepseek-v3-250324"),
    ],
)
def test_relay_keeps_short_numbered_models_that_are_distinct(base, numbered):
    kept, hidden = _filter(RELAY, [base, numbered])

    assert set(kept) == {base, numbered}
    assert hidden == 0


def test_short_date_snapshots_collapse_only_for_vendors_using_them():
    assert _filter("xai", ["grok-4.3", "grok-4.3-1212"]) == (("grok-4.3",), 1)
    assert _filter("mistral", ["ministral-3b", "ministral-3b-2512"]) == (("ministral-3b",), 1)
    assert _filter("openai", ["gpt-4.1-mini", "gpt-4.1-mini-0814"]) == (("gpt-4.1-mini",), 1)
    # DeepSeek is not one of them: a numbered id is its own model there.
    kept, hidden = _filter("deepseek", ["deepseek-flash", "deepseek-flash-0925"])
    assert set(kept) == {"deepseek-flash", "deepseek-flash-0925"}
    assert hidden == 0


def test_recommended_models_are_never_collapsed():
    # ministral-14b-2512 is a preset even though ministral-14b is listed.
    kept, hidden = _filter("mistral", ["ministral-14b", "ministral-14b-2512"])

    assert set(kept) == {"ministral-14b", "ministral-14b-2512"}
    assert hidden == 0


def test_anthropic_dated_ids_are_offered_as_their_alias():
    listed = [
        "claude-opus-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-1-20250805",
        "claude-3-haiku-20240307",
        "claude-fable-5-1",
    ]

    kept, hidden = _filter("anthropic", listed)

    # The presets come first, in preset order, and claude-haiku-4-5 is one.
    assert kept[: len(TRANSLATION_MODEL_PRESETS["anthropic"])] == TRANSLATION_MODEL_PRESETS["anthropic"]
    assert "claude-opus-4-5" in kept
    for dated in ("claude-haiku-4-5-20251001", "claude-opus-4-5-20251101"):
        assert dated not in kept
    # Retired Sonnet 4.5 / Opus 4.1 / Haiku 3 and the always-thinking Fable.
    for hidden_id in ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", "claude-opus-4-1", "claude-3-haiku"):
        assert hidden_id not in kept
    assert "claude-fable-5-1" not in kept
    assert hidden == 4


def test_anthropic_alias_and_dated_id_listed_together_give_one_entry():
    assert _filter("anthropic", ["claude-haiku-4-5", "claude-haiku-4-5-20251001"]) == (
        ("claude-haiku-4-5",),
        1,
    )


# --------------------------------------------------------------------------
# Ordering (L6, L8)
# --------------------------------------------------------------------------


def test_version_sort_ranks_a_missing_component_older():
    models = [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5.4",
        "gpt-5.2",
        "claude-sonnet-4",
        "claude-sonnet-4-5",
        "gemini-3",
        "gemini-3.6-flash-lite",
    ]

    assert sorted(models, key=ml._version_sort_key) == [
        "claude-sonnet-4-5",
        "claude-sonnet-4",
        "gemini-3.6-flash-lite",
        "gemini-3",
        "gpt-5.4",
        "gpt-5.2",
        "gpt-5",
        "gpt-5-mini",
    ]


def test_relay_ranks_decorated_ids_like_their_native_family():
    openrouter = [
        "ai21/jamba-large-1.7",
        "x-ai/grok-4.3",
        "google/gemini-3.6-flash",
        "~deepseek/deepseek-flash-latest",
        "deepseek/deepseek-v4.1-flash",
        "anthropic/claude-haiku-5.5",
        "openai/gpt-5.6-luna",
        "openai/gpt-6-luna",
        "meta-llama/llama-4-maverick",
    ]

    kept = _kept(RELAY, openrouter)

    assert kept == [
        "openai/gpt-6-luna",
        "openai/gpt-5.6-luna",
        "anthropic/claude-haiku-5.5",
        "deepseek/deepseek-v4.1-flash",
        "~deepseek/deepseek-flash-latest",
        "google/gemini-3.6-flash",
        "x-ai/grok-4.3",
        "ai21/jamba-large-1.7",
        "meta-llama/llama-4-maverick",
    ]


def test_relay_ranks_the_exact_id_before_its_decorated_variants():
    kept = _kept(RELAY, ["openai/gpt-6-luna:free", "zz/other", "openai/gpt-6-luna", "gpt-6-luna"])

    assert kept == ["gpt-6-luna", "openai/gpt-6-luna:free", "openai/gpt-6-luna", "zz/other"]


def test_provider_backends_match_recommendations_exactly():
    default = TRANSLATION_BACKENDS["nvidia"]["model"]
    lookalike = "other/" + default.split("/", 1)[1]

    kept = _kept("nvidia", [lookalike, "meta/llama-4-scout-17b-16e-instruct", default])

    assert kept[0] == default
    assert kept.index(lookalike) > 0


def test_model_family_ids_used_for_relay_matching():
    assert model_family_id("anthropic/claude-haiku-5.5") == "claude-haiku-5-5"
    assert model_family_id("~openai/gpt-6-luna") == "gpt-6-luna"
    assert model_family_id("deepseek/deepseek-v4-flash:free") == "deepseek-v4-flash"
    assert model_family_id("openai/gpt-6.1-sol") == "gpt-6.1-sol"
    assert same_model_family("openai/gpt-6-luna", "gpt-6-luna")
    assert not same_model_family("", "")


# --------------------------------------------------------------------------
# Caps and counts (L7)
# --------------------------------------------------------------------------


def test_offered_list_is_capped_after_filtering_and_counted_as_hidden():
    chat = [f"vendor{index}/chat-model-{index}" for index in range(MAX_LISTED_MODELS + 300)]
    junk = [f"vendor/image-model-{index}" for index in range(2500)]

    kept, hidden = _filter(RELAY, junk + chat)

    assert len(kept) == MAX_LISTED_MODELS
    assert hidden == 2500 + 300


def test_hidden_count_includes_invalid_and_duplicate_entries():
    kept, hidden = _filter(RELAY, ["", "bad\x00id", "gpt-6-luna", "gpt-6-luna", "tts-1"])

    assert kept == ("gpt-6-luna",)
    assert hidden == 4


# --------------------------------------------------------------------------
# Built-in lists
# --------------------------------------------------------------------------


def test_builtin_model_list_puts_the_default_first_without_duplicates():
    assert builtin_model_list("openai") == TRANSLATION_MODEL_PRESETS["openai"]
    assert builtin_model_list("openai")[0] == TRANSLATION_BACKENDS["openai"]["model"]
    assert builtin_model_list("local_ai") == (TRANSLATION_BACKENDS["local_ai"]["model"],)
    assert builtin_model_list("zhipu") == TRANSLATION_MODEL_PRESETS["zhipu"]


def test_relay_builtin_model_follows_the_protocol():
    assert builtin_model_list(RELAY) == (TRANSLATION_BACKENDS[RELAY]["model"],)
    assert builtin_model_list(RELAY, protocol="anthropic") == (TRANSLATION_BACKENDS["anthropic"]["model"],)
    # The legacy ids resolve to the relay.
    assert builtin_model_list("grok_compatible") == builtin_model_list(RELAY)


def test_which_backends_read_their_list_from_the_provider():
    for backend in (RELAY, "openai", "anthropic", "local_ai", "deepseek", "zhipu", "nvidia", "hunyuan"):
        assert backend_supports_model_listing(backend), backend
    for backend in ("doubao", "google_web", "microsoft_edge_web", "deepl", "mymemory", "libretranslate"):
        assert not backend_supports_model_listing(backend), backend


def test_relay_model_options_fall_back_to_the_protocol_default():
    from src.utils.ui_config import get_backend_model_options, relay_protocol_default

    assert get_backend_model_options(RELAY_BACKEND, "", {"protocol": "anthropic"}) == (
        relay_protocol_default("anthropic", "model"),
    )
    assert get_backend_model_options(RELAY_BACKEND, "", {"protocol": "openai"}) == (
        relay_protocol_default("openai", "model"),
    )
    assert get_backend_model_options(RELAY_BACKEND, "my-relay-model", {"protocol": "anthropic"}) == (
        "my-relay-model",
    )
