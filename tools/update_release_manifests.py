"""Update mio_update.json and docs/installer_manifest.json after a release build.

Usage:
    python tools/update_release_manifests.py [installer_exe_path]

    If installer_exe_path is omitted, auto-detects from dist/ based on version.py.

Required env var:
    MIO_MANIFEST_SEED  — 64-hex-char Ed25519 seed for signing the manifests.
                         Must correspond to UPDATE_MANIFEST_PUBLIC_KEY in version.py.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.version import APP_VERSION
from src.updater.manifest_signature import sign_manifest, public_key_from_seed


MANI_KEYS_ORDERED = [
    "version", "installer_name", "url", "installer_url",
    "size_bytes", "sha256", "homepage_url", "published_at",
    "signature_algorithm", "signature_key_id", "signature",
]

INSTALLER_MANI_KEYS_ORDERED = [
    "version", "installer_name", "installer_url",
    "size_bytes", "sha256", "model_runtime_dir", "app_exe",
    "homepage_url", "published_at",
    "signature_algorithm", "signature_key_id", "signature",
]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _ordered_json(data: dict, keys: list[str]) -> str:
    ordered = {k: data[k] for k in keys if k in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    return json.dumps(ordered, ensure_ascii=False, indent=2)


def main() -> int:
    seed_hex = os.environ.get("MIO_MANIFEST_SEED", "").strip()
    if not seed_hex:
        print("ERROR: MIO_MANIFEST_SEED env var is not set.", file=sys.stderr)
        print("  Set it to the 64-hex-char Ed25519 seed that matches", file=sys.stderr)
        print(f"  UPDATE_MANIFEST_PUBLIC_KEY in src/version.py.", file=sys.stderr)
        return 1

    version = f"v{APP_VERSION}"

    if len(sys.argv) > 1:
        installer_path = Path(sys.argv[1])
    else:
        installer_path = ROOT / "dist" / f"MioTranslator-Setup-{version}.exe"

    if not installer_path.exists():
        print(f"ERROR: Installer not found: {installer_path}", file=sys.stderr)
        return 1

    print(f"Installer : {installer_path}")
    size_bytes = installer_path.stat().st_size
    sha256 = _sha256_file(installer_path)
    print(f"Size      : {size_bytes:,} bytes")
    print(f"SHA-256   : {sha256}")

    pub = public_key_from_seed(seed_hex)
    from src.version import UPDATE_MANIFEST_PUBLIC_KEY
    if pub != UPDATE_MANIFEST_PUBLIC_KEY:
        print(
            f"ERROR: Seed produces public key {pub!r},\n"
            f"       but version.py expects   {UPDATE_MANIFEST_PUBLIC_KEY!r}",
            file=sys.stderr,
        )
        return 1

    published_at = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime(
        "%Y-%m-%dT%H:%M:%S+08:00"
    )
    installer_name = f"MioTranslator-Setup-{version}.exe"
    github_url = (
        f"https://github.com/CokoIya/MioVRC_Translator/releases/download"
        f"/{version}/{installer_name}"
    )

    # ── mio_update.json ──────────────────────────────────────────────────────
    update_manifest: dict = {
        "version": version,
        "installer_name": installer_name,
        "url": github_url,
        "installer_url": github_url,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "homepage_url": "https://78hejiu.top",
        "published_at": published_at,
        "signature_algorithm": "ed25519",
        "signature_key_id": "mio-update-ed25519-v1",
    }
    sig_update = sign_manifest(update_manifest, seed_hex)
    update_manifest["signature"] = sig_update

    update_path = ROOT / "mio_update.json"
    update_path.write_text(_ordered_json(update_manifest, MANI_KEYS_ORDERED), encoding="utf-8")
    print(f"\nWritten: {update_path}")

    # ── docs/installer_manifest.json ────────────────────────────────────────
    existing_installer_path = ROOT / "docs" / "installer_manifest.json"
    try:
        existing = json.loads(existing_installer_path.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    installer_manifest: dict = {
        "version": version,
        "installer_name": installer_name,
        "installer_url": github_url,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "model_runtime_dir": existing.get(
            "model_runtime_dir",
            r"%LOCALAPPDATA%\Mio RealTime Translator\runtime_models\iic--SenseVoiceSmall",
        ),
        "app_exe": "MioTranslator.exe",
        "homepage_url": "https://78hejiu.top",
        "published_at": published_at,
        "signature_algorithm": "ed25519",
        "signature_key_id": "mio-update-ed25519-v1",
    }
    sig_installer = sign_manifest(installer_manifest, seed_hex)
    installer_manifest["signature"] = sig_installer

    existing_installer_path.write_text(
        _ordered_json(installer_manifest, INSTALLER_MANI_KEYS_ORDERED),
        encoding="utf-8",
    )
    print(f"Written: {existing_installer_path}")
    print("\nDone. Verify the signatures before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
