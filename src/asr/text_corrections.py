from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils.app_paths import resource_base_dirs, writable_app_dir

DEFAULT_MANIFEST_URL = "https://78hejiu.top/dictionaries/asr_dictionary_manifest.json"
LEGACY_MANIFEST_URLS = {
    "https://raw.githubusercontent.com/CokoIya/MioVRC_Translator/main/"
    "assets/dictionaries/asr_dictionary_manifest.json",
}
BUNDLED_FILENAME = "asr_terms.base.json"
OFFICIAL_FILENAME = "asr_terms.official.json"
USER_FILENAME = "asr_terms.user.json"

USER_DICTIONARY_TEMPLATE = {
    "version": 1,
    "source": "user",
    "description": "User overrides for ASR corrections.",
    "entries": [],
}


def dictionaries_dir() -> Path:
    path = writable_app_dir() / "dictionaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def official_dictionary_path() -> Path:
    return dictionaries_dir() / OFFICIAL_FILENAME


def user_dictionary_path() -> Path:
    return dictionaries_dir() / USER_FILENAME


def bundled_dictionary_paths() -> list[Path]:
    relative = Path("assets") / "dictionaries" / BUNDLED_FILENAME
    return [base / relative for base in resource_base_dirs()]


def ensure_user_dictionary() -> Path:
    path = user_dictionary_path()
    if not path.exists():
        path.write_text(
            json.dumps(USER_DICTIONARY_TEMPLATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return path


def _correction_config(config: dict | None) -> dict:
    asr_cfg = config.get("asr", {}) if isinstance(config, dict) else {}
    correction_cfg = asr_cfg.get("correction", {}) if isinstance(asr_cfg, dict) else {}
    return correction_cfg if isinstance(correction_cfg, dict) else {}


def correction_enabled(config: dict | None) -> bool:
    correction_cfg = _correction_config(config)
    return bool(correction_cfg.get("enabled", True))


def correction_manifest_url(config: dict | None) -> str:
    correction_cfg = _correction_config(config)
    manifest_url = str(correction_cfg.get("official_manifest_url", "")).strip()
    if not manifest_url or manifest_url in LEGACY_MANIFEST_URLS:
        return DEFAULT_MANIFEST_URL
    return manifest_url


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _dictionary_layers() -> list[tuple[str, Path, dict[str, Any] | None]]:
    ensure_user_dictionary()
    layers: list[tuple[str, Path, dict[str, Any] | None]] = []
    for path in bundled_dictionary_paths():
        if path.exists():
            layers.append(("bundled", path, _load_json(path)))
            break
    layers.append(("official", official_dictionary_path(), _load_json(official_dictionary_path())))
    layers.append(("user", user_dictionary_path(), _load_json(user_dictionary_path())))
    return layers


@dataclass(frozen=True)
class CorrectionRule:
    pattern: str
    replacement: str
    mode: str
    languages: tuple[str, ...]
    case_sensitive: bool

    def applies_to_language(self, language: str | None) -> bool:
        if not self.languages or "*" in self.languages:
            return True
        normalized = str(language or "").strip()
        return normalized in self.languages

    def apply(self, text: str) -> str:
        if not text or not self.pattern:
            return text

        if self.mode == "exact":
            if self.case_sensitive:
                return self.replacement if text == self.pattern else text
            return self.replacement if text.casefold() == self.pattern.casefold() else text

        flags = 0 if self.case_sensitive else re.IGNORECASE
        escaped = re.escape(self.pattern)
        if self.mode == "word":
            regex = re.compile(rf"\b{escaped}\b", flags)
            return regex.sub(self.replacement, text)

        if self.case_sensitive:
            return text.replace(self.pattern, self.replacement)

        regex = re.compile(escaped, flags)
        return regex.sub(self.replacement, text)


def _iter_rules_from_entry(entry: dict[str, Any]) -> list[CorrectionRule]:
    replacement = str(entry.get("replacement") or entry.get("replace") or "").strip()
    if not replacement:
        return []

    patterns_raw = entry.get("patterns")
    if isinstance(patterns_raw, list):
        patterns = [str(value).strip() for value in patterns_raw if str(value).strip()]
    else:
        single = str(entry.get("pattern") or entry.get("match") or "").strip()
        patterns = [single] if single else []

    if not patterns:
        return []

    languages_raw = entry.get("languages")
    if isinstance(languages_raw, list):
        languages = tuple(str(value).strip() for value in languages_raw if str(value).strip())
    else:
        languages = ()

    mode = str(entry.get("mode") or "substring").strip().lower()
    if mode not in {"substring", "exact", "word"}:
        mode = "substring"
    case_sensitive = bool(entry.get("case_sensitive", False))

    return [
        CorrectionRule(
            pattern=pattern,
            replacement=replacement,
            mode=mode,
            languages=languages,
            case_sensitive=case_sensitive,
        )
        for pattern in patterns
    ]


def _load_rules() -> tuple[list[CorrectionRule], list[dict[str, Any]]]:
    merged: dict[tuple[str, str, tuple[str, ...], bool], CorrectionRule] = {}
    layer_info: list[dict[str, Any]] = []

    for name, path, payload in _dictionary_layers():
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        count = 0
        if isinstance(entries, list):
            for raw_entry in entries:
                if not isinstance(raw_entry, dict):
                    continue
                for rule in _iter_rules_from_entry(raw_entry):
                    key = (rule.mode, rule.pattern, rule.languages, rule.case_sensitive)
                    merged[key] = rule
                    count += 1
        layer_info.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "version": str((payload or {}).get("version", "")).strip(),
                "entry_count": count,
            }
        )

    rules = sorted(
        merged.values(),
        key=lambda rule: (
            0 if rule.mode == "exact" else 1 if rule.mode == "word" else 2,
            -len(rule.pattern),
        ),
    )
    return rules, layer_info


def dictionary_status() -> dict[str, Any]:
    rules, layers = _load_rules()
    return {
        "rule_count": len(rules),
        "layers": layers,
        "user_path": str(user_dictionary_path()),
        "official_path": str(official_dictionary_path()),
    }


class LayeredASRCorrector:
    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._signature: tuple[tuple[str, bool, int], ...] | None = None
        self._rules: list[CorrectionRule] = []

    def _current_signature(self) -> tuple[tuple[str, bool, int], ...]:
        ensure_user_dictionary()
        paths = []
        for bundled in bundled_dictionary_paths():
            if bundled.exists():
                paths.append(bundled)
                break
        paths.extend((official_dictionary_path(), user_dictionary_path()))

        signature = []
        for path in paths:
            exists = path.exists()
            mtime = path.stat().st_mtime_ns if exists else 0
            signature.append((str(path), exists, mtime))
        return tuple(signature)

    def _reload_if_needed(self) -> None:
        signature = self._current_signature()
        if signature == self._signature:
            return
        self._rules, _ = _load_rules()
        self._signature = signature

    def apply(self, text: str, language: str | None = None) -> str:
        if not correction_enabled(self._config):
            return text
        self._reload_if_needed()
        corrected = text
        for rule in self._rules:
            if rule.applies_to_language(language):
                corrected = rule.apply(corrected)
        return corrected


def _read_remote_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:
        charset = response.headers.get_content_charset("utf-8")
        payload = response.read().decode(charset)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("Dictionary manifest is not a JSON object")
    return data


def _read_remote_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def update_official_dictionary(config: dict | None = None) -> dict[str, Any]:
    manifest_url = correction_manifest_url(config)
    try:
        manifest = _read_remote_json(manifest_url)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download dictionary manifest: {exc}") from exc

    dictionary_url = str(
        manifest.get("dictionary_url") or manifest.get("url") or ""
    ).strip()
    if not dictionary_url:
        raise RuntimeError("Dictionary manifest is missing dictionary_url")

    try:
        payload = _read_remote_bytes(dictionary_url)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download dictionary data: {exc}") from exc

    expected_sha256 = str(manifest.get("sha256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError("Dictionary checksum verification failed")

    try:
        decoded = payload.decode("utf-8")
        parsed = json.loads(decoded)
    except Exception as exc:
        raise RuntimeError(f"Downloaded dictionary is invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict) or not isinstance(parsed.get("entries", []), list):
        raise RuntimeError("Downloaded dictionary is missing a valid entries list")

    target_path = official_dictionary_path()
    existing = target_path.read_bytes() if target_path.exists() else b""
    changed = existing != payload
    if changed:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)

    return {
        "changed": changed,
        "version": str(manifest.get("version") or parsed.get("version") or "").strip(),
        "entry_count": len(parsed.get("entries", [])),
        "path": str(target_path),
        "manifest_url": manifest_url,
        "dictionary_url": dictionary_url,
    }
