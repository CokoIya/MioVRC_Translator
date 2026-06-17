# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""
MainWindow 状态管理集成补丁

这个文件包含了将 AppState 集成到 MainWindow 的改进代码。
使用方法：将这些方法添加到 MainWindow 类中，替换原有的状态管理逻辑。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

from src.ui_qt.state_manager import AppState
from src.ui_qt.style_cache import get_style_cache

if TYPE_CHECKING:
    from src.ui_qt.main_window import MainWindow

logger = logging.getLogger(__name__)


def init_state_manager(self: MainWindow) -> None:
    """
    初始化状态管理器

    在 MainWindow.__init__ 的开头调用此方法
    """
    # 创建状态管理器
    self._state = AppState()

    # 从配置初始化状态
    self._state.update({
        'desktop_capture_enabled': bool(self._config.get("vrc_listen", {}).get("enabled", False)),
        'listen_overlay_enabled': bool(self._config.get("vrc_listen", {}).get("show_overlay", False)),
        'listen_send_to_chatbox': bool(self._config.get("vrc_listen", {}).get("send_to_chatbox", True)),
        'tts_enabled': bool(self._config.get("tts", {}).get("enabled", False)),
        'theme_preference': self._main_theme_preference,
        'theme': self._main_theme,
    })

    # 订阅状态变化
    self._subscribe_state_changes()

    logger.debug("State manager initialized")


def _subscribe_state_changes(self: MainWindow) -> None:
    """订阅状态变化并绑定UI更新"""

    # 运行状态变化
    self._state.subscribe('running', lambda running: self._on_running_state_changed(running))

    # 麦克风静音状态
    self._state.subscribe('mic_muted', lambda muted: self._refresh_mic_mute_button())

    # 桌面捕获状态
    self._state.subscribe('desktop_capture_enabled', lambda enabled: self._on_desktop_capture_state_changed(enabled))

    # 悬浮窗状态
    self._state.subscribe('listen_overlay_enabled', lambda enabled: self._on_listen_overlay_state_changed(enabled))

    # 模式变化
    self._state.subscribe('app_mode', lambda mode: self._refresh_mode_buttons())

    # 主题变化
    self._state.subscribe('theme', lambda theme: self._on_theme_state_changed(theme))

    # 监听中状态
    self._state.subscribe('listen_in_speech', lambda in_speech: self._refresh_floating_window_status(in_speech))


def _on_running_state_changed(self: MainWindow, running: bool) -> None:
    """运行状态变化处理"""
    self._refresh_start_button()
    if not running:
        self._state.update({
            'mic_in_speech': False,
            'listen_in_speech': False,
        })


def _on_desktop_capture_state_changed(self: MainWindow, enabled: bool) -> None:
    """桌面捕获状态变化处理"""
    self._refresh_desktop_capture_button()
    self._sync_settings_window_vrc_listen_state()
    if hasattr(self, '_floating_window') and self._floating_window:
        self._refresh_floating_window_status(False)


def _on_listen_overlay_state_changed(self: MainWindow, enabled: bool) -> None:
    """悬浮窗状态变化处理"""
    self._refresh_listen_overlay_button()
    self._sync_settings_window_vrc_listen_state()
    self._sync_avatar_overlay_state(force=True)


def _on_theme_state_changed(self: MainWindow, theme: str) -> None:
    """主题状态变化处理（延迟刷新子窗口）"""
    # 子窗口刷新延迟到动画结束后，避免卡顿
    if not self._destroying:
        QTimer.singleShot(250, lambda: self._refresh_child_windows(animate=False))


# ================================================================
# 改进的状态设置方法（使用状态管理器）
# ================================================================

def set_mic_muted_improved(self: MainWindow, muted: bool, *, bottom_key: str | None = None) -> None:
    """
    改进的麦克风静音设置方法

    替换原有的 _set_mic_muted 方法
    """
    # 更新内部状态（保持兼容性）
    self._mic_muted = bool(muted)

    # 更新状态管理器（触发UI更新）
    self._state.set('mic_muted', bool(muted))

    # 其他操作
    key = bottom_key or ("mic_mute_on" if muted else "mic_mute_off")
    self._set_bottom(self._copy(key))
    self._sync_avatar_muted_state(force=True)
    self._sync_avatar_speaking_state(force=True)


def set_desktop_capture_enabled_improved(self: MainWindow, enabled: bool, *, persist: bool) -> None:
    """
    改进的桌面捕获设置方法

    替换原有的 _set_desktop_capture_enabled 方法
    """
    new_value = bool(enabled)

    if new_value and self._running:
        try:
            self._start_listen()
        except Exception as exc:
            logger.warning("Desktop listen failed to start: %s", exc)
            new_value = False
            self._config.setdefault("vrc_listen", {})["enabled"] = False
            self._state.set('desktop_capture_enabled', False)
            self._set_bottom(str(exc))
            if persist:
                self._schedule_config_save()
            return
    elif not new_value:
        self._stop_listen()

    # 更新内部状态
    self._desktop_capture_enabled = new_value
    if not self._desktop_capture_enabled:
        self._listen_in_speech = False
        self._state.set('listen_in_speech', False)

    # 更新配置
    self._config.setdefault("vrc_listen", {})["enabled"] = new_value

    # 更新状态管理器（自动触发UI更新）
    self._state.set('desktop_capture_enabled', new_value)

    if persist:
        self._schedule_config_save()

    self._set_bottom(self._copy("desktop_audio_saved"))


def set_listen_overlay_enabled_improved(self: MainWindow, enabled: bool, *, persist: bool) -> None:
    """
    改进的悬浮窗设置方法

    替换原有的 _set_listen_overlay_enabled 方法
    """
    # 更新内部状态
    self._listen_overlay_enabled = bool(enabled)

    # 更新配置
    self._config.setdefault("vrc_listen", {})["show_overlay"] = bool(enabled)

    # 更新状态管理器（自动触发UI更新）
    self._state.set('listen_overlay_enabled', bool(enabled))

    # 其他操作
    service = self._ensure_overlay_service(create_backend=enabled)
    service.set_enabled(enabled, reveal=enabled)

    if persist:
        self._schedule_config_save()


def apply_theme_change_improved(self: MainWindow, new_theme: str, *, animate: bool = False) -> None:
    """
    改进的主题切换方法（优化性能）

    替换原有的 _apply_theme_change 方法
    """
    from src.ui_qt.theme import normalize_theme_preference, resolve_theme
    from src.ui_qt.main_window import MAIN_THEME_CONFIG_KEY, BackgroundWidget
    from src.ui_qt.window_utils import apply_window_chrome_theme, play_theme_fade

    old_theme = self._main_theme
    preference = normalize_theme_preference(new_theme)
    resolved_theme = resolve_theme(preference)
    theme_changed = resolved_theme != old_theme

    def apply_theme() -> None:
        # 更新配置
        ui_cfg = self._config.setdefault("ui", {})
        ui_cfg[MAIN_THEME_CONFIG_KEY] = preference

        # 更新内部状态
        self._main_theme_preference = preference
        self._main_theme = resolved_theme

        # 更新状态管理器
        self._state.update({
            'theme_preference': preference,
            'theme': resolved_theme,
        })

        # 更新背景widget
        central = self.centralWidget()
        if isinstance(central, BackgroundWidget):
            central.set_theme(resolved_theme)

        # 保存配置
        self._schedule_config_save()

        # 重新加载样式（使用缓存）
        self._reload_theme_style()

        # 更新窗口chrome
        apply_window_chrome_theme(self, resolved_theme)

        # 延迟刷新子窗口（避免卡顿）
        if theme_changed:
            QTimer.singleShot(250, lambda: self._refresh_child_windows(animate=False))

    # 播放动画
    if animate and theme_changed:
        play_theme_fade(
            self.centralWidget() or self,
            update=apply_theme,
            duration_ms=180,  # 缩短动画时间
        )
        return

    apply_theme()


def reload_theme_style_improved(self: MainWindow) -> None:
    """
    改进的样式重载方法（使用缓存）

    替换原有的 _reload_theme_style 方法
    """
    from src.ui_qt.styles import build_app_stylesheet, build_main_window_styles
    from PySide6.QtWidgets import QApplication

    # 使用缓存的样式表（大幅提升性能）
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_app_stylesheet(self._main_theme))

    self.setStyleSheet(build_main_window_styles(self._main_theme))

    logger.debug(f"Theme styles reloaded: {self._main_theme}")


def do_start_improved(self: MainWindow) -> None:
    """
    改进的启动方法

    在原有的 _do_start 方法中，启动后更新状态管理器
    """
    # ... 原有的启动逻辑 ...

    # 启动成功后更新状态
    self._state.set('running', True)


def do_stop_improved(self: MainWindow) -> None:
    """
    改进的停止方法

    在原有的 _do_stop 方法中，停止后更新状态管理器
    """
    # ... 原有的停止逻辑 ...

    # 停止后更新状态
    self._state.update({
        'running': False,
        'mic_in_speech': False,
        'listen_in_speech': False,
        'translating': False,
    })


# ================================================================
# 性能监控和调试工具
# ================================================================

def log_state_change(self: MainWindow, key: str, value: any) -> None:
    """记录状态变化（用于调试）"""
    logger.debug(f"State changed: {key} = {value}")


def get_cache_stats(self: MainWindow) -> dict:
    """获取缓存统计信息"""
    cache = get_style_cache()
    return {
        'cache_count': cache.get_count(),
        'cache_size_bytes': cache.get_size(),
        'cache_size_kb': cache.get_size() / 1024,
    }
