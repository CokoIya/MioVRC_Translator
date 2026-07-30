from src.utils import app_paths, catalog_fetcher, config_manager, startup_tasks


class _FakeThread:
    def __init__(self, *, target, daemon, name) -> None:
        self.target = target
        self.daemon = daemon
        self.name = name
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1

    def is_alive(self) -> bool:
        return False


def test_post_ui_startup_tasks_are_nonblocking_and_idempotent(monkeypatch):
    startup_tasks._reset_post_ui_startup_tasks_for_tests()
    fake_threads: list[_FakeThread] = []
    catalog_callbacks = []

    def make_thread(**kwargs):
        thread = _FakeThread(**kwargs)
        fake_threads.append(thread)
        return thread

    monkeypatch.setattr(startup_tasks.threading, "Thread", make_thread)
    monkeypatch.setattr(
        catalog_fetcher,
        "refresh_catalog",
        lambda callback: catalog_callbacks.append(callback),
    )

    assert startup_tasks.schedule_post_ui_startup_tasks() is True
    assert startup_tasks.schedule_post_ui_startup_tasks() is False
    assert len(fake_threads) == 1
    assert fake_threads[0].daemon is True
    assert fake_threads[0].name == "startup-maintenance"
    assert fake_threads[0].start_count == 1
    assert len(catalog_callbacks) == 1

    startup_tasks._reset_post_ui_startup_tasks_for_tests()


def test_background_maintenance_runs_migration_before_model_cleanup(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_paths,
        "run_deferred_legacy_data_migration",
        lambda: calls.append("migration"),
    )
    monkeypatch.setattr(
        config_manager,
        "cleanup_obsolete_runtime_models",
        lambda: calls.append("cleanup"),
    )

    startup_tasks._run_maintenance()

    assert calls == ["migration", "cleanup"]
