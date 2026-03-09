"""翻訳バックエンドの生成と基本的な設定検証を行う  """

from .anthropic_translator import AnthropicTranslator
from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from src.utils.ui_config import (
    DEFAULT_BACKEND,
    get_backend_spec,
    normalize_backend,
)


def _require_text(value: str, label: str):
    if not str(value or "").strip():
        raise ValueError(f"{label} 未设置")


def create_translator(config: dict) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    backend = normalize_backend(trans_cfg.get("backend", DEFAULT_BACKEND))
    spec = get_backend_spec(backend)

    if backend == "openai":
        c = trans_cfg.get("openai", {})
        _require_text(c.get("api_key", ""), "OpenAI API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=str(spec["model"]),
            base_url=str(spec["base_url"]),
        )

    if backend == "deepseek":
        c = trans_cfg.get("deepseek", {})
        _require_text(c.get("api_key", ""), "DeepSeek API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=str(spec["model"]),
            base_url=str(spec["base_url"]),
        )

    if backend == "qianwen":
        c = trans_cfg.get("qianwen", {})
        _require_text(c.get("api_key", ""), "Qianwen API Key")
        return OpenAITranslator(
            api_key=c.get("api_key", "").strip(),
            model=str(spec["model"]),
            base_url=str(spec["base_url"]),
            extra_body=dict(spec.get("extra_body", {})),
        )

    if backend == "anthropic":
        c = trans_cfg.get("anthropic", {})
        _require_text(c.get("api_key", ""), "Anthropic API Key")
        return AnthropicTranslator(
            api_key=c.get("api_key", "").strip(),
            model=str(spec["model"]),
        )

    raise ValueError(f"未知翻译后端: {backend}")
