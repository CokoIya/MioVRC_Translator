import unittest
from unittest.mock import patch

from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    ManifestSignatureError,
    public_key_from_seed,
    sign_manifest,
    verify_ed25519,
    verify_manifest_signature,
    verify_manifest_signature_with_trusted_keys,
)


class ManifestSignatureTests(unittest.TestCase):
    def test_rfc8032_ed25519_known_vector(self):
        seed = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
        public_key = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
        signature = (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        )

        self.assertEqual(public_key_from_seed(seed), public_key)
        self.assertTrue(verify_ed25519(public_key, signature, b""))
        self.assertFalse(verify_ed25519(public_key, signature, b"tampered"))

    def test_cryptography_unavailability_fails_closed(self):
        with patch(
            "src.updater.manifest_signature._CRYPTOGRAPHY_IMPORT_ERROR",
            ImportError("missing cryptography"),
        ):
            with self.assertRaisesRegex(ManifestSignatureError, "cryptography"):
                public_key_from_seed("11" * 32)

    def test_sign_and_verify_manifest(self):
        seed = "22" * 32
        public_key = public_key_from_seed(seed)
        manifest = {
            "version": "v1.2.3",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.2.3/app.exe",
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: "test-key",
        }
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
        self.assertTrue(
            verify_manifest_signature(
                manifest,
                public_key,
                required=True,
                expected_key_id="test-key",
            )
        )

    def test_tampered_manifest_fails(self):
        seed = "33" * 32
        public_key = public_key_from_seed(seed)
        manifest = {
            "version": "v1.2.3",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.2.3/app.exe",
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
        }
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
        manifest["sha256"] = "b" * 64
        with self.assertRaises(ManifestSignatureError):
            verify_manifest_signature(manifest, public_key, required=True)

    def test_missing_signature_can_be_optional(self):
        manifest = {"version": "v1.2.3"}
        public_key = public_key_from_seed("44" * 32)
        self.assertFalse(verify_manifest_signature(manifest, public_key, required=False))
        with self.assertRaises(ManifestSignatureError):
            verify_manifest_signature(manifest, public_key, required=True)

    def test_trusted_key_allowlist_supports_rotation_overlap(self):
        old_seed = "55" * 32
        new_seed = "66" * 32
        manifest = {
            "version": "v2.0.0",
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: "new-key",
        }
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, new_seed)

        verified_key_id = verify_manifest_signature_with_trusted_keys(
            manifest,
            (
                ("old-key", public_key_from_seed(old_seed)),
                ("new-key", public_key_from_seed(new_seed)),
            ),
            required=True,
        )

        self.assertEqual(verified_key_id, "new-key")

    def test_trusted_key_allowlist_rejects_unknown_or_conflicting_keys(self):
        seed = "77" * 32
        manifest = {
            "version": "v2.0.0",
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: "unknown-key",
        }
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)

        with self.assertRaisesRegex(ManifestSignatureError, "not trusted"):
            verify_manifest_signature_with_trusted_keys(
                manifest,
                (("trusted-key", public_key_from_seed("78" * 32)),),
                required=True,
            )
        with self.assertRaisesRegex(ManifestSignatureError, "more than once"):
            verify_manifest_signature_with_trusted_keys(
                manifest,
                (
                    ("unknown-key", public_key_from_seed(seed)),
                    ("unknown-key", public_key_from_seed("79" * 32)),
                ),
                required=True,
            )


if __name__ == "__main__":
    unittest.main()
