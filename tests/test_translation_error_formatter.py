"""Friendly translation error messages for each provider."""

import pytest

from src.utils.localization import SUPPORTED_UI_LANGUAGES
from src.utils.translation_error_formatter import _TEXTS, format_translation_error

_HUNYUAN_AUTH_FAILURE = (
    "Error code: 401 - {'error': {'message': 'Incorrect API key provided', "
    "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}"
)


@pytest.mark.parametrize("language", SUPPORTED_UI_LANGUAGES)
def test_hunyuan_auth_failure_explains_tokenhub_keys(language):
    """The old Hunyuan console keys stopped working when TokenHub took over."""

    error = format_translation_error(_HUNYUAN_AUTH_FAILURE, "hunyuan", language)

    assert error.category == "auth"
    hint = _TEXTS[language]["auth_hint_hunyuan"]
    assert hint in error.detailed_message
    assert hint != _TEXTS[language]["auth_hint_default"]
    assert "TokenHub" in hint


def test_hunyuan_auth_hint_is_localized_in_every_language():
    english = _TEXTS["en"]["auth_hint_hunyuan"]

    for language in SUPPORTED_UI_LANGUAGES:
        hint = _TEXTS[language].get("auth_hint_hunyuan", "")
        assert hint, language
        if language != "en":
            assert hint != english, language


def test_hunyuan_auth_hint_names_both_tokenhub_sites_and_the_retired_console():
    hint = _TEXTS["en"]["auth_hint_hunyuan"]

    assert "China" in hint
    assert "Global" in hint
    assert "retired Hunyuan console" in hint


def test_forbidden_hunyuan_request_uses_the_same_hint():
    error = format_translation_error("HTTP 403 Forbidden", "hunyuan", "zh-CN")

    assert error.category == "auth"
    assert _TEXTS["zh-CN"]["auth_hint_hunyuan"] in error.detailed_message


def test_other_providers_keep_their_own_auth_hint():
    error = format_translation_error(_HUNYUAN_AUTH_FAILURE, "deepseek", "en")

    assert error.category == "auth"
    assert _TEXTS["en"]["auth_hint_deepseek"] in error.detailed_message
    assert _TEXTS["en"]["auth_hint_hunyuan"] not in error.detailed_message
