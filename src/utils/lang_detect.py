import re

ZH_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
JA_RANGE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
KO_RANGE = re.compile(r"[\uac00-\ud7af]")
RU_RANGE = re.compile(r"[\u0400-\u04ff]")


def detect_language(text: str) -> str:
    """
    自动检测文本语言
    优先级: 日语 > 中文/韩语/俄语 > 英语

    使用字符计数和阈值判断，避免误判混合语言文本
    """
    if not text:
        return "en"

    ja = len(JA_RANGE.findall(text))
    zh = len(ZH_RANGE.findall(text))
    ko = len(KO_RANGE.findall(text))
    ru = len(RU_RANGE.findall(text))

    # 计算总字符数（排除空格）
    total_chars = len([c for c in text if not c.isspace()])
    if total_chars == 0:
        return "en"

    # 假名 (hiragana / katakana) 是日语独有，一旦出现就应判定为 ja，
    # 否则会被共享的 CJK 汉字（zh 阈值）抢先返回 "zh"。
    if ja > 0:
        return "ja"

    # 使用阈值判断（至少占30%才认为是该语言）
    threshold = 0.3

    if zh > 0 and zh / total_chars >= threshold:
        return "zh"
    if ko > 0 and ko / total_chars >= threshold:
        return "ko"
    if ru > 0 and ru / total_chars >= threshold:
        return "ru"

    total = zh + ko + ru
    if total == 0:
        return "en"
    if zh >= ko and zh >= ru:
        return "zh"
    if ko >= zh and ko >= ru:
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
