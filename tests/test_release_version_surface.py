from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RELEASE_VERSION = "1.5.1"
EXPECTED_WINDOWS_NUMERIC_VERSION = "1.5.1.0"
# Official installers and the website are published independently of public app sources.
EXPECTED_WEBSITE_VERSION = "1.6.4"


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


def test_public_source_build_and_website_release_versions() -> None:
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
        == EXPECTED_RELEASE_VERSION
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

    version_token = f"v{EXPECTED_WEBSITE_VERSION}"
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

    # Artifact-derived manifest metadata is finalized and signed only after the
    # production installer exists. Human-authored notes must be ready first
    # because the manifest generator deliberately preserves them.
    for relative_path, release_version in (
        ("mio_update.json", EXPECTED_RELEASE_VERSION),
        ("docs/installer_manifest.json", EXPECTED_WEBSITE_VERSION),
    ):
        expected_prefix = f"v{release_version}"
        manifest = json.loads(_read(relative_path))
        assert manifest["version"] == expected_prefix
        assert str(manifest["notes"]).startswith(expected_prefix)
        localized = manifest["notes_i18n"]
        assert set(localized) == expected_languages
        assert all(str(note).startswith(expected_prefix) for note in localized.values())


WEBSITE_LOCALES = ("zh-CN", "en", "ja", "ru", "ko")


def _website_translations(website: str) -> dict[str, dict[str, str]]:
    table = re.search(r"const translations = \{\n(.*?)\n    \};", website, flags=re.DOTALL)
    assert table is not None, "Missing website translations table"
    # re.split with a capture group yields [prefix, locale, body, locale, body, ...].
    parts = re.split(r"^      '?(zh-CN|en|ja|ru|ko)'?: \{$", table.group(1), flags=re.MULTILINE)
    return {
        locale: dict(re.findall(r"\b([A-Za-z]\w*): '((?:[^'\\]|\\.)*)'", body))
        for locale, body in zip(parts[1::2], parts[2::2])
    }


def _website_screen_sizes(website: str) -> dict[str, tuple[int, int]]:
    block = _required_match(r"const SCREEN_SIZES = \{([^}]*)\};", website, label="SCREEN_SIZES").group(1)
    return {
        scene: (int(width), int(height))
        for scene, width, height in re.findall(r"'?([\w-]+)'?: \[(\d+), (\d+)\]", block)
    }


def test_website_presents_the_classic_release_in_every_language() -> None:
    website = _read("docs/index.html")
    translations = _website_translations(website)
    assert tuple(translations) == WEBSITE_LOCALES
    keys = set(translations["zh-CN"])
    assert len(keys) > 100
    version_token = f"v{EXPECTED_WEBSITE_VERSION}"
    for locale, copy in translations.items():
        assert set(copy) == keys, f"{locale} website copy has different keys"
        assert all(value.strip() for value in copy.values()), f"{locale} has empty website copy"
        for key in ("description", "heroBadge", "downloadIntro"):
            assert version_token in copy[key], f"{locale}.{key} does not name {version_token}"
        for key in ("description", "feature3Body", "guide5Title"):
            assert "SteamVR" in copy[key], f"{locale}.{key} no longer mentions SteamVR"
        assert "v1.3.8.5" in copy["olderNote"]

    # The classic (Qt) app is the product again and carries the name MioVRCT. The shelved
    # Electron edition is not offered: its MioVRCT-Setup-1.6.x installers remove the classic app.
    downloads = _read("docs/downloads/index.html")
    for page in (website, downloads):
        assert "MioVRCT-Setup" not in page
        assert "MioVRCT" in page
    for locale, copy in translations.items():
        assert copy["pageTitle"].startswith("MioVRCT"), locale
        assert "Mio RealTime Translator" not in " ".join(copy.values()), locale
    assert "SteamVR" in downloads

    release = _required_match(r"const RELEASE = \{([^}]*)\};", website, label="RELEASE block").group(1)
    fields = dict(re.findall(r"(\w+): '([^']*)'", release))
    expected_installer = f"MioTranslator-Setup-{version_token}.exe"
    assert fields["version"] == EXPECTED_WEBSITE_VERSION
    assert fields["file"] == expected_installer
    assert fields["url"] == (
        "https://github.com/CokoIya/MioVRC_Translator/releases/download/"
        f"{version_token}/{expected_installer}"
    )
    assert re.fullmatch(r"\d+ MiB", fields["sizeLabel"])

    # The download works without JavaScript: the static markup carries the same release.
    anchor = _required_match(r'<a[^>]*id="download-direct-link"[^>]*>', website, label="download link").group(0)
    assert f'href="{fields["url"]}"' in anchor
    assert f'download="{expected_installer}"' in anchor
    assert f'<span data-release="version">{version_token}</span>' in website
    assert f'<span data-release="sizeLabel">{fields["sizeLabel"]}</span>' in website


def test_website_screenshots_exist_for_every_language_and_theme() -> None:
    website = _read("docs/index.html")
    sizes = _website_screen_sizes(website)
    scenes = set(re.findall(r'data-scene="([\w-]+)"', website))
    assert scenes and scenes <= set(sizes)
    for tag in re.findall(r'<img[^>]*data-scene="[\w-]+"[^>]*>', website):
        scene = _required_match(r'data-scene="([\w-]+)"', tag, label="scene").group(1)
        width = int(_required_match(r'\swidth="(\d+)"', tag, label=f"{scene} width").group(1))
        height = int(_required_match(r'\sheight="(\d+)"', tag, label=f"{scene} height").group(1))
        assert (width, height) == sizes[scene], f"static size of {scene} differs from SCREEN_SIZES"

    screens = REPO_ROOT / "docs" / "assets" / "screens"
    manifest = json.loads((screens / "manifest.json").read_text(encoding="utf-8"))
    for locale in WEBSITE_LOCALES:
        for theme in ("dark", "light"):
            listed = manifest[locale][theme]
            assert set(listed) == scenes, f"{locale}/{theme} screenshots are stale or missing"
            for scene in scenes:
                assert (screens / locale / theme / f"{scene}.webp").is_file()
                width, height = sizes[scene]
                image_width = listed[scene]["width"]
                image_height = listed[scene]["height"]
                # Window shots are stored at 2x for sharp text, headset panels at native size;
                # either way the page box keeps the picture's aspect ratio (within a pixel).
                assert image_width in (width, 2 * width), f"{locale}/{theme}/{scene} width"
                assert abs(image_height * width - height * image_width) <= image_width, (
                    f"{locale}/{theme}/{scene} aspect ratio"
                )


def test_release_build_preserves_existing_dist_outputs() -> None:
    script = _read("build_release.ps1")
    assert r'dist\release-v$releaseVersion' in script
    assert r'build\release-v$releaseVersion' in script
    assert "Refusing to overwrite an existing release output" in script
    assert "Unsafe release output parent" in script
    assert "--distpath $bundleDistPath --workpath $releaseWorkPath" in script
    assert 'Join-Path $bundlePath "MioTranslator.exe"' in script
    assert "--bundle $bundlePath" in script
    assert '"/DSourceDir=$bundlePath"' in script
    assert "#ifndef SourceDir" in _read("MioTranslator-installer.iss")


def test_public_build_excludes_private_vr_modules() -> None:
    spec = _read("MioTranslator.spec")
    for module in (
        "src.core.vr_handsframe",
        "src.core.vr_head_anchor",
        "src.core.vr_caption_anchor",
        "src.ui_qt.vr_handsframe_style",
    ):
        assert f'"{module}"' not in spec


def test_current_release_docs_describe_single_installer() -> None:
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
    translations = _website_translations(website)
    for key in (
        "downloadTitle",
        "downloadIntro",
        "downloadNote",
        "downloadButton",
        "releaseNotes",
        "qq1",
        "qq2",
        "communityNote",
        "olderNote",
    ):
        assert f'data-i18n="{key}"' in website
        assert all(translations[locale].get(key) for locale in WEBSITE_LOCALES), key
    for removed_field in (
        "download-checksum",
        "download-signature",
        "download-signing",
    ):
        assert removed_field not in website
