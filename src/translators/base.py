"""全翻訳バックエンドの抽象基底クラス  """

from abc import ABC, abstractmethod


class BaseTranslator(ABC):
    """全翻訳バックエンドに対する統一インターフェース  """

    @abstractmethod
    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """
        テキストを src_lang から tgt_lang に翻訳する  

        Args:
            text: 翻訳元テキスト  
            src_lang: ISO 639-1 ソース言語コード（例: "zh", "ja", "en"）  
            tgt_lang: ISO 639-1 ターゲット言語コード  

        Returns:
            翻訳後のテキスト文字列  
        """

    def _build_prompt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """翻訳用プロンプトを生成する  """
        lang_map = {
            "zh": "中文", "ja": "日本語", "en": "English",
            "ko": "한국어", "fr": "Français", "de": "Deutsch",
            "es": "Español",
        }
        src = lang_map.get(src_lang, src_lang)
        tgt = lang_map.get(tgt_lang, tgt_lang)
        return (
            f"以下の{src}テキストを{tgt}に翻訳してください。訳文のみ出力し、説明は不要です：\n{text}"
        )
