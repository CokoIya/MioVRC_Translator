from email.message import Message
import io
import socket
import urllib.error

import pytest

from src.utils import provider_network, secure_http


class _Response:
    def __init__(self, payload=b"{}", *, url="https://trusted.example/data", headers=None):
        self._stream = io.BytesIO(payload)
        self._url = url
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.closed = False

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True


class _Opener:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _redirect(source: str, target: str):
    headers = Message()
    headers["Location"] = target
    return urllib.error.HTTPError(source, 302, "Found", headers, io.BytesIO())


def test_urllib_redirect_is_validated_before_following(monkeypatch):
    opener = _Opener(
        [_redirect("https://trusted.example/data", "https://evil.example/payload")]
    )
    monkeypatch.setattr(secure_http.urllib.request, "build_opener", lambda *_: opener)

    with pytest.raises(RuntimeError, match="trusted HTTPS source"):
        with secure_http.open_trusted_https_url(
            "https://trusted.example/data",
            trusted_hosts={"trusted.example"},
            timeout=1,
            label="test",
        ):
            pass

    assert len(opener.requests) == 1


def test_urllib_trusted_redirect_uses_identity_encoding_and_closes(monkeypatch):
    response = _Response(
        b"payload",
        url="https://cdn.example/payload",
        headers={"Content-Length": "7"},
    )
    opener = _Opener(
        [
            _redirect("https://trusted.example/data", "https://cdn.example/payload"),
            response,
        ]
    )
    monkeypatch.setattr(secure_http.urllib.request, "build_opener", lambda *_: opener)

    with secure_http.open_trusted_https_url(
        "https://trusted.example/data",
        trusted_hosts={"trusted.example", "cdn.example"},
        timeout=2,
        label="test",
        headers={"Authorization": "secret"},
    ) as opened:
        assert secure_http.read_bounded_response(opened, limit=8, label="test") == b"payload"

    assert response.closed
    first, second = [entry[0] for entry in opener.requests]
    assert first.get_header("Accept-encoding") == "identity"
    assert first.get_header("Authorization") == "secret"
    assert second.get_header("Accept-encoding") == "identity"
    assert second.get_header("Authorization") is None


def test_bounded_urllib_reader_rejects_compression_and_length_mismatch():
    compressed = _Response(b"data", headers={"Content-Encoding": "gzip"})
    with pytest.raises(RuntimeError, match="Content-Encoding"):
        secure_http.read_bounded_response(compressed, limit=8, label="test")

    truncated = _Response(b"data", headers={"Content-Length": "5"})
    with pytest.raises(RuntimeError, match="does not match"):
        secure_http.read_bounded_response(truncated, limit=8, label="test")


@pytest.mark.parametrize(
    "url",
    (
        "https://api.example.com/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1/",
        "http://[::1]:8000/v1",
    ),
)
def test_api_base_url_accepts_https_and_loopback_http(url):
    assert secure_http.validate_api_base_url(url).endswith("/v1")


@pytest.mark.parametrize(
    "url",
    (
        "http://api.example.com/v1",
        "http://192.168.1.10:8000/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1?key=secret",
        "https://api.example.com/v1#fragment",
        "https://api.example.com:99999/v1",
        "file:///tmp/api",
    ),
)
def test_api_base_url_rejects_credential_leak_and_malformed_targets(url):
    with pytest.raises(ValueError):
        secure_http.validate_api_base_url(url)


def test_keyless_local_service_can_explicitly_allow_literal_private_http():
    assert secure_http.validate_api_base_url(
        "http://192.168.1.10:8000/v1",
        allow_private_http=True,
    ) == "http://192.168.1.10:8000/v1"
    with pytest.raises(ValueError):
        secure_http.validate_api_base_url(
            "http://translator.lan:8000/v1",
            allow_private_http=True,
        )


@pytest.mark.parametrize(
    "url",
    (
        "http://mio.localhost:11434/v1",
        "http://100.100.100.100:11434/v1",
    ),
)
def test_local_service_can_allow_reserved_local_http_hosts(url):
    assert secure_http.validate_api_base_url(
        url,
        allow_private_http=True,
    ) == url


@pytest.mark.parametrize(
    "host",
    ("host.docker.internal", "gateway.docker.internal"),
)
def test_reserved_docker_http_host_requires_safe_dns_results(monkeypatch, host):
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("192.168.65.2", 0),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("100.64.0.3", 0),
            ),
        ],
    )
    url = f"http://{host}:11434/v1"

    assert secure_http.validate_api_base_url(
        url,
        allow_private_http=True,
    ) == url


@pytest.mark.parametrize(
    "addresses",
    (
        ("8.8.8.8",),
        ("192.168.65.2", "8.8.8.8"),
    ),
)
def test_reserved_docker_http_host_rejects_public_dns_results(
    monkeypatch,
    addresses,
):
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 0),
            )
            for address in addresses
        ],
    )

    with pytest.raises(ValueError, match="must use HTTPS") as exc_info:
        secure_http.validate_api_base_url(
            "http://host.docker.internal:11434/v1",
            label="Translation API",
            allow_private_http=True,
        )

    assert "host.docker.internal" not in str(exc_info.value)
