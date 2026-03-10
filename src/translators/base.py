from abc import ABC, abstractmethod


class BaseTranslator(ABC):

    @abstractmethod
    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        pass

    def _build_prompt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        lang_map = {
            "zh": "中文", "ja": "日本語", "en": "English",
            "ko": "한국어", "fr": "Français", "de": "Deutsch",
            "es": "Español", "ru": "Русский",
        }
        src = lang_map.get(src_lang, src_lang)
        tgt = lang_map.get(tgt_lang, tgt_lang)
        return (
            f"以下の{src}テキストを{tgt}に翻訳してください。訳文のみ出力し、説明は不要です:\n{text}"
        )
