# Mio RealTime Translator v1.3.9.3

## Changes

- Made `无限高质免费翻译(强推)` the default translation service for new installations and automatic provider selection. It requires no API key; manually selected cloud or local services remain unchanged.
- Added Grok-compatible `grok-4.6` as the newest default model while preserving custom relay Base URLs, model IDs, headers, and streaming settings.
- Fixed Qwen TTS result URLs being rejected when a proxy/TUN resolver returns DashScope's synthetic Fake-IP addresses. HTTPS, port, hostname, DNS pinning, and SSRF protections remain enforced.
- Fixed the SenseVoice model setup download progress bar. The pinned bundle now reports its verified total size, includes already downloaded files, rolls back failed partial attempts, and renders percentages correctly.
- Renamed the Microsoft Edge Web translation display label to `无限高质免费翻译(强推)` (localized equivalents are included); the backend identifier remains `microsoft_edge_web`.

## Upgrade

Users on v1.3.9.2 or earlier can install this release directly. Existing manual provider selections and downloaded SenseVoice files are preserved.
