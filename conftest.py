import os
import pathlib


def pytest_configure(config):
    local_tmp = pathlib.Path(__file__).parent / ".pytest_tmp"
    local_tmp.mkdir(exist_ok=True)
    os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(local_tmp))
