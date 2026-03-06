"""設定に応じた翻訳バックエンドインスタンスを生成する。"""

from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from .anthropic_translator import AnthropicTranslator


def create_translator(config: dict) -> BaseTranslator:
    """
    config['translation']['backend'] で指定されたバックエンドを生成する。
    対応バックエンド: openai, deepseek, qianwen, anthropic, custom
    """
    trans_cfg = config.get("translation", {})
    backend = trans_cfg.get("backend", "openai")

    if backend == "openai":
        c = trans_cfg.get("openai", {})
        return OpenAITranslator(
            api_key=c.get("api_key", ""),
            model=c.get("model", "gpt-4o-mini"),
            base_url=c.get("base_url", "https://api.openai.com/v1"),
        )

    elif backend == "deepseek":
        c = trans_cfg.get("deepseek", {})
        return OpenAITranslator(
            api_key=c.get("api_key", ""),
            model=c.get("model", "deepseek-chat"),
            base_url=c.get("base_url", "https://api.deepseek.com/v1"),
        )

    elif backend == "qianwen":
        c = trans_cfg.get("qianwen", {})
        return OpenAITranslator(
            api_key=c.get("api_key", ""),
            model=c.get("model", "qwen-mt-turbo"),
            base_url=c.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )

    elif backend == "anthropic":
        c = trans_cfg.get("anthropic", {})
        return AnthropicTranslator(
            api_key=c.get("api_key", ""),
            model=c.get("model", "claude-haiku-4-5-20251001"),
        )

    elif backend == "custom":
        c = trans_cfg.get("custom", {})
        return OpenAITranslator(
            api_key=c.get("api_key", ""),
            model=c.get("model", ""),
            base_url=c.get("base_url", ""),
        )

    else:
        raise ValueError(f"不明な翻訳バックエンド: {backend}")
