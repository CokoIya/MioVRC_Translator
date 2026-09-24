"""The signed catalog section and the replaceable Edge speech identity."""

from __future__ import annotations

import copy
import json

import pytest

from src.asr import edge_stt_protocol as protocol
from src.updater.manifest_signature import (
    canonical_manifest_bytes,
    generate_seed_hex,
    public_key_from_seed,
    sign_ed25519,
    sign_manifest,
)
from src.utils import signed_catalog

NEW_IDENTITY = {
    "trusted_client_token": "0123456789ABCDEF0123456789ABCDEF",
    "chromium_full_version": "150.0.4000.10",
    "origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
}


@pytest.fixture
def signer(monkeypatch):
    seed = generate_seed_hex()
    monkeypatch.setattr(
        signed_catalog,
        "TRUSTED_CATALOG_PUBLIC_KEYS",
        (("test-catalog-key", public_key_from_seed(seed)),),
    )
    protocol.set_identity(None)
    yield seed
    protocol.set_identity(None)


def _signed(seed: str, payload: dict, *, key_id: str = "test-catalog-key") -> dict:
    return {
        "version": 1,
        "translation_backends": {},
        "signed": {
            "payload": payload,
            "signature_algorithm": "ed25519",
            "signature_key_id": key_id,
            "signature": sign_ed25519(seed, signed_catalog.signing_message(payload)),
        },
    }


def test_a_verified_section_replaces_the_edge_identity(signer):
    signed_catalog.apply_signed_catalog(_signed(signer, {"edge_stt": NEW_IDENTITY}))

    identity = protocol.current_identity()
    assert identity.trusted_client_token == NEW_IDENTITY["trusted_client_token"]
    url = protocol.build_recognition_url("ja-JP")
    assert "TrustedClientToken=0123456789ABCDEF0123456789ABCDEF" in url
    assert "Sec-MS-GEC-Version=1-150.0.4000.10" in url
    headers = protocol.wss_headers()
    assert headers["Origin"] == NEW_IDENTITY["origin"]
    assert "Chrome/150.0.0.0" in headers["User-Agent"]


def test_a_tampered_payload_is_ignored_and_the_builtin_identity_returns(signer):
    catalog = _signed(signer, {"edge_stt": NEW_IDENTITY})
    signed_catalog.apply_signed_catalog(catalog)
    tampered = copy.deepcopy(catalog)
    tampered["signed"]["payload"]["edge_stt"]["origin"] = (
        "chrome-extension://pppppppppppppppppppppppppppppppp"
    )

    signed_catalog.apply_signed_catalog(tampered)

    assert protocol.current_identity() == protocol.DEFAULT_IDENTITY


def test_an_untrusted_key_id_is_ignored(signer):
    catalog = _signed(signer, {"edge_stt": NEW_IDENTITY}, key_id="someone-else")

    assert signed_catalog.verified_payload(catalog) is None


def test_a_catalog_without_the_section_restores_the_builtin_identity(signer):
    signed_catalog.apply_signed_catalog(_signed(signer, {"edge_stt": NEW_IDENTITY}))
    signed_catalog.apply_signed_catalog({"version": 1, "translation_backends": {}})

    assert protocol.current_identity() == protocol.DEFAULT_IDENTITY


def test_malformed_signed_values_are_not_used(signer):
    bad = dict(NEW_IDENTITY, trusted_client_token="not-a-token")

    signed_catalog.apply_signed_catalog(_signed(signer, {"edge_stt": bad}))

    assert protocol.current_identity() == protocol.DEFAULT_IDENTITY


def test_a_manifest_signature_cannot_be_replayed_as_a_catalog_signature(signer):
    payload = {"edge_stt": NEW_IDENTITY}
    catalog = _signed(signer, payload)
    # Same key, same payload, but signed the way update manifests are.
    catalog["signed"]["signature"] = sign_manifest(payload, signer)

    assert signed_catalog.verified_payload(catalog) is None
    assert signed_catalog.signing_message(payload) != canonical_manifest_bytes(payload)


def test_the_published_catalog_is_valid_json_and_any_signed_section_verifies():
    """docs/catalog.json is what players download; a broken signature there
    would silently put everyone back on the built-in values."""

    with open("docs/catalog.json", encoding="utf-8") as handle:
        data = json.load(handle)
    if "signed" in data:
        assert signed_catalog.verified_payload(data) is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("trusted_client_token", "0123"),
        ("chromium_full_version", "150"),
        ("origin", "https://example.com"),
        ("origin", "chrome-extension://ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP"),
    ],
)
def test_identity_parse_rejects_malformed_fields(field, value):
    assert protocol.EdgeSpeechIdentity.parse(dict(NEW_IDENTITY, **{field: value})) is None


def test_default_identity_matches_the_shipped_constants():
    assert protocol.DEFAULT_IDENTITY.trusted_client_token == protocol.TRUSTED_CLIENT_TOKEN
    assert protocol.WSS_HEADERS == protocol.wss_headers(protocol.DEFAULT_IDENTITY)
    assert protocol.DEFAULT_IDENTITY.sec_ms_gec_version == protocol.SEC_MS_GEC_VERSION


def test_sign_catalog_tool_round_trip(signer, tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sign_catalog_tool", "tools/release/sign_catalog.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool, "TRUSTED_CATALOG_PUBLIC_KEYS", signed_catalog.TRUSTED_CATALOG_PUBLIC_KEYS)
    monkeypatch.setattr(tool, "CATALOG_SIGNATURE_KEY_ID", "test-catalog-key")
    monkeypatch.setenv("MIO_RELEASE_SIGNING_SEED", signer)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"version": 1, "translation_backends": {}}), encoding="utf-8")

    assert tool.main(
        [
            "--catalog", str(catalog),
            "--edge-stt-token", NEW_IDENTITY["trusted_client_token"],
            "--edge-stt-chromium-version", NEW_IDENTITY["chromium_full_version"],
            "--edge-stt-origin", NEW_IDENTITY["origin"],
        ]
    ) == 0
    signed = json.loads(catalog.read_text(encoding="utf-8"))
    assert signed_catalog.verified_payload(signed)["edge_stt"] == NEW_IDENTITY
    assert tool.main(["--catalog", str(catalog), "--verify"]) == 0

    assert tool.main(["--catalog", str(catalog), "--clear-edge-stt"]) == 0
    assert "signed" not in json.loads(catalog.read_text(encoding="utf-8"))
