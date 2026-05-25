from __future__ import annotations

import re


BAD_PREFIXES = (
    "Transcription:",
    "Transcript:",
    "The transcription is:",
    "Japanese transcription:",
    "文字起こし：",
    "文字起こし:",
    "転写：",
    "転写:",
    "音声認識結果：",
    "音声認識結果:",
)

BAD_PHRASES = (
    "聞き取れません",
    "聞き取れなかった",
    "音声がありません",
    "認識できません",
    "can't transcribe",
    "cannot transcribe",
    "no clear speech",
)

_CJK_SCRIPT_RANGE = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_CJK_CLOSE_PUNCT = r"\u3001\u3002\uff0c\uff01\uff1f\uff1b\uff1a\uff09\uff3d\uff5d\u300d\u300f"
_CJK_OPEN_PUNCT = r"\uff08\uff3b\uff5b\u300c\u300e"


def normalize_spoken_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = re.sub(
        rf"(?<=[{_CJK_SCRIPT_RANGE}])\s+(?=[{_CJK_SCRIPT_RANGE}])",
        "",
        normalized,
    )
    normalized = re.sub(rf"\s+(?=[{_CJK_CLOSE_PUNCT}])", "", normalized)
    normalized = re.sub(rf"(?<=[{_CJK_OPEN_PUNCT}])\s+", "", normalized)
    return normalized.strip()


def clean_asr_text(text: str) -> str:
    cleaned = normalize_spoken_text(text)
    if not cleaned:
        return ""

    for prefix in BAD_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    cleaned = cleaned.strip("「」『』\"'` \n\r\t")
    lowered = cleaned.lower()
    for phrase in BAD_PHRASES:
        if phrase.lower() in lowered:
            return ""
    return normalize_spoken_text(cleaned)
