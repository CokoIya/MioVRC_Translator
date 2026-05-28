"""Tests for update checker and version comparison."""
import json
from pathlib import Path
import unittest

from src.updater import update_checker
from src.updater.manifest_signature import (
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    ManifestSignatureError,
    public_key_from_seed,
    sign_manifest,
)
from src.updater.update_checker import (
    UpdateInfo,
    _parse_update_info,
    _parse_version,
    _is_newer,
    _manifest_request_url,
    _select_newest_update_info,
    _parse_sha256,
    is_trusted_download_url,
    update_notes_for_language,
)


class UpdateCheckerTests(unittest.TestCase):
    """Original update checker tests."""

    def test_parse_update_info_requires_checksum(self):
        with self.assertRaises(RuntimeError):
            _parse_update_info(
                {
                    "version": "v9.9.9",
                    "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                }
            )

    def test_parse_update_info_accepts_trusted_https_with_sha(self):
        info = _parse_update_info(
            {
                "version": "v9.9.9",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                "installer_name": "app.exe",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "notes_ja": "日本語の更新内容",
                "notes_en": "English release notes",
            }
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.size_bytes, 123)
        self.assertEqual(info.sha256, "a" * 64)
        self.assertEqual(info.localized_notes["ja"], "日本語の更新内容")
        self.assertEqual(info.localized_notes["en"], "English release notes")

    def test_parse_update_info_accepts_release_notes_aliases(self):
        info = _parse_update_info(
            {
                "version": "v9.9.9",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                "sha256": "a" * 64,
                "release_notes": "Default notes",
                "release_notes_i18n": {"zh-CN": "中文更新说明"},
            }
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.notes, "Default notes")
        self.assertEqual(update_notes_for_language(info, "zh-CN"), "中文更新说明")
        self.assertEqual(update_notes_for_language(info, "ja"), "Default notes")

    def test_rejects_untrusted_or_plain_http_download_url(self):
        self.assertFalse(is_trusted_download_url("http://github.com/example/app.exe"))
        self.assertFalse(is_trusted_download_url("https://example.com/app.exe"))

    def test_manifest_request_url_adds_cache_buster(self):
        url = _manifest_request_url(
            "https://78hejiu.top/installer_manifest.json?lang=zh&_mio_update_check=old",
            timestamp_ms=12345,
        )
        self.assertEqual(
            url,
            "https://78hejiu.top/installer_manifest.json?lang=zh&_mio_update_check=12345",
        )

    def test_verify_update_manifest_requires_trusted_signature_when_configured(self):
        seed = "11" * 32
        key_id = "test-key"
        public_key = public_key_from_seed(seed)
        manifest = {
            "version": "v9.9.9",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: key_id,
        }
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
        original_public_key = update_checker.UPDATE_MANIFEST_PUBLIC_KEY
        original_key_id = update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID
        original_required = update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE
        try:
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY = public_key
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID = key_id
            update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE = True
            self.assertTrue(update_checker._verify_update_manifest(manifest))

            tampered = dict(manifest)
            tampered["version"] = "v9.9.10"
            with self.assertRaises(ManifestSignatureError):
                update_checker._verify_update_manifest(tampered)
        finally:
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY = original_public_key
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID = original_key_id
            update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE = original_required

    def test_optional_bad_manifest_signature_does_not_block_sha256_path(self):
        original_public_key = update_checker.UPDATE_MANIFEST_PUBLIC_KEY
        original_key_id = update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID
        original_required = update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE
        try:
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY = public_key_from_seed("55" * 32)
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID = "test-key"
            update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE = False
            manifest = {
                "version": "v9.9.9",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                "sha256": "a" * 64,
                SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
                SIGNATURE_KEY_ID_FIELD: "test-key",
                SIGNATURE_FIELD: "b" * 128,
            }
            self.assertFalse(update_checker._verify_update_manifest(manifest))
            self.assertIsNotNone(_parse_update_info(manifest))
        finally:
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY = original_public_key
            update_checker.UPDATE_MANIFEST_PUBLIC_KEY_ID = original_key_id
            update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE = original_required

    def test_select_newest_update_info_uses_highest_version(self):
        newest = _select_newest_update_info(
            [
                UpdateInfo(version="v1.3.1", download_url="https://78hejiu.top/old.exe", sha256="a" * 64),
                UpdateInfo(version="v1.3.2.3", download_url="https://78hejiu.top/new.exe", sha256="b" * 64),
                UpdateInfo(version="v1.3.2-beta1", download_url="https://78hejiu.top/beta.exe", sha256="c" * 64),
            ]
        )
        self.assertIsNotNone(newest)
        self.assertEqual(newest.version, "v1.3.2.3")

    def test_repository_installer_manifest_is_parseable(self):
        manifest_path = Path(__file__).resolve().parents[1] / "docs" / "installer_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        update_checker._verify_update_manifest(data)
        info = _parse_update_info(data)
        self.assertIsNotNone(info)
        self.assertGreater(info.size_bytes or 0, 0)
        self.assertEqual(len(info.sha256), 64)


class TestVersionParsing(unittest.TestCase):
    """Test version string parsing."""

    def test_parse_version_simple(self):
        """Should parse simple version numbers."""
        release, prerelease = _parse_version("1.2.3")
        self.assertEqual(release, (1, 2, 3))
        self.assertEqual(prerelease, (1,))

    def test_parse_version_with_v_prefix(self):
        """Should handle 'v' prefix."""
        release, prerelease = _parse_version("v1.2.3")
        self.assertEqual(release, (1, 2, 3))
        self.assertEqual(prerelease, (1,))

    def test_parse_version_beta(self):
        """Should parse beta versions."""
        release, prerelease = _parse_version("1.2.3-beta1")
        self.assertEqual(release, (1, 2, 3))
        self.assertEqual(prerelease, (0, 2, 1))

    def test_parse_version_empty(self):
        """Should raise on empty version."""
        with self.assertRaises(ValueError):
            _parse_version("")


class TestVersionComparison(unittest.TestCase):
    """Test version comparison logic."""

    def test_is_newer_major_version(self):
        """Major version increase should be newer."""
        self.assertTrue(_is_newer("2.0.0", "1.9.9"))
        self.assertFalse(_is_newer("1.0.0", "2.0.0"))

    def test_is_newer_minor_version(self):
        """Minor version increase should be newer."""
        self.assertTrue(_is_newer("1.3.0", "1.2.9"))
        self.assertFalse(_is_newer("1.2.0", "1.3.0"))

    def test_is_newer_patch_version(self):
        """Patch version increase should be newer."""
        self.assertTrue(_is_newer("1.2.4", "1.2.3"))
        self.assertFalse(_is_newer("1.2.3", "1.2.4"))

    def test_is_newer_same_version(self):
        """Same version should not be newer."""
        self.assertFalse(_is_newer("1.2.3", "1.2.3"))

    def test_is_newer_stable_vs_beta(self):
        """Stable should be newer than beta of same release."""
        self.assertTrue(_is_newer("1.2.3", "1.2.3-beta1"))
        self.assertFalse(_is_newer("1.2.3-beta1", "1.2.3"))


class TestSHA256Parsing(unittest.TestCase):
    """Test SHA256 hash parsing."""

    def test_parse_sha256_valid_lowercase(self):
        """Should accept valid lowercase SHA256."""
        valid_hash = "a" * 64
        result = _parse_sha256(valid_hash)
        self.assertEqual(result, valid_hash)

    def test_parse_sha256_valid_uppercase(self):
        """Should normalize uppercase to lowercase."""
        valid_hash = "A" * 64
        result = _parse_sha256(valid_hash)
        self.assertEqual(result, "a" * 64)

    def test_parse_sha256_invalid_length(self):
        """Should reject invalid length."""
        self.assertEqual(_parse_sha256("a" * 63), "")
        self.assertEqual(_parse_sha256("a" * 65), "")

    def test_parse_sha256_empty(self):
        """Should return empty for empty input."""
        self.assertEqual(_parse_sha256(""), "")
        self.assertEqual(_parse_sha256(None), "")


class TestTrustedDownloadURL(unittest.TestCase):
    """Test trusted download URL validation."""

    def test_trusted_url_78hejiu(self):
        """Should trust 78hejiu.top."""
        self.assertTrue(is_trusted_download_url("https://78hejiu.top/file.exe"))

    def test_trusted_url_github(self):
        """Should trust github.com."""
        self.assertTrue(
            is_trusted_download_url(
                "https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.0.0/app.exe"
            )
        )

    def test_untrusted_url_other_github_repo(self):
        """Should not trust arbitrary GitHub repositories."""
        self.assertFalse(is_trusted_download_url("https://github.com/user/repo/releases/file.exe"))

    def test_release_asset_redirect_host_requires_explicit_flag(self):
        """GitHub release asset hosts are only trusted as redirects."""
        url = "https://objects.githubusercontent.com/github-production-release-asset/app.exe"
        self.assertFalse(is_trusted_download_url(url))
        self.assertTrue(is_trusted_download_url(url, allow_release_asset_redirect=True))

    def test_untrusted_url_http(self):
        """Should not trust HTTP URLs."""
        self.assertFalse(is_trusted_download_url("http://78hejiu.top/file.exe"))

    def test_untrusted_url_unknown_host(self):
        """Should not trust unknown hosts."""
        self.assertFalse(is_trusted_download_url("https://evil.com/file.exe"))


if __name__ == "__main__":
    unittest.main()
