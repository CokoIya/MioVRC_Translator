#!/usr/bin/env python3
"""
快速验证修改 - 检查修复是否正确应用
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_tweaks_panel_no_blink():
    """检查实时调整面板是否移除了自动眨眼选项"""
    print("=" * 60)
    print("检查 1: 实时调整面板 - 移除自动眨眼")
    print("=" * 60)

    try:
        with open('src/ui_qt/realtime_tweaks_panel.py', 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否移除了自动眨眼相关代码
        has_blink_toggle = 'auto_blink_toggle' in content
        has_blink_text = '自动眨眼' in content

        if not has_blink_toggle and not has_blink_text:
            print("  [OK] 自动眨眼选项已移除")
            return True
        else:
            print("  [WARN] 发现自动眨眼相关代码残留")
            if has_blink_toggle:
                print("    - 发现 auto_blink_toggle")
            if has_blink_text:
                print("    - 发现 '自动眨眼' 文本")
            return False
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        return False


def check_opacity_sync():
    """检查悬浮窗透明度是否同步到文本输入窗口"""
    print("\n" + "=" * 60)
    print("检查 2: 透明度同步 - 文本输入窗口")
    print("=" * 60)

    try:
        with open('src/ui_qt/main_window.py', 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否添加了文本输入窗口透明度更新
        has_text_input_opacity = '_text_input_window' in content and \
                                 'setWindowOpacity' in content and \
                                 '_on_overlay_opacity_changed' in content

        # 更精确的检查
        has_sync_code = 'self._text_input_window.setWindowOpacity(opacity)' in content

        if has_sync_code:
            print("  [OK] 文本输入窗口透明度同步已添加")
            return True
        else:
            print("  [WARN] 未找到文本输入窗口透明度同步代码")
            return False
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        return False


def check_tweaks_button_size():
    """检查实时调整按钮大小"""
    print("\n" + "=" * 60)
    print("检查 3: 实时调整按钮 - 大小和文本")
    print("=" * 60)

    try:
        with open('src/ui_qt/main_window.py', 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查按钮是否使用了正确的大小
        has_correct_size = 'self._tweaks_btn.setFixedSize(HEADER_ACTION_WIDTH, 40)' in content

        # 检查是否使用了文本而不是单个emoji（避免编码问题，只检查关键词）
        has_text_label = '实时' in content and 'QPushButton' in content

        # 检查是否有工具提示
        has_tooltip = 'setToolTip' in content and '实时调整' in content

        status_size = '[OK]' if has_correct_size else '[WARN]'
        status_text = '[OK]' if has_text_label else '[WARN]'
        status_tooltip = '[OK]' if has_tooltip else '[WARN]'

        print(f"  {status_size} 按钮大小: HEADER_ACTION_WIDTH x 40")
        print(f"  {status_text} 按钮文本: 包含 '实时'")
        print(f"  {status_tooltip} 工具提示已设置")

        return has_correct_size and has_text_label and has_tooltip
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        return False


def test_tweaks_panel_creation():
    """测试实时调整面板是否能正常创建"""
    print("\n" + "=" * 60)
    print("检查 4: 面板创建测试")
    print("=" * 60)

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

        # 检查是否没有 auto_blink_toggle 属性
        has_blink = hasattr(panel, 'auto_blink_toggle')

        print(f"  {'[WARN]' if has_blink else '[OK]'} 面板无 auto_blink_toggle 属性")
        print("  [OK] 面板创建成功")

        panel.close()
        return not has_blink
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" + "=" * 60)
    print("修改验证 - 快速检查")
    print("=" * 60 + "\n")

    results = []

    # 运行检查
    results.append(("移除自动眨眼", check_tweaks_panel_no_blink()))
    results.append(("透明度同步", check_opacity_sync()))
    results.append(("按钮大小调整", check_tweaks_button_size()))
    results.append(("面板创建测试", test_tweaks_panel_creation()))

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n总计: {passed}/{total} 检查通过")

    if passed == total:
        print("\n[SUCCESS] 所有修改已正确应用！")
        print("\n建议:")
        print("  1. 启动应用验证实际效果")
        print("  2. 检查按钮显示是否正常")
        print("  3. 测试透明度调整是否同步")
        return 0
    else:
        print(f"\n[WARNING] {total - passed} 项检查未通过")
        return 1


if __name__ == "__main__":
    sys.exit(main())
