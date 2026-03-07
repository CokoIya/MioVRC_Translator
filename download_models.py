"""
Whisper base と small のモデルを、パッケージ同梱用に
リポジトリの `models/` ディレクトリへダウンロードする。

初回起動前、またはインストーラー作成前に実行する。  
    python download_models.py

保存先:
    models/whisper-base/
    models/whisper-small/

ダウンロード後は、PyInstaller とインストーラーがこれらのファイルを同梱できる。  
実行時に同梱モデルが見つからない場合、GUI は初回利用時に
LocalAppData へフォールバックダウンロードする。
"""

import sys

from src.asr.model_manager import (
    ALLOWED_SIZES,
    ensure_packaging_model,
    packaging_model_dir,
    packaging_models_dir,
)

SIZES = ALLOWED_SIZES


def download(size: str):
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


if __name__ == "__main__":
    print(f"Saving models to: {packaging_models_dir().resolve()}\n")
    for s in SIZES:
        download(s)
    print("\nAll models ready. You can now launch the app.")
