"""
SenseVoiceSmall を  パッケージ同梱用にリポジトリ内   models     へ保存する

使い方
    python tools/download_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.asr.sensevoice_model_manager import (
    ensure_packaging_model,
    packaging_model_dir,
    packaging_models_dir,
)
from src.asr.model_manager import verify_model_integrity
from src.asr.model_registry import get_asr_engine_spec


def _print_progress(event) -> None:
    if isinstance(event, dict):
        message = str(event.get("message", "")).strip()
        labels = {
            "download_prepare": "preparing download",
            "downloading": "downloading",
            "download_complete": "download complete",
            "loading": "loading model",
            "ready": "model ready",
        }
        message = labels.get(message, message)
        progress = event.get("progress")
        if isinstance(progress, (int, float)):
            print(f"[progress] {message} {progress * 100:.1f}%")
            return
        print(f"[progress] {message}")
        return
    print(event)


def download_sensevoice():
    dest = packaging_model_dir()
    spec = get_asr_engine_spec("sensevoice-small")
    if (
        dest.exists()
        and ((dest / "configuration.json").exists() or (dest / "config.yaml").exists())
        and (dest / "model.pt").exists()
        and verify_model_integrity(dest, spec)
    ):
        print(f"[skip] sensevoice-small already exists at {dest}")
        return

    print(f"[download] sensevoice-small -> {dest} ...")
    try:
        ensure_packaging_model(progress_callback=_print_progress)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"[done] sensevoice-small saved to {dest}")


if __name__ == "__main__":
    print(f"Saving models to: {packaging_models_dir().resolve()}\n")
    download_sensevoice()
    print("\nSenseVoiceSmall is ready. You can now launch the app.")