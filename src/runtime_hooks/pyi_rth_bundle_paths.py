from __future__ import annotations

import os
import sys
from pathlib import Path


def _add_runtime_dir(path: Path) -> None:
    if not path.is_dir():
        return

    path_str = str(path)
    try:
        os.add_dll_directory(path_str)
    except (AttributeError, FileNotFoundError, OSError):
        pass

    current_path = os.environ.get("PATH", "")
    entries = current_path.split(os.pathsep) if current_path else []
    if path_str not in entries:
        os.environ["PATH"] = (
            path_str if not current_path else path_str + os.pathsep + current_path
        )


if getattr(sys, "frozen", False):
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    for candidate in (
        bundle_root,
        bundle_root / "torch" / "lib",
        bundle_root / "torchaudio" / "lib",
    ):
        _add_runtime_dir(candidate)
