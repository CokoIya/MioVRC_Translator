"""Utilities for plugin-managed Style-Bert-VITS2 model folders."""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.utils.app_paths import resource_base_dirs, writable_app_dir

logger = logging.getLogger(__name__)

MODEL_FILE_SUFFIXES = {".safetensors", ".pth", ".pt", ".onnx"}
VOICE_ID_SEPARATOR = " :: "


class StyleBertVits2ModelError(RuntimeError):
    """Raised when a custom Style-Bert-VITS2 model folder is invalid."""


@dataclass(frozen=True)
class StyleBertVits2ModelInfo:
    """Validated metadata for one imported Style-Bert-VITS2 model."""

    name: str
    directory: Path
    config_path: Path
    style_vectors_path: Path
    model_path: Path
    speakers: tuple[str, ...]
    styles: tuple[str, ...]


@dataclass(frozen=True)
class StyleBertVits2Preset:
    """Friendly catalog entry for a known custom voice layout."""

    key: str
    title: str
    model_path: str
    speaker_id: str
    language: str


@dataclass(frozen=True)
class StyleBertVits2ModelBundle:
    """Download metadata for one shared Hololive model bundle."""

    model_path: str
    files: tuple[str, ...]


def style_bert_models_dir() -> Path:
    """Return the plugin-managed directory for imported custom voices."""
    target = writable_app_dir() / "tts_models" / "style_bert_vits2"
    target.mkdir(parents=True, exist_ok=True)
    return target


def inspect_style_bert_model_dir(model_dir: Path) -> StyleBertVits2ModelInfo:
    """Validate and summarize one Style-Bert-VITS2 model directory."""
    directory = Path(model_dir).expanduser()
    if not directory.is_dir():
        raise StyleBertVits2ModelError("The selected path is not a folder.")

    config_path = directory / "config.json"
    style_vectors_path = directory / "style_vectors.npy"
    if not config_path.is_file():
        raise StyleBertVits2ModelError("Missing config.json.")
    if not style_vectors_path.is_file():
        raise StyleBertVits2ModelError("Missing style_vectors.npy.")

    model_files = sorted(
        (
            file
            for file in directory.iterdir()
            if file.is_file()
            and file.suffix.lower() in MODEL_FILE_SUFFIXES
            and not file.name.startswith(".")
        ),
        key=lambda file: file.stat().st_mtime,
        reverse=True,
    )
    if not model_files:
        raise StyleBertVits2ModelError(
            "Missing model weights (.safetensors, .pth, .pt, or .onnx)."
        )

    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StyleBertVits2ModelError(f"config.json could not be read: {exc}") from exc

    data = raw_config.get("data") if isinstance(raw_config, dict) else None
    if not isinstance(data, dict):
        raise StyleBertVits2ModelError("config.json does not contain a valid data block.")

    speakers = _dict_keys_as_tuple(data.get("spk2id"))
    styles = _dict_keys_as_tuple(data.get("style2id"))
    if not speakers:
        raise StyleBertVits2ModelError("No speakers were found in config.json.")
    if not styles:
        raise StyleBertVits2ModelError("No styles were found in config.json.")

    return StyleBertVits2ModelInfo(
        name=directory.name,
        directory=directory,
        config_path=config_path,
        style_vectors_path=style_vectors_path,
        model_path=model_files[0],
        speakers=speakers,
        styles=styles,
    )


def import_style_bert_model_path(source: Path) -> list[StyleBertVits2ModelInfo]:
    """Import one model folder, or each valid child folder in a model root."""
    source_path = Path(source).expanduser()
    if not source_path.is_dir():
        raise StyleBertVits2ModelError("The selected path is not a folder.")

    candidates: list[Path] = []
    try:
        inspect_style_bert_model_dir(source_path)
    except StyleBertVits2ModelError:
        candidates = sorted(
            (child for child in source_path.iterdir() if child.is_dir()),
            key=lambda path: path.name.lower(),
        )
    else:
        candidates = [source_path]

    imported: list[StyleBertVits2ModelInfo] = []
    validation_errors: list[str] = []
    for candidate in candidates:
        try:
            inspected = inspect_style_bert_model_dir(candidate)
        except StyleBertVits2ModelError as exc:
            validation_errors.append(f"{candidate.name}: {exc}")
            continue

        target_root = style_bert_models_dir()
        target_dir = _replaceable_target_dir(target_root, inspected.name)
        if target_dir.exists():
            _remove_managed_model_dir(target_dir, target_root)
        shutil.copytree(inspected.directory, target_dir)
        _write_import_metadata(target_dir, inspected)
        imported.append(inspect_style_bert_model_dir(target_dir))

    if imported:
        logger.info("Imported %d Style-Bert-VITS2 model folder(s)", len(imported))
        return imported

    detail = validation_errors[0] if validation_errors else "No usable model folders were found."
    raise StyleBertVits2ModelError(detail)


def list_imported_style_bert_models() -> list[StyleBertVits2ModelInfo]:
    """List valid custom Style-Bert-VITS2 model folders already imported."""
    imported: list[StyleBertVits2ModelInfo] = []
    for child in sorted(style_bert_models_dir().iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            imported.append(inspect_style_bert_model_dir(child))
        except StyleBertVits2ModelError as exc:
            logger.warning("Skipping invalid Style-Bert-VITS2 model folder %s: %s", child, exc)
    return imported


@lru_cache(maxsize=1)
def load_style_bert_vits2_presets() -> tuple[StyleBertVits2Preset, ...]:
    """Load bundled friendly-name metadata for known custom voice packs."""
    relative = Path("assets") / "tts" / "hololive_style_bert_vits2_catalog.json"
    for base in resource_base_dirs():
        candidate = base / relative
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load Style-Bert-VITS2 preset catalog %s: %s", candidate, exc)
            return ()
        if not isinstance(payload, list):
            return ()
        presets: list[StyleBertVits2Preset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            title = str(item.get("title") or "").strip()
            model_path = str(item.get("model_path") or "").strip()
            speaker_id = str(item.get("speaker_id") or "").strip()
            language = str(item.get("language") or "").strip().upper()
            if not all((key, title, model_path, speaker_id)):
                continue
            presets.append(
                StyleBertVits2Preset(
                    key=key,
                    title=title,
                    model_path=model_path,
                    speaker_id=speaker_id,
                    language=language or "JP",
                )
            )
        return tuple(presets)
    return ()


def style_bert_preset_title(model_name: str, speaker: str) -> str | None:
    """Resolve a human-friendly title when an imported voice matches the bundled catalog."""
    for preset in load_style_bert_vits2_presets():
        if preset.model_path == model_name and preset.speaker_id == speaker:
            return preset.title
    return None


def style_bert_preset_language(model_name: str, speaker: str) -> str | None:
    """Resolve the catalog language for a known imported voice."""
    for preset in load_style_bert_vits2_presets():
        if preset.model_path == model_name and preset.speaker_id == speaker:
            return preset.language
    return None


def hololive_preset_choice_rows() -> tuple[tuple[str, str], ...]:
    """Return stable `(display_label, model_path)` rows for settings UI."""
    rows: list[tuple[str, str]] = []
    for preset in load_style_bert_vits2_presets():
        rows.append((preset.title, preset.model_path))
    return tuple(rows)


def hololive_model_bundle(model_path: str) -> StyleBertVits2ModelBundle | None:
    """Return the shared downloadable files for one known Hololive model pack."""
    clean = str(model_path or "").strip()
    if not clean:
        return None
    if not any(preset.model_path == clean for preset in load_style_bert_vits2_presets()):
        return None
    return StyleBertVits2ModelBundle(
        model_path=clean,
        files=(f"{clean}.safetensors", "config.json", "style_vectors.npy"),
    )


def style_bert_voice_id(model_name: str, speaker: str, style: str) -> str:
    """Build a stable, readable voice ID for settings and config."""
    return VOICE_ID_SEPARATOR.join((model_name, speaker, style))


def parse_style_bert_voice_id(value: str) -> tuple[str, str, str]:
    """Parse a Style-Bert-VITS2 voice ID from config or the settings menu."""
    parts = [part.strip() for part in str(value or "").split(VOICE_ID_SEPARATOR)]
    if len(parts) != 3 or not all(parts):
        raise StyleBertVits2ModelError("The selected custom voice is no longer valid.")
    return parts[0], parts[1], parts[2]


def _dict_keys_as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(str(key).strip() for key in value.keys() if str(key).strip())


def _replaceable_target_dir(root: Path, raw_name: str) -> Path:
    return root / _safe_folder_name(raw_name)


def _remove_managed_model_dir(target_dir: Path, root: Path) -> None:
    try:
        resolved_root = root.resolve(strict=False)
        resolved_target = target_dir.resolve(strict=False)
        resolved_target.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise StyleBertVits2ModelError(f"Unsafe model replacement path: {target_dir}") from exc
    if resolved_target == resolved_root:
        raise StyleBertVits2ModelError("Refusing to replace the model root.")
    if target_dir.exists():
        shutil.rmtree(target_dir)


def _write_import_metadata(target_dir: Path, inspected: StyleBertVits2ModelInfo) -> None:
    payload = {
        "schema": "mio-style-bert-vits2-import-v1",
        "source_name": inspected.name,
        "speakers": list(inspected.speakers),
        "styles": list(inspected.styles),
    }
    try:
        (target_dir / ".mio-style-bert-vits2.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.debug("Failed to write Style-Bert-VITS2 import metadata", exc_info=True)


def _safe_folder_name(value: str) -> str:
    name = Path(str(value or "").strip() or "custom-voice").name
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
    return name or "custom-voice"
