# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class AppState:
    """
    集中式应用状态管理器

    负责管理应用的核心UI状态，确保所有组件的状态同步。
    使用发布-订阅模式，当状态变化时自动通知所有订阅者。
    """

    def __init__(self) -> None:
        self._state: dict[str, Any] = {
            # 核心运行状态
            'running': False,
            'translating': False,

            # 音频状态
            'mic_muted': False,
            'mic_in_speech': False,
            'desktop_capture_enabled': False,
            'listen_in_speech': False,
            'listen_overlay_enabled': False,
            'listen_send_to_chatbox': True,

            # 应用模式
            'app_mode': None,  # AppMode enum

            # TTS状态
            'tts_enabled': False,

            # UI状态
            'theme': 'dark',
            'theme_preference': 'system',

            # 设备状态
            'active_mic_device': None,
            'active_listen_device': None,

            # 实时调整参数 (tomari-guruguru inspired)
            'mic_gain': 1.6,                  # 麦克风增益 (0.3 - 5.0)
            'vad_threshold': 0.5,             # VAD 灵敏度 (0.1 - 1.0)
            'tts_speed': 1.0,                 # TTS 语速 (0.5 - 2.0)
            'tts_volume': 0.8,                # TTS 音量 (0.0 - 1.0)
            'overlay_opacity': 0.88,          # 悬浮窗透明度 (0.45 - 1.0)
            'translation_delay_ms': 500,      # 翻译延迟缓冲 (0 - 2000ms)
            'tweaks_manual_close': True,      # 实时调整面板仅手动关闭
            'envelope_attack_rate': 0.6,      # 音频包络 Attack 速率
            'envelope_release_rate': 0.12,    # 音频包络 Release 速率
        }

        # 订阅者字典：key -> list of callbacks
        self._listeners: dict[str, list[Callable[[Any], None]]] = {}

        # 全局订阅者（监听所有变化）
        self._global_listeners: list[Callable[[str, Any], None]] = []

        # 状态变化锁，防止循环更新
        self._updating = False

    def get(self, key: str, default: Any = None) -> Any:
        """获取状态值"""
        return self._state.get(key, default)

    def set(self, key: str, value: Any, *, silent: bool = False) -> bool:
        """
        设置状态值

        Args:
            key: 状态键
            value: 新值
            silent: 如果为True，不触发订阅者通知

        Returns:
            是否实际发生了变化
        """
        old_value = self._state.get(key)

        # 值未变化，不需要更新
        if old_value == value:
            return False

        self._state[key] = value

        if not silent and not self._updating:
            self._notify(key, value, old_value)

        return True

    def update(self, updates: dict[str, Any], *, silent: bool = False) -> set[str]:
        """
        批量更新多个状态

        Args:
            updates: 状态更新字典
            silent: 如果为True，不触发订阅者通知

        Returns:
            实际发生变化的键集合
        """
        changed_keys = set()

        self._updating = True
        try:
            for key, value in updates.items():
                if self.set(key, value, silent=True):
                    changed_keys.add(key)
        finally:
            self._updating = False

        # 批量通知
        if not silent and changed_keys:
            for key in changed_keys:
                self._notify(key, self._state[key], None)

        return changed_keys

    def subscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        """
        订阅特定状态的变化

        Args:
            key: 要订阅的状态键
            callback: 状态变化时的回调函数，接收新值作为参数
        """
        if key not in self._listeners:
            self._listeners[key] = []

        if callback not in self._listeners[key]:
            self._listeners[key].append(callback)

    def subscribe_all(self, callback: Callable[[str, Any], None]) -> None:
        """
        订阅所有状态变化

        Args:
            callback: 状态变化时的回调函数，接收(key, value)作为参数
        """
        if callback not in self._global_listeners:
            self._global_listeners.append(callback)

    def unsubscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        """取消订阅特定状态"""
        if key in self._listeners and callback in self._listeners[key]:
            self._listeners[key].remove(callback)

    def unsubscribe_all(self, callback: Callable[[str, Any], None]) -> None:
        """取消全局订阅"""
        if callback in self._global_listeners:
            self._global_listeners.remove(callback)

    def _notify(self, key: str, new_value: Any, old_value: Any = None) -> None:
        """通知订阅者状态变化"""
        # 通知特定状态的订阅者
        if key in self._listeners:
            for callback in self._listeners[key][:]:  # 复制列表以防止迭代时修改
                try:
                    callback(new_value)
                except Exception:
                    logger.exception(f"Error in state listener for key '{key}'")

        # 通知全局订阅者
        for callback in self._global_listeners[:]:
            try:
                callback(key, new_value)
            except Exception:
                logger.exception(f"Error in global state listener for key '{key}'")

    def get_all(self) -> dict[str, Any]:
        """获取所有状态的副本"""
        return self._state.copy()

    def reset(self) -> None:
        """重置所有状态到初始值"""
        self._state = {
            'running': False,
            'translating': False,
            'mic_muted': False,
            'mic_in_speech': False,
            'desktop_capture_enabled': False,
            'listen_in_speech': False,
            'listen_overlay_enabled': False,
            'listen_send_to_chatbox': True,
            'app_mode': None,
            'tts_enabled': False,
            'theme': 'dark',
            'theme_preference': 'system',
            'active_mic_device': None,
            'active_listen_device': None,
            'mic_gain': 1.6,
            'vad_threshold': 0.5,
            'tts_speed': 1.0,
            'tts_volume': 0.8,
            'overlay_opacity': 0.88,
            'translation_delay_ms': 500,
            'tweaks_manual_close': True,
            'envelope_attack_rate': 0.6,
            'envelope_release_rate': 0.12,
        }


class StateProperty:
    """
    状态属性描述符，用于简化状态访问

    使用方法：
        class MyWindow:
            def __init__(self, state: AppState):
                self._state = state

            running = StateProperty('running')

        # 自动从 self._state 获取/设置
        window.running = True
    """

    def __init__(self, key: str) -> None:
        self.key = key

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        state = getattr(obj, '_state', None)
        if state is None:
            raise AttributeError(f"Object has no _state attribute")
        return state.get(self.key)

    def __set__(self, obj: Any, value: Any) -> None:
        state = getattr(obj, '_state', None)
        if state is None:
            raise AttributeError(f"Object has no _state attribute")
        state.set(self.key, value)
