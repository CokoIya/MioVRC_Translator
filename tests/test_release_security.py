from __future__ import annotations

import importlib.util
from pathlib import Path

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
    assert "Ed25519 installer and manifest signatures" in script
    assert "Remove-Item Env:MIO_RELEASE_SIGNING_SEED" in script
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


def test_optional_authenticode_uses_https_timestamping() -> None:
    installer = _read("MioTranslator-installer.iss")

    assert "https://timestamp.digicert.com" in installer
    assert "http://timestamp.digicert.com" not in installer


def test_updater_uses_ed25519_public_keys_instead_of_certificate_thumbprints() -> None:
    version = _read("src/version.py")
    verifier = _read("src/updater/installer_verifier.py")

    assert "TRUSTED_INSTALLER_PUBLIC_KEYS" in version
    assert "TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS" in version
    assert "INSTALLER_SIGNATURE_KEY_ID" in version
    assert "TRUSTED_INSTALLER_SIGNER_THUMBPRINTS" not in version
    assert "verify_installer_metadata_signature" in verifier
    assert "Get-AuthenticodeSignature" not in verifier


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
    spec = importlib.util.spec_from_file_location("check_release_environment_test", path)
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
