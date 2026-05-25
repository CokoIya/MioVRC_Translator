from src.utils import sponsor_fetcher


def test_sponsors_request_url_adds_cache_buster():
    url = sponsor_fetcher._sponsors_request_url(
        "https://example.com/sponsors.json",
        123.456,
    )

    assert url == "https://example.com/sponsors.json?_=123456"


def test_sponsors_request_url_preserves_existing_query():
    url = sponsor_fetcher._sponsors_request_url(
        "https://example.com/sponsors.json?lang=zh",
        123.456,
    )

    assert url == "https://example.com/sponsors.json?lang=zh&_=123456"


def test_get_sponsors_refreshes_even_when_cache_exists(monkeypatch):
    cached = {"version": 1, "sponsors": [{"name": "old"}]}
    fresh = {"version": 2, "sponsors": [{"name": "new"}]}
    callbacks = []
    saved = []

    class _InlineThread:
        def __init__(self, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(sponsor_fetcher, "_load_cache", lambda: cached)
    monkeypatch.setattr(sponsor_fetcher, "_fetch_remote", lambda: (fresh, "https://example.com"))
    monkeypatch.setattr(sponsor_fetcher, "_save_cache", lambda data: saved.append(data))
    monkeypatch.setattr(sponsor_fetcher.threading, "Thread", _InlineThread)

    sponsor_fetcher.get_sponsors(callbacks.append)

    assert callbacks == [cached, fresh]
    assert saved == [fresh]


def test_get_sponsors_force_refresh_prefers_fresh(monkeypatch):
    cached = {"version": 1, "sponsors": [{"name": "old"}]}
    fresh = {"version": 2, "sponsors": [{"name": "new"}]}
    callbacks = []

    class _InlineThread:
        def __init__(self, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(sponsor_fetcher, "_load_cache", lambda: cached)
    monkeypatch.setattr(sponsor_fetcher, "_fetch_remote", lambda: (fresh, "https://example.com"))
    monkeypatch.setattr(sponsor_fetcher, "_save_cache", lambda _data: None)
    monkeypatch.setattr(sponsor_fetcher.threading, "Thread", _InlineThread)

    sponsor_fetcher.get_sponsors(callbacks.append, force_refresh=True)

    assert callbacks == [fresh]


def test_get_sponsors_force_refresh_falls_back_to_cache_on_failure(monkeypatch):
    cached = {"version": 1, "sponsors": [{"name": "old"}]}
    callbacks = []

    class _InlineThread:
        def __init__(self, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(sponsor_fetcher, "_load_cache", lambda: cached)
    monkeypatch.setattr(sponsor_fetcher, "_fetch_remote", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sponsor_fetcher.threading, "Thread", _InlineThread)

    sponsor_fetcher.get_sponsors(callbacks.append, force_refresh=True)

    assert callbacks == [cached]


def test_fetch_remote_tries_github_first_then_mirror(monkeypatch):
    calls = []
    fresh = {"version": 2, "sponsors": [{"name": "new"}]}

    def fake_fetch(url):
        calls.append(url)
        if url == sponsor_fetcher.GITHUB_SPONSORS_URL:
            raise RuntimeError("github down")
        return fresh

    monkeypatch.setattr(sponsor_fetcher, "_fetch_remote_from_url", fake_fetch)

    data, source_url = sponsor_fetcher._fetch_remote()

    assert calls == [
        sponsor_fetcher.GITHUB_SPONSORS_URL,
        sponsor_fetcher.MIRROR_SPONSORS_URL,
    ]
    assert data == fresh
    assert source_url == sponsor_fetcher.MIRROR_SPONSORS_URL
