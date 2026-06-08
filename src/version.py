from __future__ import annotations

# ── 每次发版前改这两行 ──────────────────────────────────────────────────────
APP_VERSION = "1.3.7.3"
UPDATE_CHECK_URL = "https://78hejiu.top/installer_manifest.json"
UPDATE_CHECK_URLS: tuple[str, ...] = (
    UPDATE_CHECK_URL,
    "https://github.com/CokoIya/MioVRC_Translator/releases/latest/download/installer_manifest.json",
)
UPDATE_MANIFEST_PUBLIC_KEY_ID = "mio-update-ed25519-v1"
UPDATE_MANIFEST_PUBLIC_KEY = "df75e9094c3d3d4d60dd3c7413fd256be8379c32e35c336b1972d3c3e5137f0f"
REQUIRE_UPDATE_MANIFEST_SIGNATURE = False
TRUSTED_INSTALLER_SIGNER_THUMBPRINTS: tuple[str, ...] = ()
TRUSTED_INSTALLER_SIGNER_SUBJECTS: tuple[str, ...] = ()
# ────────────────────────────────────────────────────────────────────────────
