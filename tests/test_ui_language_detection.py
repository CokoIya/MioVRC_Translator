from contextlib import contextmanager
from email.message import Message
import io

from src.utils import ui_language_detection


class _Response:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.headers = Message()
        self.headers["Content-Length"] = str(len(payload))

    def read(self, size=-1):
        return self._stream.read(size)


def test_windows_display_language_takes_priority_over_regional_locale(monkeypatch):
    monkeypatch.setattr(
        ui_language_detection,
        "_windows_display_language_tag",
        lambda: "ja-JP",
    )
    monkeypatch.setattr(
        ui_language_detection.locale,
        "getlocale",
        lambda: ("ru_RU", "UTF-8"),
    )
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")

    assert ui_language_detection._language_from_locale() == "ja"


def test_supported_windows_display_language_tags_are_normalized():
    expected = {
        "zh-CN": "zh-CN",
        "zh_Hant-TW": "zh-CN",
        "en-US": "en",
        "ja-JP": "ja",
        "ru-RU": "ru",
        "ko-KR": "ko",
        "de-DE": "en",
    }

    for locale_name, language in expected.items():
        assert ui_language_detection._language_from_tag(locale_name) == language


def test_missing_locale_information_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(
        ui_language_detection,
        "_windows_display_language_tag",
        lambda: None,
    )
    monkeypatch.setattr(
        ui_language_detection.locale,
        "getlocale",
        lambda: (None, None),
    )
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)

    assert ui_language_detection._language_from_locale() == "en"


def test_first_run_language_is_detected_but_saved_manual_language_is_preserved(
    monkeypatch,
):
    monkeypatch.setattr(
        ui_language_detection,
        "detect_initial_ui_language",
        lambda **_kwargs: "ko",
    )
    first_run = {"ui": {"language": "", "language_source": "auto"}}

    assert ui_language_detection.bootstrap_ui_language(first_run, prefer_auto=True)
    assert first_run["ui"]["language"] == "ko"
    assert first_run["ui"]["language_source"] == "auto"

    saved = {"ui": {"language": "ja", "language_source": "manual"}}
    assert not ui_language_detection.bootstrap_ui_language(saved, prefer_auto=True)
    assert saved["ui"]["language"] == "ja"
    assert saved["ui"]["language_source"] == "manual"


def test_ip_language_lookup_is_bounded_and_opt_in(monkeypatch):
    calls = []

    @contextmanager
    def open_response(*args, **kwargs):
        calls.append((args, kwargs))
        yield _Response(b'{"country_code":"JP"}')

    monkeypatch.setattr(
        ui_language_detection,
        "open_trusted_https_url",
        open_response,
    )
    monkeypatch.setattr(ui_language_detection, "_language_from_locale", lambda: "en")

    assert ui_language_detection.detect_initial_ui_language() == "en"
    assert calls == []
    assert (
        ui_language_detection.detect_initial_ui_language(allow_ip_lookup=True)
        == "ja"
    )
    assert calls[0][1]["trusted_hosts"] == frozenset({"ipapi.co"})
    assert calls[0][1]["max_redirects"] == 2


def test_ip_language_lookup_fails_closed_on_oversized_response(monkeypatch):
    @contextmanager
    def open_response(*_args, **_kwargs):
        yield _Response(b"x" * (ui_language_detection._MAX_IP_LOOKUP_BYTES + 1))

    monkeypatch.setattr(
        ui_language_detection,
        "open_trusted_https_url",
        open_response,
    )

    assert ui_language_detection._language_from_ip() is None
