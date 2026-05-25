from __future__ import annotations

# ── 每次发版前改这两行 ──────────────────────────────────────────────────────
APP_VERSION = "1.3.5.2"
UPDATE_CHECK_URL = "https://78hejiu.top/installer_manifest.json"
UPDATE_CHECK_URLS: tuple[str, ...] = (
    UPDATE_CHECK_URL,
    "https://github.com/CokoIya/MioVRC_Translator/releases/latest/download/installer_manifest.json",
)
UPDATE_MANIFEST_PUBLIC_KEY_ID = "mio-update-ed25519-v1"
UPDATE_MANIFEST_PUBLIC_KEY = "b1727e19a47262441c67be84fe030bcd7af3cd4c0859cd090f129ea7469a1ac8"
REQUIRE_UPDATE_MANIFEST_SIGNATURE = bool(UPDATE_MANIFEST_PUBLIC_KEY)
TRUSTED_INSTALLER_SIGNER_THUMBPRINTS: tuple[str, ...] = ()
TRUSTED_INSTALLER_SIGNER_SUBJECTS: tuple[str, ...] = ()
# ────────────────────────────────────────────────────────────────────────────
