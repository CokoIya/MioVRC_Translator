"""
实时调整面板
基于 tomari-guruguru 项目的 Tweaks Panel 概念
参数调整立即生效，无需保存重启
"""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from src.ui_qt.icon_utils import ui_icon, ui_icon_url
from src.ui_qt.theme import icon_tint, theme_tokens
from src.utils.i18n import tr

logger = logging.getLogger(__name__)


class RealtimeTweaksPanel(QDialog):
    """
    实时调整面板

    特点：
    - 参数立即生效，无需保存按钮
    - 浮动窗口，可拖动
    - 分组展示
    - 显示当前数值

    使用示例：
        panel = RealtimeTweaksPanel(parent, state_manager, ui_language, theme)
        panel.show()
    """

    def __init__(
        self,
        parent: QWidget | None,
        state_manager,
        ui_language: str = "zh-CN",
        theme: str = "dark",
    ):
        # Keep this as a normal floating window. Using a parented Tool window
        # can make Windows keep it above the main app even without explicit
        # topmost flags.
        super().__init__(None)
        self._owner = parent
        self._state = state_manager
        self._ui_lang = ui_language
        self._theme = theme
        self._icon_labels: list[tuple[QLabel, str, bool]] = []
        self._manual_close_only = bool(self._state.get("tweaks_manual_close", True))

        # 窗口属性
        self.setWindowTitle("实时调整")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(336, 620)

        # 拖动相关
        self._drag_position = None

        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        """构建UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 容器（用于应用圆角和背景）
        container = QWidget()
        container.setObjectName("tweaksContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(16, 12, 16, 16)
        container_layout.setSpacing(12)

        # ---- 标题栏 ----
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        header_layout.addWidget(self._icon_label("sliders.svg", strong=True))

        title_label = QLabel("实时调整")
        title_label.setObjectName("tweaksTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        self._close_btn = QPushButton("")
        self._close_btn.setObjectName("tweaksCloseBtn")
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setIconSize(QSize(16, 16))
        self._close_btn.clicked.connect(self.close)
        header_layout.addWidget(self._close_btn)

        container_layout.addLayout(header_layout)

        # ---- 麦克风部分 ----
        self._add_section(container_layout, "mic.svg", "麦克风")

        self.mic_gain_slider = self._add_slider(
            container_layout,
            "增益",
            30,
            500,
            160,
            lambda v: self._state.set("mic_gain", v / 100),
            suffix="x",
            decimals=2,
        )

        # ---- VAD 部分 ----
        self._add_section(container_layout, "radio.svg", "语音检测 (VAD)")

        self.vad_threshold_slider = self._add_slider(
            container_layout,
            "灵敏度",
            10,
            100,
            50,
            lambda v: self._state.set("vad_threshold", v / 100),
            suffix="%",
        )

        # ---- TTS 部分 ----
        self._add_section(container_layout, "volume.svg", "语音合成 (TTS)")

        self.tts_speed_slider = self._add_slider(
            container_layout,
            "语速",
            50,
            200,
            100,
            lambda v: self._state.set("tts_speed", v / 100),
            suffix="x",
            decimals=2,
        )

        self.tts_volume_slider = self._add_slider(
            container_layout,
            "音量",
            0,
            100,
            80,
            lambda v: self._state.set("tts_volume", v / 100),
            suffix="%",
        )

        # ---- 悬浮窗部分 ----
        self._add_section(container_layout, "eye.svg", "悬浮窗")

        self.overlay_opacity_slider = self._add_slider(
            container_layout,
            "透明度",
            45,
            100,
            88,
            lambda v: self._state.set("overlay_opacity", v / 100),
            suffix="%",
        )

        # ---- 高级设置 ----
        self._add_section(container_layout, "cpu.svg", "高级")

        self.translation_delay_slider = self._add_slider(
            container_layout,
            "翻译延迟缓冲",
            0,
            2000,
            500,
            lambda v: self._state.set("translation_delay_ms", v),
            suffix="ms",
        )

        self.manual_close_toggle = self._add_toggle(
            container_layout,
            "手动关闭",
            self._manual_close_only,
            self._set_manual_close_only,
        )

        container_layout.addStretch()

        # ---- 底部信息 ----
        info_label = QLabel("拖动标题栏移动 • 调整立即生效")
        info_label.setObjectName("tweaksInfo")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(info_label)

        main_layout.addWidget(container)

    def _icon_label(self, filename: str, *, strong: bool = False) -> QLabel:
        label = QLabel()
        label.setObjectName("tweaksIcon")
        label.setFixedSize(18, 18)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_labels.append((label, filename, strong))
        return label

    def _refresh_icons(self) -> None:
        for label, filename, strong in self._icon_labels:
            color = str(theme_tokens(self._theme)["ACCENT"]) if strong else icon_tint(self._theme)
            icon = ui_icon(filename, 16, color)
            if icon.isNull():
                label.clear()
            else:
                label.setPixmap(icon.pixmap(16, 16))
        if hasattr(self, "_close_btn"):
            icon = ui_icon("x.svg", 16, icon_tint(self._theme, strong=True))
            close_text = tr(self._ui_lang, "text_input_close")
            self._close_btn.setIcon(icon)
            self._close_btn.setText(close_text if icon.isNull() else "")
            self._close_btn.setToolTip(close_text)

    def _add_section(self, layout: QVBoxLayout, icon_name: str, title: str):
        """添加分组标题"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._icon_label(icon_name))
        label = QLabel(title)
        label.setObjectName("tweaksSection")
        row.addWidget(label)
        row.addStretch()
        layout.addLayout(row)

    def _add_slider(
        self,
        layout: QVBoxLayout,
        label: str,
        min_val: int,
        max_val: int,
        default_val: int,
        on_change: Callable[[int], None],
        suffix: str = "",
        decimals: int = 0,
    ) -> QSlider:
        """添加滑块控件"""
        row_layout = QVBoxLayout()
        row_layout.setSpacing(6)

        # 标签和数值
        label_layout = QHBoxLayout()
        label_widget = QLabel(label)
        label_widget.setObjectName("tweaksLabel")
        label_layout.addWidget(label_widget)

        label_layout.addStretch()

        value_label = QLabel(self._format_value(default_val, suffix, decimals))
        value_label.setObjectName("tweaksValue")
        label_layout.addWidget(value_label)

        row_layout.addLayout(label_layout)

        # 滑块
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("tweaksSlider")
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(default_val)
        slider.setTickPosition(QSlider.TickPosition.NoTicks)

        def on_value_changed(v):
            value_label.setText(self._format_value(v, suffix, decimals))
            on_change(v)

        slider.valueChanged.connect(on_value_changed)
        row_layout.addWidget(slider)

        layout.addLayout(row_layout)
        return slider

    def _add_toggle(
        self,
        layout: QVBoxLayout,
        label: str,
        default_val: bool,
        on_change: Callable[[bool], None],
    ) -> QCheckBox:
        """添加开关控件"""
        row_layout = QHBoxLayout()

        label_widget = QLabel(label)
        label_widget.setObjectName("tweaksLabel")
        row_layout.addWidget(label_widget)

        row_layout.addStretch()

        toggle = QCheckBox()
        toggle.setObjectName("tweaksToggle")
        toggle.setChecked(default_val)
        toggle.stateChanged.connect(
            lambda state: on_change(Qt.CheckState(state) == Qt.CheckState.Checked)
        )
        row_layout.addWidget(toggle)

        layout.addLayout(row_layout)
        return toggle

    def _format_value(self, value: int, suffix: str, decimals: int) -> str:
        """格式化数值显示"""
        if decimals > 0:
            formatted = f"{value / (10 ** decimals):.{decimals}f}"
        else:
            formatted = str(value)
        return f"{formatted}{suffix}"

    def _set_manual_close_only(self, enabled: bool) -> None:
        self._manual_close_only = bool(enabled)
        self._state.set("tweaks_manual_close", self._manual_close_only)

    def _apply_styles(self):
        """应用样式"""
        tokens = theme_tokens(self._theme)
        slider_handle = ui_icon_url("slider-thumb.svg")
        self.setStyleSheet(
            f"""
            #tweaksContainer {{
                background: {tokens['PANEL_BG']};
                border: 1px solid {tokens['PANEL_BORDER']};
                border-radius: {tokens['RADIUS_L']}px;
            }}

            #tweaksTitle {{
                font-size: 14px;
                font-weight: 600;
                color: {tokens['TEXT_PRIMARY']};
                letter-spacing: 0;
            }}

            #tweaksIcon {{
                background: transparent;
            }}

            #tweaksCloseBtn {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                color: {tokens['TEXT_SECONDARY']};
                padding: 0;
            }}

            #tweaksCloseBtn:hover {{
                background: {tokens['FIELD_HOVER']};
                border-color: {tokens['PANEL_BORDER']};
                color: {tokens['TEXT_PRIMARY']};
            }}

            #tweaksSection {{
                font-size: 11px;
                font-weight: 600;
                color: {tokens['TEXT_MUTED']};
                text-transform: uppercase;
                letter-spacing: 0;
                margin-top: 8px;
                margin-bottom: 4px;
            }}

            #tweaksLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {tokens['TEXT_PRIMARY']};
            }}

            #tweaksValue {{
                font-size: 12px;
                font-weight: 600;
                color: {tokens['TEXT_SECONDARY']};
                font-variant-numeric: tabular-nums;
            }}

            #tweaksSlider {{
                height: 20px;
            }}

            #tweaksSlider::groove:horizontal {{
                background: {tokens['FIELD_BG']};
                height: 4px;
                border-radius: 2px;
            }}

            #tweaksSlider::handle:horizontal {{
                image: {slider_handle};
                background: {tokens['PANEL_BG']};
                width: 22px;
                height: 22px;
                margin: -9px 0;
                border: 1px solid {tokens['ACCENT_BORDER']};
                border-radius: 11px;
            }}

            #tweaksSlider::handle:horizontal:hover {{
                image: {slider_handle};
            }}

            #tweaksToggle {{
                spacing: 0px;
            }}

            #tweaksToggle::indicator {{
                width: 40px;
                height: 22px;
                border-radius: 11px;
                background: {tokens['FIELD_BG']};
                border: 1px solid {tokens['PANEL_BORDER']};
            }}

            #tweaksToggle::indicator:checked {{
                background: {tokens['ACCENT']};
            }}

            #tweaksInfo {{
                font-size: 11px;
                color: {tokens['TEXT_MUTED']};
                margin-top: 8px;
            }}
        """
        )
        self._refresh_icons()

    # ---- 窗口拖动 ----
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 只在标题栏区域允许拖动（顶部 40px）
            if event.position().y() < 40:
                self._drag_position = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_position is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        self._drag_position = None

    def changeEvent(self, event):  # noqa: N802
        if (
            event.type() == QEvent.Type.ActivationChange
            and not self._manual_close_only
            and not self.isActiveWindow()
        ):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def refresh_theme(self, theme: str):
        """刷新主题"""
        self._theme = theme
        self._apply_styles()

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = ui_language
        self._refresh_icons()


# ---- 快速测试 ----
if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    # 模拟状态管理器
    class MockStateManager:
        def set(self, key, value):
            print(f"状态更新: {key} = {value}")

        def update(self, updates):
            for key, value in updates.items():
                self.set(key, value)

    app = QApplication(sys.argv)

    state = MockStateManager()
    panel = RealtimeTweaksPanel(None, state, "zh-CN", "dark")
    panel.show()

    sys.exit(app.exec())
