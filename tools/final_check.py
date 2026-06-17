#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终启动验证 - 确认所有功能就绪
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 70)
print(" VRC Translator - 实时调整功能 - 最终验证")
print("=" * 70)

def run_all_checks():
    """运行所有检查"""

    print("\n[1/3] 运行集成测试...")
    print("-" * 70)

    result1 = os.system("python tools/test_realtime_integration.py")

    print("\n[2/3] 运行修改验证...")
    print("-" * 70)

    result2 = os.system("python tools/verify_fixes.py")

    print("\n[3/3] 检查文件完整性...")
    print("-" * 70)

    files = [
        "src/audio/envelope_follower.py",
        "src/ui_qt/realtime_tweaks_panel.py",
        "src/audio/vad_detector.py",
        "src/ui_qt/state_manager.py",
        "src/ui_qt/main_window.py",
    ]

    all_exist = True
    for f in files:
        exists = os.path.exists(f)
        status = "[OK]" if exists else "[MISS]"
        print(f"  {status} {f}")
        if not exists:
            all_exist = False

    print("\n" + "=" * 70)
    print(" 验证总结")
    print("=" * 70)

    if result1 == 0 and result2 == 0 and all_exist:
        print("\n[SUCCESS] 所有验证通过！")
        print("\n准备就绪！现在可以启动应用了：")
        print("\n  python main.py")
        print("\n启动后：")
        print("  1. 在主窗口标题栏找到 [⚙ 实时] 按钮")
        print("  2. 点击打开实时调整面板")
        print("  3. 拖动滑块调整参数，立即生效")
        print("\n详细使用说明：")
        print("  - HOW_TO_USE_REALTIME_TWEAKS.md")
        print("  - QUICK_START.md")
        return 0
    else:
        print("\n[WARNING] 部分验证未通过")
        print("请检查上述错误信息")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(run_all_checks())
    except KeyboardInterrupt:
        print("\n\n[CANCELLED] 用户中断")
        sys.exit(1)
