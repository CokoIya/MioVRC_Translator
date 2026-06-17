#!/usr/bin/env python3
"""
快速验证脚本 - 检查实时调整功能是否正确集成
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_files():
    """检查所有必需文件是否存在"""
    print("=" * 60)
    print("检查 1: 文件完整性")
    print("=" * 60)

    files = [
        ("src/audio/envelope_follower.py", "音频包络跟随器"),
        ("src/ui_qt/realtime_tweaks_panel.py", "实时调整面板"),
        ("src/audio/vad_detector.py", "VAD 检测器"),
        ("src/ui_qt/state_manager.py", "状态管理器"),
        ("src/ui_qt/main_window.py", "主窗口"),
        ("tools/test_realtime_integration.py", "集成测试"),
    ]

    all_exist = True
    for path, name in files:
        exists = os.path.exists(path)
        status = "[OK]" if exists else "[MISS]"
        print(f"  {status} {name}: {path}")
        if not exists:
            all_exist = False

    return all_exist


def check_imports():
    """检查模块导入是否正常"""
    print("\n" + "=" * 60)
    print("检查 2: 模块导入")
    print("=" * 60)

    imports = [
        ("src.audio.envelope_follower", "EnvelopeFollower"),
        ("src.ui_qt.state_manager", "AppState"),
    ]

    all_ok = True
    for module_path, class_name in imports:
        try:
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            print(f"  [OK] {module_path}.{class_name}")
        except Exception as e:
            print(f"  [ERR] {module_path}.{class_name}: {e}")
            all_ok = False

    return all_ok


def check_state_params():
    """检查状态管理器参数"""
    print("\n" + "=" * 60)
    print("检查 3: 状态管理器参数")
    print("=" * 60)

    try:
        from src.ui_qt.state_manager import AppState

        state = AppState()

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

        all_ok = True
        for param in params:
            value = state.get(param)
            if value is not None:
                print(f"  [OK] {param}: {value}")
            else:
                print(f"  [MISS] {param}: 未定义")
                all_ok = False

        return all_ok
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        return False


def check_vad_integration():
    """检查 VAD 检测器集成"""
    print("\n" + "=" * 60)
    print("检查 4: VAD 检测器集成")
    print("=" * 60)

    try:
        from src.audio.vad_detector import VADDetector

        vad = VADDetector(use_envelope_follower=True)

        checks = [
            (hasattr(vad, 'envelope_follower'), "包络跟随器属性"),
            (hasattr(vad, 'get_smooth_rms'), "get_smooth_rms 方法"),
            (hasattr(vad, 'set_envelope_params'), "set_envelope_params 方法"),
            (vad.envelope_follower is not None, "包络跟随器已启用"),
        ]

        all_ok = True
        for check, name in checks:
            status = "[OK]" if check else "[MISS]"
            print(f"  {status} {name}")
            if not check:
                all_ok = False

        return all_ok
    except Exception as e:
        print(f"  [ERR] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def generate_checklist():
    """生成用户验证清单"""
    print("\n" + "=" * 60)
    print("用户验证清单")
    print("=" * 60)
    print("""
请在启动应用后手动验证以下项目：

[ ] 1. 应用正常启动
[ ] 2. 主窗口标题栏显示 [设置] 按钮（[Gear]图标）
[ ] 3. 点击 [设置] 按钮，实时调整面板弹出
[ ] 4. 面板显示所有调整项（麦克风、VAD、TTS等）
[ ] 5. 拖动滑块，参数值实时更新
[ ] 6. 拖动面板标题栏，面板可以移动
[ ] 7. 点击 [X] 按钮，面板关闭
[ ] 8. 切换主题，面板主题自动同步
[ ] 9. 关闭应用后重新启动，参数保持

如果以上都能正常工作，说明集成成功！
""")


def main():
    print("\n" + "=" * 60)
    print("实时调整功能 - 快速验证")
    print("=" * 60 + "\n")

    results = []

    # 运行检查
    results.append(("文件完整性", check_files()))
    results.append(("模块导入", check_imports()))
    results.append(("状态管理器参数", check_state_params()))
    results.append(("VAD 检测器集成", check_vad_integration()))

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n自动检查: {passed}/{total} 通过")

    if passed == total:
        print("\n[SUCCESS] 所有自动检查通过！")
        print("\n下一步: 启动应用进行手动验证")
        print("命令: python main.py")
    else:
        print(f"\n[WARNING] {total - passed} 项检查失败")
        print("请检查上述错误信息")

    # 生成用户验证清单
    generate_checklist()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
