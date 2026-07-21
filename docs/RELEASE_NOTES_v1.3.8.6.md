# Mio RealTime Translator v1.3.8.6

## Important signing-key migration

This release establishes the project-specific Ed25519 v2 update-signing
identity and retires v1. The v1 private seed was unavailable for a signed
overlap release, so v1.3.8.5 and earlier cannot authenticate this release
through the in-app updater. Download the installer manually over HTTPS from
this GitHub Release and compare the v2 public-key fingerprint with the copy
published at <https://miovrc.com>. GitHub/website TLS is the bootstrap trust;
the new signature cannot authenticate its own previously unknown key. After
that comparison, verify the published SHA-256 checksum and detached v2
signature. After installing v1.3.8.6, later v2-signed updates can be
authenticated normally.

- 简体中文：本版本启用项目专用的 Ed25519 v2 更新签名身份并停用 v1。由于无法使用旧 v1 私钥完成重叠签名，v1.3.8.5 及更早版本必须通过 HTTPS 从本 GitHub Release 手动安装，并先将 v2 公钥指纹与 <https://miovrc.com> 公布的副本进行比对；GitHub/官网 TLS 是本次手动恢复的初始信任根。随后再核对 SHA-256 校验和与 v2 分离签名。
- 日本語：本リリースではプロジェクト専用の Ed25519 v2 更新署名 ID を有効化し、v1 を廃止します。旧 v1 秘密鍵で移行用の重複署名を作成できないため、v1.3.8.5 以前からは HTTPS 経由でこの GitHub Release を使って手動インストールし、最初に v2 公開鍵フィンガープリントを <https://miovrc.com> の値と照合してください。この手動復旧では GitHub/公式サイトの TLS が初期信頼点です。その後に SHA-256 チェックサムと v2 分離署名を確認してください。
- Русский: В выпуске введён проектный ключ подписи обновлений Ed25519 v2, а v1 выведен из доверия. Поскольку переходный релиз нельзя подписать старым закрытым ключом v1, пользователям v1.3.8.5 и более ранних версий необходимо установить выпуск вручную по HTTPS со страницы GitHub Release и сначала сравнить отпечаток открытого ключа v2 со значением на <https://miovrc.com>. Начальной точкой доверия для восстановления служит TLS GitHub/официального сайта; затем следует проверить SHA-256 и отдельную подпись v2.
- 한국어: 이 릴리스는 프로젝트 전용 Ed25519 v2 업데이트 서명 ID를 사용하고 v1 신뢰를 폐기합니다. 이전 v1 개인 키로 전환용 중복 서명을 만들 수 없으므로 v1.3.8.5 이하 버전에서는 HTTPS GitHub Release에서 수동 설치하고 먼저 v2 공개 키 지문을 <https://miovrc.com>에 게시된 값과 비교해야 합니다. 이번 수동 복구의 초기 신뢰점은 GitHub/공식 사이트 TLS이며, 그 다음 SHA-256 체크섬과 v2 분리 서명을 확인하십시오.

## Highlights

- Hardened GPT/OpenAI-compatible, Claude/Anthropic-compatible, and Grok/xAI-compatible official and relay paths while preserving custom Base URLs, headers, proxies, API keys, and model IDs.
- Added structured latency diagnostics that separate local queue wait, connection-pool wait, measurable DNS/TCP/TLS/header stages, provider processing, first token/audio, full response, post-processing, reorder, UI, OSC/TTS, and total wall-clock time.
- Added bounded pool/connect/read/wall deadlines, cancellation propagation, poisoned-client recovery, stale-work retirement, strict ordering, and deterministic shutdown behavior across realtime, manual, reverse, ASR-rewrite, and typed-text-rewrite paths.
- Reworked Qwen API TTS admission, synthesis, audio handling, playback, virtual-device delivery, resource ownership, rapid consecutive playback, queue saturation, and shutdown recovery so a stalled request cannot permanently block later work.
- Localized all new provider settings, credential validation, warnings, timeouts, failures, and recovery notifications across Chinese, English, Japanese, Russian, and Korean.

The detailed player-log correlation and provider/runtime audit is available in
[`RUNTIME_PROVIDER_AUDIT_2026-07-20.md`](RUNTIME_PROVIDER_AUDIT_2026-07-20.md).

## Release assets

- `MioTranslator-Setup-v1.3.8.6.exe` — production installer and in-app update payload
- `installer_manifest.json` — signed updater manifest expected by the GitHub fallback URL
- `mio_update.json` — signed repository release metadata
- `release_signing_keys.json` — public signing-key catalog, lifecycle state, and fingerprints
- `MioTranslator-v1.3.8.6-SHA256SUMS.txt` — deterministic SHA-256 list for the installer, both manifests, and public-key catalog
- `MioTranslator-v1.3.8.6-SHA256SUMS.txt.sig.json` — detached, domain-separated Ed25519 v2 signature metadata

The active non-secret verification identity is:

- Manifest key ID: `mio-update-ed25519-v2`
- Installer key ID: `mio-installer-ed25519-v2`
- Public key: `299d00127d293c1122b3e6f207b719d47df3546307c8757c67f8ca0d5f2d218e`
- Public-key SHA-256: `2a4ec078ce01b14b935a45cb04ac12eba7445a619075dbb0f50c25618e337b83`

## Verification

After downloading all release assets into one directory, compare the installer
digest with the matching line in `MioTranslator-v1.3.8.6-SHA256SUMS.txt`:

```powershell
Get-FileHash .\MioTranslator-Setup-v1.3.8.6.exe -Algorithm SHA256
```

The repository verification tool validates the installer, both signed
manifests, the deterministic checksum file, the detached signature metadata,
and the compiled active public key without requiring the private seed:

```powershell
python tools\release\generate_release_checksums.py `
  .\MioTranslator-Setup-v1.3.8.6.exe `
  .\mio_update.json `
  .\installer_manifest.json `
  .\release_signing_keys.json `
  --verify-only
```

Authenticode is optional for this project and is not the in-app updater trust
root. Ed25519 verification and the signed SHA-256/size metadata remain
mandatory.
