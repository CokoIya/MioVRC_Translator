from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RELEASE_VERSION = "1.3.9.2"
EXPECTED_WINDOWS_NUMERIC_VERSION = "1.3.9.2"


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _required_match(pattern: str, text: str, *, label: str) -> re.Match[str]:
    match = re.search(pattern, text, flags=re.MULTILINE)
    assert match is not None, f"Missing {label}"
    return match


def _resource_tuple_version(text: str, field: str) -> str:
    match = _required_match(
        rf"^\s*{re.escape(field)}=\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\),",
        text,
        label=field,
    )
    return ".".join(match.groups())


def test_release_version_is_consistent_across_build_and_current_docs() -> None:
    version_source = _read("src/version.py")
    app_version = _required_match(
        r'^APP_VERSION\s*=\s*"([^"]+)"',
        version_source,
        label="APP_VERSION",
    ).group(1)
    assert app_version == EXPECTED_RELEASE_VERSION

    installer = _read("MioTranslator-installer.iss")
    assert (
        _required_match(
            r'^#define AppVersion\s+"v([^"]+)"',
            installer,
            label="Inno AppVersion",
        ).group(1)
        == app_version
    )
    assert (
        _required_match(
            r'^#define AppNumericVersion\s+"([^"]+)"',
            installer,
            label="Inno AppNumericVersion",
        ).group(1)
        == EXPECTED_WINDOWS_NUMERIC_VERSION
    )

    resource = _read("windows_version_info.txt")
    assert _resource_tuple_version(resource, "filevers") == EXPECTED_WINDOWS_NUMERIC_VERSION
    assert _resource_tuple_version(resource, "prodvers") == EXPECTED_WINDOWS_NUMERIC_VERSION
    for field in ("FileVersion", "ProductVersion"):
        assert (
            _required_match(
                rf'StringStruct\(u"{field}",\s*u"v([^"]+)"\)',
                resource,
                label=f"Windows {field}",
            ).group(1)
            == app_version
        )

    changelog = _read("CHANGELOG.md")
    assert (
        _required_match(
            r"^## \[([^]]+)]",
            changelog,
            label="latest changelog heading",
        ).group(1)
        == app_version
    )

    version_token = f"v{app_version}"
    expected_installer = f"MioTranslator-Setup-{version_token}.exe"
    expected_url = (
        "https://github.com/CokoIya/MioVRC_Translator/releases/download/"
        f"{version_token}/{expected_installer}"
    )
    hidden_verification_tokens = (
        f"MioTranslator-{version_token}-SHA256SUMS.txt",
        "download-checksum",
        "download-signature",
        "download-signing",
        "release_signing_keys.json",
        "2a4ec078ce01b14b935a45cb04ac12eba7445a619075dbb0f50c25618e337b83",
    )
    for relative_path in ("docs/index.html", "docs/downloads/index.html"):
        page = _read(relative_path)
        assert expected_installer in page
        assert expected_url in page
        assert version_token in page
        assert "v1.3.8.5" in page
        assert all(token not in page for token in hidden_verification_tokens)
        for previous_patch in range(10):
            previous_version = f"v1.3.8.{previous_patch}"
            assert f"MioTranslator-Setup-{previous_version}.exe" not in page
            assert f"/releases/download/{previous_version}/" not in page


def test_release_notes_are_prepared_for_manifest_signing() -> None:
    expected_languages = {"zh-CN", "en", "ja", "ru", "ko"}
    expected_prefix = f"v{EXPECTED_RELEASE_VERSION}"

    # Artifact-derived manifest metadata is finalized and signed only after the
    # production installer exists. Human-authored notes must be ready first
    # because the manifest generator deliberately preserves them.
    for relative_path in ("mio_update.json", "docs/installer_manifest.json"):
        manifest = json.loads(_read(relative_path))
        assert str(manifest["notes"]).startswith(expected_prefix)
        localized = manifest["notes_i18n"]
        assert set(localized) == expected_languages
        assert all(str(note).startswith(expected_prefix) for note in localized.values())


def test_current_release_docs_describe_single_installer_and_custom_relays() -> None:
    for relative_path in (
        "docs/README.zh-CN.md",
        "docs/README.en.md",
        "docs/README.ja.md",
    ):
        readme = _read(relative_path).casefold()
        assert "`full`" not in readme
        assert "`lite`" not in readme

    website = _read("docs/index.html")
    assert "Base URL and Model are automatically locked" not in website
    assert "Base URL 与 Model 将按后端自动锁定" not in website
    assert "compatible relays keep custom Base URLs" in website
    assert "兼容中继的自定义 Base URL" in website
    for field in (
        "download-sub",
        "download-card-body",
        "download-release-note",
        "download-model-text",
        "download-model-note",
        "download-qq-1-text",
        "download-qq-2-text",
        "download-community-note",
    ):
        assert f'id="{field}"' in website
        assert website.count(f"'{field}':") == 5
    for removed_field in (
        "download-checksum",
        "download-signature",
        "download-signing",
    ):
        assert removed_field not in website
