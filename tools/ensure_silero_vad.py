#!/usr/bin/env python3
"""Install the pinned Silero VAD TorchScript asset for release builds."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.secure_http import (  # noqa: E402
    open_trusted_https_url,
    read_bounded_response,
)


SILERO_VAD_REVISION = "7e30209a3e901f9842f81b225f3e93d8199902b1"
SILERO_VAD_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    f"{SILERO_VAD_REVISION}/src/silero_vad/data/silero_vad.jit"
)
SILERO_VAD_SHA256 = (
    "e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720"
)
SILERO_VAD_SIZE = 2_272_526
_MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024
_TRUSTED_HOSTS = frozenset({"raw.githubusercontent.com"})
DEFAULT_DESTINATION = _REPO_ROOT / "src" / "audio" / "models" / "silero_vad.jit"


def verify_model(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != SILERO_VAD_SIZE:
            return False
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        return digest == SILERO_VAD_SHA256
    except OSError:
        return False


def ensure_model(destination: Path = DEFAULT_DESTINATION) -> Path:
    destination = destination.resolve(strict=False)
    if verify_model(destination):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with open_trusted_https_url(
        SILERO_VAD_URL,
        trusted_hosts=_TRUSTED_HOSTS,
        timeout=30.0,
        label="Silero VAD model",
        max_redirects=2,
        headers={"User-Agent": "MioTranslator-release-builder/1"},
    ) as response:
        payload = read_bounded_response(
            response,
            limit=_MAX_DOWNLOAD_BYTES,
            label="Silero VAD model",
        )

    if len(payload) != SILERO_VAD_SIZE:
        raise RuntimeError("Silero VAD model size verification failed")
    if hashlib.sha256(payload).hexdigest() != SILERO_VAD_SHA256:
        raise RuntimeError("Silero VAD model SHA256 verification failed")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".silero-vad-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if not verify_model(destination):
        destination.unlink(missing_ok=True)
        raise RuntimeError("Installed Silero VAD model failed final verification")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the pinned model without downloading it",
    )
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    destination = args.destination.resolve(strict=False)
    if args.check:
        if not verify_model(destination):
            raise SystemExit(f"Silero VAD model is missing or invalid: {destination}")
    else:
        ensure_model(destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
