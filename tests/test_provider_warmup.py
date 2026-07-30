from __future__ import annotations

import asyncio

from src.utils.provider_warmup import (
    warmup_async_httpx_client,
    warmup_httpx_client,
    warmup_requests_session,
)


class _Response:
    status_code = 401

    def __init__(self, chunks=()) -> None:
        self.closed = False
        self.chunks = tuple(chunks)

    def iter_content(self, chunk_size=1):
        assert chunk_size == 8192
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def test_requests_warmup_is_bounded_non_redirecting_and_closes_response():
    response = _Response()

    class Session:
        def __init__(self) -> None:
            self.call = None

        def request(self, method, url, **kwargs):
            self.call = (method, url, kwargs)
            return response

    session = Session()
    result = warmup_requests_session(
        session,
        "https://provider.example/v1/models",
        method="HEAD",
        headers={"Authorization": "Bearer secret"},
        timeout_s=999,
    )

    assert result.succeeded is True
    assert result.status_code == 401
    assert result.connection_reusable is True
    assert response.closed is True
    assert session.call == (
        "HEAD",
        "https://provider.example/v1/models",
        {
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 5.0,
            "allow_redirects": False,
            "stream": True,
        },
    )


def test_requests_warmup_rejects_billable_method_and_url_userinfo():
    class Session:
        def request(self, *_args, **_kwargs):
            raise AssertionError("request must not be sent")

    post_result = warmup_requests_session(
        Session(),
        "https://provider.example/v1/audio",
        method="POST",
    )
    credential_result = warmup_requests_session(
        Session(),
        "https://user:secret@provider.example/v1/models",
    )

    assert post_result.attempted is False
    assert post_result.succeeded is False
    assert credential_result.attempted is False
    assert credential_result.succeeded is False


def test_requests_get_warmup_drains_bounded_metadata_for_pool_reuse():
    response = _Response([b'{"data":', b"[]}"])

    class Session:
        def request(self, *_args, **_kwargs):
            return response

    result = warmup_requests_session(
        Session(),
        "https://provider.example/v1/models",
        method="GET",
    )

    assert result.succeeded is True
    assert result.connection_reusable is True
    assert response.closed is True


def test_httpx_warmup_uses_streaming_context_without_redirects():
    response = _Response()

    class StreamContext:
        def __enter__(self):
            return response

        def __exit__(self, *_args):
            response.close()

    class Client:
        def __init__(self) -> None:
            self.call = None

        def stream(self, method, url, **kwargs):
            self.call = (method, url, kwargs)
            return StreamContext()

    client = Client()
    result = warmup_httpx_client(
        client,
        "https://provider.example/v1/models",
        headers={"x-api-key": "secret"},
        timeout_s=0,
    )

    assert result.succeeded is True
    assert result.connection_reusable is True
    assert response.closed is True
    assert client.call == (
        "HEAD",
        "https://provider.example/v1/models",
        {
            "headers": {"x-api-key": "secret"},
            "timeout": 0.1,
            "follow_redirects": False,
        },
    )


def test_async_httpx_warmup_uses_streaming_context_without_redirects():
    response = _Response()

    class AsyncStreamContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *_args):
            response.close()

    class Client:
        def __init__(self) -> None:
            self.call = None

        def stream(self, method, url, **kwargs):
            self.call = (method, url, kwargs)
            return AsyncStreamContext()

    client = Client()
    result = asyncio.run(
        warmup_async_httpx_client(
            client,
            "https://provider.example/v1/models",
            headers={"Authorization": "Bearer secret"},
            timeout_s=1.5,
        )
    )

    assert result.succeeded is True
    assert result.connection_reusable is True
    assert response.closed is True
    assert client.call == (
        "HEAD",
        "https://provider.example/v1/models",
        {
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 1.5,
            "follow_redirects": False,
        },
    )
