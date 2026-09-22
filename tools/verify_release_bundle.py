"""Verify what PyInstaller actually put in ``dist/MioTranslator``.

``build_release.ps1`` already proves the frozen app starts and that speech
recognition can load. Neither of those notices a data file that stopped being
collected, or a package that was deleted from the source tree months ago and is
still being shipped because a stale pin kept it installed. This checks the
payload itself.

The forbidden list is read from ``MioTranslator.spec`` rather than repeated
here, so removing an engine keeps only one place to edit.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "dist" / "MioTranslator"

# Files a release is useless without. Each entry is a path inside the bundle.
REQUIRED_PAYLOAD = (
    "MioTranslator.exe",
    "config.example.json",
    "LICENSE",
    "NOTICE",
    "assets",
)

# Collected only when present in the source tree, so absence is not an error
# unless the source file exists.
CONDITIONAL_PAYLOAD = {
    "src/audio/models/silero_vad.jit": "_internal/src/audio/models/silero_vad.jit",
    # The recognizers for scripts the bundled OCR package does not carry.
    "assets/ocr/japan_PP-OCRv4_rec_mobile.onnx": "_internal/assets/ocr/japan_PP-OCRv4_rec_mobile.onnx",
    "assets/ocr/korean_PP-OCRv4_rec_mobile.onnx": "_internal/assets/ocr/korean_PP-OCRv4_rec_mobile.onnx",
}

# The project's own tree, which must never be searched for excluded names:
# our "src/tts" package would otherwise match Coqui's "TTS" on a
# case-insensitive filesystem and force the most valuable check to be muted.
OWN_SOURCE_ROOTS = ("src",)

# Excluded names too generic to match on. Keep this list short: every entry is
# a check that no longer runs.
FORBIDDEN_EXEMPTIONS = {
    # A generic word that appears inside unrelated package layouts.
    "trainer",
}


def _spec_excludes(spec_path: Path) -> list[str]:
    """Read the ``excludes`` list out of the PyInstaller spec."""

    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "excludes" not in targets or not isinstance(node.value, ast.List):
            continue
        return [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    raise RuntimeError(f"no excludes list found in {spec_path}")


def _bundle_entries(bundle: Path) -> list[Path]:
    return [path for path in bundle.rglob("*") if path.is_file()]


def _module_relative_parts(bundle: Path, path: Path) -> tuple[str, ...]:
    """Return a path relative to the bundle's module search root."""

    parts = path.relative_to(bundle).parts
    return parts[1:] if parts and parts[0] == "_internal" else parts


def _matches_excluded_module(parts: tuple[str, ...], name: str) -> bool:
    """True when these path components are that excluded module's payload.

    A spec ``excludes`` entry names an importable module, so it can only match
    at the top of the module search root. Matching a bare component anywhere
    flagged modelscope's own ``models/audio/tts`` and the app's ``assets/tts``
    as if they were Coqui.
    """

    expected = [component.lower() for component in name.split(".")]
    if len(parts) < len(expected):
        return False
    lowered = [component.lower() for component in parts]
    # Every component but the last must match a directory exactly.
    if lowered[: len(expected) - 1] != expected[: len(expected) - 1]:
        return False
    final = lowered[len(expected) - 1]
    target = expected[-1]
    if final == target:
        return True
    # The leaf may be a module file rather than a package directory, and
    # extension modules carry an ABI tag: "QtWebEngineCore.cp311-win_amd64.pyd".
    stem = final.split(".", 1)[0]
    return stem == target


def _forbidden_hits(bundle: Path, excluded: list[str]) -> list[str]:
    """Find shipped files belonging to a package the spec excludes."""

    watched = [name for name in excluded if name not in FORBIDDEN_EXEMPTIONS]
    hits: dict[str, int] = {}
    for path in _bundle_entries(bundle):
        parts = _module_relative_parts(bundle, path)
        # The application's own modules are never third-party payload.
        if parts and parts[0] in OWN_SOURCE_ROOTS:
            continue
        for name in watched:
            if _matches_excluded_module(parts, name):
                hits[name] = hits.get(name, 0) + 1
    return [
        f"{name}: {count} file(s) shipped although the spec excludes it"
        for name, count in sorted(hits.items())
    ]


def _missing_payload(bundle: Path) -> list[str]:
    missing = []
    for relative in REQUIRED_PAYLOAD:
        for candidate in (bundle / relative, bundle / "_internal" / relative):
            if candidate.exists():
                break
        else:
            missing.append(f"{relative} is missing from the bundle")
    for source_relative, bundle_relative in CONDITIONAL_PAYLOAD.items():
        if not (ROOT / source_relative).is_file():
            continue
        if not (bundle / bundle_relative).exists():
            missing.append(
                f"{bundle_relative} is missing although {source_relative} exists"
            )
    return missing


def _spec_source_hiddenimports(spec_path: Path) -> list[str]:
    """Return the project's own modules the spec pins as hidden imports."""

    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("src."):
                modules.add(node.value)
    return sorted(modules)


def _unimportable_modules(bundle: Path, modules: list[str]) -> list[str]:
    """Ask the frozen app to import each module and report what it cannot."""

    if not modules:
        return []
    executable = bundle / "MioTranslator.exe"
    if not executable.is_file():
        return [f"{executable} is missing, cannot run the import check"]
    environment = dict(os.environ)
    environment["MIO_TRANSLATOR_IMPORT_CHECK"] = ",".join(modules)
    try:
        completed = subprocess.run(
            [str(executable), "--mio-import-check"],
            capture_output=True,
            text=True,
            timeout=300,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ["the frozen import check did not finish within 300s"]
    if completed.returncode == 0:
        return []
    details = (completed.stdout or "") + (completed.stderr or "")
    return [line for line in details.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--spec", type=Path, default=ROOT / "MioTranslator.spec")
    parser.add_argument(
        "--skip-import-check",
        action="store_true",
        help="Do not launch the frozen executable (useful on a build agent).",
    )
    args = parser.parse_args()

    bundle: Path = args.bundle
    if not bundle.is_dir():
        print(f"Bundle directory not found: {bundle}", file=sys.stderr)
        return 1

    excluded = _spec_excludes(args.spec)
    problems = _missing_payload(bundle) + _forbidden_hits(bundle, excluded)
    if not args.skip_import_check:
        problems += _unimportable_modules(bundle, _spec_source_hiddenimports(args.spec))

    files = _bundle_entries(bundle)
    total_mb = sum(path.stat().st_size for path in files) / (1024 * 1024)

    if problems:
        print("Release bundle verification failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"Release bundle OK: {len(files)} files, {total_mb:.0f} MB, "
        f"{len(excluded)} excluded packages absent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
