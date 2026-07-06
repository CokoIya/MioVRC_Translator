from __future__ import annotations

import subprocess
import sys


def _assert_module_does_not_import_heavy_runtime(module_name: str) -> None:
    script = f"""
import importlib
import sys

importlib.import_module({module_name!r})
heavy_roots = {{
    "TTS",
    "funasr",
    "style_bert_vits2",
    "torch",
    "torchaudio",
    "transformers",
}}
loaded = sorted(name for name in heavy_roots if name in sys.modules)
if loaded:
    raise SystemExit("unexpected heavy runtime imports: " + ", ".join(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_tts_manager_import_does_not_load_local_ai_runtimes() -> None:
    _assert_module_does_not_import_heavy_runtime("src.tts.manager")


def test_settings_window_import_does_not_load_local_ai_runtimes() -> None:
    _assert_module_does_not_import_heavy_runtime("src.ui_qt.settings_window")
