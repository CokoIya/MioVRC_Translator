from __future__ import annotations

import pytest

from src.translators.factory import create_translator
from src.utils.ui_config import (
    DISABLED_TRANSLATION_BACKENDS,
    get_backend_order,
    normalize_backend,
)


def test_no_backend_is_disabled_and_claude_is_selectable_again() -> None:
    assert DISABLED_TRANSLATION_BACKENDS == frozenset()
    order = set(get_backend_order())
    for backend in ("anthropic", "anthropic_compatible"):
        assert backend in order
        assert normalize_backend(backend) == backend


@pytest.mark.parametrize("backend", ("anthropic", "anthropic_compatible"))
def test_factory_builds_the_claude_translator(backend: str) -> None:
    translator = create_translator(
        {
            "translation": {
                "backend": backend,
                backend: {"api_key": "sk-ant-test-key-0000000000"},
            }
        }
    )
    try:
        assert translator.__class__.__name__ == "AnthropicTranslator"
        assert translator.model == "claude-opus-5"
    finally:
        translator.close()
