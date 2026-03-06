"""
One-time script to download Whisper base and small models into models/.

Run once before first launch (or before building the installer):
    python download_models.py

Models are saved to:
    models/whisper-base/
    models/whisper-small/

After downloading, the app loads them locally with no network access.
"""

import pathlib
import sys


MODELS_DIR = pathlib.Path(__file__).parent / "models"
SIZES = ("base", "small")


def download(size: str):
    dest = MODELS_DIR / f"whisper-{size}"
    if dest.exists():
        print(f"[skip] whisper-{size} already exists at {dest}")
        return

    print(f"[download] whisper-{size} → {dest} ...")
    try:
        from faster_whisper import download_model
    except ImportError:
        print("ERROR: faster-whisper is not installed. Run: pip install faster-whisper")
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    download_model(size, output_dir=str(dest))
    print(f"[done] whisper-{size} saved to {dest}")


if __name__ == "__main__":
    print(f"Saving models to: {MODELS_DIR.resolve()}\n")
    for s in SIZES:
        download(s)
    print("\nAll models ready. You can now launch the app.")
