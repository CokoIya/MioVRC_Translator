# Changelog

All notable production changes to Mio RealTime Translator are documented here.

## [1.3.7.9] - 2026-07-12

### Security and updates

- Replaced private-certificate requirements with mandatory Ed25519 public-key verification for update manifests and installer metadata.
- Added race-resistant installer hashing, signature verification, bounded downloads, atomic manifest publication, and safer update handoff/repair behavior.
- Hardened HTTP clients against unsafe URLs, redirects, SSRF, unbounded responses, credential leakage, and insecure executable resolution.
- Improved configuration, path, logging, model-download, and packaged-runtime safeguards.

### Real-time pipeline and performance

- Introduced a bounded staged ASR → rewrite → translation → delivery pipeline with dedicated queues, worker limits, backpressure, cancellation, session isolation, and ordered OSC delivery.
- Added concurrent TTS synthesis with ordered playback so later speech can enter ASR while earlier sentences continue through translation and speech output.
- Isolated reverse translation by source and generation so it cannot block, cancel, or reorder the microphone pipeline.
- Reduced ASR and translation overhead with shared audio encoding, connection/session reuse, provider-aware concurrency, context management, and lower-contention scheduling.
- Improved TTS throughput, caching, playback-device recovery, and XTTS CPU/CUDA execution, precision selection, diagnostics, fallback, and packaged-runtime compatibility.

### Audio and recognition

- Added canonical Windows audio inventory across WASAPI, MME, DirectSound, and WDM-KS, including default-device resolution, inactive endpoint filtering, hot-plug refresh, backend fallbacks, and diagnostics.
- Improved microphone/desktop capture resilience, VAD behavior, ASR model lifecycle, model downloads, fallback recognition, and partial-result handling.

### Translation and models

- Added optional ASR rewrite presets for anime, cat-speech, Classical Chinese, and humorous academic styles before translation.
- Disabled model thinking/reasoning modes where supported and added context-aware translation history with source isolation.
- Refreshed provider catalogs, including `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and Claude Sonnet 5 for direct and compatible relay providers.
- Removed GPT-5.4 models, Claude Opus models, and Claude Sonnet models earlier than 4.6 from selectable catalogs and migrated obsolete saved selections.

### Packaging and diagnostics

- Rebuilt and locked the Python 3.11 release environment with the XTTS, Style-Bert-VITS2, ASR, Japanese text, and CUDA-support runtime dependencies required by packaged builds.
- Added source and frozen-runtime self-tests, dependency integrity checks, release-environment checks, and safer PyInstaller/Inno Setup release automation.
- Improved runtime log discovery, redaction, UI diagnostics, and installer compatibility.

## [1.3.7.8] - 2026-07-06

- Fixed the in-app Install & Restart update flow.
- Reduced startup and idle resource use for online-service and text-only workflows.
- Kept local ASR, XTTS voice cloning, reference-audio import, and multilingual resources available on demand.
