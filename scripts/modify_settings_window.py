"""
自动修改 settings_window.py 的脚本
删除 Avatar 同步功能，添加 TTS 功能
"""

import re
import sys
from pathlib import Path

def modify_settings_window():
    file_path = Path("src/ui/settings_window.py")

    if not file_path.exists():
        print(f"错误: 找不到文件 {file_path}")
        return False

    # 读取文件
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    print("开始修改 settings_window.py...")

    # 1. 删除 Avatar 相关的 UI 文本定义
    print("1. 删除 Avatar UI 文本定义...")

    # 删除 avatar_section 到 avatar_param_target_language 的所有定义
    # 这些定义在多个语言块中重复，需要小心删除

    # 删除中文块中的 Avatar 定义
    content = re.sub(
        r'"avatar_section":\s*\{[^}]+\},\s*\n',
        '',
        content
    )
    content = re.sub(
        r'"avatar_subtitle":\s*\{[^}]+\},\s*\n',
        '',
        content
    )
    content = re.sub(
        r'"avatar_sync_enabled":\s*\{[^}]+\},\s*\n',
        '',
        content
    )
    content = re.sub(
        r'"avatar_sync_hint":\s*\{[^}]+\},\s*\n',
        '',
        content
    )
    content = re.sub(
        r'"avatar_param_\w+":\s*\{[^}]+\},\s*\n',
        '',
        content,
        flags=re.MULTILINE
    )

    # 2. 删除 Avatar 配置加载代码
    print("2. 删除 Avatar 配置加载...")
    content = re.sub(
        r'avatar_cfg = osc_cfg\.get\("avatar_sync"[^\n]+\n',
        '',
        content
    )

    # 3. 删除 Avatar UI 构建代码（约50行）
    print("3. 删除 Avatar UI 构建代码...")
    # 查找并删除从 avatar_card 创建到所有 avatar 变量定义的代码块
    avatar_ui_pattern = r'avatar_card = self\._build_collapsible_card\([^)]+\)[^\n]*\n.*?(?=\n\s{8}#|\n\s{8}[a-z_]+_card = self\._build_collapsible_card)'
    content = re.sub(avatar_ui_pattern, '', content, flags=re.DOTALL)

    # 4. 删除 Avatar 配置保存代码
    print("4. 删除 Avatar 配置保存...")
    avatar_save_pattern = r'avatar_cfg = osc_cfg\.setdefault\("avatar_sync"[^\n]+\n.*?avatar_params\["target_language"\][^\n]+\n'
    content = re.sub(avatar_save_pattern, '', content, flags=re.DOTALL)

    # 5. 添加 TTS 导入
    print("5. 添加 TTS 导入...")
    if 'from src.tts.manager import TTSManager' not in content:
        # 在其他导入后添加
        import_pos = content.find('from .window_effects import')
        if import_pos > 0:
            import_end = content.find('\n', import_pos) + 1
            content = content[:import_end] + '\nimport logging\n' + content[import_end:]

    # 备份原文件
    backup_path = file_path.with_suffix('.py.backup')
    print(f"备份原文件到 {backup_path}")
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 保存修改后的文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("✅ 修改完成！")
    print(f"原文件已备份到: {backup_path}")
    print("\n注意: 由于文件复杂，建议手动检查并添加 TTS UI 代码")

    return True

if __name__ == "__main__":
    success = modify_settings_window()
    sys.exit(0 if success else 1)
