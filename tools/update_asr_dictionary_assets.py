from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_DICTIONARY_URL = "https://78hejiu.top/dictionaries/asr_terms.official.json"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dictionary(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Dictionary payload is not a JSON object: {path}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError(f"Dictionary entries must be a JSON list: {path}")
    version = str(payload.get("version", "")).strip()
    if not version:
        raise RuntimeError(f"Dictionary version is missing: {path}")
    return payload


def _dump_json(path: Path, payload: dict) -> bytes:
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialized)
    return serialized


def _bundled_payload(official_payload: dict) -> dict:
    bundled = dict(official_payload)
    bundled["version"] = f"bundled-{official_payload['version']}"
    bundled["source"] = "bundled"
    bundled["description"] = "Bundled baseline ASR correction rules."
    return bundled


def sync_dictionary_assets(source: Path, dictionary_url: str) -> dict[str, object]:
    root = _project_root()
    official_payload = _load_dictionary(source)

    assets_dir = root / "assets" / "dictionaries"
    docs_dir = root / "docs" / "dictionaries"
    official_targets = [
        assets_dir / "asr_terms.official.json",
        docs_dir / "asr_terms.official.json",
    ]
    bundled_target = assets_dir / "asr_terms.base.json"
    manifest_targets = [
        assets_dir / "asr_dictionary_manifest.json",
        docs_dir / "asr_dictionary_manifest.json",
    ]

    serialized = b""
    for target in official_targets:
        serialized = _dump_json(target, official_payload)

    _dump_json(bundled_target, _bundled_payload(official_payload))

    sha256 = hashlib.sha256(serialized).hexdigest()
    manifest_payload = {
        "version": str(official_payload["version"]),
        "dictionary_url": dictionary_url,
        "sha256": sha256,
    }
    for target in manifest_targets:
        _dump_json(target, manifest_payload)

    return {
        "version": official_payload["version"],
        "entry_count": len(official_payload.get("entries", [])),
        "sha256": sha256,
        "dictionary_url": dictionary_url,
        "source": str(source),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ASR dictionary assets and manifests.")
    parser.add_argument(
        "--source",
        default="dictionaries/asr_terms.official.json",
        help="Source dictionary JSON path relative to repo root.",
    )
    parser.add_argument(
        "--dictionary-url",
        default=DEFAULT_DICTIONARY_URL,
        help="Published dictionary download URL stored in the manifest.",
    )
    args = parser.parse_args()

    root = _project_root()
    result = sync_dictionary_assets(
        (root / args.source).resolve(),
        str(args.dictionary_url).strip() or DEFAULT_DICTIONARY_URL,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

