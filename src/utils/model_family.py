"""Normalise model ids that relays and aggregators decorate.

Relay stations and aggregators list the same model under several spellings:
``anthropic/claude-haiku-5.5``, ``~openai/gpt-6-luna``,
``deepseek/deepseek-v4-flash:free``. Per-model rules (reasoning controls,
list filters, ordering, placeholder matching) are written for the vendor's
own id, so they match against the bare family id returned here.

This module imports nothing from ``src``.
"""

from __future__ import annotations

import re

_VARIANT_SUFFIX_RE = re.compile(r":[a-z0-9._-]+$")


def model_family_id(model_id: object) -> str:
    """Lower-cased bare model id for rule matching.

    Strips a leading ``~`` (OpenRouter's "latest" marker), one or more
    ``vendor/`` namespaces and a ``:variant`` suffix (``:free``, ``:online``,
    ``:thinking``). Claude ids written with dotted versions
    (``claude-haiku-5.5``) are mapped to Anthropic's hyphenated form
    (``claude-haiku-5-5``); dots in other families are part of the vendor id
    (``gpt-6.1-sol``, ``gemini-3.6-flash``) and are kept.

    Do not use this for NVIDIA NIM or local servers: there the namespace or
    tag is part of the model id the provider expects.
    """

    text = str(model_id or "").strip().lower()
    if text.startswith("~"):
        text = text[1:]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = _VARIANT_SUFFIX_RE.sub("", text)
    if text.startswith("claude-"):
        text = text.replace(".", "-")
    return text


def same_model_family(left: object, right: object) -> bool:
    """Whether two ids name the same model once relay decorations are removed."""

    left_id = model_family_id(left)
    return bool(left_id) and left_id == model_family_id(right)
