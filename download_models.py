"""
Whisper Small と SenseVoiceSmall を、パッケージ同梱用にリポジトリ内 `models/` へ保存する。

通常:
    python download_models.py
    -> whisper-small を保存する。

ベータ版同梱:
    python download_models.py --sensevoice
    -> whisper-small に加えて sensevoice-small も保存する。
"""

from __future__ import annotations

import sys

from src.asr.model_manager import (
    ALLOWED_SIZES,
    ensure_packaging_model,
    packaging_model_dir,
    packaging_models_dir,
)
from src.asr.sensevoice_model_manager import (
    ensure_packaging_model as ensure_packaging_sensevoice_model,
    packaging_model_dir as packaging_sensevoice_model_dir,
)

SIZES = ALLOWED_SIZES


def download_whisper(size: str):
    dest = packaging_model_dir(size)
    if dest.exists() and (dest / "config.json").exists() and (dest / "model.bin").exists():
        print(f"[skip] whisper-{size} already exists at {dest}")
        return

    print(f"[download] whisper-{size} -> {dest} ...")
    try:
        ensure_packaging_model(size, progress_callback=print)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"[done] whisper-{size} saved to {dest}")


def download_sensevoice():
    dest = packaging_sensevoice_model_dir()
    if dest.exists() and (dest / "configuration.json").exists():
        print(f"[skip] sensevoice-small already exists at {dest}")
        return

    print(f"[download] sensevoice-small -> {dest} ...")
    try:
        ensure_packaging_sensevoice_model(progress_callback=print)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"[done] sensevoice-small saved to {dest}")


if __name__ == "__main__":
    include_sensevoice = "--sensevoice" in sys.argv or "--all" in sys.argv

    print(f"Saving models to: {packaging_models_dir().resolve()}\n")
    for size in SIZES:
        download_whisper(size)
    if include_sensevoice:
        download_sensevoice()
    print("\nAll requested models are ready. You can now launch the app.")
