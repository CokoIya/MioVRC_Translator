from __future__ import annotations

import ast
import configparser
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from src.ui_qt.audio_diagnostics_window import _COPY as AUDIO_DIAGNOSTICS_COPY
from src.ui_qt.main_window import MAIN_COPY
from src.ui_qt.mode_wizard_dialog import (
    _COPY as MODE_WIZARD_COPY,
    _MODE_ORDER as MODE_WIZARD_MODE_ORDER,
)
from src.ui_qt.qt_localization import QT_FILE_DIALOG_TEXTS
from src.ui_qt.settings_window import (
    DENOISE_LABELS,
    FIELD_HINTS,
    FONT_LABELS,
    QT_SETTINGS_COPY,
    ROLEPLAY_PRESET_DISPLAY_TEXTS,
    ROLEPLAY_PRESETS,
    THEME_LABELS,
)
from src.ui_qt.vad_calibration_window import _COPY as VAD_CALIBRATION_COPY
from src.utils.i18n import UI_TEXTS
from src.utils.localization import (
    RESERVED_COMPATIBILITY_UI_KEYS,
    SUPPORTED_UI_LANGUAGES,
    validate_language_catalog,
)
from src.utils.translation_error_formatter import _TEXTS as TRANSLATION_ERROR_TEXTS
from src.utils.ui_config import (
    LANGUAGE_DISPLAY_NAMES,
    OUTPUT_FORMAT_2_LABELS,
    OUTPUT_FORMAT_LABELS,
    SOCIAL_MODE_LABELS,
    SOCIAL_POLITENESS_LABELS,
    SOCIAL_TONE_LABELS,
    TRANSLATION_BACKEND_LABELS,
)


ROOT = Path(__file__).resolve().parents[1]

LANGUAGE_NEUTRAL_GLOBAL_KEYS = {
    "api_key",
    "base_url",
    "char_count",
    "device_cpu",
    "engine_gtts_label",
    "log_warning",
    "model_download_amount_gb",
    "model_download_engine_info",
    "model_download_progress_file",
    "model_download_speed_eta",
    "provider_anthropic",
    "provider_deepseek",
    "provider_gemini",
    "provider_openai",
    "provider_qwen",
    "quick_switch_section_tts",
    "settings_megabytes_suffix",
    "text_input_pin_off",
    "text_input_pin_on",
    "tts_device_cpu",
    "tts_engine_mimo_tts",
    "tts_engine_qwen_tts",
    "tts_read_button",
    "vrchat_tab",
    "window_title",
    "xtts_runtime_component_separator",
}


def _transpose_key_catalog(
    catalog: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    transposed = {language: {} for language in SUPPORTED_UI_LANGUAGES}
    for key, values in catalog.items():
        for language in SUPPORTED_UI_LANGUAGES:
            if language in values:
                transposed[language][key] = values[language]
    return transposed


@pytest.mark.parametrize(
    ("name", "catalog"),
    (
        ("global", UI_TEXTS),
        ("main", _transpose_key_catalog(MAIN_COPY)),
        ("settings", _transpose_key_catalog(QT_SETTINGS_COPY)),
        ("settings_hints", _transpose_key_catalog(FIELD_HINTS)),
        ("settings_themes", THEME_LABELS),
        ("settings_fonts", FONT_LABELS),
        ("settings_denoise", DENOISE_LABELS),
        (
            "settings_roleplay_presets",
            _transpose_key_catalog(
                {
                    preset_id: profile["labels"]
                    for preset_id, profile in ROLEPLAY_PRESETS.items()
                }
            ),
        ),
        ("audio_diagnostics", AUDIO_DIAGNOSTICS_COPY),
        ("vad_calibration", VAD_CALIBRATION_COPY),
        ("mode_wizard", MODE_WIZARD_COPY),
        ("qt_file_dialogs", QT_FILE_DIALOG_TEXTS),
        ("translation_errors", TRANSLATION_ERROR_TEXTS),
        ("language_names", LANGUAGE_DISPLAY_NAMES),
        ("output_formats", _transpose_key_catalog(OUTPUT_FORMAT_LABELS)),
        ("secondary_output_formats", _transpose_key_catalog(OUTPUT_FORMAT_2_LABELS)),
        ("social_modes", SOCIAL_MODE_LABELS),
        ("social_politeness", SOCIAL_POLITENESS_LABELS),
        ("social_tones", SOCIAL_TONE_LABELS),
        ("translation_backend_labels", _transpose_key_catalog(TRANSLATION_BACKEND_LABELS)),
    ),
)
def test_translation_catalogs_are_complete_and_well_formed(name, catalog):
    issues = validate_language_catalog(catalog)
    assert issues == (), f"{name}: {issues[:20]}"


def test_translation_keys_use_stable_machine_readable_names():
    catalogs = (
        UI_TEXTS,
        _transpose_key_catalog(MAIN_COPY),
        _transpose_key_catalog(QT_SETTINGS_COPY),
        _transpose_key_catalog(FIELD_HINTS),
        THEME_LABELS,
        FONT_LABELS,
        DENOISE_LABELS,
        _transpose_key_catalog(
            {
                preset_id: profile["labels"]
                for preset_id, profile in ROLEPLAY_PRESETS.items()
            }
        ),
        AUDIO_DIAGNOSTICS_COPY,
        VAD_CALIBRATION_COPY,
        MODE_WIZARD_COPY,
        QT_FILE_DIALOG_TEXTS,
        TRANSLATION_ERROR_TEXTS,
        LANGUAGE_DISPLAY_NAMES,
        _transpose_key_catalog(OUTPUT_FORMAT_LABELS),
        _transpose_key_catalog(OUTPUT_FORMAT_2_LABELS),
        SOCIAL_MODE_LABELS,
        SOCIAL_POLITENESS_LABELS,
        SOCIAL_TONE_LABELS,
        _transpose_key_catalog(TRANSLATION_BACKEND_LABELS),
    )
    invalid: list[str] = []
    for catalog in catalogs:
        for language in SUPPORTED_UI_LANGUAGES:
            for key in catalog.get(language, {}):
                if not key or not key.replace("_", "").isalnum() or key != key.lower():
                    invalid.append(str(key))
    assert sorted(set(invalid)) == []


def test_supported_locales_do_not_silently_reuse_english_ui_sentences():
    english = UI_TEXTS["en"]
    failures: list[str] = []
    for language in SUPPORTED_UI_LANGUAGES:
        if language == "en":
            continue
        for key, value in UI_TEXTS[language].items():
            if value != english.get(key) or key in LANGUAGE_NEUTRAL_GLOBAL_KEYS:
                continue
            failures.append(f"{language}:{key}={value!r}")
    assert failures == []


def test_component_catalogs_do_not_silently_reuse_english_ui_sentences():
    roleplay_catalog = _transpose_key_catalog(
        {
            preset_id: profile["labels"]
            for preset_id, profile in ROLEPLAY_PRESETS.items()
        }
    )
    catalogs = {
        "main": _transpose_key_catalog(MAIN_COPY),
        "settings": _transpose_key_catalog(QT_SETTINGS_COPY),
        "settings_hints": _transpose_key_catalog(FIELD_HINTS),
        "settings_themes": THEME_LABELS,
        "settings_fonts": FONT_LABELS,
        "settings_denoise": DENOISE_LABELS,
        "settings_roleplay_presets": roleplay_catalog,
        "audio_diagnostics": AUDIO_DIAGNOSTICS_COPY,
        "vad_calibration": VAD_CALIBRATION_COPY,
        "mode_wizard": MODE_WIZARD_COPY,
        "qt_file_dialogs": QT_FILE_DIALOG_TEXTS,
        "translation_errors": TRANSLATION_ERROR_TEXTS,
        "language_names": LANGUAGE_DISPLAY_NAMES,
        "output_formats": _transpose_key_catalog(OUTPUT_FORMAT_LABELS),
        "secondary_output_formats": _transpose_key_catalog(OUTPUT_FORMAT_2_LABELS),
        "social_modes": SOCIAL_MODE_LABELS,
        "social_politeness": SOCIAL_POLITENESS_LABELS,
        "social_tones": SOCIAL_TONE_LABELS,
        "translation_backend_labels": _transpose_key_catalog(TRANSLATION_BACKEND_LABELS),
    }
    neutral_keys = {
        ("main", "guide_short"),
        ("audio_diagnostics", "none"),
        ("social_modes", "roleplay"),
        *(
            ("translation_backend_labels", backend)
            for backend in {
                "anthropic",
                "deepseek",
                "doubao",
                "gemini",
                "hunyuan",
                "kimi",
                "libretranslate",
                "mistral",
                "mymemory",
                "nvidia",
                "openai",
                "qianwen",
                "xai",
                "xiaomi",
                "zhipu",
            }
        ),
        *(
            ("settings_roleplay_presets", preset_id)
            for preset_id in ROLEPLAY_PRESETS
            if preset_id != "custom"
        ),
    }
    failures: list[str] = []
    for catalog_name, catalog in catalogs.items():
        english = catalog["en"]
        for language in SUPPORTED_UI_LANGUAGES:
            if language == "en":
                continue
            for key, value in catalog[language].items():
                if value != english.get(key) or (catalog_name, key) in neutral_keys:
                    continue
                failures.append(f"{catalog_name}:{language}:{key}={value!r}")
    assert failures == []


def test_roleplay_editor_copy_covers_every_supported_language():
    failures: list[str] = []
    for preset_id, fields in ROLEPLAY_PRESET_DISPLAY_TEXTS.items():
        for field_name in ("persona_prompt", "persona_glossary"):
            translations = fields.get(field_name, {})
            if set(translations) != set(SUPPORTED_UI_LANGUAGES):
                failures.append(
                    f"{preset_id}:{field_name}: locales={sorted(translations)}"
                )
                continue
            english = translations["en"]
            canonical = str(ROLEPLAY_PRESETS[preset_id][field_name])
            if english != canonical:
                failures.append(f"{preset_id}:{field_name}: English differs from canonical")
            for language, value in translations.items():
                if not str(value).strip():
                    failures.append(f"{preset_id}:{field_name}:{language}: empty")
                if language != "en" and value == english:
                    failures.append(
                        f"{preset_id}:{field_name}:{language}: English fallback"
                    )
    assert failures == []


def test_translation_values_are_unicode_safe_and_free_of_replacement_text():
    catalogs = (
        UI_TEXTS,
        _transpose_key_catalog(MAIN_COPY),
        _transpose_key_catalog(QT_SETTINGS_COPY),
        _transpose_key_catalog(FIELD_HINTS),
        THEME_LABELS,
        FONT_LABELS,
        DENOISE_LABELS,
        _transpose_key_catalog(
            {
                preset_id: profile["labels"]
                for preset_id, profile in ROLEPLAY_PRESETS.items()
            }
        ),
        AUDIO_DIAGNOSTICS_COPY,
        VAD_CALIBRATION_COPY,
        MODE_WIZARD_COPY,
        QT_FILE_DIALOG_TEXTS,
        TRANSLATION_ERROR_TEXTS,
        LANGUAGE_DISPLAY_NAMES,
        _transpose_key_catalog(OUTPUT_FORMAT_LABELS),
        _transpose_key_catalog(OUTPUT_FORMAT_2_LABELS),
        SOCIAL_MODE_LABELS,
        SOCIAL_POLITENESS_LABELS,
        SOCIAL_TONE_LABELS,
        _transpose_key_catalog(TRANSLATION_BACKEND_LABELS),
    )
    failures: list[str] = []
    for catalog in catalogs:
        for language in SUPPORTED_UI_LANGUAGES:
            for key, raw_value in catalog[language].items():
                value = str(raw_value)
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError as exc:
                    failures.append(f"{language}:{key}: invalid Unicode ({exc})")
                    continue
                if "\ufffd" in value:
                    failures.append(f"{language}:{key}: replacement character")
                if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
                    failures.append(f"{language}:{key}: control character")
    assert failures == []


def test_output_format_labels_have_balanced_localized_parentheses():
    failures: list[str] = []
    for catalog_name, catalog in (
        ("primary", OUTPUT_FORMAT_LABELS),
        ("secondary", OUTPUT_FORMAT_2_LABELS),
    ):
        for key, translations in catalog.items():
            for language, value in translations.items():
                text = str(value)
                if text.count("(") != text.count(")") or text.count("（") != text.count("）"):
                    failures.append(f"{catalog_name}:{language}:{key}={text!r}")
    assert failures == []


def test_global_translation_keys_are_used_or_documented_for_compatibility():
    referenced_literals: set[str] = set()
    catalog_path = ROOT / "src" / "utils" / "i18n.py"
    for path in (ROOT / "src").rglob("*.py"):
        if path == catalog_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced_literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    catalog_keys = set(UI_TEXTS["en"])
    undocumented_unused = (
        catalog_keys - referenced_literals - RESERVED_COMPATIBILITY_UI_KEYS
    )

    assert undocumented_unused == set()
    assert RESERVED_COMPATIBILITY_UI_KEYS <= catalog_keys


def _source_literals_outside_catalog_assignment(
    relative_path: str,
    catalog_name: str,
) -> set[str]:
    tree = ast.parse(
        (ROOT / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )
    literals: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if any(
                isinstance(target, ast.Name) and target.id == catalog_name
                for target in node.targets
            ):
                return
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name) and node.target.id == catalog_name:
                return
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str):
                literals.add(node.value)

    Visitor().visit(tree)
    return literals


def test_component_translation_keys_are_used_by_their_own_ui():
    catalogs = {
        "main": (
            MAIN_COPY,
            _source_literals_outside_catalog_assignment(
                "src/ui_qt/main_window.py", "MAIN_COPY"
            ),
        ),
        "audio_diagnostics": (
            AUDIO_DIAGNOSTICS_COPY["en"],
            _source_literals_outside_catalog_assignment(
                "src/ui_qt/audio_diagnostics_window.py", "_COPY"
            ),
        ),
        "vad_calibration": (
            VAD_CALIBRATION_COPY["en"],
            _source_literals_outside_catalog_assignment(
                "src/ui_qt/vad_calibration_window.py", "_COPY"
            )
            | {"confidence_low", "confidence_medium", "confidence_high"},
        ),
        "mode_wizard": (
            MODE_WIZARD_COPY["en"],
            _source_literals_outside_catalog_assignment(
                "src/ui_qt/mode_wizard_dialog.py", "_COPY"
            )
            | {
                f"{mode_id}_{suffix}"
                for mode_id in MODE_WIZARD_MODE_ORDER
                for suffix in ("title", "body")
            },
        ),
        "qt_file_dialogs": (
            QT_FILE_DIALOG_TEXTS["en"],
            _source_literals_outside_catalog_assignment(
                "src/ui_qt/qt_localization.py", "QT_FILE_DIALOG_TEXTS"
            ),
        ),
    }

    failures = {
        name: sorted(set(catalog) - referenced)
        for name, (catalog, referenced) in catalogs.items()
        if set(catalog) - referenced
    }
    assert failures == {}


def test_literal_translation_calls_reference_declared_keys():
    failures: list[str] = []
    main_path = ROOT / "src" / "ui_qt" / "main_window.py"
    settings_path = ROOT / "src" / "ui_qt" / "settings_window.py"
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            argument = None
            catalogs: tuple[Mapping[str, object], ...] = ()
            if call_name == "tr" and len(node.args) > 1:
                argument = node.args[1]
                catalogs = (UI_TEXTS["en"],)
            elif call_name == "_t" and node.args:
                argument = node.args[0]
                catalogs = (UI_TEXTS["en"],)
            elif call_name == "_copy" and node.args:
                argument = node.args[0]
                if path == main_path:
                    catalogs = (MAIN_COPY, UI_TEXTS["en"])
                elif path == settings_path:
                    catalogs = (QT_SETTINGS_COPY, UI_TEXTS["en"])
            if not (
                catalogs
                and isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            ):
                continue
            key = argument.value
            if any(key in catalog for catalog in catalogs):
                continue
            relative = path.relative_to(ROOT)
            failures.append(f"{relative}:{node.lineno} {call_name}({key!r})")
    assert failures == []


def _read_installer_locale(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(path, encoding="utf-8-sig")
    return parser


def _installer_placeholders(value: str) -> tuple[str, ...]:
    return tuple(sorted(re.findall(r"%\d+|%n|\[[A-Za-z0-9_/]+\]", value)))


def test_installer_language_files_have_matching_keys_and_placeholders():
    locale_paths = sorted((ROOT / "installer" / "i18n").glob("*.isl"))
    assert {path.stem for path in locale_paths} == {
        "ChineseSimplified",
        "English",
        "Japanese",
        "Korean",
        "Russian",
    }
    locales = {path.stem: _read_installer_locale(path) for path in locale_paths}
    reference = locales["English"]
    failures: list[str] = []
    for locale_name, parser in locales.items():
        if set(parser.sections()) != set(reference.sections()):
            failures.append(f"{locale_name}: section mismatch")
            continue
        for section in reference.sections():
            if set(parser[section]) != set(reference[section]):
                failures.append(f"{locale_name}:{section}: key mismatch")
                continue
            for key, value in parser[section].items():
                if reference[section][key].strip() and not value.strip():
                    failures.append(f"{locale_name}:{section}.{key}: empty")
                if _installer_placeholders(value) != _installer_placeholders(
                    reference[section][key]
                ):
                    failures.append(
                        f"{locale_name}:{section}.{key}: placeholder mismatch"
                    )
    assert failures == []
