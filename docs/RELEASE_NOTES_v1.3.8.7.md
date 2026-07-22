# Mio RealTime Translator v1.3.8.7

## Upgrade path

Users on v1.3.8.6 can install this release through Mio's authenticated in-app
updater. Users on v1.3.8.5 or earlier must manually install this version once
because those runtimes predate the active Ed25519 v2 signing identity. Future
v2-signed updates can then be authenticated normally.

## Highlights

- Strengthened transform-only system prompts and output validation for ASR rewrite, typed-text rewrite, forward translation, and reverse translation. Conversation history is limited to resolving pronouns, omitted subjects, terminology, and ambiguity.
- Rejects and retries conversational answers, continuations, opinions, advice, explanations, labels, quotation wrappers, and unrelated additions across GPT/OpenAI-compatible, Claude/Anthropic-compatible, Grok/xAI-compatible, DeepSeek, and Qwen paths.
- Added `Original Text Only (Read Translation)`: the original text is displayed or sent to the chatbox while TTS reads the translated text aloud.
- Improved reverse-translation responsiveness in noisy, crowded scenes by prioritizing fresh speech, bounding queued work, and retiring stale reverse-recognition and translation tasks.
- Added regression coverage for transformation safety, output dispatch, realtime and manual pipelines, reverse scheduling, lifecycle behavior, TTS, credentials, configuration validation, and localization.

## Release assets

- `MioTranslator-Setup-v1.3.8.7.exe` — production installer and in-app update payload
- `installer_manifest.json` — signed updater manifest expected by the GitHub fallback URL
- `mio_update.json` — signed repository release metadata
- `release_signing_keys.json` — public signing-key catalog, lifecycle state, and fingerprints
- `MioTranslator-v1.3.8.7-SHA256SUMS.txt` — deterministic SHA-256 list for the installer, both manifests, and public-key catalog
- `MioTranslator-v1.3.8.7-SHA256SUMS.txt.sig.json` — detached, domain-separated Ed25519 v2 signature metadata

The active non-secret verification identity is unchanged from v1.3.8.6:

- Manifest key ID: `mio-update-ed25519-v2`
- Installer key ID: `mio-installer-ed25519-v2`
- Public key: `299d00127d293c1122b3e6f207b719d47df3546307c8757c67f8ca0d5f2d218e`
- Public-key SHA-256: `2a4ec078ce01b14b935a45cb04ac12eba7445a619075dbb0f50c25618e337b83`

## Verification

After downloading all release assets into one directory, compare the installer
digest with the matching line in `MioTranslator-v1.3.8.7-SHA256SUMS.txt`:

```powershell
Get-FileHash .\MioTranslator-Setup-v1.3.8.7.exe -Algorithm SHA256
```

The repository verification tool validates the installer, both signed
manifests, the deterministic checksum file, the detached signature metadata,
and the compiled active public key without requiring the private seed:

```powershell
python tools\release\generate_release_checksums.py `
  .\MioTranslator-Setup-v1.3.8.7.exe `
  .\mio_update.json `
  .\installer_manifest.json `
  .\release_signing_keys.json `
  --verify-only
```

Authenticode is optional for this project and is not the in-app updater trust
root. Ed25519 verification and the signed SHA-256/size metadata remain
mandatory.
