from __future__ import annotations

from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language


_RUNTIME_COMPONENT_TEXT_KEYS = {
    "TTS": "xtts_runtime_component_coqui",
    "sklearn": "xtts_runtime_component_sklearn",
    "av": "xtts_runtime_component_audio_decoder",
    "av.audio.resampler": "xtts_runtime_component_audio_resampler",
    "pypinyin": "xtts_runtime_component_chinese",
    "ko_speech_tools": "xtts_runtime_component_korean",
    "num2words": "xtts_runtime_component_numbers",
    "cutlet": "xtts_runtime_component_japanese",
    "fugashi": "xtts_runtime_component_japanese",
    "unidic_lite": "xtts_runtime_component_japanese",
    "mojimoji": "xtts_runtime_component_japanese",
}

_RUNTIME_COMPONENT_NAME_KEYS = {
    "Coqui TTS runtime": "xtts_runtime_component_coqui",
    "scikit-learn runtime": "xtts_runtime_component_sklearn",
    "MP3/audio decoder runtime": "xtts_runtime_component_audio_decoder",
    "MP3/audio resampler runtime": "xtts_runtime_component_audio_resampler",
    "Chinese text frontend": "xtts_runtime_component_chinese",
    "Korean text frontend": "xtts_runtime_component_korean",
    "multilingual number normalizer": "xtts_runtime_component_numbers",
    "Japanese text frontend": "xtts_runtime_component_japanese",
    "Japanese tokenizer": "xtts_runtime_component_japanese",
    "Japanese dictionary": "xtts_runtime_component_japanese",
    "Japanese normalizer": "xtts_runtime_component_japanese",
}


def localized_xtts_runtime_components(status: object, ui_language: object) -> str:
    """Return safe localized component names without exposing import errors."""

    language = normalize_ui_language(ui_language)
    missing_modules = tuple(getattr(status, "missing_modules", ()) or ())
    component_keys = [
        _RUNTIME_COMPONENT_TEXT_KEYS.get(
            str(module),
            "xtts_runtime_component_generic",
        )
        for module in missing_modules
    ]
    if not missing_modules:
        component_keys.extend(
            _RUNTIME_COMPONENT_NAME_KEYS.get(
                str(name),
                "xtts_runtime_component_generic",
            )
            for name in tuple(getattr(status, "missing_component_names", ()) or ())
            if not str(name).startswith("Coqui TTS import error:")
        )
    if getattr(status, "import_error", None) or any(
        str(name).startswith("Coqui TTS import error:")
        for name in tuple(getattr(status, "missing_component_names", ()) or ())
    ):
        component_keys.append("xtts_runtime_component_coqui")
    if not component_keys:
        component_keys.append("xtts_runtime_component_generic")

    component_names = list(dict.fromkeys(tr(language, key) for key in component_keys))
    return tr(language, "xtts_runtime_component_separator").join(component_names)


def localized_xtts_runtime_error_component(error: object, ui_language: object) -> str:
    """Classify a raw XTTS import/runtime error into localized safe copy."""

    language = normalize_ui_language(ui_language)
    message = str(error or "").casefold()
    if "sklearn" in message or "scikit-learn" in message:
        key = "xtts_runtime_component_sklearn"
    elif "av.audio.resampler" in message or "resampler" in message:
        key = "xtts_runtime_component_audio_resampler"
    elif any(token in message for token in ("pyav", "ffmpeg", "audio decoder", "no module named 'av'")):
        key = "xtts_runtime_component_audio_decoder"
    elif "pypinyin" in message or "chinese text frontend" in message:
        key = "xtts_runtime_component_chinese"
    elif "ko_speech_tools" in message or "korean text frontend" in message:
        key = "xtts_runtime_component_korean"
    elif "num2words" in message or "number normalizer" in message:
        key = "xtts_runtime_component_numbers"
    elif any(
        token in message
        for token in ("cutlet", "fugashi", "unidic", "mojimoji", "japanese text")
    ):
        key = "xtts_runtime_component_japanese"
    elif any(
        token in message
        for token in ("coqui", "tts.api", "no module named 'tts'", "voice cloning runtime")
    ):
        key = "xtts_runtime_component_coqui"
    else:
        key = "xtts_runtime_component_generic"
    return tr(language, key)
