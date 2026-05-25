from __future__ import annotations

import ctypes
import locale
import logging

logger = logging.getLogger(__name__)

# Windows primary language IDs
_LANG_CHINESE = 0x0004
_LANG_JAPANESE = 0x0011
_LANG_KOREAN = 0x0012
_LANG_RUSSIAN = 0x0019
_LANG_FRENCH = 0x000C
_LANG_GERMAN = 0x0007
_LANG_SPANISH = 0x000A
_LANG_PORTUGUESE = 0x0016
_LANG_ITALIAN = 0x0010

_PRIMARY_TO_CODE: dict[int, str] = {
    _LANG_CHINESE: "zh",
    _LANG_JAPANESE: "ja",
    _LANG_KOREAN: "ko",
    _LANG_RUSSIAN: "ru",
    _LANG_FRENCH: "fr",
    _LANG_GERMAN: "de",
    _LANG_SPANISH: "es",
    _LANG_PORTUGUESE: "pt",
    _LANG_ITALIAN: "it",
}

# Languages that use SenseVoice (Chinese/Cantonese)
SENSEVOICE_LANGUAGES = frozenset({"zh", "yue"})
WEBSPEECH_ENGINE = "webspeech"
SENSEVOICE_ENGINE = "sensevoice-small"


def get_system_language() -> str:
    """Return the two-letter primary language code of the system UI language."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary = lang_id & 0x3FF
        code = _PRIMARY_TO_CODE.get(primary)
        if code:
            logger.debug("System language detected via WinAPI: %s (LANGID=0x%04X)", code, lang_id)
            return code
    except Exception:
        logger.debug("WinAPI language detection failed, falling back to locale module", exc_info=True)

    try:
        loc = locale.getdefaultlocale()[0] or ""
        code = loc[:2].lower() if len(loc) >= 2 else "en"
        logger.debug("System language detected via locale module: %s", code)
        return code
    except Exception:
        logger.debug("locale module language detection failed", exc_info=True)

    return "en"


def select_default_asr_engine() -> str:
    """Return the recommended ASR engine ID based on system language."""
    lang = get_system_language()
    if lang in SENSEVOICE_LANGUAGES:
        return SENSEVOICE_ENGINE
    return WEBSPEECH_ENGINE


def is_chinese_system() -> bool:
    return get_system_language() in SENSEVOICE_LANGUAGES
