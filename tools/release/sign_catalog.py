"""Sign the override section of ``docs/catalog.json`` with the release key.

The app applies the signed section at startup and after each catalog refresh
(``src/utils/signed_catalog.py``). Use it when Microsoft rotates what its free
speech recogniser checks, so every install picks up the new values without a
new installer.

The Ed25519 seed is read from ``MIO_RELEASE_SIGNING_SEED`` or from the file
named by ``MIO_RELEASE_SIGNING_SEED_FILE``; never pass it on the command line.

    # replace the Edge speech identity
    python tools/release/sign_catalog.py \\
        --edge-stt-token 0123456789ABCDEF0123456789ABCDEF \\
        --edge-stt-chromium-version 144.0.3700.12 \\
        --edge-stt-origin chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold

    # go back to the identity built into the release
    python tools/release/sign_catalog.py --clear-edge-stt

    # check what docs/catalog.json carries (no seed needed)
    python tools/release/sign_catalog.py --verify

Publish the edited file like any other change to docs/ (GitHub Pages serves it
at https://miovrc.com/catalog.json and the app also reads the raw GitHub copy).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.asr.edge_stt_protocol import DEFAULT_IDENTITY, EdgeSpeechIdentity  # noqa: E402
from src.updater.manifest_signature import (  # noqa: E402
    ManifestSignatureError,
    public_key_from_seed,
    sign_ed25519,
)
from src.utils.signed_catalog import (  # noqa: E402
    SIGNATURE_ALGORITHM,
    SIGNED_FIELD,
    signing_message,
    verified_payload,
)
from src.version import CATALOG_SIGNATURE_KEY_ID, TRUSTED_CATALOG_PUBLIC_KEYS  # noqa: E402

DEFAULT_CATALOG = ROOT / "docs" / "catalog.json"


def _load_seed() -> str:
    seed = os.environ.get("MIO_RELEASE_SIGNING_SEED", "").strip()
    seed_file = os.environ.get("MIO_RELEASE_SIGNING_SEED_FILE", "").strip()
    if not seed and seed_file:
        seed = Path(seed_file).read_text(encoding="ascii").strip()
    if not seed:
        raise SystemExit(
            "ERROR: set MIO_RELEASE_SIGNING_SEED or MIO_RELEASE_SIGNING_SEED_FILE."
        )
    trusted = dict(TRUSTED_CATALOG_PUBLIC_KEYS)[CATALOG_SIGNATURE_KEY_ID]
    if public_key_from_seed(seed) != trusted:
        raise SystemExit("ERROR: the seed does not match the trusted catalog key.")
    return seed


def _read_catalog(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: {path} is not a JSON object.")
    return data


def _write_catalog(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _describe(data: dict) -> int:
    if SIGNED_FIELD not in data:
        print("No signed section: the app uses its built-in values.")
        return 0
    payload = verified_payload(data)
    if payload is None:
        print("Signed section present but it does NOT verify; the app ignores it.")
        return 1
    print("Signed section verifies. Payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    edge = payload.get("edge_stt")
    if edge is not None and EdgeSpeechIdentity.parse(edge) is None:
        print("WARNING: edge_stt values are malformed; the app ignores them.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--verify", action="store_true", help="only report what is signed")
    parser.add_argument("--edge-stt-token")
    parser.add_argument("--edge-stt-chromium-version")
    parser.add_argument("--edge-stt-origin")
    parser.add_argument(
        "--clear-edge-stt",
        action="store_true",
        help="drop the Edge speech override (the app returns to its built-in values)",
    )
    args = parser.parse_args(argv)

    data = _read_catalog(args.catalog)
    if args.verify:
        return _describe(data)

    payload = dict(verified_payload(data) or {})
    payload.pop("issued_at", None)
    edge_args =(args.edge_stt_token, args.edge_stt_chromium_version, args.edge_stt_origin)
    if args.clear_edge_stt:
        payload.pop("edge_stt", None)
    elif any(edge_args):
        current = EdgeSpeechIdentity.parse(payload.get("edge_stt")) or DEFAULT_IDENTITY
        identity = EdgeSpeechIdentity.parse(
            {
                "trusted_client_token": args.edge_stt_token or current.trusted_client_token,
                "chromium_full_version": (
                    args.edge_stt_chromium_version or current.chromium_full_version
                ),
                "origin": args.edge_stt_origin or current.origin,
            }
        )
        if identity is None:
            raise SystemExit(
                "ERROR: token must be 32 hex characters, the version four dotted "
                "numbers, and the origin chrome-extension://<32 letters a-p>."
            )
        payload["edge_stt"] = {
            "trusted_client_token": identity.trusted_client_token,
            "chromium_full_version": identity.chromium_full_version,
            "origin": identity.origin,
        }
    else:
        parser.error("nothing to sign: pass --edge-stt-* values, --clear-edge-stt or --verify")

    if not payload:
        data.pop(SIGNED_FIELD, None)
        _write_catalog(args.catalog, data)
        print(f"Removed the signed section from {args.catalog}.")
        return 0

    payload["issued_at"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        signature = sign_ed25519(_load_seed(), signing_message(payload))
    except ManifestSignatureError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    data[SIGNED_FIELD] = {
        "payload": payload,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_key_id": CATALOG_SIGNATURE_KEY_ID,
        "signature": signature,
    }
    _write_catalog(args.catalog, data)
    if verified_payload(data) is None:
        raise SystemExit("ERROR: the written signature does not verify.")
    print(f"Signed {args.catalog} ({', '.join(sorted(payload))}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
