# Production release process

This document describes the supported Windows production-release workflow for
Mio RealTime Translator. It intentionally produces one full-feature installer;
there are no separate full/lite release pipelines.

## Required tools

- CPython 3.11 x64 at `C:\Program Files\Python311\python.exe`
- Inno Setup 6.5.0 or newer
- Git with sufficient history and objects for a complete diff
- GitHub CLI (`gh`) or an equivalent authenticated GitHub release client
- Optional: a trusted Authenticode PFX and password

The Python build is reproducible from `requirements.lock.txt`. Do not package
from the general development environment.

## Secrets

The mandatory Ed25519 seed must remain outside the repository in an encrypted
secret store or a file with protected, non-inherited ACLs. Never pass a literal
seed, token, password, or private key on a command line or place it in a log.
Keep an encrypted, access-controlled backup in a separate secret manager;
directory deletion by an account that controls a higher-level volume path is
an availability risk even when the seed file itself cannot be read.

Use one of these inputs:

```powershell
$env:MIO_RELEASE_SIGNING_SEED = '<loaded in memory by a secret manager>'
```

```powershell
$env:MIO_RELEASE_SIGNING_SEED_FILE = 'X:\secure\mio-release-ed25519-v2.seed.hex'
```

`build_release.ps1` removes the seed and seed-file setting before invoking
PyInstaller or Inno Setup, restores only the in-memory seed for final signing,
and clears it afterward. Optional Authenticode uses
`MIO_TRANSLATOR_SIGN_PFX` plus `MIO_TRANSLATOR_SIGN_PASS`.

## Version and human-authored release data

Before building, update and validate:

- `src/version.py`
- `MioTranslator-installer.iss`
- `windows_version_info.txt`
- `CHANGELOG.md`
- `mio_update.json` release notes and all `notes_i18n` entries
- `docs/installer_manifest.json` release notes and all `notes_i18n` entries
- current website/download references and release notes

Do not hand-edit installer size, SHA-256, publication time, signature fields,
or final website size/hash values. Those are derived from the final installer.

## Clean environment and pre-build gates

Remove only verified repo-local build outputs and caches. Refuse links, reparse
points, computed paths outside the repository, or unrecognized directories.

Rebuild the locked environment:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\rebuild_release_environment.ps1
```

Before packaging, run compilation, the complete test suite available before
manifest generation, localization checks, JSON parsing, security tests, Ruff,
PowerShell syntax validation, dependency checks, and `git diff --check`. The
repository-manifest signature assertion is expected to remain pending only
between changing `APP_VERSION` and signing the final installer-derived
manifests.

The dependency audit has one reviewed compatibility exception:
`PYSEC-2026-2132` affects `click.edit()`, but the application never calls that
API and the packaged gTTS runtime does not import its Click-based CLI. The only
current gTTS release requires `click<8.2`, so audit Click with an explicit
`--ignore-vuln PYSEC-2026-2132` until gTTS publishes a compatible release.

`PYSEC-2026-3447` affects Unicode normalization while Setuptools builds source
distributions on macOS. This Windows PyInstaller/Inno release does not build or
ship an sdist, while `pyopenjtalk-dict` still requires the `pkg_resources`
module removed in Setuptools 83. Keep the compatible Setuptools 81 toolchain
pin and audit it with an explicit `--ignore-vuln PYSEC-2026-3447` until that
runtime dependency migrates away from `pkg_resources`.

## Build, sign, and verify

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\build_release.ps1 `
  -ReleaseSigningSeedFile 'X:\secure\mio-release-ed25519-v2.seed.hex'
```

The build performs source and frozen runtime self-tests, creates the Inno
installer, signs and verifies both updater manifests, and creates a
domain-separated detached signature over deterministic checksums.

Expected `v1.3.8.9` assets:

- `dist\MioTranslator-Setup-v1.3.8.9.exe`
- `mio_update.json`
- `docs\installer_manifest.json`
- `docs\release_signing_keys.json`
- `dist\MioTranslator-v1.3.8.9-SHA256SUMS.txt`
- `dist\MioTranslator-v1.3.8.9-SHA256SUMS.txt.sig.json`

Run seedless verification:

```powershell
.\.venv-release311\Scripts\python.exe `
  tools\release\generate_release_checksums.py `
  .\dist\MioTranslator-Setup-v1.3.8.9.exe `
  .\mio_update.json `
  .\docs\installer_manifest.json `
  .\docs\release_signing_keys.json `
  --verify-only
```

Then update website size copy from the verified installer, rerun all
tests including the repository-manifest assertion, and repeat every static
gate. Smoke-install into an isolated directory, run the installed executable's
`--mio-selftest`, and uninstall it before publication.

## Diff, commit, tag, and publication

Review the complete diff and staged files. Exclude player logs, configuration,
API keys, tokens, seed files, SSH private keys, PFX/P12 files, passwords,
virtual environments, caches, temporary files, and unrelated generated data.

Use GitHub CLI/device login or a repository-scoped environment token for
release publication. Configure Git's HTTPS credential helper through GitHub
CLI and do not embed credentials in the remote URL.

```powershell
gh auth setup-git
git remote set-url --push origin https://github.com/CokoIya/MioVRC_Translator.git
git push origin main
git push origin v1.3.8.9
```

The GitHub Release must be tagged `v1.3.8.9` and must upload the installer,
both updater JSON manifests, the public-key catalog, the checksum file, and
detached signature metadata.
`docs/installer_manifest.json` must be uploaded with the exact asset name
`installer_manifest.json`, because the in-app updater fallback URL depends on
that name. Use `docs/RELEASE_NOTES_v1.3.8.9.md` as the release body.

After upload, download the public assets again, rerun seedless verification on
the downloaded copies, and confirm the website and updater URLs resolve to the
same version, size, SHA-256, key IDs, and signatures.

Do not use `tools/release/upload_to_r2.py`; it is an obsolete historical helper
with old hard-coded releases. Do not publish the unversioned ZIP created by
`tools/release/rezip.py` as an updater payload.
