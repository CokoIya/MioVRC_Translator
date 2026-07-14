"""Tests for update checker and version comparison."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

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
from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    INSTALLER_SIGNATURE_ALGORITHM_FIELD,
    INSTALLER_SIGNATURE_FIELD,
    INSTALLER_SIGNATURE_KEY_ID_FIELD,
    InstallerSignatureError,
    sign_installer_metadata,
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


def _add_installer_signature(
    manifest: dict,
    seed: str = "55" * 32,
    key_id: str = "test-installer-key",
) -> dict:
    manifest[INSTALLER_SIGNATURE_ALGORITHM_FIELD] = INSTALLER_SIGNATURE_ALGORITHM
    manifest[INSTALLER_SIGNATURE_KEY_ID_FIELD] = key_id
    manifest[INSTALLER_SIGNATURE_FIELD] = sign_installer_metadata(
        seed,
        sha256=manifest["sha256"],
        size_bytes=manifest["size_bytes"],
    )
    return manifest


class _FakeManifestResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int = 200,
        payload: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.url = url
        self.status_code = status_code
        self.payload = payload
        self.headers = dict(headers or {})
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        del chunk_size
        if self.payload:
            yield self.payload

    def close(self):
        self.closed = True


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

    def test_parse_update_info_requires_positive_installer_size(self):
        base = {
            "version": "v9.9.9",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
            "sha256": "a" * 64,
        }
        for invalid_size in (None, "", 0, -1, "invalid"):
            manifest = dict(base)
            if invalid_size is not None:
                manifest["size_bytes"] = invalid_size
            with self.subTest(size_bytes=invalid_size):
                with self.assertRaisesRegex(RuntimeError, "installer size"):
                    _parse_update_info(manifest)


    def test_parse_update_info_accepts_trusted_https_with_sha(self):
        info = _parse_update_info(
            _add_installer_signature({
                "version": "v9.9.9",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                "installer_name": "app.exe",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "notes_ja": "日本語の更新内容",
                "notes_en": "English release notes",
            })
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.size_bytes, 123)
        self.assertEqual(info.sha256, "a" * 64)
        self.assertEqual(info.localized_notes["ja"], "日本語の更新内容")
        self.assertEqual(info.localized_notes["en"], "English release notes")

    def test_parse_update_info_accepts_release_notes_aliases(self):
        info = _parse_update_info(
            _add_installer_signature({
                "version": "v9.9.9",
                "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
                "sha256": "a" * 64,
                "size_bytes": 123,
                "release_notes": "Default notes",
                "release_notes_i18n": {"zh-CN": "中文更新说明"},
            })
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.notes, "Default notes")
        self.assertEqual(update_notes_for_language(info, "zh-CN"), "中文更新说明")
        self.assertEqual(update_notes_for_language(info, "ja"), "Default notes")

    def test_parse_update_info_rejects_invalid_version(self):
        manifest = _add_installer_signature(
            {
                "version": "not-a-version",
                "installer_url": (
                    "https://github.com/CokoIya/MioVRC_Translator/"
                    "releases/download/v9.9.9/app.exe"
                ),
                "sha256": "a" * 64,
                "size_bytes": 123,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "invalid version"):
            _parse_update_info(manifest)

    def test_rejects_untrusted_or_plain_http_download_url(self):
        self.assertFalse(is_trusted_download_url("http://github.com/example/app.exe"))
        self.assertFalse(is_trusted_download_url("https://example.com/app.exe"))
        self.assertFalse(
            is_trusted_download_url(
                "https://raw.githubusercontent.com/CokoIya/MioVRC_Translator/main/app.exe",
                allow_release_asset_redirect=True,
            )
        )
        self.assertFalse(
            is_trusted_download_url(
                "https://attacker.githubusercontent.com/app.exe",
                allow_release_asset_redirect=True,
            )
        )
        self.assertTrue(
            is_trusted_download_url(
                "https://release-assets.githubusercontent.com/github-production-release-asset/app.exe",
                allow_release_asset_redirect=True,
            )
        )

    def test_manifest_request_url_adds_cache_buster(self):
        url = _manifest_request_url(
            "https://miovrc.com/installer_manifest.json?lang=zh&_mio_update_check=old",
            timestamp_ms=12345,
        )
        self.assertEqual(
            url,
            "https://miovrc.com/installer_manifest.json?lang=zh&_mio_update_check=12345",
        )

    def test_fetch_update_info_accepts_utf8_bom_manifest(self):
        seed = "22" * 32
        key_id = "test-key"
        manifest = {
            "version": "v9.9.9",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
            "installer_name": "app.exe",
            "size_bytes": 123,
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: key_id,
        }
        _add_installer_signature(manifest, seed, key_id)
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)

        payload = b"\xef\xbb\xbf" + json.dumps(manifest).encode("utf-8")

        class FakeResponse:
            status_code = 200
            url = "https://miovrc.com/installer_manifest.json"
            headers = {"content-length": str(len(payload))}

            def __init__(self):
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                del chunk_size
                yield payload

            def close(self):
                self.closed = True

        response = FakeResponse()
        with patch.object(update_checker, "UPDATE_MANIFEST_PUBLIC_KEY", public_key_from_seed(seed)), \
             patch.object(update_checker, "UPDATE_MANIFEST_PUBLIC_KEY_ID", key_id), \
             patch.object(update_checker, "TRUSTED_INSTALLER_PUBLIC_KEYS", ((key_id, public_key_from_seed(seed)),)), \
             patch("src.updater.update_checker.requests.get", return_value=response) as request:
            info = update_checker._fetch_update_info("https://miovrc.com/installer_manifest.json")

        self.assertIsNotNone(info)
        self.assertEqual(info.version, "v9.9.9")
        self.assertTrue(response.closed)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertTrue(request.call_args.kwargs["stream"])

    def test_fetch_update_info_rejects_untrusted_intermediate_redirect(self):
        response = _FakeManifestResponse(
            url="https://miovrc.com/installer_manifest.json",
            status_code=302,
            headers={"Location": "https://evil.example/installer_manifest.json"},
        )
        with patch(
            "src.updater.update_checker.requests.get",
            return_value=response,
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "redirect URL is not trusted"):
                update_checker._fetch_update_info(
                    "https://miovrc.com/installer_manifest.json"
                )

        self.assertEqual(request.call_count, 1)
        self.assertTrue(response.closed)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_fetch_update_info_rejects_oversized_manifest_and_closes_response(self):
        response = _FakeManifestResponse(
            url="https://miovrc.com/installer_manifest.json",
            payload=b"123456789",
            headers={"Content-Length": "9"},
        )
        with patch.object(update_checker, "_MAX_MANIFEST_BYTES", 8), patch(
            "src.updater.update_checker.requests.get",
            return_value=response,
        ):
            with self.assertRaisesRegex(RuntimeError, "maximum allowed size"):
                update_checker._fetch_update_info(
                    "https://miovrc.com/installer_manifest.json"
                )

        self.assertTrue(response.closed)

    def test_fetch_update_info_follows_only_validated_redirects(self):
        seed = "33" * 32
        key_id = "redirect-key"
        manifest = {
            "version": "v9.9.9",
            "installer_url": (
                "https://github.com/CokoIya/MioVRC_Translator/"
                "releases/download/v9.9.9/app.exe"
            ),
            "installer_name": "app.exe",
            "size_bytes": 123,
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: key_id,
        }
        _add_installer_signature(manifest, seed, key_id)
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
        payload = json.dumps(manifest).encode("utf-8")
        first = _FakeManifestResponse(
            url="https://miovrc.com/installer_manifest.json",
            status_code=302,
            headers={
                "Location": (
                    "https://github.com/CokoIya/MioVRC_Translator/"
                    "releases/download/v9.9.9/installer_manifest.json"
                )
            },
        )
        second = _FakeManifestResponse(
            url=(
                "https://release-assets.githubusercontent.com/"
                "github-production-release-asset/manifest"
            ),
            payload=payload,
            headers={"Content-Length": str(len(payload))},
        )

        with patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY",
            public_key_from_seed(seed),
        ), patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY_ID",
            key_id,
        ), patch.object(
            update_checker,
            "TRUSTED_INSTALLER_PUBLIC_KEYS",
            ((key_id, public_key_from_seed(seed)),),
        ), patch(
            "src.updater.update_checker.requests.get",
            side_effect=[first, second],
        ) as request:
            info = update_checker._fetch_update_info(
                "https://miovrc.com/installer_manifest.json"
            )

        self.assertEqual(info.version, "v9.9.9")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        for call in request.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_fetch_update_info_rejects_untrusted_initial_url_without_request(self):
        with patch("src.updater.update_checker.requests.get") as request:
            with self.assertRaisesRegex(RuntimeError, "manifest URL is not trusted"):
                update_checker._fetch_update_info(
                    "https://evil.example/installer_manifest.json"
                )
        request.assert_not_called()

    def test_verify_update_manifest_requires_trusted_signature_when_configured(self):
        seed = "11" * 32
        key_id = "test-key"
        manifest = {
            "version": "v9.9.9",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
            "size_bytes": 123,
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: key_id,
        }
        _add_installer_signature(manifest, seed, key_id)
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)
        with patch.object(update_checker, "UPDATE_MANIFEST_PUBLIC_KEY", public_key_from_seed(seed)), \
             patch.object(update_checker, "UPDATE_MANIFEST_PUBLIC_KEY_ID", key_id), \
             patch.object(update_checker, "TRUSTED_INSTALLER_PUBLIC_KEYS", ((key_id, public_key_from_seed(seed)),)):
            self.assertTrue(update_checker._verify_update_manifest(manifest))

            tampered = dict(manifest)
            tampered["version"] = "v9.9.10"
            with self.assertRaises(ManifestSignatureError):
                update_checker._verify_update_manifest(tampered)

    def test_verify_update_manifest_accepts_trusted_rotation_overlap_key(self):
        old_seed = "13" * 32
        new_seed = "14" * 32
        new_key_id = "test-key-v2"
        manifest = {
            "version": "v9.9.9",
            "installer_url": (
                "https://github.com/CokoIya/MioVRC_Translator/"
                "releases/download/v9.9.9/app.exe"
            ),
            "size_bytes": 123,
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: new_key_id,
        }
        _add_installer_signature(manifest, new_seed, new_key_id)
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, new_seed)

        with patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY",
            public_key_from_seed(old_seed),
        ), patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY_ID",
            "test-key-v1",
        ), patch.object(
            update_checker,
            "TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS",
            (
                ("test-key-v1", public_key_from_seed(old_seed)),
                (new_key_id, public_key_from_seed(new_seed)),
            ),
        ), patch.object(
            update_checker,
            "TRUSTED_INSTALLER_PUBLIC_KEYS",
            ((new_key_id, public_key_from_seed(new_seed)),),
        ):
            self.assertTrue(update_checker._verify_update_manifest(manifest))

    def test_valid_manifest_still_requires_trusted_installer_signature(self):
        seed = "12" * 32
        key_id = "test-key"
        manifest = {
            "version": "v9.9.9",
            "installer_url": (
                "https://github.com/CokoIya/MioVRC_Translator/"
                "releases/download/v9.9.9/app.exe"
            ),
            "size_bytes": 123,
            "sha256": "a" * 64,
            SIGNATURE_ALGORITHM_FIELD: SIGNATURE_ALGORITHM,
            SIGNATURE_KEY_ID_FIELD: key_id,
        }
        _add_installer_signature(manifest, seed, key_id)
        manifest[INSTALLER_SIGNATURE_FIELD] = "0" * 128
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, seed)

        with patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY",
            public_key_from_seed(seed),
        ), patch.object(
            update_checker,
            "UPDATE_MANIFEST_PUBLIC_KEY_ID",
            key_id,
        ), patch.object(
            update_checker,
            "TRUSTED_INSTALLER_PUBLIC_KEYS",
            ((key_id, public_key_from_seed(seed)),),
        ):
            with self.assertRaisesRegex(
                ManifestSignatureError,
                "Installer metadata signature",
            ):
                update_checker._verify_update_manifest(manifest)

    def test_runtime_flag_mutation_cannot_weaken_manifest_verification(self):
        manifest = {
            "version": "v9.9.9",
            "installer_url": "https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
            "sha256": "a" * 64,
        }
        update_checker.REQUIRE_UPDATE_MANIFEST_SIGNATURE = False
        with self.assertRaises(ManifestSignatureError):
            update_checker._verify_update_manifest(manifest)

    def test_select_newest_update_info_uses_highest_version(self):
        newest = _select_newest_update_info(
            [
                UpdateInfo(version="v1.3.1", download_url="https://miovrc.com/old.exe", sha256="a" * 64),
                UpdateInfo(version="v1.3.2.3", download_url="https://miovrc.com/new.exe", sha256="b" * 64),
                UpdateInfo(version="v1.3.2-beta1", download_url="https://miovrc.com/beta.exe", sha256="c" * 64),
            ]
        )
        self.assertIsNotNone(newest)
        self.assertEqual(newest.version, "v1.3.2.3")

    def test_latest_installer_fetch_accepts_current_version_for_repair(self):
        current = UpdateInfo(
            version=update_checker.APP_VERSION,
            download_url="https://miovrc.com/current.exe",
            sha256="a" * 64,
        )
        older = UpdateInfo(
            version="v1.0.0",
            download_url="https://miovrc.com/older.exe",
            sha256="b" * 64,
        )

        with patch("src.updater.update_checker._update_check_urls", return_value=("primary", "backup")):
            with patch("src.updater.update_checker._fetch_update_info", side_effect=[older, current]):
                info = update_checker._fetch_latest_installer_info_from_sources()

        self.assertEqual(info, current)

    def test_latest_installer_fetch_rejects_signed_downgrade(self):
        older = UpdateInfo(
            version="v1.0.0",
            download_url="https://miovrc.com/older.exe",
            sha256="b" * 64,
        )

        with patch(
            "src.updater.update_checker._update_check_urls",
            return_value=("primary",),
        ), patch(
            "src.updater.update_checker._fetch_update_info",
            return_value=older,
        ):
            with self.assertRaisesRegex(RuntimeError, "older than"):
                update_checker._fetch_latest_installer_info_from_sources()

    def test_repository_release_manifests_are_signed_and_parseable(self):
        root = Path(__file__).resolve().parents[1]
        for relative_path in ("mio_update.json", "docs/installer_manifest.json"):
            with self.subTest(path=relative_path):
                data = json.loads((root / relative_path).read_text(encoding="utf-8"))
                self.assertTrue(update_checker._verify_update_manifest(data))
                info = _parse_update_info(data)
                self.assertIsNotNone(info)
                self.assertEqual(info.version, f"v{update_checker.APP_VERSION}")
                self.assertGreater(info.size_bytes or 0, 0)
                self.assertEqual(len(info.sha256), 64)

    def test_signature_failures_are_not_retried(self):
        urls = ("primary", "backup")
        errors: list[str] = []

        with patch.object(update_checker, "_update_check_in_progress", False), patch.object(
            update_checker,
            "_update_check_urls",
            return_value=urls,
        ), patch.object(
            update_checker,
            "_fetch_update_info",
            side_effect=ManifestSignatureError("signature is missing"),
        ) as fetch:
            thread = update_checker.check_for_update(
                lambda _info: self.fail("unsigned update must not be accepted"),
                on_error=errors.append,
                max_retries=3,
                retry_delays=(),
            )
            self.assertIsNotNone(thread)
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(fetch.call_count, len(urls))
        self.assertEqual(len(errors), 1)
        self.assertIn("signature is missing", errors[0])

    def test_retries_only_transient_sources_after_permanent_failure(self):
        calls = {"permanent": 0, "transient": 0}
        no_update: list[bool] = []
        errors: list[str] = []

        def fetch(url: str):
            calls[url] += 1
            if url == "permanent":
                raise ManifestSignatureError("invalid signature")
            if calls[url] == 1:
                raise OSError("temporary network failure")
            return UpdateInfo(
                version=update_checker.APP_VERSION,
                download_url="https://miovrc.com/current.exe",
                size_bytes=1,
                sha256="a" * 64,
            )

        with patch.object(update_checker, "_update_check_in_progress", False), patch.object(
            update_checker,
            "_update_check_urls",
            return_value=("permanent", "transient"),
        ), patch.object(update_checker, "_fetch_update_info", side_effect=fetch):
            thread = update_checker.check_for_update(
                lambda _info: self.fail("current version must not trigger an update"),
                on_no_update=lambda: no_update.append(True),
                on_error=errors.append,
                max_retries=3,
                retry_delays=(),
            )
            self.assertIsNotNone(thread)
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(calls, {"permanent": 1, "transient": 2})
        self.assertEqual(no_update, [True])
        self.assertEqual(errors, [])

    def test_concurrent_update_checks_share_worker_and_notify_every_caller(self):
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        first_no_update: list[bool] = []
        second_no_update: list[bool] = []

        def fetch_candidates(*, permanent_source_errors):
            del permanent_source_errors
            fetch_started.set()
            self.assertTrue(release_fetch.wait(timeout=2))
            return [], {}

        with patch.object(update_checker, "_update_check_in_progress", False), patch.object(
            update_checker,
            "_update_check_thread",
            None,
        ), patch.object(
            update_checker,
            "_update_check_subscribers",
            [],
        ), patch.object(
            update_checker,
            "_fetch_update_candidates_from_sources",
            side_effect=fetch_candidates,
        ) as fetch:
            first = update_checker.check_for_update(
                lambda _info: self.fail("no update was expected"),
                on_no_update=lambda: first_no_update.append(True),
                max_retries=1,
                retry_delays=(),
            )
            self.assertTrue(fetch_started.wait(timeout=2))
            second = update_checker.check_for_update(
                lambda _info: self.fail("no update was expected"),
                on_no_update=lambda: second_no_update.append(True),
                max_retries=1,
                retry_delays=(),
            )
            self.assertIs(second, first)
            release_fetch.set()
            first.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first_no_update, [True])
        self.assertEqual(second_no_update, [True])

    def test_concurrent_update_check_errors_notify_every_caller(self):
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        first_errors: list[str] = []
        second_errors: list[str] = []

        def fail_fetch(*, permanent_source_errors):
            del permanent_source_errors
            fetch_started.set()
            self.assertTrue(release_fetch.wait(timeout=2))
            raise OSError("temporary update service failure")

        with patch.object(update_checker, "_update_check_in_progress", False), patch.object(
            update_checker,
            "_update_check_thread",
            None,
        ), patch.object(
            update_checker,
            "_update_check_subscribers",
            [],
        ), patch.object(
            update_checker,
            "_fetch_update_candidates_from_sources",
            side_effect=fail_fetch,
        ) as fetch:
            first = update_checker.check_for_update(
                lambda _info: self.fail("an update must not be reported"),
                on_error=first_errors.append,
                max_retries=1,
                retry_delays=(),
            )
            self.assertTrue(fetch_started.wait(timeout=2))
            second = update_checker.check_for_update(
                lambda _info: self.fail("an update must not be reported"),
                on_error=second_errors.append,
                max_retries=3,
                retry_delays=(),
            )
            self.assertIs(second, first)
            release_fetch.set()
            first.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first_errors, ["temporary update service failure"])
        self.assertEqual(second_errors, ["temporary update service failure"])

    def test_concurrent_installer_fetches_share_worker_and_release_subscribers(self):
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        first_results: list[UpdateInfo] = []
        second_results: list[UpdateInfo] = []
        update_info = UpdateInfo(
            version=update_checker.APP_VERSION,
            download_url="https://miovrc.com/current.exe",
            size_bytes=1,
            sha256="a" * 64,
        )

        def fetch_installer(*, permanent_source_errors):
            del permanent_source_errors
            fetch_started.set()
            self.assertTrue(release_fetch.wait(timeout=2))
            return update_info

        with patch.object(
            update_checker,
            "_installer_fetch_thread",
            None,
        ), patch.object(
            update_checker,
            "_installer_fetch_subscribers",
            [],
        ), patch.object(
            update_checker,
            "_fetch_latest_installer_info_from_sources",
            side_effect=fetch_installer,
        ) as fetch:
            first = update_checker.fetch_latest_installer_info(
                first_results.append,
                max_retries=1,
                retry_delays=(),
            )
            self.assertTrue(fetch_started.wait(timeout=2))
            second = update_checker.fetch_latest_installer_info(
                second_results.append,
                max_retries=3,
                retry_delays=(),
            )
            self.assertIs(second, first)
            self.assertEqual(len(update_checker._installer_fetch_subscribers), 2)

            release_fetch.set()
            first.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first_results, [update_info])
            self.assertEqual(second_results, [update_info])
            self.assertIsNone(update_checker._installer_fetch_thread)
            self.assertEqual(update_checker._installer_fetch_subscribers, [])

    def test_wrapped_installer_signature_failure_is_permanent(self):
        try:
            try:
                raise InstallerSignatureError("invalid installer signature")
            except InstallerSignatureError as exc:
                raise RuntimeError("manifest rejected") from exc
        except RuntimeError as wrapped:
            self.assertTrue(
                update_checker._is_permanent_update_source_error(wrapped)
            )


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
        """Should trust miovrc.com."""
        self.assertTrue(is_trusted_download_url("https://miovrc.com/file.exe"))

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

    def test_rejects_credentials_nonstandard_port_and_fragment(self):
        urls = (
            "https://user@miovrc.com/file.exe",
            "https://miovrc.com:444/file.exe",
            "https://miovrc.com/file.exe#fragment",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(is_trusted_download_url(url))

    def test_untrusted_url_http(self):
        """Should not trust HTTP URLs."""
        self.assertFalse(is_trusted_download_url("http://miovrc.com/file.exe"))

    def test_untrusted_url_unknown_host(self):
        """Should not trust unknown hosts."""
        self.assertFalse(is_trusted_download_url("https://evil.com/file.exe"))


if __name__ == "__main__":
    unittest.main()
