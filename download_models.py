"""
SenseVoiceSmall を  パッケージ同梱用にリポジトリ内   models     へ保存する  

使い方  
    python download  models  py
"""

from __future__ import annotations

import sys

from src.asr.sensevoice_model_manager import (
    ensure_packaging_model,
    packaging_model_dir,
    packaging_models_dir,
)


def download_sensevoice():
    dest = packaging_model_dir()
    if dest.exists() and (dest / "configuration.json").exists() and (dest / "model.pt").exists():
        print(f"[skip] sensevoice-small already exists at {dest}")
        return

    print(f"[download] sensevoice-small -> {dest} ...")
    try:
        ensure_packaging_model(progress_callback=print)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"[done] sensevoice-small saved to {dest}")


if __name__ == "__main__":
    print(f"Saving models to: {packaging_models_dir().resolve()}\n")
    download_sensevoice()
    print("\nSenseVoiceSmall is ready. You can now launch the app.")
