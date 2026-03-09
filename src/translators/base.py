"""翻訳バックエンド共通の抽象基底クラス  """

from abc import ABC, abstractmethod


class BaseTranslator(ABC):
    """全翻訳バックエンドの共通インターフェース  """

    @abstractmethod
    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """
          text   を   src  lang   から   tgt  lang   へ翻訳する  

        Args  
            text   翻訳元のテキスト  
            src  lang   ISO 639  1 の入力言語コード    例       zh          ja          en      
            tgt  lang   ISO 639  1 の出力言語コード  

        Returns  
            翻訳後のテキスト  
        """

    def _build_prompt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """翻訳用プロンプトを生成する  """
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
