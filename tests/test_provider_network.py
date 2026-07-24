from __future__ import annotations

import socket
import ssl
from types import SimpleNamespace

import pytest
import requests

import src.utils.provider_network as provider_network
from src.utils.provider_network import (
    SystemTrustHTTPAdapter,
    configure_requests_session_for_url,
    direct_connection_ssl_context,
    should_bypass_environment_proxies,
    should_retry_requests_attempt,
)


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost:11434/v1",
        "http://mio.localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.1.20:11434/v1",
        "http://100.100.100.100:11434/v1",
        "https://10.0.0.12:8443/v1",
        "http://[::1]:1234/v1",
        "https://[fe80::1]:8443/v1",
    ),
)
def test_local_provider_endpoints_bypass_environment_proxies(url):
    assert should_bypass_environment_proxies(url) is True


@pytest.mark.parametrize(
    "url",
    (
        "https://api.openai.com/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://8.8.8.8/v1",
        "not-a-url",
        "",
    ),
)
def test_public_provider_endpoints_keep_environment_proxy_support(url):
    assert should_bypass_environment_proxies(url) is False


def _docker_dns_results(*addresses: str):
    results = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
        results.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return results


@pytest.mark.parametrize(
    "host",
    ("host.docker.internal", "gateway.docker.internal"),
)
def test_reserved_docker_hosts_bypass_only_for_all_local_dns_results(
    monkeypatch,
    host,
):
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _docker_dns_results(
            "127.0.0.1",
            "192.168.65.2",
            "169.254.10.20",
            "100.64.0.4",
            "::1",
            "fe80::1",
        ),
    )

    assert should_bypass_environment_proxies(f"http://{host}:11434/v1") is True


@pytest.mark.parametrize(
    "addresses",
    (
        ("8.8.8.8",),
        ("192.168.65.2", "8.8.8.8"),
    ),
)
def test_reserved_docker_hosts_reject_public_or_mixed_dns_results(
    monkeypatch,
    addresses,
):
    monkeypatch.setattr(
        provider_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _docker_dns_results(*addresses),
    )

    assert (
        should_bypass_environment_proxies(
            "http://host.docker.internal:11434/v1"
        )
        is False
    )


def test_reserved_docker_host_resolution_failure_is_not_treated_as_local(
    monkeypatch,
):
    def fail_resolution(*_args, **_kwargs):
        raise socket.gaierror("name unavailable")

    monkeypatch.setattr(provider_network.socket, "getaddrinfo", fail_resolution)

    assert (
        should_bypass_environment_proxies(
            "http://gateway.docker.internal:11434/v1"
        )
        is False
    )


@pytest.mark.parametrize("status_code", (408, 429, 500, 502, 503, 504, 599))
def test_retry_classifier_accepts_transient_http_statuses(status_code):
    assert should_retry_requests_attempt(RuntimeError("provider failed"), status_code=status_code)


@pytest.mark.parametrize("status_code", (400, 401, 403, 404, 409, 422, 456))
def test_retry_classifier_rejects_permanent_http_statuses(status_code):
    assert not should_retry_requests_attempt(
        RuntimeError("provider rejected request"),
        status_code=status_code,
    )


@pytest.mark.parametrize(
    "error",
    (
        requests.exceptions.ConnectTimeout("connect timeout"),
        requests.exceptions.ReadTimeout("read timeout"),
        requests.exceptions.ConnectionError("connection reset"),
        requests.exceptions.ChunkedEncodingError("truncated response"),
    ),
)
def test_retry_classifier_accepts_transport_failures(error):
    assert should_retry_requests_attempt(error)


def test_retry_classifier_rejects_tls_validation_and_unknown_failures():
    assert not should_retry_requests_attempt(
        requests.exceptions.SSLError("certificate verify failed")
    )
    assert not should_retry_requests_attempt(ValueError("invalid provider payload"))


def test_retry_classifier_accepts_transient_tls_interruption():
    assert should_retry_requests_attempt(
        requests.exceptions.SSLError("EOF occurred in violation of protocol")
    )


def test_direct_requests_session_preserves_private_ca_bundle(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "C:/certs/private-ca.pem")
    session = requests.Session()

    configure_requests_session_for_url(session, "https://10.0.0.12:8443/v1")

    assert session.trust_env is False
    assert session.verify == "C:/certs/private-ca.pem"


def test_direct_httpx_context_preserves_environment_ca(monkeypatch):
    class FakeSSLContext:
        def __init__(self):
            self.loaded = []

        def load_verify_locations(self, *, cafile=None, capath=None):
            self.loaded.append((cafile, capath))

    expected = FakeSSLContext()
    monkeypatch.setattr(provider_network, "_truststore", None)
    monkeypatch.setenv("SSL_CERT_FILE", "C:/certs/private-httpx-ca.pem")
    monkeypatch.setattr(
        "src.utils.provider_network.ssl.create_default_context",
        lambda: expected,
    )

    assert (
        direct_connection_ssl_context("https://192.168.1.20:8443/v1")
        is expected
    )
    assert expected.loaded == [("C:/certs/private-httpx-ca.pem", None)]


def test_public_httpx_https_uses_native_trust_and_keeps_custom_ca(
    monkeypatch,
    tmp_path,
):
    created = []

    class FakeSSLContext:
        def __init__(self, protocol):
            self.protocol = protocol
            self.loaded = []
            created.append(self)

        def load_verify_locations(self, *, cafile=None, capath=None):
            self.loaded.append((cafile, capath))

    monkeypatch.setattr(
        provider_network,
        "_truststore",
        SimpleNamespace(SSLContext=FakeSSLContext),
    )
    custom_ca = tmp_path / "private-ca.pem"
    custom_ca.write_text("test CA", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(custom_ca))

    context = direct_connection_ssl_context("https://llamacpp.example/v1")

    assert context is created[0]
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert context.loaded == [(str(custom_ca), None)]


def test_public_httpx_https_system_trust_does_not_disable_proxy_support(
    monkeypatch,
):
    class FakeSSLContext:
        def __init__(self, protocol):
            self.protocol = protocol

    monkeypatch.setattr(
        provider_network,
        "_truststore",
        SimpleNamespace(SSLContext=FakeSSLContext),
    )

    assert should_bypass_environment_proxies("https://llamacpp.example/v1") is False
    assert isinstance(
        direct_connection_ssl_context("https://llamacpp.example/v1"),
        FakeSSLContext,
    )


def test_requests_session_mounts_native_trust_adapter_for_public_https(
    monkeypatch,
):
    created = []

    class FakeSSLContext:
        def __init__(self, protocol):
            self.protocol = protocol
            created.append(self)

    monkeypatch.setattr(
        provider_network,
        "_truststore",
        SimpleNamespace(SSLContext=FakeSSLContext),
    )
    session = requests.Session()
    try:
        configure_requests_session_for_url(
            session,
            "https://translate.example/v1",
        )
        adapter = session.get_adapter("https://translate.example/v1")
        prepared = requests.Request(
            "GET",
            "https://translate.example/v1",
        ).prepare()
        _host, pool_kwargs = adapter.build_connection_pool_key_attributes(
            prepared,
            True,
        )

        assert session.trust_env is True
        assert isinstance(adapter, SystemTrustHTTPAdapter)
        assert pool_kwargs["ssl_context"] is created[0]
        assert pool_kwargs["cert_reqs"] == "CERT_REQUIRED"
        assert "ca_certs" not in pool_kwargs
    finally:
        session.close()


def test_requests_native_trust_adapter_adds_explicit_ca_bundle(
    monkeypatch,
    tmp_path,
):
    created = []

    class FakeSSLContext:
        def __init__(self, protocol):
            self.protocol = protocol
            self.loaded = []
            created.append(self)

        def load_verify_locations(self, *, cafile=None, capath=None):
            self.loaded.append((cafile, capath))

    monkeypatch.setattr(
        provider_network,
        "_truststore",
        SimpleNamespace(SSLContext=FakeSSLContext),
    )
    custom_ca = tmp_path / "requests-private-ca.pem"
    custom_ca.write_text("test CA", encoding="utf-8")
    adapter = SystemTrustHTTPAdapter()
    try:
        prepared = requests.Request(
            "GET",
            "https://llamacpp.example/v1",
        ).prepare()
        _host, pool_kwargs = adapter.build_connection_pool_key_attributes(
            prepared,
            str(custom_ca),
        )

        assert pool_kwargs["ssl_context"] is created[0]
        assert created[0].loaded == [(str(custom_ca), None)]
        assert "ca_certs" not in pool_kwargs
    finally:
        adapter.close()


def test_plain_http_never_receives_an_ssl_context(monkeypatch):
    class UnexpectedSSLContext:
        def __init__(self, _protocol):
            raise AssertionError("HTTP must not construct a TLS context")

    monkeypatch.setattr(
        provider_network,
        "_truststore",
        SimpleNamespace(SSLContext=UnexpectedSSLContext),
    )
    adapter = SystemTrustHTTPAdapter()
    try:
        prepared = requests.Request("GET", "http://localhost:11434/v1").prepare()
        _host, pool_kwargs = adapter.build_connection_pool_key_attributes(
            prepared,
            True,
        )

        assert "ssl_context" not in pool_kwargs
    finally:
        adapter.close()


def test_retry_classifier_reads_status_from_requests_exception_response():
    error = requests.exceptions.HTTPError("server unavailable")
    error.response = SimpleNamespace(status_code=503)

    assert should_retry_requests_attempt(error)
