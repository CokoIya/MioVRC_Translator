from __future__ import annotations

from src.translators.base import BaseTranslator, TranslationContextStore
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


def test_translation_context_store_bounds_distinct_session_keys():
    store = TranslationContextStore(max_context_keys=3)

    for session_id in range(8):
        store.remember(
            session_id=session_id,
            text=f"source-{session_id}",
            translated=f"target-{session_id}",
            src_lang="en",
            tgt_lang="ja",
            context_source="mic",
        )

    retained_keys = set(store._recent) | set(store._pending_sources)
    assert len(retained_keys) == 3
    assert {key[0] for key in retained_keys} == {5, 6, 7}


def test_base_translator_close_clears_owned_context_history():
    translator = _Translator()
    translator._context_store.remember(
        session_id="session",
        text="hello",
        translated="こんにちは",
        src_lang="en",
        tgt_lang="ja",
        context_source="mic",
    )
    assert translator._context_store._recent

    translator.close()

    assert translator._context_store._recent == {}
    assert translator._context_store._pending_sources == {}
