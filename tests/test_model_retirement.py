"""The shared table of retired translation model ids."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.utils import catalog_loader, config_manager, model_retirement, ui_config
from src.utils.model_retirement import (
    ANTHROPIC_MODEL_ID_PREFIXES,
    ANTHROPIC_SUCCESSORS,
    CATALOG_RETIRED_MODELS,
    OPENAI_SUCCESSORS,
    RETIRED_MODEL_REPLACEMENTS,
    THINKING_MODEL_REPLACEMENTS,
    is_retired_translation_model,
    retired_catalog_model_ids,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _src_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.split(".")[0] == "src")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] == "src":
                found.append("." * node.level + (node.module or ""))
    return found


@pytest.mark.parametrize("module", ["model_retirement.py", "model_family.py"])
def test_shared_model_tables_import_nothing_from_src(module):
    # Imported by config_manager, ui_config, catalog_loader, the translators
    # and the settings window: any src import here could close a cycle.
    assert _src_imports(SRC / "utils" / module) == []


def test_reexports_are_the_shared_objects():
    pairs = [
        (config_manager._LEGACY_OPENAI_MODEL_PREFIXES, model_retirement.LEGACY_OPENAI_MODEL_PREFIXES),
        (config_manager._LEGACY_OPENAI_MODEL_IDS, model_retirement.LEGACY_OPENAI_MODEL_IDS),
        (config_manager._OPENAI_SUCCESSORS, model_retirement.OPENAI_SUCCESSORS),
        (config_manager._LEGACY_ANTHROPIC_MODEL_IDS, model_retirement.LEGACY_ANTHROPIC_MODEL_IDS),
        (config_manager._ANTHROPIC_SUCCESSORS, model_retirement.ANTHROPIC_SUCCESSORS),
        (
            config_manager._UNROUTABLE_QWEN_GENERAL_MODEL_IDS,
            model_retirement.UNROUTABLE_QWEN_GENERAL_MODEL_IDS,
        ),
        (config_manager._LEGACY_GEMINI_MODEL_IDS, model_retirement.LEGACY_GEMINI_MODEL_IDS),
        (config_manager._LEGACY_DOUBAO_MODEL_IDS, model_retirement.LEGACY_DOUBAO_MODEL_IDS),
        (config_manager._THINKING_MODEL_REPLACEMENTS, model_retirement.THINKING_MODEL_REPLACEMENTS),
        (config_manager._RETIRED_MODEL_REPLACEMENTS, model_retirement.RETIRED_MODEL_REPLACEMENTS),
        (config_manager.is_retired_translation_model, is_retired_translation_model),
        (ui_config.ANTHROPIC_MODEL_ID_PREFIXES, ANTHROPIC_MODEL_ID_PREFIXES),
        (ui_config.is_retired_translation_model, is_retired_translation_model),
        (catalog_loader.retired_catalog_model_ids, retired_catalog_model_ids),
        (catalog_loader.is_retired_translation_model, is_retired_translation_model),
    ]

    for reexported, shared in pairs:
        assert reexported is shared


@pytest.mark.parametrize(
    "model_id",
    ["gpt-5.1", "claude-3-haiku-20240307", "deepseek-v4-pro", "gemini-2.5-flash", "grok-3", "kimi-k2.5"],
)
@pytest.mark.parametrize("backend", ["openai_compatible", "local_ai"])
def test_relay_and_local_ids_are_never_judged(backend, model_id):
    assert is_retired_translation_model(backend, model_id) is False


@pytest.mark.parametrize(
    ("backend", "model_id"),
    [
        ("openai", "gpt-5.1"),
        ("openai", "GPT-5.4-NANO"),
        ("openai", "  gpt-3.5-turbo  "),
        ("openai", "o1-mini"),
        ("openai", "gpt-4-turbo"),
        ("anthropic", "claude-sonnet-4-5-20250929"),
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("anthropic", "claude-opus-4-1"),
        ("anthropic", "claude-2.1"),
        ("deepseek", "deepseek-reasoner"),
        ("deepseek", "deepseek-v4-flash"),
        ("qianwen", "qwen3.6-plus"),
        ("gemini", "gemini-2.5-flash"),
        ("xai", "grok-4.20-0309-reasoning"),
        ("hunyuan", "hunyuan-turbos-latest"),
        ("doubao", "doubao-seed-2.0-pro"),
        ("mistral", "magistral-small-latest"),
        ("nvidia", "nvidia/nemotron-3-nano-30b-a3b"),
    ],
)
def test_retired_provider_ids(backend, model_id):
    assert is_retired_translation_model(backend, model_id) is True


@pytest.mark.parametrize(
    ("backend", "model_id"),
    [
        ("openai", "gpt-6-luna"),
        ("openai", "gpt-5.4-mini"),
        ("anthropic", "claude-haiku-4-5"),
        ("anthropic", "claude-opus-4-5"),
        ("anthropic", "claude-opus-5-5"),
        ("anthropic", "claude-sonnet-5-5"),
        ("deepseek", "deepseek-flash"),
        ("zhipu", "glm-4.7-flash"),
        ("gemini", "gemini-3.6-flash"),
        ("xai", "grok-4.20-0309-non-reasoning"),
        ("openai", ""),
        ("openai", None),
    ],
)
def test_current_provider_ids_are_not_retired(backend, model_id):
    assert is_retired_translation_model(backend, model_id) is False


def test_every_successor_is_a_current_model():
    for backend, table in RETIRED_MODEL_REPLACEMENTS.items():
        for old, new in table.items():
            assert is_retired_translation_model(backend, old), (backend, old)
            assert not is_retired_translation_model(backend, new), (backend, old, new)
    for backend, table in THINKING_MODEL_REPLACEMENTS.items():
        for old, new in table.items():
            assert not is_retired_translation_model(backend, new), (backend, old, new)
    for new in OPENAI_SUCCESSORS.values():
        assert not is_retired_translation_model("openai", new)
    for new in ANTHROPIC_SUCCESSORS.values():
        assert not is_retired_translation_model("anthropic", new)


def test_catalog_retired_ids_are_lower_cased_per_backend():
    for backend, models in CATALOG_RETIRED_MODELS.items():
        ids = retired_catalog_model_ids(backend)
        assert ids == frozenset(model.strip().lower() for model in models)
        for model in models:
            assert is_retired_translation_model(backend, model.upper())
    assert retired_catalog_model_ids("openai_compatible") == frozenset()
    assert retired_catalog_model_ids("") == frozenset()
