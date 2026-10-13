from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = ("openvr", "src.core.steamvr_", "src.core.vr_", "src.ui_qt.vr_")


def test_desktop_ui_imports_without_private_vr_modules():
    script = """
import importlib.abc
import sys

class BlockPrivateModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('openvr', 'src.core.steamvr_', 'src.core.vr_', 'src.ui_qt.vr_')):
            raise ImportError('Private VR module requested: ' + fullname)
        return None

sys.meta_path.insert(0, BlockPrivateModules())
from src.ui_qt.main_window import MainWindow
from src.ui_qt.settings_window import SettingsWindow
from src.utils import config_manager
config = {}
config_manager._ensure_vrc_listen_config(config, loaded={})
assert not any(key.startswith(('vr_', 'steamvr', 'screenshot')) for key in config['vrc_listen'])
assert not hasattr(MainWindow, '_start_vr_runtime_if_configured')
assert not hasattr(SettingsWindow, '_build_vr_overlay_section')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_source_has_no_private_vr_imports():
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(module.startswith(PRIVATE_PREFIXES) for module in modules), path
