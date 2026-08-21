from src.utils.localization import (
    format_locale_number,
    format_locale_percent,
    normalize_ui_language,
    translate_key_catalog,
    translate_language_catalog,
    validate_language_catalog,
)
from src.utils.ui_config import get_backend_label, get_ui_language


def test_language_normalization_supports_common_platform_locale_names():
    assert normalize_ui_language("zh_Hans_CN") == "zh-CN"
    assert normalize_ui_language("en-US") == "en"
    assert normalize_ui_language("ja-JP") == "ja"
    assert normalize_ui_language("ru-RU") == "ru"
    assert normalize_ui_language("ko-KR") == "ko"
    assert normalize_ui_language("unsupported") == "zh-CN"


def test_catalog_lookup_uses_english_then_default_fallback():
    language_catalog = {
        "zh-CN": {"default_only": "默认", "shared": "中文"},
        "en": {"english_only": "English", "shared": "English shared"},
        "ja": {"shared": "日本語"},
    }
    key_catalog = {
        "hello": {"en": "Hello {name}", "zh-CN": "你好，{name}"},
    }

    assert translate_language_catalog(language_catalog, "ko", "english_only") == "English"
    assert translate_language_catalog(language_catalog, "ko", "default_only") == "默认"
    assert translate_language_catalog(language_catalog, "ja", "missing") == "missing"
    assert translate_key_catalog(key_catalog, "ru", "hello", name="Mio") == "Hello Mio"
    assert translate_key_catalog({"only_ja": {"ja": "日本語"}}, "ru", "only_ja") == "only_ja"


def test_catalog_formatting_defect_does_not_crash_ui():
    catalog = {"en": {"broken": "Hello {name}"}}

    assert translate_language_catalog(catalog, "en", "broken") == "Hello {name}"


def test_catalog_validation_detects_missing_empty_malformed_and_placeholder_errors():
    catalog = {
        "en": {
            "ok": "Hello {name}",
            "empty": "English",
            "broken": "Broken {name}",
        },
        "zh-CN": {
            "ok": "你好 {name}",
            "empty": "",
            "broken": "错误 {other}",
        },
        "ja": {"ok": "こんにちは {name}", "empty": "日本語", "broken": "{"},
        "ru": {"ok": "Привет {name}", "empty": "Русский", "broken": "{name}"},
        "ko": {"ok": "안녕하세요 {name}", "empty": "한국어"},
    }

    issues = validate_language_catalog(catalog)
    issue_codes = {(issue.code, issue.key, issue.language) for issue in issues}

    assert ("empty", "empty", "zh-CN") in issue_codes
    assert ("placeholder_mismatch", "broken", "zh-CN") in issue_codes
    assert ("malformed", "broken", "ja") in issue_codes
    assert ("missing", "broken", "ko") in issue_codes


def test_locale_number_formatting_is_process_locale_independent():
    assert format_locale_number(12345.5, "en", decimals=1, grouping=True) == "12,345.5"
    assert format_locale_number(12345.5, "ru", decimals=1, grouping=True) == "12\u202f345,5"
    assert format_locale_percent(87.5, "ja", decimals=1) == "87.5%"


def test_ui_config_normalizes_platform_locale_variants():
    assert get_ui_language({"ui": {"language": "ja-JP"}}) == "ja"
    assert get_ui_language({"ui": {"language": "ru_RU"}}) == "ru"
    assert get_ui_language({"ui": {"language": "ko-KR"}}) == "ko"


def test_backend_labels_localize_qualifiers_without_changing_backend_codes():
    assert get_backend_label("openai_compatible", "zh-CN") == "GPT 兼容服务"
    assert get_backend_label("local_ai", "ja") == "ローカル AI"
    assert get_backend_label("deepl", "ru") == "DeepL (бесплатный тариф)"
    assert get_backend_label("anthropic_compatible", "ko") == "Claude 호환 서비스"
    assert get_backend_label("microsoft_edge_web", "zh-CN") == "无限高质免费翻译(强推)"
    assert get_backend_label("openai_compatible") == "GPT Compatible"
