# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""The signed section of the remote catalog.

The catalog itself is fetched over TLS without a signature, so it may only
tune labels, model names and timeouts (``catalog_loader``). Values that change
how the app talks to a service - today the identity Microsoft's free speech
recogniser checks - may come only from this section, which the release key
signs::

    "signed": {
        "payload": {"edge_stt": {"trusted_client_token": ..., ...}},
        "signature_algorithm": "ed25519",
        "signature_key_id": "mio-catalog-ed25519-v2",
        "signature": "<hex>"
    }

A catalog without the section, or with one that does not verify, puts the
built-in values back: the current catalog is always the source of truth.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from src.asr import edge_stt_protocol
from src.updater.manifest_signature import (
    canonical_manifest_bytes,
    normalize_trusted_manifest_public_keys,
    verify_ed25519,
)
from src.version import TRUSTED_CATALOG_PUBLIC_KEYS

logger = logging.getLogger(__name__)

SIGNED_FIELD = "signed"
SIGNATURE_ALGORITHM = "ed25519"
# Domain separation: a signature over a catalog payload can never be replayed
# as an update-manifest or installer signature made with the same key.
SIGNING_CONTEXT = b"Mio RealTime Translator catalog overrides v1\x00"


def signing_message(payload: Mapping[str, object]) -> bytes:
    return SIGNING_CONTEXT + canonical_manifest_bytes(payload)


def verified_payload(
    catalog: object,
    trusted_keys: Mapping[object, object] | Iterable[tuple[object, object]] | None = None,
) -> dict | None:
    """The signed payload when it verifies against a trusted key, else None."""

    if trusted_keys is None:
        trusted_keys = TRUSTED_CATALOG_PUBLIC_KEYS
    if not isinstance(catalog, Mapping):
        return None
    section = catalog.get(SIGNED_FIELD)
    if section is None:
        return None
    if not isinstance(section, Mapping):
        logger.warning("Catalog signed section ignored: not an object")
        return None
    payload = section.get("payload")
    key_id = str(section.get("signature_key_id") or "").strip()
    signature = str(section.get("signature") or "").strip().lower()
    algorithm = str(section.get("signature_algorithm") or SIGNATURE_ALGORITHM).strip().lower()
    if not isinstance(payload, dict) or algorithm != SIGNATURE_ALGORITHM:
        logger.warning("Catalog signed section ignored: malformed")
        return None
    try:
        public_key = normalize_trusted_manifest_public_keys(trusted_keys).get(key_id)
    except Exception:
        public_key = None
    if public_key is None:
        logger.warning("Catalog signed section ignored: untrusted key id")
        return None
    try:
        valid = verify_ed25519(public_key, signature, signing_message(payload))
    except Exception:
        valid = False
    if not valid:
        logger.warning("Catalog signed section ignored: signature does not verify")
        return None
    return payload


def apply_signed_catalog(catalog: object) -> None:
    """Apply what the signed section carries (or restore the built-in values)."""

    payload = verified_payload(catalog)
    identity = None
    if payload is not None and "edge_stt" in payload:
        identity = edge_stt_protocol.EdgeSpeechIdentity.parse(payload.get("edge_stt"))
        if identity is None:
            logger.warning("Catalog signed Edge speech identity ignored: malformed values")
    if edge_stt_protocol.set_identity(identity):
        if identity is None:
            logger.info("Edge speech recognition uses the built-in client identity")
        else:
            logger.info(
                "Edge speech recognition uses the signed catalog identity "
                "(chromium=%s)",
                identity.chromium_full_version,
            )
