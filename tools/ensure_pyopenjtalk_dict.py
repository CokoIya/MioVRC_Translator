"""Ensure pyopenjtalk's Open JTalk dictionary exists before packaging.

pyopenjtalk downloads ``open_jtalk_dic_utf_8-1.11`` lazily on first Japanese
G2P use.  A frozen desktop app should not perform that download during TTS
playback, so the release build pre-warms the dictionary in the release venv and
PyInstaller then collects it from the package directory.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        import pyopenjtalk
    except Exception as exc:
        print(f"pyopenjtalk is not importable: {exc}", file=sys.stderr)
        return 1

    dictionary_dir = Path(
        getattr(pyopenjtalk, "OPEN_JTALK_DICT_DIR", b"").decode("utf-8", "ignore")
    )
    if not dictionary_dir.exists():
        print(f"Preparing pyopenjtalk dictionary: {dictionary_dir}")
        pyopenjtalk.g2p("こんにちは")

    if not dictionary_dir.exists():
        print(f"pyopenjtalk dictionary is still missing: {dictionary_dir}", file=sys.stderr)
        return 1

    required = ("char.bin", "matrix.bin", "sys.dic", "unk.dic")
    missing = [name for name in required if not (dictionary_dir / name).is_file()]
    if missing:
        print(
            f"pyopenjtalk dictionary is incomplete: {dictionary_dir} missing {missing}",
            file=sys.stderr,
        )
        return 1

    print(f"pyopenjtalk dictionary ready: {dictionary_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
