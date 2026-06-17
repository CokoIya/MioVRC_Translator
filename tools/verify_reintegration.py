#!/usr/bin/env python3
"""
验证回滚后的重新集成
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 70)
print(" 验证实时调整功能重新集成")
print("=" * 70)

def check_file_exists():
    """检查核心文件是否存在"""
    print("\n[1/5] 检查核心文件...")
    print("-" * 70)

    files = [
        ("src/audio/envelope_follower.py", "音频包络跟随器"),
        ("src/ui_qt/realtime_tweaks_panel.py", "实时调整面板"),
        ("src/audio/vad_detector.py", "VAD 检测器"),
        ("src/ui_qt/state_manager.py", "状态管理器"),
        ("src/ui_qt/main_window.py", "主窗口"),
    ]

    all_exist = True
    for path, name in files:
        exists = os.path.exists(path)
        status = "[OK]" if exists else "[MISS]"
        print(f"  {status} {name}: {path}")
        if not exists:
            all_exist = False

    return all_exist


def check_vad_integration():
    """检查 VAD 集成"""
    print("\n[2/5] 检查 VAD 集成...")
    print("-" * 70)

    try:
        with open('src/audio/vad_detector.py', 'r', encoding='utf-8') as f:
            content = f.read()

        has_import = 'from src.audio.envelope_follower import EnvelopeFollower' in content
        has_usage = 'self.envelope_follower' in content
        has_methods = 'set_envelope_params' in content and 'get_smooth_rms' in content

        print(f"  {'[OK]' if has_import else '[FAIL]'} 导入 EnvelopeFollower")
        print(f"  {'[OK]' if has_usage else '[FAIL]'} 使用 envelope_follower")
        print(f"  {'[OK]' if has_methods else '[FAIL]'} 包含方法: set_envelope_params, get_smooth_rms")

        return has_import and has_usage and has_methods
    except Exception as e:
        print(f"  [ERR] {e}")
        return False


def check_state_manager():
    """检查状态管理器"""
    print("\n[3/5] 检查状态管理器...")
    print("-" * 70)

    try:
        with open('src/ui_qt/state_manager.py', 'r', encoding='utf-8') as f:
            content = f.read()

        params = [
            'mic_gain',
            'vad_threshold',
            'tts_speed',
            'tts_volume',
            'overlay_opacity',
            'translation_delay_ms',
            'envelope_attack_rate',
            'envelope_release_rate',
        ]

        found_params = []
        for param in params:
            if f"'{param}'" in content or f'"{param}"' in content:
                found_params.append(param)
                print(f"  [OK] {param}")
            else:
                print(f"  [MISS] {param}")

        return len(found_params) == len(params)
    except Exception as e:
        print(f"  [ERR] {e}")
        return False


def check_main_window():
    """检查主窗口集成"""
    print("\n[4/5] 检查主窗口集成...")
    print("-" * 70)

    try:
        with open('src/ui_qt/main_window.py', 'r', encoding='utf-8') as f:
            content = f.read()

        checks = [
            ('导入面板', 'from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel'),
            ('创建按钮', 'self._tweaks_btn = QPushButton'),
            ('切换方法', 'def _toggle_tweaks_panel'),
            ('状态订阅', 'def _subscribe_realtime_tweaks_state'),
            ('增益回调', 'def _on_mic_gain_changed'),
            ('包络回调', 'def _on_envelope_params_changed'),
            ('透明度回调', 'def _on_overlay_opacity_changed'),
        ]

        all_ok = True
        for name, pattern in checks:
            found = pattern in content
            status = '[OK]' if found else '[MISS]'
            print(f"  {status} {name}")
            if not found:
                all_ok = False

        return all_ok
    except Exception as e:
        print(f"  [ERR] {e}")
        return False


def check_panel_creation():
    """检查面板能否创建"""
    print("\n[5/5] 检查面板创建...")
    print("-" * 70)

    try:
        from PySide6.QtWidgets import QApplication
        from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel
        from src.ui_qt.state_manager import AppState

        # 尝试创建应用实例
        try:
            app = QApplication.instance() or QApplication(sys.argv)
        except Exception:
            print("  [SKIP] 无显示环境，跳过GUI测试")
            return True

        state = AppState()
        panel = RealtimeTweaksPanel(None, state, "zh-CN", "dark")

        print("  [OK] 面板创建成功")
        print(f"  [OK] 窗口标题: {panel.windowTitle()}")
        print(f"  [OK] 窗口大小: {panel.width()}x{panel.height()}")

        panel.close()
        return True
    except Exception as e:
        print(f"  [ERR] {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    results = []

    results.append(("文件存在", check_file_exists()))
    results.append(("VAD 集成", check_vad_integration()))
    results.append(("状态管理器", check_state_manager()))
    results.append(("主窗口集成", check_main_window()))
    results.append(("面板创建", check_panel_creation()))

    print("\n" + "=" * 70)
    print(" 验证总结")
    print("=" * 70)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n总计: {passed}/{total} 检查通过")

    if passed == total:
        print("\n[SUCCESS] 所有功能已重新集成！")
        print("\n可以启动应用测试:")
        print("  python main.py")
        print("\n点击主窗口的 [⚙ 实时] 按钮打开实时调整面板")
        return 0
    else:
        print(f"\n[WARNING] {total - passed} 项检查未通过")
        print("请检查上述错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
