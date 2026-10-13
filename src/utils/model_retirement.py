"""Translation model ids that providers have retired.

One table for every consumer: config migration (config_manager), the remote
catalog sanitizer (catalog_loader), saved and freshly fetched model lists
(ui_config, model_listing). This module imports nothing from ``src`` so all of
them can depend on it without import cycles.
"""

from __future__ import annotations

# The Claude families the translator knows how to drive (see
# AnthropicTranslator._apply_model_request_defaults). A saved official-Claude
# model outside them (3.x, 4.0, 4.1) is migrated, and a remote catalog cannot
# put anything else into the presets. "claude-opus-5" also covers Opus 5.5.
ANTHROPIC_MODEL_ID_PREFIXES: tuple[str, ...] = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-5",
    "claude-haiku-4-5",
    "claude-fable-5",
)

# Model ids the providers have shut down (or announced for shutdown), checked
# against each vendor's model and deprecation pages on 2026-10-12. Only
# expired ids belong here: a model that is merely older stays selectable.
LEGACY_OPENAI_MODEL_PREFIXES = (
    "gpt-3.5",
    "gpt-4-",
    "o1",
    "o3-mini",
    "o4-mini",
)
LEGACY_OPENAI_MODEL_IDS = {
    "gpt-4",
    "gpt-4.5-preview",
    "gpt-4.1-nano",
    "gpt-5.1",
    "gpt-5.3-codex",
    "gpt-5.4-nano",
    "gpt-5-chat-latest",
    "gpt-5.1-chat-latest",
    "gpt-5.2-chat-latest",
    "gpt-5.3-chat-latest",
    "chatgpt-4o-latest",
    "gpt-5-2025-08-07",
    "gpt-5-mini-2025-08-07",
    "gpt-5-nano-2025-08-07",
    "gpt-5.6-mini",
    "gpt-5.6-nano",
}
OPENAI_SUCCESSORS = {
    "gpt-5.1": "gpt-6-sol",
    "gpt-5.3-codex": "gpt-6-sol",
    "gpt-5-2025-08-07": "gpt-6-sol",
    "gpt-4.5-preview": "gpt-6-sol",
    "gpt-4-turbo": "gpt-6-sol",
    "gpt-4": "gpt-6-sol",
    "o1": "gpt-6-sol",
}
LEGACY_ANTHROPIC_MODEL_IDS = {
    "claude-sonnet-4-5",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-0",
    "claude-opus-4-20250514",
    "claude-opus-4-0",
    "claude-opus-4-1",
    "claude-opus-4-1-20250805",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
    "claude-3-opus-20240229",
}
ANTHROPIC_SUCCESSORS = {
    "claude-sonnet-4-5": "claude-sonnet-5-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-5-5",
    "claude-sonnet-4-20250514": "claude-sonnet-5-5",
    "claude-sonnet-4-0": "claude-sonnet-5-5",
    "claude-3-7-sonnet-20250219": "claude-sonnet-5-5",
    "claude-3-5-sonnet-20241022": "claude-sonnet-5-5",
    "claude-3-5-sonnet-20240620": "claude-sonnet-5-5",
    "claude-opus-4-20250514": "claude-opus-4-8",
    "claude-opus-4-0": "claude-opus-4-8",
    "claude-opus-4-1": "claude-opus-4-8",
    "claude-opus-4-1-20250805": "claude-opus-4-8",
    "claude-3-opus-20240229": "claude-opus-4-8",
    "claude-3-5-haiku-20241022": "claude-haiku-5-5",
    "claude-3-haiku-20240307": "claude-haiku-5-5",
}
UNROUTABLE_QWEN_GENERAL_MODEL_IDS = {
    "qwen3.6-max-preview",
    "qwen3.6-plus",
    "qwen3.6-flash",
}
# Gemini ids retired with no successor named here fall back to the default;
# ids with a direct successor are in RETIRED_MODEL_REPLACEMENTS.
LEGACY_GEMINI_MODEL_IDS: set[str] = set()
LEGACY_DOUBAO_MODEL_IDS = {
    "doubao-seed-2.0-pro",
}
THINKING_MODEL_REPLACEMENTS = {
    "deepseek": {
        "deepseek-reasoner": "deepseek-flash",
    },
    "kimi": {
        "kimi-k2-thinking": "kimi-k2.6",
        "kimi-k2-thinking-turbo": "kimi-k2.6",
        "kimi-thinking-preview": "kimi-k2.6",
    },
    "xai": {
        "grok-4.20-0309-reasoning": "grok-4.20-0309-non-reasoning",
        # Both aliases resolve to the reasoning variant.
        "grok-4.20": "grok-4.20-0309-non-reasoning",
        "grok-4.20-0309": "grok-4.20-0309-non-reasoning",
    },
    "mistral": {
        "magistral-small-latest": "mistral-small-2603",
        "magistral-medium-latest": "mistral-medium-3-5",
        "magistral-small-2509": "mistral-small-2603",
        "magistral-small-2507": "mistral-small-2603",
        "magistral-small-2506": "mistral-small-2603",
        "magistral-medium-2509": "mistral-medium-3-5",
        "magistral-medium-2507": "mistral-medium-3-5",
        "magistral-medium-2506": "mistral-medium-3-5",
    },
}
RETIRED_MODEL_REPLACEMENTS = {
    "openai": {},
    "deepseek": {
        # DeepSeek is offered as flash only; V4-Flash itself was retired on
        # 2026-09-10 and is only temporarily routed to V4.1-Flash.
        "deepseek-v4-pro": "deepseek-flash",
        "deepseek-v4-flash": "deepseek-flash",
        "deepseek-v4-flash-vision-exp": "deepseek-flash",
        "deepseek-chat": "deepseek-flash",
    },
    "qianwen": {
        "qwen3.7-max": "qwen-mt-flash",
        "qwen-mt-turbo": "qwen-mt-flash",
        "qwen-mt-turbo-latest": "qwen-mt-flash",
    },
    "hunyuan": {
        # The old Hunyuan API closed on 2026-09-30; TokenHub serves hy3 and
        # the hy-mt2 translation models instead.
        "hunyuan-turbos-latest": "hy3",
        "hunyuan-turbo-latest": "hy3",
        "hunyuan-turbos-20250926": "hy3",
        "hunyuan-turbo": "hy3",
        "hunyuan-lite": "hy3",
        "hunyuan-standard": "hy3",
        "hunyuan-large": "hy3",
        "hunyuan-pro": "hy3",
        "hunyuan-t1-latest": "hy3",
        "hunyuan-2.0-instruct-20251111": "hy3",
        "hunyuan-a13b": "hy3",
        "hy3-preview": "hy3",
        "hunyuan-translation-preview": "hy-mt2-plus",
        "hunyuan-translation": "hy-mt2-plus",
        "hunyuan-translation-lite": "hy-mt2-lite",
    },
    "xiaomi": {
        "mimo-v2.5-pro": "mimo-v2.6-pro",
        "mimo-v2.5": "mimo-v2.6-flash",
        "mimo-v2-flash": "mimo-v2.6-flash",
        "mimo-v2-pro": "mimo-v2.6-pro",
        "mimo-v2-omni": "mimo-v2.6-flash",
    },
    "gemini": {
        "gemini-3.7-flash": "gemini-3.8-flash",
        "gemini-3.5-flash": "gemini-3.6-flash",
        "gemini-3-flash-preview": "gemini-3.6-flash",
        "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite-preview": "gemini-3.5-flash-lite",
        "gemini-3-pro-preview": "gemini-3.8-flash",
        "gemini-3.1-pro-preview": "gemini-3.8-flash",
        "gemini-2.5-pro": "gemini-3.8-flash",
        "gemini-2.5-flash": "gemini-3.6-flash",
        "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.5-flash-preview-05-20": "gemini-3.6-flash",
        "gemini-2.5-flash-preview-09-25": "gemini-3.6-flash",
        "gemini-2.5-flash-lite-preview-09-2025": "gemini-3.5-flash-lite",
        "gemini-2.5-pro-preview-03-25": "gemini-3.8-flash",
        "gemini-2.5-pro-preview-05-06": "gemini-3.8-flash",
        "gemini-2.5-pro-preview-06-05": "gemini-3.8-flash",
        "gemini-2.0-flash": "gemini-3.6-flash",
        "gemini-2.0-flash-001": "gemini-3.6-flash",
        "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.0-flash-lite-001": "gemini-3.5-flash-lite",
        "gemini-2.0-flash-lite-preview": "gemini-3.5-flash-lite",
        "gemini-2.0-flash-lite-preview-02-05": "gemini-3.5-flash-lite",
    },
    "kimi": {
        "kimi-k2.5": "kimi-k2.6",
        "kimi-k2-0905-preview": "kimi-k2.6",
        "kimi-k2-0711-preview": "kimi-k2.6",
        "kimi-k2-turbo-preview": "kimi-k2.6",
        "kimi-latest": "kimi-k2.6",
        "moonshot-v1-8k": "kimi-k2.6",
        "moonshot-v1-32k": "kimi-k2.6",
        "moonshot-v1-128k": "kimi-k2.6",
        "moonshot-v1-auto": "kimi-k2.6",
        "moonshot-v1-8k-vision-preview": "kimi-k2.6",
        "moonshot-v1-32k-vision-preview": "kimi-k2.6",
        "moonshot-v1-128k-vision-preview": "kimi-k2.6",
    },
    "xai": {
        "grok-4.20-multi-agent-0309": "grok-4.20-0309-non-reasoning",
        "grok-4.20-non-reasoning": "grok-4.20-0309-non-reasoning",
        "grok-4.20-non-reasoning-latest": "grok-4.20-0309-non-reasoning",
        "grok-4.20-beta-non-reasoning": "grok-4.20-0309-non-reasoning",
        "grok-4.3-latest": "grok-4.3",
        "grok-4-1-fast-non-reasoning": "grok-4.3",
        "grok-4-1-fast-reasoning": "grok-4.3",
        "grok-4-fast-non-reasoning": "grok-4.3",
        "grok-4-fast-reasoning": "grok-4.3",
        "grok-4-0709": "grok-4.3",
        "grok-3": "grok-4.3",
        "grok-3-mini": "grok-4.3",
        "grok-code-fast-1": "grok-4.3",
    },
    "zhipu": {
        "glm-4.5-flash": "glm-4.7-flash",
        "glm-4-flash": "glm-4.7-flash",
        "glm-4-air": "glm-4.7-flashx",
        "glm-4-airx": "glm-4.7-flashx",
        "glm-4-plus": "glm-4.7",
        "glm-4": "glm-4.7",
    },
    # A date-suffixed Claude id is not a valid model string.
    "anthropic": {"claude-haiku-4-5-20251001": "claude-haiku-4-5"},
    "mistral": {
        "mistral-medium-2508": "mistral-medium-3-5",
        "mistral-medium-2505": "mistral-medium-3-5",
        "mistral-medium-2312": "mistral-medium-3-5",
        "mistral-small-2506": "mistral-small-2603",
        "mistral-small-2503": "mistral-small-2603",
        "mistral-small-2501": "mistral-small-2603",
        "mistral-small-2409": "mistral-small-2603",
        "mistral-small-2402": "mistral-small-2603",
        "ministral-8b-2410": "ministral-8b-2512",
        "ministral-3b-2410": "ministral-3b-2512",
        "mistral-large-2411": "mistral-medium-3-5",
        "mistral-large-2407": "mistral-large-2512",
        "mistral-large-2402": "mistral-large-2512",
        "pixtral-large-2411": "mistral-medium-3-5",
        "pixtral-12b-2409": "ministral-14b-2512",
        "open-mistral-nemo": "ministral-8b-2512",
        "open-mistral-nemo-2407": "ministral-8b-2512",
        "open-mistral-7b": "ministral-8b-2512",
        "open-mixtral-8x7b": "mistral-small-2603",
        "open-mixtral-8x22b": "mistral-small-2603",
        "mistral-saba-2502": "mistral-small-2603",
        "devstral-2512": "mistral-medium-3-5",
        "zai-glm-5-2": "mistral-medium-3-5",
    },
    "doubao": {
        "doubao-seed-2-0-pro-260215": "doubao-seed-2-1-lite-260915",
        "doubao-seed-2-0-lite-260215": "doubao-seed-2-1-lite-260915",
        "doubao-seed-2-0-mini-260215": "doubao-seed-2-0-mini-260428",
        "doubao-seed-2-0-code-preview-260215": "doubao-seed-2-1-lite-260915",
        "deepseek-v4-pro-260425": "deepseek-v4-1-flash-260910",
        "deepseek-v4-pro-ga-260813": "deepseek-v4-1-flash-260910",
        "deepseek-v4-flash-260425": "deepseek-v4-flash-ga-260731",
        "deepseek-v3-2-251201": "deepseek-v4-flash-ga-260731",
        "doubao-seed-1-8-251228": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-251015": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-250615": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-vision-250815": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-flash-250828": "doubao-seed-2-0-mini-260428",
        "doubao-seed-1-6-flash-250615": "doubao-seed-2-0-mini-260428",
        "doubao-seed-1-6-flash-250715": "doubao-seed-2-0-mini-260428",
        "doubao-seed-1-6-lite-251015": "doubao-seed-2-0-mini-260428",
        "doubao-1-5-lite-32k-250115": "doubao-seed-2-0-mini-260428",
        "doubao-1-5-pro-32k-250115": "doubao-seed-2-1-lite-260915",
        "doubao-1-5-pro-256k-250115": "doubao-seed-2-1-lite-260915",
        "doubao-1-5-thinking-pro-250415": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-thinking-250715": "doubao-seed-2-1-lite-260915",
        "doubao-seed-1-6-thinking-250615": "doubao-seed-2-1-lite-260915",
        "doubao-pro-32k-241215": "doubao-seed-2-1-lite-260915",
        "doubao-lite-32k-240828": "doubao-seed-2-0-mini-260428",
        "deepseek-v3-250324": "deepseek-v4-1-flash-260910",
        "deepseek-r1-250528": "deepseek-v4-1-flash-260910",
        "deepseek-v3-1-terminus": "deepseek-v4-1-flash-260910",
        "deepseek-v3-1-250821": "deepseek-v4-1-flash-260910",
        "glm-4-7-251222": "glm-5-2-260617",
        "glm-5-1-260428": "doubao-seed-2-1-lite-260915",
        "kimi-k2-5-260127": "doubao-seed-2-1-lite-260915",
        "kimi-k2-250905": "doubao-seed-2-1-lite-260915",
        "kimi-k2-thinking-251104": "doubao-seed-2-1-lite-260915",
    },
    "nvidia": {
        "nvidia/nemotron-3-nano-30b-a3b": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nvidia-nemotron-nano-9b-v2": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/llama-3.3-nemotron-super-49b-v1": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/llama-3.1-nemotron-nano-8b-v1": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-mini-4b-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "deepseek-ai/deepseek-v4-pro": "deepseek-ai/deepseek-v4.1-flash",
        "deepseek-ai/deepseek-v4-flash": "deepseek-ai/deepseek-v4.1-flash",
        "deepseek-ai/deepseek-v4-flash-0731": "deepseek-ai/deepseek-v4.1-flash",
        "meta/llama-3.3-70b-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "meta/llama-3.1-8b-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "meta/llama-3.1-70b-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "meta/llama-4-maverick-17b-128e-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "openai/gpt-oss-120b": "openai/gpt-oss-20b",
        "qwen/qwen3-next-80b-a3b-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "mistralai/mistral-nemotron": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "z-ai/glm-5.2": "z-ai/glm-5.3-flash",
        "z-ai/glm4.7": "z-ai/glm-5.3-flash",
        "moonshotai/kimi-k2-instruct": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "google/gemma-3-27b-it": "google/gemma-4-31b-it",
        "minimaxai/minimax-m2.7": "nvidia/nemotron-3.5-lightning-30b-a3b",
    },
}

# Ids a remote catalog may never re-add (lower-cased per backend).
CATALOG_RETIRED_MODELS = {
    "deepseek": {"deepseek-v4-pro", "deepseek-v4-flash"},
    "hunyuan": {"hunyuan-turbos-latest", "hunyuan-turbo-latest", "hunyuan-lite"},
    "xiaomi": {"mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-flash"},
    "gemini": {"gemini-2.5-pro", "gemini-3.7-flash", "gemini-3.5-flash"},
    "kimi": {"kimi-k2-0905-preview", "kimi-k2-turbo-preview", "kimi-k2.5"},
    "xai": {"grok-4.20-multi-agent-0309", "grok-4.20-0309-reasoning", "grok-4.20"},
    "mistral": {"magistral-small-latest", "magistral-medium-latest"},
    "nvidia": {"nvidia/nemotron-3-nano-30b-a3b", "nvidia/llama-3.1-nemotron-nano-8b-v1"},
    "doubao": {"doubao-seed-2-0-pro-260215"},
}


def retired_catalog_model_ids(backend_id: str) -> frozenset[str]:
    """Ids a remote catalog may never re-add for ``backend_id`` (lower-cased)."""

    return frozenset(
        str(model).strip().lower()
        for model in CATALOG_RETIRED_MODELS.get(str(backend_id or ""), ())
    )


def is_retired_translation_model(backend: str, model_id: object) -> bool:
    """Whether a model id is known to be retired (or deliberately not offered).

    Used to keep such ids out of model lists fetched from the provider and
    out of the remote catalog. Relay model ids are owned by the relay and are
    never judged here: no table is keyed by the relay backend.
    """

    model = str(model_id or "").strip().lower()
    if not model:
        return False
    if model in RETIRED_MODEL_REPLACEMENTS.get(backend, {}):
        return True
    if model in THINKING_MODEL_REPLACEMENTS.get(backend, {}):
        return True
    if backend == "openai" and (
        model in LEGACY_OPENAI_MODEL_IDS
        or model.startswith(LEGACY_OPENAI_MODEL_PREFIXES)
    ):
        return True
    if backend == "anthropic" and (
        model in LEGACY_ANTHROPIC_MODEL_IDS
        or (model.startswith("claude-") and not model.startswith(ANTHROPIC_MODEL_ID_PREFIXES))
    ):
        return True
    if backend == "gemini" and model in LEGACY_GEMINI_MODEL_IDS:
        return True
    if backend == "doubao" and model in LEGACY_DOUBAO_MODEL_IDS:
        return True
    if backend == "qianwen" and model in UNROUTABLE_QWEN_GENERAL_MODEL_IDS:
        return True
    return model in retired_catalog_model_ids(backend)
