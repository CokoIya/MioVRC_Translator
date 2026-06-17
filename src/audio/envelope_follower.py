"""
音频包络跟随器
基于 tomari-guruguru 项目的包络跟随算法
用于平滑音频电平变化，消除抖动
"""
import threading
from typing import Optional


class EnvelopeFollower:
    """
    音频包络跟随器

    实现平滑的 Attack/Release 包络跟随算法：
    - Attack（声音变大）：快速跟随
    - Release（声音变小）：慢速跟随

    这符合人类听觉习惯，可用于：
    - 音量表显示
    - VAD 平滑
    - 口型同步驱动
    """

    def __init__(
        self,
        attack_rate: float = 0.6,
        release_rate: float = 0.12,
        gain: float = 1.0
    ):
        """
        初始化包络跟随器

        Args:
            attack_rate: Attack 速率 (0.0-1.0)，越大响应越快
            release_rate: Release 速率 (0.0-1.0)，越小衰减越慢
            gain: 输入增益倍数
        """
        self.attack_rate = attack_rate
        self.release_rate = release_rate
        self.gain = gain

        self._envelope = 0.0
        self._lock = threading.Lock()

    def process(self, raw_level: float) -> float:
        """
        处理单个音频电平样本

        Args:
            raw_level: 原始音频电平 (0.0-1.0)

        Returns:
            平滑后的包络值 (0.0-1.0+)
        """
        with self._lock:
            # 应用增益
            level = raw_level * self.gain

            # Attack/Release 包络跟随
            if level > self._envelope:
                # Attack: 快速跟随
                self._envelope += (level - self._envelope) * self.attack_rate
            else:
                # Release: 慢速跟随
                self._envelope += (level - self._envelope) * self.release_rate

            return self._envelope

    def process_batch(self, raw_levels: list[float]) -> list[float]:
        """
        批量处理音频电平

        Args:
            raw_levels: 原始电平列表

        Returns:
            平滑后的包络值列表
        """
        return [self.process(level) for level in raw_levels]

    def reset(self):
        """重置包络状态"""
        with self._lock:
            self._envelope = 0.0

    @property
    def envelope(self) -> float:
        """当前包络值（只读）"""
        with self._lock:
            return self._envelope

    def set_attack_rate(self, rate: float):
        """设置 Attack 速率 (0.0-1.0)"""
        with self._lock:
            self.attack_rate = max(0.0, min(1.0, rate))

    def set_release_rate(self, rate: float):
        """设置 Release 速率 (0.0-1.0)"""
        with self._lock:
            self.release_rate = max(0.0, min(1.0, rate))

    def set_gain(self, gain: float):
        """设置输入增益"""
        with self._lock:
            self.gain = max(0.0, gain)


class ThresholdDetector:
    """
    基于包络的多阈值检测器

    用于将连续的包络值转换为离散状态
    例如：静默/说话，或 闭嘴/半开/全开
    """

    def __init__(
        self,
        thresholds: list[float],
        debounce_ms: int = 70
    ):
        """
        初始化阈值检测器

        Args:
            thresholds: 阈值列表，升序排列
                       例如 [0.07, 0.2] 表示 3 个状态：
                       0: < 0.07
                       1: 0.07 - 0.2
                       2: >= 0.2
            debounce_ms: 防抖动时间（毫秒）
        """
        self.thresholds = sorted(thresholds)
        self.debounce_ms = debounce_ms

        self._last_state: Optional[int] = None
        self._last_switch_time = 0
        self._lock = threading.Lock()

    def detect(self, envelope_value: float, current_time_ms: int) -> int:
        """
        检测当前状态

        Args:
            envelope_value: 包络值
            current_time_ms: 当前时间（毫秒）

        Returns:
            状态索引 (0 到 len(thresholds))
        """
        with self._lock:
            # 确定新状态
            state = 0
            for i, threshold in enumerate(self.thresholds):
                if envelope_value >= threshold:
                    state = i + 1

            # 防抖动：如果状态变化且未到防抖时间，保持旧状态
            if (self._last_state is not None and
                state != self._last_state and
                current_time_ms - self._last_switch_time < self.debounce_ms):
                return self._last_state

            # 状态变化，更新记录
            if state != self._last_state:
                self._last_state = state
                self._last_switch_time = current_time_ms

            return state

    def reset(self):
        """重置状态"""
        with self._lock:
            self._last_state = None
            self._last_switch_time = 0


# ---- 使用示例 ----
if __name__ == "__main__":
    import time
    import random

    # 创建包络跟随器
    follower = EnvelopeFollower(
        attack_rate=0.6,
        release_rate=0.12,
        gain=1.6
    )

    # 创建三阶段检测器（闭嘴/半开/全开）
    detector = ThresholdDetector(
        thresholds=[0.07, 0.2],
        debounce_ms=70
    )

    print("音频包络跟随器测试")
    print("-" * 50)

    # 模拟音频电平变化
    for i in range(100):
        # 模拟随机音频电平
        raw_level = abs(random.gauss(0.15, 0.1))

        # 包络跟随
        envelope = follower.process(raw_level)

        # 状态检测
        state = detector.detect(envelope, int(time.time() * 1000))
        state_names = ["闭嘴", "半开", "全开"]

        # 可视化
        bar = "█" * int(envelope * 50)
        print(f"[{i:3d}] Raw: {raw_level:.3f} | Env: {envelope:.3f} {bar:50s} | {state_names[state]}")

        time.sleep(0.05)
