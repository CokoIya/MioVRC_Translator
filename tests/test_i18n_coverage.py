from src.ui_qt.main_window import MAIN_COPY
from src.ui_qt.settings_window import FIELD_HINTS, QT_SETTINGS_COPY
from src.utils.i18n import UI_TEXTS, tr
from src.utils.ui_config import (
    backend_region_for_ui_language,
    get_backend_region_base_url,
    get_output_format_2_options,
    get_output_format_options,
    get_qwen_translation_base_url,
    normalize_qwen_translation_region,
    normalize_backend_region,
    XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_EU,
    NVIDIA_TRANSLATION_BASE_URL,
)


SUPPORTED_UI_LANGUAGES = ("zh-CN", "en", "ja", "ru", "ko")


def test_global_ui_texts_cover_supported_languages():
    all_keys = set().union(*(texts.keys() for texts in UI_TEXTS.values()))

    for language in SUPPORTED_UI_LANGUAGES:
        missing = sorted(all_keys - set(UI_TEXTS[language]))
        assert missing == []


def test_settings_copy_tables_cover_supported_languages():
    for table in (MAIN_COPY, QT_SETTINGS_COPY, FIELD_HINTS):
        missing = {
            key: [language for language in SUPPORTED_UI_LANGUAGES if language not in values]
            for key, values in table.items()
            if any(language not in values for language in SUPPORTED_UI_LANGUAGES)
        }
        assert missing == {}


def test_status_running_is_localized_for_all_languages():
    for language in SUPPORTED_UI_LANGUAGES:
        assert tr(language, "status_running") != "status_running"
        assert tr(language, "tts_engine_unavailable", engine="edge") != "tts_engine_unavailable"


def test_high_visibility_qt_messages_are_localized():
    for key in (
        "listen_prefix",
        "settings_update_available_message",
        "settings_save_failed_message",
        "recognition_window_too_short",
        "tts_output_device_detected",
    ):
        table = MAIN_COPY.get(key) or QT_SETTINGS_COPY.get(key)
        assert table is not None
        assert all(table[language] for language in SUPPORTED_UI_LANGUAGES)


def test_output_format_options_are_localized():
    english_labels = [label for label, _code in get_output_format_options("en")]
    russian_labels = [label for label, _code in get_output_format_options("ru")]
    korean_labels = [label for label, _code in get_output_format_2_options("ko")]

    assert "Translation only" in english_labels
    assert "Только перевод" in russian_labels
    assert "번역2 끄기" in korean_labels


def test_qwen_translation_region_helpers():
    assert normalize_qwen_translation_region("intl") == "singapore"
    assert normalize_qwen_translation_region("china") == "china_mainland"
    assert get_qwen_translation_base_url("singapore").startswith("https://dashscope-intl.")
    assert normalize_backend_region("xiaomi", "token-plan-sgp") == "singapore_cluster"
    assert get_backend_region_base_url("xiaomi", "europe_cluster") == XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_EU
    assert backend_region_for_ui_language("xiaomi", "zh-CN") == "china_cluster"
    assert get_backend_region_base_url("nvidia", "hosted") == NVIDIA_TRANSLATION_BASE_URL
