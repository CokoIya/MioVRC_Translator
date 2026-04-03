# Mio VRC Downloader

This folder contains the standalone BOOTH-friendly downloader bootstrapper.

It is intentionally separate from the main installer pipeline:

- `tools/booth_downloader/mio_vrc_download.py`
  Small GUI downloader that fetches the latest official installer, verifies it, keeps a local cached copy, installs it to the selected folder, and launches `MioTranslator.exe`.
- `tools/booth_downloader/update_installer_manifest.py`
  Helper script that computes the installer size and SHA256, then writes the public manifest consumed by the downloader.

## Build the downloader

```powershell
powershell -ExecutionPolicy Bypass -File .\build_booth_downloader.ps1
```

The build produces two outputs:

```text
dist\Mio_vrc_download.exe
dist\Mio_vrc_download_bundle.zip
```

`dist\Mio_vrc_download.exe`
Single-file convenience build.

`dist\Mio_vrc_download_bundle.zip`
Safer onedir bundle for BOOTH. This is the preferred package when you want to minimize antivirus false positives as much as possible.

## Optional code signing

If you have a code-signing certificate, set these environment variables before building:

```powershell
$env:MIO_VRC_SIGN_PFX = "C:\path\to\certificate.pfx"
$env:MIO_VRC_SIGN_PASS = "your-password"
powershell -ExecutionPolicy Bypass -File .\build_booth_downloader.ps1
```

The build script will try to locate `signtool.exe`, sign both downloader executables, and timestamp them.

## Update the public manifest

```powershell
python .\tools\booth_downloader\update_installer_manifest.py `
  --installer .\dist\MioTranslator-Setup-v1.2.3.exe `
  --url "https://pub-bec4964d84f7494190362b2ea44fb6e9.r2.dev/MioTranslator-Setup-v1.2.3.exe" `
  --version v1.2.3 `
  --published-at 2026-03-29T01:50:30+08:00
```

This writes:

```text
docs\installer_manifest.json
```

When the website is deployed, the downloader reads it from:

```text
https://78hejiu.top/installer_manifest.json
```

## Practical anti-false-positive notes

- Keep the download hosted on your official domain and keep the publisher name stable.
- Sign the downloader before uploading whenever possible.
- Prefer `dist\Mio_vrc_download_bundle.zip` for BOOTH if you want the lowest false-positive risk. This is an inference based on avoiding onefile self-extraction at launch.
- Reuse the same downloader binary between app releases when only the manifest changes.
- If Microsoft Defender still flags a release, submit the signed file for analysis after publishing.
