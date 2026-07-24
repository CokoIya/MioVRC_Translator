from __future__ import annotations

# ── 每次发版前改这两行 ──────────────────────────────────────────────────────
APP_VERSION = "1.3.8.9"
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
