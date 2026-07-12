from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "MioTranslator-installer.iss"
INSTALLER_LOCALES = ROOT / "installer" / "i18n"
ENGLISH_BASELINE = "English.isl"
EXPECTED_LOCALE_FILES = {
    ENGLISH_BASELINE,
    "ChineseSimplified.isl",
    "Japanese.isl",
    "Korean.isl",
    "Russian.isl",
}
EXPECTED_SECTIONS = {"LangOptions", "Messages", "CustomMessages"}
EXPECTED_INNO_MESSAGE_COUNT = 281
_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
_SECTION_RE = re.compile(r"\[([^]]+)]\Z")
_PLACEHOLDER_RE = re.compile(
    r"%(?:n|[1-9])|\[[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?\]",
    re.IGNORECASE,
)
_BRACKET_TOKEN_RE = re.compile(r"\[[A-Za-z0-9_/]+]")
_MESSAGE_FILE_RE = re.compile(r'installer\\i18n\\([^";]+\.isl)', re.IGNORECASE)
_CUSTOM_MESSAGE_USE_RE = re.compile(r"\{cm:([A-Za-z][A-Za-z0-9_]*)")

# These strings are language-neutral buttons, units, or formatting templates.
_ALLOWED_IDENTICAL_MESSAGES = {
    "ChineseSimplified.isl": {
        "ComponentSize1",
        "ComponentSize2",
        "UninstallDisplayNameMark",
        "UninstallDisplayNameMarks",
    },
    "Japanese.isl": {
        "ButtonOK",
        "ComponentSize1",
        "ComponentSize2",
        "UninstallDisplayNameMark",
        "UninstallDisplayNameMarks",
    },
    "Korean.isl": {
        "ComponentSize1",
        "ComponentSize2",
        "UninstallDisplayNameMark",
        "UninstallDisplayNameMarks",
    },
    "Russian.isl": {
        "ButtonOK",
        "UninstallDisplayNameMark",
        "UninstallDisplayNameMarks",
    },
}
_EXPECTED_DIALOG_FONTS = {
    "ChineseSimplified.isl": "Microsoft YaHei UI",
    "English.isl": "Segoe UI",
    "Japanese.isl": "Yu Gothic UI",
    "Korean.isl": "Malgun Gothic",
    "Russian.isl": "Segoe UI",
}
_EXPECTED_LANGUAGE_IDS = {
    "ChineseSimplified.isl": "$0804",
    "English.isl": "$0409",
    "Japanese.isl": "$0411",
    "Korean.isl": "$0412",
    "Russian.isl": "$0419",
}


def _parse_sections(
    path: Path,
) -> tuple[
    dict[str, dict[str, str]],
    list[str],
    list[str],
    Counter[str],
    set[str],
]:
    sections: dict[str, dict[str, str]] = defaultdict(dict)
    normalized_keys: dict[str, dict[str, str]] = defaultdict(dict)
    duplicates: list[str] = []
    malformed: list[str] = []
    section_counts: Counter[str] = Counter()
    unexpected_sections: set[str] = set()
    current = ""

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue

        if line.startswith("["):
            match = _SECTION_RE.fullmatch(line)
            if match is None:
                malformed.append(f"line {line_number}: malformed section header")
                current = ""
                continue
            current = match.group(1)
            if current in EXPECTED_SECTIONS:
                section_counts[current] += 1
            else:
                unexpected_sections.add(current)
            continue

        if current not in EXPECTED_SECTIONS:
            continue
        if "=" not in line:
            malformed.append(f"line {line_number}: expected key=value in [{current}]")
            continue

        key, value = (part.strip() for part in line.split("=", 1))
        if _KEY_RE.fullmatch(key) is None:
            malformed.append(f"line {line_number}: malformed key {key!r}")
            continue

        folded_key = key.casefold()
        previous_key = normalized_keys[current].get(folded_key)
        if previous_key is not None:
            duplicates.append(
                f"line {line_number}: duplicate [{current}] key "
                f"{key!r} (first spelling {previous_key!r})"
            )
            continue

        normalized_keys[current][folded_key] = key
        sections[current][key] = value

    return (
        dict(sections),
        duplicates,
        malformed,
        section_counts,
        unexpected_sections,
    )


def _placeholder_signature(value: str) -> tuple[str, ...]:
    return tuple(sorted((token.casefold() for token in _PLACEHOLDER_RE.findall(value))))


def _assert_placeholders_well_formed(filename: str, section: str, key: str, value: str):
    remainder = _PLACEHOLDER_RE.sub("", value)
    assert "%" not in remainder, (
        f"{filename}: malformed percent placeholder in {section}.{key}: {value!r}"
    )
    assert _BRACKET_TOKEN_RE.search(remainder) is None, (
        f"{filename}: malformed bracket placeholder in {section}.{key}: {value!r}"
    )


def test_installer_references_only_the_pinned_local_message_files():
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
    referenced = set(_MESSAGE_FILE_RE.findall(script))

    assert "compiler:Default.isl" not in script
    assert referenced == EXPECTED_LOCALE_FILES
    assert {path.name for path in INSTALLER_LOCALES.glob("*.isl")} == (
        EXPECTED_LOCALE_FILES
    )


def test_installer_automatically_uses_the_windows_display_language():
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")

    assert re.search(r"(?mi)^LanguageDetectionMethod=uilanguage\s*$", script)
    assert re.search(r"(?mi)^ShowLanguageDialog=no\s*$", script)
    assert re.search(r"(?mi)^UsePreviousLanguage=yes\s*$", script)
    assert re.search(
        r"(?m)^Name: \"english\"; MessagesFile: ",
        script,
    )
    assert '\"language_source\": \"auto\"' in script


def test_installer_locale_catalogs_are_complete_and_well_formed():
    parsed = {
        path.name: _parse_sections(path)
        for path in sorted(INSTALLER_LOCALES.glob("*.isl"))
    }
    reference_sections = parsed[ENGLISH_BASELINE][0]

    assert len(reference_sections["Messages"]) == EXPECTED_INNO_MESSAGE_COUNT
    assert "AdditionalTasks" in reference_sections["CustomMessages"]

    for filename, result in parsed.items():
        sections, duplicates, malformed, section_counts, unexpected_sections = result
        text = (INSTALLER_LOCALES / filename).read_text(encoding="utf-8-sig")

        assert duplicates == [], filename
        assert malformed == [], filename
        assert section_counts == Counter({section: 1 for section in EXPECTED_SECTIONS}), (
            filename
        )
        assert unexpected_sections == set(), filename
        assert "Inno Setup version 6.5.0+" in text.splitlines()[0], filename
        assert "\ufffd" not in text, filename
        assert "\x00" not in text, filename

        assert set(sections["LangOptions"]) == set(reference_sections["LangOptions"]), (
            filename
        )
        for key, value in sections["LangOptions"].items():
            assert value, f"{filename}: empty LangOptions.{key}"
        assert sections["LangOptions"]["DialogFontName"] == _EXPECTED_DIALOG_FONTS[
            filename
        ]
        assert sections["LangOptions"]["WelcomeFontName"] == _EXPECTED_DIALOG_FONTS[
            filename
        ]
        assert sections["LangOptions"]["LanguageID"] == _EXPECTED_LANGUAGE_IDS[
            filename
        ]
        assert sections["LangOptions"]["DialogFontSize"] == "9"
        assert sections["LangOptions"]["WelcomeFontSize"] == "12"

        for section in ("Messages", "CustomMessages"):
            assert set(sections[section]) == set(reference_sections[section]), (
                f"{filename}: {section} key mismatch"
            )
            for key, value in sections[section].items():
                reference_value = reference_sections[section][key]
                if reference_value:
                    assert value, f"{filename}: empty {section}.{key}"
                _assert_placeholders_well_formed(filename, section, key, value)
                assert _placeholder_signature(value) == _placeholder_signature(
                    reference_value
                ), (
                    f"{filename}: placeholder mismatch for {section}.{key}: "
                    f"expected={_placeholder_signature(reference_value)} "
                    f"actual={_placeholder_signature(value)}"
                )


def test_installer_catalogs_do_not_silently_reuse_english_ui_copy():
    parsed = {
        path.name: _parse_sections(path)[0]
        for path in sorted(INSTALLER_LOCALES.glob("*.isl"))
    }
    english_messages = parsed[ENGLISH_BASELINE]["Messages"]

    for filename, allowed_keys in _ALLOWED_IDENTICAL_MESSAGES.items():
        localized_messages = parsed[filename]["Messages"]
        identical_keys = {
            key
            for key, value in localized_messages.items()
            if value and value == english_messages[key]
        }
        assert identical_keys == allowed_keys, filename

        english_custom = parsed[ENGLISH_BASELINE]["CustomMessages"]
        identical_custom = {
            key
            for key, value in parsed[filename]["CustomMessages"].items()
            if value and value == english_custom[key]
        }
        assert identical_custom == set(), filename


def test_installer_custom_messages_are_sourced_from_each_language_catalog():
    script_sections, duplicates, malformed, _, _ = _parse_sections(INSTALLER_SCRIPT)
    assert duplicates == []
    assert malformed == []

    english_custom = _parse_sections(INSTALLER_LOCALES / ENGLISH_BASELINE)[0][
        "CustomMessages"
    ]
    # Unqualified script-level CustomMessages override every selected .isl
    # catalog, which would silently force English text in all installers.
    assert script_sections.get("CustomMessages", {}) == {}

    used_keys = set(
        _CUSTOM_MESSAGE_USE_RE.findall(INSTALLER_SCRIPT.read_text(encoding="utf-8-sig"))
    )
    assert used_keys <= set(english_custom)
