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
