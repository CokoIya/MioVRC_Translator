# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import hashlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class StyleCache:
    """
    样式表缓存管理器

    缓存已编译的Qt样式表，避免重复生成，提升主题切换性能。
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._cache_keys: dict[str, str] = {}

    def get(
        self,
        cache_key: str,
        generator: Callable[[], str],
        *,
        params: dict | None = None,
    ) -> str:
        """
        获取缓存的样式表，如果不存在则生成并缓存

        Args:
            cache_key: 缓存键（如 "app_dark", "main_window_light"）
            generator: 样式表生成函数
            params: 可选的参数字典，用于生成更精确的缓存键

        Returns:
            样式表字符串
        """
        # 如果有参数，生成更精确的缓存键
        if params:
            param_hash = self._hash_params(params)
            full_key = f"{cache_key}:{param_hash}"
        else:
            full_key = cache_key

        # 如果缓存中存在，直接返回
        if full_key in self._cache:
            return self._cache[full_key]

        # 生成样式表
        try:
            stylesheet = generator()
            self._cache[full_key] = stylesheet
            logger.debug(f"Generated and cached stylesheet: {full_key}")
            return stylesheet
        except Exception:
            logger.exception(f"Failed to generate stylesheet for key: {cache_key}")
            return ""

    def invalidate(self, cache_key: str | None = None) -> None:
        """
        使缓存失效

        Args:
            cache_key: 要失效的缓存键，如果为None则清空所有缓存
        """
        if cache_key is None:
            self._cache.clear()
            self._cache_keys.clear()
            logger.debug("Cleared all stylesheet cache")
        else:
            # 删除所有以该键开头的缓存项
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(cache_key)]
            for key in keys_to_remove:
                del self._cache[key]
            logger.debug(f"Invalidated stylesheet cache: {cache_key}")

    def _hash_params(self, params: dict) -> str:
        """生成参数的哈希值"""
        param_str = str(sorted(params.items()))
        return hashlib.md5(param_str.encode()).hexdigest()[:8]

    def get_size(self) -> int:
        """获取缓存大小（字节）"""
        return sum(len(s.encode('utf-8')) for s in self._cache.values())

    def get_count(self) -> int:
        """获取缓存项数量"""
        return len(self._cache)


# 全局样式缓存实例
_style_cache = StyleCache()


def get_style_cache() -> StyleCache:
    """获取全局样式缓存实例"""
    return _style_cache


def cached_stylesheet(
    cache_key: str,
    theme: str,
    generator: Callable[[str], str],
) -> str:
    """
    便捷函数：获取带主题的缓存样式表

    Args:
        cache_key: 基础缓存键（如 "app", "main_window"）
        theme: 主题名称（如 "dark", "light"）
        generator: 样式表生成函数，接收theme参数

    Returns:
        样式表字符串
    """
    full_key = f"{cache_key}_{theme}"
    return _style_cache.get(full_key, lambda: generator(theme))
