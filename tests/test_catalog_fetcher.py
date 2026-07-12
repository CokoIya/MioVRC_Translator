import threading

from src.utils import catalog_fetcher


def test_catalog_request_url_adds_cache_buster_and_preserves_query():
    assert catalog_fetcher._catalog_request_url(
        "https://example.com/catalog.json?lang=en",
        123.456,
    ) == "https://example.com/catalog.json?lang=en&_=123456"


def test_concurrent_catalog_refreshes_share_one_worker_and_release_callbacks(
    monkeypatch,
):
    cached = {"version": 1, "translation_backends": {}}
    fresh = {"version": 2, "translation_backends": {"qianwen": {}}}
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
        return fresh, "https://example.com/catalog.json"

    monkeypatch.setattr(catalog_fetcher, "_load_cache", lambda: cached)
    monkeypatch.setattr(catalog_fetcher, "_fetch_remote", fetch_remote)
    monkeypatch.setattr(catalog_fetcher, "_save_cache", lambda _data: None)

    catalog_fetcher.get_catalog(first_results.append)
    assert fetch_started.wait(timeout=1)
    worker = catalog_fetcher._fetch_thread
    assert worker is not None

    catalog_fetcher.get_catalog(second_results.append, force_refresh=True)
    assert catalog_fetcher._fetch_thread is worker
    assert len(catalog_fetcher._fetch_subscribers) == 2

    release_fetch.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert fetch_count == 1
    assert first_results == [cached, fresh]
    assert second_results == [fresh]
    assert catalog_fetcher._fetch_thread is None
    assert catalog_fetcher._fetch_subscribers == []
