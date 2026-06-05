from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    generate_seed_hex,
    public_key_from_seed,
    sign_manifest,
    verify_manifest_signature,
)


def _read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_seed(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().split()[0]


def _generate_key(args: argparse.Namespace) -> int:
    seed = generate_seed_hex()
    public_key = public_key_from_seed(seed)
    seed_path = Path(args.seed_out)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(seed + "\n", encoding="utf-8")
    if args.public_out:
        public_path = Path(args.public_out)
        public_path.parent.mkdir(parents=True, exist_ok=True)
        public_path.write_text(public_key + "\n", encoding="utf-8")
    print(f"Private seed written to: {seed_path}")
    print(f"Public key: {public_key}")
    if args.public_out:
        print(f"Public key written to: {Path(args.public_out)}")
    return 0


def _sign(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _read_json(manifest_path)
    signature = sign_manifest(manifest, _read_seed(Path(args.seed_file)))
    manifest[SIGNATURE_ALGORITHM_FIELD] = SIGNATURE_ALGORITHM
    if args.key_id:
        manifest[SIGNATURE_KEY_ID_FIELD] = str(args.key_id).strip()
    manifest[SIGNATURE_FIELD] = signature
    _write_json(Path(args.output or manifest_path), manifest)
    print(f"Signed manifest: {Path(args.output or manifest_path)}")
    print(f"Signature: {signature}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _read_json(manifest_path)
    public_key = str(args.public_key or "").strip()
    if not public_key and args.public_key_file:
        public_key = Path(args.public_key_file).read_text(encoding="utf-8").strip().split()[0]
    verify_manifest_signature(
        manifest,
        public_key,
        required=True,
        expected_key_id=args.key_id,
    )
    print(f"Manifest signature OK: {manifest_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and verify Mio update manifest signatures.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-key", help="Generate an Ed25519 signing seed.")
    generate.add_argument("--seed-out", required=True, help="Path for the private seed hex file.")
    generate.add_argument("--public-out", default="", help="Optional path for the public key hex file.")
    generate.set_defaults(func=_generate_key)

    sign = subparsers.add_parser("sign", help="Sign an update manifest JSON file.")
    sign.add_argument("--manifest", required=True, help="Manifest JSON path.")
    sign.add_argument("--seed-file", required=True, help="Private seed hex file.")
    sign.add_argument("--output", default="", help="Output path. Defaults to updating the manifest in place.")
    sign.add_argument("--key-id", default="", help="Optional signature key id to write into the manifest.")
    sign.set_defaults(func=_sign)

    verify = subparsers.add_parser("verify", help="Verify a signed update manifest.")
    verify.add_argument("--manifest", required=True, help="Manifest JSON path.")
    verify.add_argument("--public-key", default="", help="Public key hex.")
    verify.add_argument("--public-key-file", default="", help="File containing public key hex.")
    verify.add_argument("--key-id", default="", help="Optional expected signature key id.")
    verify.set_defaults(func=_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
