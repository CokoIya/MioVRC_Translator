from __future__ import annotations

from collections.abc import Mapping

_POLITENESS_STYLE = {
    "casual": "casual and friendly",
    "neutral": "natural and conversational",
    "polite": "polite and warm",
    "formal": "formal, clear, and respectful",
}

_TONE_STYLE = {
    "natural": "natural",
    "warm": "warm and caring",
    "playful": "playful and expressive",
    "soft": "soft and gentle",
    "cool": "calm and cool",
    "clear": "clear and composed",
    "energetic": "energetic and lively",
}


def build_qwen_tts_persona_instructions(config: Mapping[str, object] | None) -> str:
    """Build concise Qwen instruct TTS reading guidance from translation social config."""
    if not isinstance(config, Mapping):
        return ""
    trans_cfg = config.get("translation", {})
    if not isinstance(trans_cfg, Mapping):
        return ""
    social_cfg = trans_cfg.get("social", {})
    if not isinstance(social_cfg, Mapping):
        return ""
    mode = str(social_cfg.get("mode", "standard") or "standard").strip().lower()
    if mode != "roleplay":
        return ""

    persona_name = _short_text(social_cfg.get("persona_name"), limit=48)
    persona_prompt = _short_text(social_cfg.get("persona_prompt"), limit=240)
    tone = _TONE_STYLE.get(
        str(social_cfg.get("tone", "natural") or "natural").strip().lower(),
        _short_text(social_cfg.get("tone"), limit=48) or "natural",
    )
    politeness = _POLITENESS_STYLE.get(
        str(social_cfg.get("politeness", "neutral") or "neutral").strip().lower(),
        _short_text(social_cfg.get("politeness"), limit=48)
        or "natural and conversational",
    )

    parts = [
        "Read the text aloud exactly as written; do not add, remove, or rewrite words.",
        "Use natural live-chat pacing and express the emotion implied by the text.",
        f"Speaking style: {tone}; register: {politeness}.",
    ]
    if persona_name:
        parts.append(f"Persona: {persona_name}.")
    if persona_prompt:
        parts.append(f"Persona guidance: {persona_prompt}")
    return " ".join(parts)


def qwen_tts_model_supports_instructions(model: object) -> bool:
    return "instruct" in str(model or "").strip().lower()


def _short_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."
