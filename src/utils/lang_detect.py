"""Unicode の文字範囲を使って簡易的に言語を判定する。"""

import re

ZH_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
JA_RANGE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
KO_RANGE = re.compile(r"[\uac00-\ud7af]")


def detect_language(text: str) -> str:
    """ISO 639-1 の言語コードを返す。  判定できない場合は `en` を返す。"""
    if not text:
        return "en"
    ja = len(JA_RANGE.findall(text))
    zh = len(ZH_RANGE.findall(text))
    ko = len(KO_RANGE.findall(text))
    total = ja + zh + ko
    if total == 0:
        return "en"
    if ja >= zh and ja >= ko:
        return "ja"
    if zh >= ja and zh >= ko:
        return "zh"
    return "ko"


LANG_NAMES = {
    "zh": "中文",
    "ja": "日本語",
    "en": "English",
    "ko": "한국어",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
}
