from __future__ import annotations

from src.translators.base import BaseTranslator
from src.translators.factory import FallbackTranslator


class _Resource:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _Translator(BaseTranslator):
    def __init__(self) -> None:
        super().__init__()
        self._client = _Resource()

    def translate(self, text, src_lang, tgt_lang, context_source="default"):
        del src_lang, tgt_lang, context_source
        return text


def test_base_translator_closes_owned_client_once():
    translator = _Translator()
    resource = translator._client

    translator.close()
    translator.close()

    assert resource.close_calls == 1


def test_fallback_translator_closes_primary_and_created_fallbacks_once():
    primary = _Translator()
    fallback = _Translator()
    translator = FallbackTranslator(primary, [])
    translator._fallbacks["secondary"] = fallback

    translator.close()
    translator.close()

    assert primary._client.close_calls == 1
    assert fallback._client.close_calls == 1
