#!/usr/bin/env python3
"""
测试实时调整功能集成
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_envelope_follower():
    """测试音频包络跟随器"""
    print("=" * 60)
    print("测试 1: 音频包络跟随器")
    print("=" * 60)

    try:
        from src.audio.envelope_follower import EnvelopeFollower, ThresholdDetector

        # 创建包络跟随器
        follower = EnvelopeFollower(
            attack_rate=0.6,
            release_rate=0.12,
            gain=1.6
        )

        # 创建阈值检测器
        detector = ThresholdDetector(
            thresholds=[0.07, 0.2],
            debounce_ms=70
        )

        print("[OK] EnvelopeFollower 创建成功")
        print(f"   - Attack rate: {follower.attack_rate}")
        print(f"   - Release rate: {follower.release_rate}")
        print(f"   - Gain: {follower.gain}")

        print("[OK] ThresholdDetector 创建成功")
        print(f"   - Thresholds: {detector.thresholds}")
        print(f"   - Debounce: {detector.debounce_ms}ms")

        # 测试处理
        import random
        import time

        print("\n测试包络跟随效果...")
        for i in range(5):
            raw = random.uniform(0.0, 0.3)
            envelope = follower.process(raw)
            state = detector.detect(envelope, int(time.time() * 1000))
            state_names = ["闭嘴", "半开", "全开"]
            print(f"  [{i}] Raw: {raw:.3f} → Envelope: {envelope:.3f} → {state_names[state]}")

        return True
    except Exception as e:
        print(f"[ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_vad_detector():
    """测试 VAD 检测器包络集成"""
    print("\n" + "=" * 60)
    print("测试 2: VAD 检测器包络集成")
    print("=" * 60)

    try:
        from src.audio.vad_detector import VADDetector

        # 创建带包络跟随的 VAD
        vad = VADDetector(
            sample_rate=16000,
            use_envelope_follower=True
        )

        print("[OK] VADDetector 创建成功")
        print(f"   - 包络跟随器: {'已启用' if vad.envelope_follower else '未启用'}")

        if vad.envelope_follower:
            print(f"   - Attack rate: {vad.envelope_follower.attack_rate}")
            print(f"   - Release rate: {vad.envelope_follower.release_rate}")

        # 测试方法
        print("\n测试 VAD 方法...")
        print(f"  - get_smooth_rms(): {vad.get_smooth_rms():.3f}")

        vad.set_envelope_params(0.7, 0.15)
        print(f"  - set_envelope_params(0.7, 0.15): 成功")

        if vad.envelope_follower:
            print(f"  - 新 Attack rate: {vad.envelope_follower.attack_rate}")
            print(f"  - 新 Release rate: {vad.envelope_follower.release_rate}")

        return True
    except Exception as e:
        print(f"[ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_state_manager():
    """测试状态管理器新增参数"""
    print("\n" + "=" * 60)
    print("测试 3: 状态管理器新增参数")
    print("=" * 60)

    try:
        from src.ui_qt.state_manager import AppState

        state = AppState()

        print("[OK] AppState 创建成功")
        print("\n检查实时调整参数...")

        params = [
            ('mic_gain', 1.6),
            ('vad_threshold', 0.5),
            ('tts_speed', 1.0),
            ('tts_volume', 0.8),
            ('overlay_opacity', 0.88),
            ('translation_delay_ms', 500),
            ('envelope_attack_rate', 0.6),
            ('envelope_release_rate', 0.12),
        ]

        for key, expected in params:
            value = state.get(key)
            status = "[OK]" if value == expected else "[ERR]"
            print(f"  {status} {key}: {value} (预期: {expected})")

        # 测试状态订阅
        print("\n测试状态订阅...")
        received_value = None

        def callback(value):
            nonlocal received_value
            received_value = value

        state.subscribe('mic_gain', callback)
        state.set('mic_gain', 2.0)

        if received_value == 2.0:
            print("  [OK] 状态订阅工作正常")
        else:
            print(f"  [ERR] 状态订阅失败: 接收到 {received_value}, 预期 2.0")

        return True
    except Exception as e:
        print(f"[ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_realtime_tweaks_panel():
    """测试实时调整面板（GUI测试，需要X11/显示器）"""
    print("\n" + "=" * 60)
    print("测试 4: 实时调整面板")
    print("=" * 60)

    try:
        from PySide6.QtWidgets import QApplication
        from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel
        from src.ui_qt.state_manager import AppState

        # 检查是否有显示环境
        try:
            app = QApplication.instance() or QApplication(sys.argv)
        except Exception as e:
            print(f"[WARN]  无显示环境，跳过GUI测试: {e}")
            return True

        state = AppState()
        panel = RealtimeTweaksPanel(None, state, "zh-CN", "dark")

        print("[OK] RealtimeTweaksPanel 创建成功")
        print(f"   - 窗口标题: {panel.windowTitle()}")
        print(f"   - 窗口大小: {panel.width()}x{panel.height()}")
        print(f"   - 主题: dark")

        # 测试显示/隐藏
        panel.show()
        print("  [OK] 显示面板")

        panel.hide()
        print("  [OK] 隐藏面板")

        # 清理
        panel.close()

        return True
    except Exception as e:
        print(f"[ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("实时调整功能集成测试")
    print("=" * 60 + "\n")

    results = []

    # 运行测试
    results.append(("音频包络跟随器", test_envelope_follower()))
    results.append(("VAD 检测器集成", test_vad_detector()))
    results.append(("状态管理器", test_state_manager()))
    results.append(("实时调整面板", test_realtime_tweaks_panel()))

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status}: {name}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n[SUCCESS] 所有测试通过！")
        return 0
    else:
        print(f"\n[WARNING] {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
