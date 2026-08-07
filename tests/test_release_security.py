from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_pyinstaller_bundles_yaml_configuration_validator() -> None:
    spec = _read("MioTranslator.spec")

    assert '"yaml"' in spec


def test_release_build_requires_ed25519_seed_and_keeps_authenticode_optional() -> None:
    script = _read("build_release.ps1")

    assert "$env:MIO_RELEASE_SIGNING_SEED" in script
    assert "$env:MIO_MANIFEST_SEED" in script
    assert "$env:MIO_RELEASE_SIGNING_SEED_FILE" in script
    assert "$ReleaseSigningSeedFile" in script
    assert "Ed25519 installer and manifest signatures" in script
    assert "Remove-Item Env:MIO_RELEASE_SIGNING_SEED" in script
    assert "Remove-Item Env:MIO_RELEASE_SIGNING_SEED_FILE" in script
    pyinstaller_command = "& $releasePython -m PyInstaller"
    assert script.index("Remove-Item Env:MIO_RELEASE_SIGNING_SEED") < script.index(
        pyinstaller_command
    )
    assert script.index("$env:MIO_RELEASE_SIGNING_SEED = $releaseSeed") > script.index(
        pyinstaller_command
    )
    assert "$env:MIO_TRANSLATOR_SIGN_PFX" in script
    assert "$env:MIO_TRANSLATOR_SIGN_PASS" in script
    assert "$useAuthenticode" in script
    assert "Building without Authenticode" in script
    assert r"tools\update_release_manifests.py" in script
    assert r"tools\release\generate_release_checksums.py" in script
    assert ".release-metadata-backup-" in script
    assert "$releaseMetadataFinalized" in script
    assert "Release metadata backup source is unsafe" in script
    assert script.index(r"tools\release\generate_release_checksums.py") > script.index(
        r"tools\update_release_manifests.py"
    )
    signing_finally = script.index(
        "} finally {",
        script.index(r"tools\update_release_manifests.py"),
    )
    assert (
        script.index(r"tools\release\generate_release_checksums.py") < signing_finally
    )


def test_release_build_accepts_valid_per_user_inno_version_metadata() -> None:
    script = _read("build_release.ps1")

    assert "unins000.exe" in script
    assert ").ProductVersion" in script
    assert "uninstallerDirectory.Equals" in script
    assert "[IO.FileAttributes]::ReparsePoint" in script
    assert "[Version]'6.5.0'" in script


def test_release_seed_file_must_be_external_reparse_safe_and_acl_restricted() -> None:
    script = _read("tools/release/release_signing_helpers.ps1")

    assert "must be stored outside the repository" in script
    assert "must not use device or extended namespaces" in script
    assert "parent directories must not contain reparse points" in script
    assert "must not be a link or reparse point" in script
    assert "protected, non-inherited access rules" in script
    assert "unauthorized principal" in script
    assert "[IO.FileShare]::None" in script

    build_script = _read("build_release.ps1")
    assert "Read-MioReleaseSigningSeedFile" in build_script
    assert "exactly 64 hexadecimal characters" in build_script


@pytest.mark.skipif(
    os.name != "nt", reason="PowerShell path semantics are Windows-only"
)
@pytest.mark.parametrize(
    ("seed_path", "expected_error"),
    (
        (str(REPO_ROOT / "inside-repository.seed.hex"), "outside the repository"),
        (
            rf"\\?\{REPO_ROOT}\inside-repository.seed.hex",
            "device or extended namespaces",
        ),
    ),
)
def test_release_seed_helper_rejects_unsafe_windows_paths(
    seed_path: str,
    expected_error: str,
) -> None:
    helper = REPO_ROOT / "tools" / "release" / "release_signing_helpers.ps1"
    command = (
        f". '{helper}'; "
        f"Read-MioReleaseSigningSeedFile -Path '{seed_path}' "
        f"-RepositoryRoot '{REPO_ROOT}'"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert expected_error in result.stdout + result.stderr


def test_private_release_and_authentication_material_is_ignored() -> None:
    ignore_rules = _read(".gitignore").splitlines()

    for pattern in (
        ".secrets/",
        "*.seed.hex",
        "*.pfx",
        "*.p12",
        "*.key",
        "id_ed25519",
        "github-release-ed25519",
    ):
        assert pattern in ignore_rules


def test_optional_authenticode_uses_https_timestamping() -> None:
    installer = _read("MioTranslator-installer.iss")

    assert "https://timestamp.digicert.com" in installer
    assert "http://timestamp.digicert.com" not in installer


def test_release_build_requires_localization_compatible_inno_setup() -> None:
    script = _read("build_release.ps1")

    assert "Inno Setup 6.5.0 or newer" in script
    assert "[Diagnostics.FileVersionInfo]::GetVersionInfo($iscc).FileVersion" in script
    assert "[Version]'6.5.0'" in script


def test_updater_uses_ed25519_public_keys_instead_of_certificate_thumbprints() -> None:
    version = _read("src/version.py")
    verifier = _read("src/updater/installer_verifier.py")

    assert "TRUSTED_INSTALLER_PUBLIC_KEYS" in version
    assert "TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS" in version
    assert "INSTALLER_SIGNATURE_KEY_ID" in version
    assert "TRUSTED_INSTALLER_SIGNER_THUMBPRINTS" not in version
    assert "verify_installer_metadata_signature" in verifier
    assert "Get-AuthenticodeSignature" not in verifier


def test_v1386_activates_v2_signing_and_retires_v1_trust() -> None:
    from src import version

    assert version.UPDATE_MANIFEST_PUBLIC_KEY_ID == "mio-update-ed25519-v2"
    assert version.INSTALLER_SIGNATURE_KEY_ID == "mio-installer-ed25519-v2"
    assert version.UPDATE_MANIFEST_PUBLIC_KEY == (
        "299d00127d293c1122b3e6f207b719d47df3546307c8757c67f8ca0d5f2d218e"
    )
    assert dict(version.TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS) == {
        "mio-update-ed25519-v2": version.UPDATE_MANIFEST_PUBLIC_KEY,
    }
    assert dict(version.TRUSTED_INSTALLER_PUBLIC_KEYS) == {
        "mio-installer-ed25519-v2": version.UPDATE_MANIFEST_PUBLIC_KEY,
    }
    assert version.REQUIRE_UPDATE_MANIFEST_SIGNATURE is True


def test_public_release_key_catalog_matches_compiled_trust_configuration() -> None:
    from src import version

    catalog = json.loads(_read("docs/release_signing_keys.json"))
    assert catalog["active_release"] == f"v{version.APP_VERSION}"
    assert catalog["active_manifest_key_id"] == version.UPDATE_MANIFEST_PUBLIC_KEY_ID
    assert catalog["active_installer_key_id"] == version.INSTALLER_SIGNATURE_KEY_ID

    manifest_keys = dict(version.TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS)
    installer_keys = dict(version.TRUSTED_INSTALLER_PUBLIC_KEYS)
    for key in catalog["keys"]:
        public_key = key["public_key_hex"]
        assert (
            hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
            == key["public_key_sha256"]
        )
        if key["trusted_by_active_runtime"]:
            assert manifest_keys[key["manifest_key_id"]] == public_key
            assert installer_keys[key["installer_key_id"]] == public_key
        else:
            assert key["manifest_key_id"] not in manifest_keys
            assert key["installer_key_id"] not in installer_keys


def test_installer_is_per_user_and_preserves_user_data_on_uninstall() -> None:
    installer = _read("MioTranslator-installer.iss")
    lowered = installer.lower()

    assert "PrivilegesRequired=lowest" in installer
    assert r"{localappdata}\Programs\{#AppName}" in installer
    assert r"{localappdata}\Mio RealTime Translator" in installer
    assert "[uninstalldelete]" not in lowered
    assert "[installdelete]" in lowered


def _load_release_environment_checker():
    path = REPO_ROOT / "tools" / "check_release_environment.py"
    spec = importlib.util.spec_from_file_location(
        "check_release_environment_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("distribution", "minimum"),
    (
        ("aiohttp", "3.14.1"),
        ("cryptography", "48.0.1"),
        ("transformers", "5.3.0"),
        ("idna", "3.15"),
        ("msgpack", "1.2.1"),
        ("nltk", "3.10.0"),
    ),
)
def test_release_lock_satisfies_security_floors(
    distribution: str,
    minimum: str,
) -> None:
    from packaging.requirements import Requirement
    from packaging.version import Version

    requirements = {
        requirement.name.lower(): requirement
        for requirement in (
            Requirement(line)
            for line in _read("requirements.lock.txt").splitlines()
            if line and not line.startswith(("#", "-"))
        )
    }

    requirement = requirements[distribution]
    pinned = next(iter(requirement.specifier)).version
    assert Version(pinned) >= Version(minimum)


def test_requests_floor_supports_tls_aware_dns_pinning_adapter() -> None:
    from packaging.requirements import Requirement
    from packaging.version import Version

    requirement = next(
        Requirement(line.split("#", 1)[0].strip())
        for line in _read("requirements.txt").splitlines()
        if line.split("#", 1)[0].strip().lower().startswith("requests")
    )

    assert Version("2.32.2") in requirement.specifier
    assert Version("2.32.1") not in requirement.specifier


def test_release_requires_native_system_trust_for_provider_https() -> None:
    from packaging.requirements import Requirement
    from packaging.version import Version

    requirement = next(
        Requirement(line.split("#", 1)[0].strip())
        for line in _read("requirements.txt").splitlines()
        if line.split("#", 1)[0].strip().lower().startswith("truststore")
    )
    locked = next(
        Requirement(line)
        for line in _read("requirements.lock.txt").splitlines()
        if line.lower().startswith("truststore")
    )
    checker = _load_release_environment_checker()

    assert Version("0.10.4") in requirement.specifier
    assert locked == Requirement("truststore==0.10.4")
    assert "truststore" in checker.REQUIRED_MODULES


def test_provider_logs_do_not_emit_full_relay_urls_or_raw_tracebacks() -> None:
    for relative_path in (
        "src/translators/openai_translator.py",
        "src/translators/anthropic_translator.py",
    ):
        source = _read(relative_path)
        assert "base_url=%s" not in source
        assert "provider traceback" not in source
        assert "provider_endpoint_diagnostics" in source
        assert "safe_exception_summary" in source

    for relative_path in (
        "src/translators/deepl_translator.py",
        "src/translators/google_web_translator.py",
        "src/translators/libretranslate_translator.py",
        "src/translators/microsoft_edge_translator.py",
        "src/translators/mymemory_translator.py",
    ):
        source = _read(relative_path)
        assert 'failed: %s", exc' not in source
        assert "safe_exception_summary" in source

    qwen = _read("src/tts/api_tts_engines.py")
    manager = _read("src/tts/manager.py")
    manual = _read("src/core/manual_translation_controller.py")
    main_window = _read("src/ui_qt/main_window.py")
    scheduler = _read("src/core/realtime_scheduler.py")
    settings_window = _read("src/ui_qt/settings_window.py")
    tts_service = _read("src/core/tts_service.py")
    translation_pipeline = _read("src/core/translation_pipeline.py")
    output_dispatcher = _read("src/core/output_dispatcher.py")
    assert 'logger.error("Qwen TTS synthesis failed: %s"' not in qwen
    assert 'logger.error("TTS synthesis failed: %s"' not in manager
    assert 'logger.warning("Manual translation failed: %s"' not in manual
    assert "Typed-text style rewrite traceback" not in manual
    assert "ASR rewrite traceback" not in main_window
    assert "TTS deduplicated (same text" not in main_window
    assert 'Realtime translation failed source=%s sequence=%d"' not in scheduler
    assert "safe_exception_summary" in scheduler
    assert 'logger.warning("TTS test failed: %s", e)' not in settings_window
    assert "TTS test finished without playback success: %s" not in settings_window
    assert "safe_exception_summary" in settings_window
    assert (
        'logger.warning("Failed to initialize TTS manager: %s", exc)' not in tts_service
    )
    assert 'logger.warning("TTS speak failed: %s", exc)' not in tts_service
    assert (
        'logger.warning("Manual translation failed: %s", exc)'
        not in translation_pipeline
    )
    assert (
        'logger.warning("Output sink failed: %s", key, exc_info=True)'
        not in output_dispatcher
    )
    assert "safe_exception_summary" in tts_service
    assert "safe_exception_summary" in translation_pipeline
    assert "safe_exception_summary" in output_dispatcher


def test_release_environment_checker_rejects_unsatisfied_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_release_environment_checker()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "--extra-index-url https://example.invalid/simple\n"
        "secure-package>=2.0,<3.0  # security floor\n"
        "ignored-package>=99; python_version < '2'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(checker.metadata, "version", lambda _name: "1.9")

    assert checker._unsatisfied_requirements(requirements) == [
        "secure-package 1.9 does not satisfy <3.0,>=2.0"
    ]


def test_release_environment_checker_rejects_unpinned_transitive_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_release_environment_checker()
    requirements = tmp_path / "requirements.lock.txt"
    requirements.write_text("parent-package==1.0\n", encoding="utf-8")

    class FakeDistribution:
        requires = (
            "child-package>=2.0",
            "ignored-package>=99; python_version < '2'",
        )

    monkeypatch.setattr(
        checker.metadata,
        "distribution",
        lambda _name: FakeDistribution(),
    )

    assert checker._unpinned_lock_dependencies(requirements) == [
        "child-package required by parent-package is not pinned"
    ]
