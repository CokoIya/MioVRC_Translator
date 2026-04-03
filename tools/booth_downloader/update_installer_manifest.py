from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the public installer manifest for Mio VRC Downloader.")
    parser.add_argument("--installer", required=True, help="Path to the public installer exe.")
    parser.add_argument("--url", required=True, help="Public download URL for the installer.")
    parser.add_argument("--version", required=True, help="Release version, for example v1.2.3.")
    parser.add_argument("--output", default="docs/installer_manifest.json", help="Manifest output path.")
    parser.add_argument("--app-exe", default="MioTranslator.exe", help="Installed app executable name.")
    parser.add_argument("--homepage-url", default="https://78hejiu.top", help="Official homepage URL.")
    parser.add_argument(
        "--published-at",
        default="",
        help="Optional ISO-8601 timestamp such as 2026-03-29T01:50:30+08:00.",
    )
    return parser.parse_args()


def sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    args = parse_args()
    installer_path = Path(args.installer).resolve()
    if not installer_path.is_file():
        raise FileNotFoundError(f"Installer not found: {installer_path}")

    payload = {
        "version": str(args.version).strip(),
        "installer_name": installer_path.name,
        "installer_url": str(args.url).strip(),
        "size_bytes": installer_path.stat().st_size,
        "sha256": sha256_of(installer_path),
        "app_exe": str(args.app_exe).strip(),
        "homepage_url": str(args.homepage_url).strip(),
    }
    published_at = str(args.published_at).strip()
    if published_at:
        payload["published_at"] = published_at

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
