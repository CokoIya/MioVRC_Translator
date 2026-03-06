"""Translator factory and basic config validation."""

from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from .anthropic_translator import AnthropicTranslator


def _require_text(value: str, label: str):
    if not str(value or "").strip():
        raise ValueError(f"{label} 未设置")


def create_translator(config: dict) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    backend = trans_cfg.get("backend", "openai")

    if backend == "openai":
        c = trans_cfg.get("openai", {})
        _require_text(c.get("api_key", ""), "OpenAI API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=c.get("model", "gpt-4o-mini"),
            base_url=c.get("base_url", "https://api.openai.com/v1"),
        )

    if backend == "deepseek":
        c = trans_cfg.get("deepseek", {})
        _require_text(c.get("api_key", ""), "DeepSeek API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=c.get("model", "deepseek-chat"),
            base_url=c.get("base_url", "https://api.deepseek.com/v1"),
        )

    if backend == "qianwen":
        c = trans_cfg.get("qianwen", {})
        _require_text(c.get("api_key", ""), "Qianwen API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=c.get("model", "qwen-mt-turbo"),
            base_url=c.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            # Disable Qwen3 thinking mode for fast responses.
            # Qwen3 models reason by default; this bypasses that overhead.
            extra_body={"enable_thinking": False},
        )

    if backend == "anthropic":
        c = trans_cfg.get("anthropic", {})
        _require_text(c.get("api_key", ""), "Anthropic API Key")
        return AnthropicTranslator(
            api_key=c.get("api_key", "").strip(),
            model=c.get("model", "claude-haiku-4-5-20251001"),
        )

    if backend == "custom":
        c = trans_cfg.get("custom", {})
        _require_text(c.get("api_key", ""), "Custom API Key")
        _require_text(c.get("base_url", ""), "Custom Base URL")
        _require_text(c.get("model", ""), "Custom Model")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=c.get("model", "").strip(),
            base_url=c.get("base_url", "").strip(),
        )

    raise ValueError(f"未知翻译后端: {backend}")
