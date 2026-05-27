import tempfile
from pathlib import Path

import pytest

from src.core.config_service import ConfigService


class TestConfigServiceGetSet:
    def test_get_nested_value(self):
        cfg = {"a": {"b": {"c": "value"}}}
        svc = ConfigService(cfg)
        assert svc.get("a.b.c") == "value"

    def test_get_missing_key_default(self):
        cfg = {"a": {"b": 1}}
        svc = ConfigService(cfg)
        assert svc.get("a.x", default="fallback") == "fallback"

    def test_get_non_dict_default(self):
        cfg = {"a": "string"}
        svc = ConfigService(cfg)
        assert svc.get("a.b", default=None) is None

    def test_get_empty_path_returns_config(self):
        cfg = {"key": "value"}
        svc = ConfigService(cfg)
        assert svc.get("", default="fallback") == cfg

    def test_set_nested_value(self):
        cfg = {}
        svc = ConfigService(cfg)
        svc.set("a.b.c", "value")
        assert cfg["a"]["b"]["c"] == "value"

    def test_set_noop_when_value_unchanged(self):
        cfg = {"a": {"b": "unchanged"}}
        svc = ConfigService(cfg, debounce_ms=0)
        svc.set("a.b", "unchanged")
        assert cfg["a"]["b"] == "unchanged"

    def test_dot_path_parts_strip_empty(self):
        cfg = {}
        svc = ConfigService(cfg)
        svc.set("a..b", "value")
        assert cfg["a"]["b"] == "value"

    def test_flush_does_not_raise(self):
        cfg = {}
        svc = ConfigService(cfg, debounce_ms=0)
        svc.flush()
