from __future__ import annotations

import pytest

from src.translators.factory import create_translator
from src.utils.ui_config import (
    DEFAULT_BACKEND,
    DISABLED_TRANSLATION_BACKENDS,
    get_backend_order,
    normalize_backend,
)


def test_claude_backends_are_not_selectable_or_normalized() -> None:
    assert DISABLED_TRANSLATION_BACKENDS == {"anthropic", "anthropic_compatible"}
    assert not (DISABLED_TRANSLATION_BACKENDS & set(get_backend_order()))
    for backend in DISABLED_TRANSLATION_BACKENDS:
        assert normalize_backend(backend) == DEFAULT_BACKEND


@pytest.mark.parametrize("backend", ("anthropic", "anthropic_compatible"))
def test_factory_routes_disabled_claude_config_to_default_backend(backend: str) -> None:
    # normalize_backend protects the public factory entry point; a persisted
    # legacy configuration must not instantiate an Anthropic client.
    translator = create_translator({"translation": {"backend": backend}})
    assert translator.__class__.__name__ != "AnthropicTranslator"
    close = getattr(translator, "close", None)
    if callable(close):
        close()
