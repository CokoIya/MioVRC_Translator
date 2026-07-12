from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_application_writable_home(monkeypatch, tmp_path):
    """Never allow tests to read or overwrite the user's production config/data."""

    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(tmp_path / "mio-test-home"))
