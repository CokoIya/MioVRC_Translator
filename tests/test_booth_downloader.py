import unittest
import json

from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    public_key_from_seed,
    sign_manifest,
)
from tools.booth_downloader import mio_vrc_download as downloader


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


class BoothDownloaderTests(unittest.TestCase):
    def _signed_payload(self, payload: dict[str, object]) -> bytes:
        seed = "55" * 32
        original_public_key = downloader.UPDATE_MANIFEST_PUBLIC_KEY
        original_key_id = downloader.UPDATE_MANIFEST_PUBLIC_KEY_ID
        original_required = downloader.REQUIRE_UPDATE_MANIFEST_SIGNATURE
        self.addCleanup(setattr, downloader, "UPDATE_MANIFEST_PUBLIC_KEY", original_public_key)
        self.addCleanup(setattr, downloader, "UPDATE_MANIFEST_PUBLIC_KEY_ID", original_key_id)
        self.addCleanup(setattr, downloader, "REQUIRE_UPDATE_MANIFEST_SIGNATURE", original_required)
        downloader.UPDATE_MANIFEST_PUBLIC_KEY = public_key_from_seed(seed)
        downloader.UPDATE_MANIFEST_PUBLIC_KEY_ID = "test-key"
        downloader.REQUIRE_UPDATE_MANIFEST_SIGNATURE = True
        payload = dict(payload)
        payload[SIGNATURE_ALGORITHM_FIELD] = SIGNATURE_ALGORITHM
        payload[SIGNATURE_KEY_ID_FIELD] = "test-key"
        payload[SIGNATURE_FIELD] = sign_manifest(payload, seed)
        return json.dumps(payload).encode("utf-8")

    def test_manifest_rejects_untrusted_and_path_traversal_fields(self):
        self.assertFalse(downloader.is_trusted_download_url("http://example.com/a.exe"))
        with self.assertRaises(RuntimeError):
            downloader.manifest_filename("..\\evil.exe", "installer_name", (".exe",))
        with self.assertRaises(RuntimeError):
            downloader.manifest_filename("C:evil.exe", "installer_name", (".exe",))

    def test_manifest_requires_sha_and_trusted_urls(self):
        payload = self._signed_payload(
            {
                "version": "1.0.0",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.0.0/b.exe",
                "installer_name": "b.exe",
                "sha256": "a" * 64,
            }
        )
        original_request_url = downloader.request_url
        try:
            downloader.request_url = lambda url, timeout=20: _FakeResponse(payload)
            manifest = downloader.load_manifest(downloader.DEFAULT_MANIFEST_URL)
        finally:
            downloader.request_url = original_request_url
        self.assertEqual(manifest.installer_name, "b.exe")
        self.assertEqual(manifest.sha256, "a" * 64)
        self.assertFalse(downloader.is_trusted_download_url("https://github.com/a/b.exe"))

        bad_payload = self._signed_payload(
            {
                "version": "1.0.0",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.0.0/b.exe",
                "installer_name": "b.exe",
            }
        )
        try:
            downloader.request_url = lambda url, timeout=20: _FakeResponse(bad_payload)
            with self.assertRaises(RuntimeError):
                downloader.load_manifest(downloader.DEFAULT_MANIFEST_URL)
        finally:
            downloader.request_url = original_request_url

    def test_manifest_url_adds_cache_buster(self):
        self.assertEqual(
            downloader.manifest_request_url(
                "https://78hejiu.top/installer_manifest.json?x=1&_mio_update_check=old",
                timestamp_ms=6789,
            ),
            "https://78hejiu.top/installer_manifest.json?x=1&_mio_update_check=6789",
        )


if __name__ == "__main__":
    unittest.main()
