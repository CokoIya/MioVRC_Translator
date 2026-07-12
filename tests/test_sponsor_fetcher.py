import threading

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


def test_concurrent_sponsor_refreshes_share_one_worker_and_release_callbacks(
    monkeypatch,
):
    cached = {"version": 1, "sponsors": [{"name": "old"}]}
    fresh = {"version": 2, "sponsors": [{"name": "new"}]}
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_count = 0
    first_results: list[dict] = []
    second_results: list[dict] = []

    def fetch_remote():
        nonlocal fetch_count
        fetch_count += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return fresh, "https://example.com/sponsors.json"

    monkeypatch.setattr(sponsor_fetcher, "_load_cache", lambda: cached)
    monkeypatch.setattr(sponsor_fetcher, "_fetch_remote", fetch_remote)
    monkeypatch.setattr(sponsor_fetcher, "_save_cache", lambda _data: None)

    sponsor_fetcher.get_sponsors(first_results.append)
    assert fetch_started.wait(timeout=1)
    worker = sponsor_fetcher._fetch_thread
    assert worker is not None

    sponsor_fetcher.get_sponsors(second_results.append, force_refresh=True)
    assert sponsor_fetcher._fetch_thread is worker
    assert len(sponsor_fetcher._fetch_subscribers) == 2

    release_fetch.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert fetch_count == 1
    assert first_results == [cached, fresh]
    assert second_results == [fresh]
    assert sponsor_fetcher._fetch_thread is None
    assert sponsor_fetcher._fetch_subscribers == []


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


def test_validate_sponsors_payload_bounds_and_normalizes_remote_content():
    payload = {
        "version": 2,
        "updated": " 2026-07-11 ",
        "tip": {
            "EN": "line one\nline two",
            "bad\x00lang": "ignored",
            **{f"lang-{index}": "x" * 2000 for index in range(30)},
        },
        "sponsors": [
            {"name": "<b>shown as plain text</b>"},
            {"name": "x" * 500},
            {"name": "bad\x00name"},
            *({"name": f"sponsor-{index}"} for index in range(700)),
        ],
        "untrusted": {"ignored": True},
    }

    cleaned = sponsor_fetcher._validate_sponsors_payload(payload)

    assert cleaned["updated"] == "2026-07-11"
    assert cleaned["tip"]["en"] == "line one line two"
    assert len(cleaned["tip"]) == sponsor_fetcher._MAX_TIP_LANGUAGES
    assert len(cleaned["sponsors"]) == sponsor_fetcher._MAX_SPONSORS
    assert cleaned["sponsors"][0]["name"] == "<b>shown as plain text</b>"
    assert len(cleaned["sponsors"][1]["name"]) == sponsor_fetcher._MAX_SPONSOR_NAME_CHARS
    assert all("\x00" not in item["name"] for item in cleaned["sponsors"])
    assert "untrusted" not in cleaned
