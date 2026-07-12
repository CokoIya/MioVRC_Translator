# Release signing

Mio RealTime Translator does not require a commercial Windows code-signing
certificate for in-app updates. Update authenticity is rooted in Ed25519 public
keys compiled into the application.

## Trust model

A release uses two domain-separated Ed25519 signatures:

1. The update manifest signature authenticates the complete manifest, including
   version, URL, release notes, installer size, and SHA-256 digest.
2. The installer metadata signature independently authenticates the exact
   installer size and SHA-256 digest.

The downloaded file is hashed through an open file descriptor, checked for
links and replacement races, and hashed again before launch. The Windows helper
locks and re-hashes the same file immediately before executing it.

Trusted public keys and active key IDs are configured in `src/version.py`:

- `UPDATE_MANIFEST_PUBLIC_KEY`
- `UPDATE_MANIFEST_PUBLIC_KEY_ID`
- `TRUSTED_UPDATE_MANIFEST_PUBLIC_KEYS`
- `INSTALLER_SIGNATURE_KEY_ID`
- `TRUSTED_INSTALLER_PUBLIC_KEYS`

Authenticode remains optional. It can improve Windows reputation and publisher
UI, but the updater does not treat it as the cryptographic trust root.

## Private release seed

The corresponding 32-byte Ed25519 seed is a release secret, not a certificate.
Never commit it. The repository ignores `.secrets/`; CI should use an encrypted
secret store.

Set the seed for a release process:

```powershell
$env:MIO_RELEASE_SIGNING_SEED = '<64 hexadecimal characters>'
.\build_release.ps1
```

`MIO_MANIFEST_SEED` is accepted as a compatibility alias. The release tool
fails closed unless the seed derives every configured active public key. It
then signs and verifies both staged manifests before atomically replacing the
published JSON files.

To generate a new key pair in a trusted offline environment:

```powershell
python -c "from src.updater.manifest_signature import generate_seed_hex, public_key_from_seed; s=generate_seed_hex(); print('PRIVATE SEED:', s); print('PUBLIC KEY :', public_key_from_seed(s))"
```

Store the private seed outside the repository and commit only the public key.
For rotation, first add the new key alongside the old key in both trusted-key
allowlists while the old key remains active. Publish that overlap release,
then activate the new key ID in a later release. This lets already-updated
clients trust either signature without weakening verification.

## Optional Authenticode

If a certificate later becomes available, set both
`MIO_TRANSLATOR_SIGN_PFX` and `MIO_TRANSLATOR_SIGN_PASS`. The build validates
that signature but still publishes mandatory Ed25519 signatures. Supplying only
one Authenticode variable is treated as a release configuration error.
