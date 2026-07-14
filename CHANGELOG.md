# Changelog

All notable production changes to Mio RealTime Translator are documented here.

## [1.3.8.3] - 2026-07-14

### Realtime ASR and capture resilience

- Reworked Qwen3-ASR around a persistent cancellable async transport with bounded connection pools, warm connection reuse, hard request deadlines, and transport-generation isolation after timeouts or network failures.
- Added explicit provider cancellation and request-context diagnostics so stopped sessions cannot leave stale cloud recognition requests blocking newer speech.
- Added bounded ASR queue age handling that retires expired sentences without disturbing ordered completion delivery for later sentences.
- Hardened microphone capture with deterministic worker ownership, native-stream shutdown, bounded frame overflow handling, queue diagnostics, worker-failure recovery, and digital-silence detection.
- Prevented temporary realtime Qwen failures from triggering a slow local-model fallback that would amplify queue latency.

### Official service migration

- Migrated the official website, update manifests, dictionary assets, catalog and sponsor mirrors, repair links, request identity, and trusted-host allowlists to `https://miovrc.com`.

## [1.3.8.2] - 2026-07-13

### Credentials and setup

- Added unified, localized credential validation for active translation, ASR, and TTS providers before starting realtime, manual-translation, or speech-output operations.
- Added actionable missing-credential prompts that open the correct settings page and focus the relevant secret field without exposing credential values.
- Improved quick setup and tabbed API settings so each provider retains its own key while switching providers or models.
- Prevented official model-retirement migrations from rewriting model identifiers configured for generic compatible relay providers.

### Realtime latency and translation quality

- Added a bounded realtime translation timeout with retries disabled on latency-sensitive workers so stalled provider calls do not hold the ordered pipeline indefinitely.
- Reused warm bounded HTTP connections for compatible translation providers and added per-stage, provider, sequence, prompt-size, and end-to-end latency diagnostics.
- Removed text-length-dependent OSC delays so completed messages preserve FIFO order using only the configured VRChat-safe send interval.
- Bounded concurrent context snapshots, improved contextual detection for short questions and follow-up phrases, and refined Japanese conversational translation behavior.

## [1.3.8.1] - 2026-07-12

### Localization and updates

- Added a unified five-language localization layer for the application, Qt dialogs, settings pages, runtime messages, and installer catalogs with deterministic fallback and placeholder validation.
- Improved automatic UI-language detection and runtime language switching, and made the installer follow the Windows display language while preserving an existing installation's language.
- Reworked the in-app update flow with localized status and errors, verified-installer retention, deferred installation, clean cancellation, and post-restart result reporting.

### Runtime reliability and performance

- Strengthened ASR, audio capture, translation, OSC, and scheduler lifecycle management with generation-safe completion, cancellation pruning, reusable providers, and deterministic resource cleanup.
- Improved TTS queue recovery, worker replacement, request backoff, provider error handling, playback cleanup, and engine teardown under rapid start/stop cycles.
- Hardened XTTS CPU/CUDA lifecycle management with Windows commit-headroom checks, safer model release, automatic CPU fallback after CUDA failures, conditioning cleanup, and lower-latency prewarming.
- Reduced retained Qt objects, HTTP sessions, native audio resources, model references, and exception tracebacks during long-running sessions.

## [1.3.8] - 2026-07-12

### Audio capture and OSC

- Synchronized application mute with physical microphone capture and browser-owned Web Speech capture, including clean pause, cancellation, and resume behavior.
- Improved automatic microphone selection with active Core Audio session detection, canonical endpoint matching, refreshed inventories, and reliable default-device fallback.
- Kept inbound OSC active whenever MuteSelf synchronization or avatar controls require it, serialized receive handling, and suppressed duplicate mute-state events.

### TTS and interface

- Enforced HTTPS for allowlisted Qwen TTS result downloads and rejected unsafe result URLs.
- Adapted playback channel layout and sample rate to the selected output device for more reliable virtual-audio routing.
- Synchronized TTS service-region selectors and hardened settings refreshes against stale Qt widgets.
- Added a choice between the bundled 851 typeface and the Windows system UI font.

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
- Refreshed provider catalogs, including `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and Sonnet 5 for direct and compatible relay providers.
- Removed GPT-5.4 models, Opus-series models, and Sonnet models earlier than 4.6 from selectable catalogs and migrated obsolete saved selections.

### Packaging and diagnostics

- Rebuilt and locked the Python 3.11 release environment with the XTTS, Style-Bert-VITS2, ASR, Japanese text, and CUDA-support runtime dependencies required by packaged builds.
- Added source and frozen-runtime self-tests, dependency integrity checks, release-environment checks, and safer PyInstaller/Inno Setup release automation.
- Improved runtime log discovery, redaction, UI diagnostics, and installer compatibility.

## [1.3.7.8] - 2026-07-06

- Fixed the in-app Install & Restart update flow.
- Reduced startup and idle resource use for online-service and text-only workflows.
- Kept local ASR, XTTS voice cloning, reference-audio import, and multilingual resources available on demand.
