from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from src.utils import config_manager

logger = logging.getLogger(__name__)


class ConfigService(QObject):
    changed = Signal(str, object)
    saved = Signal()
    save_failed = Signal(str)

    def __init__(self, config: dict | None = None, *, debounce_ms: int = 300) -> None:
        super().__init__()
        self._config = config if isinstance(config, dict) else config_manager.load_config()
        self._debounce_ms = max(0, int(debounce_ms))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.flush)

    @property
    def config(self) -> dict:
        return self._config

    def load_config(self) -> dict:
        self._config = config_manager.load_config()
        return self._config

    def save_config(self, config: dict | None = None) -> None:
        if config is not None:
            self._config = config
        self._schedule_save()

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._config
        for part in self._parts(path):
            if not isinstance(node, MutableMapping) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = self._parts(path)
        if not parts:
            return
        node: MutableMapping[str, Any] = self._config
        for part in parts[:-1]:
            next_node = node.get(part)
            if not isinstance(next_node, MutableMapping):
                next_node = {}
                node[part] = next_node
            node = next_node
        if node.get(parts[-1]) == value:
            return
        node[parts[-1]] = value
        self.changed.emit(path, value)
        self._schedule_save()

    def flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        try:
            config_manager.save_config(self._config)
        except Exception as exc:
            logger.warning("Config save failed: %s", exc)
            self.save_failed.emit(str(exc))
            return
        self.saved.emit()

    def _schedule_save(self) -> None:
        self._save_timer.start(self._debounce_ms)

    @staticmethod
    def _parts(path: str) -> list[str]:
        return [part for part in str(path or "").split(".") if part]
