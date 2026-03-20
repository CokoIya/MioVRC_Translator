import re

ZH_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
JA_RANGE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
KO_RANGE = re.compile(r"[\uac00-\ud7af]")
RU_RANGE = re.compile(r"[\u0400-\u04ff]")


def detect_language(text: str) -> str:
    if not text:
        return "en"
    ja = len(JA_RANGE.findall(text))
    zh = len(ZH_RANGE.findall(text))
    ko = len(KO_RANGE.findall(text))
    ru = len(RU_RANGE.findall(text))
    total = ja + zh + ko + ru
    if total == 0:
        return "en"
    if ja >= zh and ja >= ko and ja >= ru:
        return "ja"
    if zh >= ja and zh >= ko and zh >= ru:
        return "zh"
    if ko >= ja and ko >= zh and ko >= ru:
        return "ko"
    if ru > 0:
        return "ru"
    return "en"


LANG_NAMES = {
    "zh": "\u4e2d\u6587",
    "ja": "\u65e5\u672c\u8a9e",
    "en": "English",
    "ko": "\ud55c\uad6d\uc5b4",
    "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439",
    "fr": "Fran\u00e7ais",
    "de": "Deutsch",
    "es": "Espa\u00f1ol",
    "pt": "Portugu\u00eas",
    "it": "Italiano",
    "th": "\u0e44\u0e17\u0e22",
    "vi": "Ti\u1ebfng Vi\u1ec7t",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
}
