import unittest

from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    ManifestSignatureError,
    public_key_from_seed,
    sign_manifest,
    verify_manifest_signature,
)


class ManifestSignatureTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
