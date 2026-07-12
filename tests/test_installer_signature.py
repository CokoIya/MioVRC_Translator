import hashlib

import pytest

from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    InstallerSignatureError,
    installer_signature_message,
    normalize_trusted_public_keys,
    parse_installer_signature_metadata,
    sign_installer_metadata,
    verify_installer_metadata_signature,
)
from src.updater.manifest_signature import public_key_from_seed


SEED = "31" * 32
KEY_ID = "installer-key-v1"
PUBLIC_KEY = public_key_from_seed(SEED)


def test_installer_metadata_signature_round_trip_and_domain_separation():
    payload = b"installer bytes"
    digest = hashlib.sha256(payload).hexdigest()
    signature = sign_installer_metadata(
        SEED,
        sha256=digest,
        size_bytes=len(payload),
    )

    assert verify_installer_metadata_signature(
        sha256=digest,
        size_bytes=len(payload),
        signature=signature,
        signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
        signature_key_id=KEY_ID,
        trusted_public_keys=((KEY_ID, PUBLIC_KEY),),
    ) == KEY_ID
    assert installer_signature_message(
        sha256=digest,
        size_bytes=len(payload),
    ).startswith(b"Mio RealTime Translator installer metadata v1\x00")

    with pytest.raises(InstallerSignatureError, match="signature is invalid"):
        verify_installer_metadata_signature(
            sha256="0" * 64,
            size_bytes=len(payload),
            signature=signature,
            signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
            signature_key_id=KEY_ID,
            trusted_public_keys=((KEY_ID, PUBLIC_KEY),),
        )
    with pytest.raises(InstallerSignatureError, match="signature is invalid"):
        verify_installer_metadata_signature(
            sha256=digest,
            size_bytes=len(payload) + 1,
            signature=signature,
            signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
            signature_key_id=KEY_ID,
            trusted_public_keys=((KEY_ID, PUBLIC_KEY),),
        )


def test_installer_signature_manifest_fields_are_strictly_required():
    signature = "ab" * 64
    metadata = parse_installer_signature_metadata(
        {
            INSTALLER_SIGNATURE_ALGORITHM_FIELD: INSTALLER_SIGNATURE_ALGORITHM,
            INSTALLER_SIGNATURE_KEY_ID_FIELD: KEY_ID,
            INSTALLER_SIGNATURE_FIELD: signature,
        }
    )
    assert metadata.signature == signature

    for missing in (
        INSTALLER_SIGNATURE_ALGORITHM_FIELD,
        INSTALLER_SIGNATURE_KEY_ID_FIELD,
        INSTALLER_SIGNATURE_FIELD,
    ):
        manifest = {
            INSTALLER_SIGNATURE_ALGORITHM_FIELD: INSTALLER_SIGNATURE_ALGORITHM,
            INSTALLER_SIGNATURE_KEY_ID_FIELD: KEY_ID,
            INSTALLER_SIGNATURE_FIELD: signature,
        }
        manifest.pop(missing)
        with pytest.raises(InstallerSignatureError):
            parse_installer_signature_metadata(manifest)


def test_trusted_public_key_configuration_is_nonempty_and_unambiguous():
    trusted = normalize_trusted_public_keys(
        ((KEY_ID, PUBLIC_KEY.upper()), (KEY_ID, PUBLIC_KEY))
    )
    assert dict(trusted) == {KEY_ID: PUBLIC_KEY}

    with pytest.raises(InstallerSignatureError, match="No trusted"):
        normalize_trusted_public_keys(())
    with pytest.raises(InstallerSignatureError, match="more than once"):
        normalize_trusted_public_keys(
            ((KEY_ID, PUBLIC_KEY), (KEY_ID, public_key_from_seed("32" * 32)))
        )
    with pytest.raises(InstallerSignatureError, match="key id"):
        normalize_trusted_public_keys((("bad key id", PUBLIC_KEY),))
