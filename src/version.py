from __future__ import annotations

# ── 每次发版前改这两行 ──────────────────────────────────────────────────────
APP_VERSION = "1.4.1"
UPDATE_CHECK_URL = "https://miovrc.com/installer_manifest.json"
UPDATE_CHECK_URLS: tuple[str, ...] = (
    UPDATE_CHECK_URL,
    "https://github.com/CokoIya/MioVRC_Translator/releases/latest/download/installer_manifest.json",
)
UPDATE_MANIFEST_PUBLIC_KEY_ID = "mio-update-ed25519-v2"
UPDATE_MANIFEST_PUBLIC_KEY = "299d00127d293c1122b3e6f207b719d47df3546307c8757c67f8ca0d5f2d218e"
TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS: tuple[tuple[str, str], ...] = (
    (UPDATE_MANIFEST_PUBLIC_KEY_ID, UPDATE_MANIFEST_PUBLIC_KEY),
)
REQUIRE_UPDATE_MANIFEST_SIGNATURE = True
INSTALLER_SIGNATURE_KEY_ID = "mio-installer-ed25519-v2"
TRUSTED_INSTALLER_PUBLIC_KEYS: tuple[tuple[str, str], ...] = (
    (INSTALLER_SIGNATURE_KEY_ID, UPDATE_MANIFEST_PUBLIC_KEY),
)
# ────────────────────────────────────────────────────────────────────────────

# A test build (build_beta.ps1) stamps its own version into the bundle instead
# of touching the lines above, so a beta never changes what a release reads.
BUILD_VERSION_STAMP = "mio_build_version.txt"


def build_version_override(bundle_dir: str | None = None) -> str:
    """The version stamped into a frozen bundle at build time, or ""."""

    import re
    import sys
    from pathlib import Path

    base = bundle_dir if bundle_dir is not None else getattr(sys, "_MEIPASS", None)
    if not base:
        return ""
    try:
        text = (Path(base) / BUILD_VERSION_STAMP).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text if re.fullmatch(r"\d+(?:\.\d+)*(?:[-_.]?[A-Za-z]+\d*)?", text) else ""


_stamped = build_version_override()
if _stamped:
    APP_VERSION = _stamped
