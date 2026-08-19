# Changelog

All notable production changes to Mio RealTime Translator are documented here.

## [1.3.9.2] - 2026-08-19

### Translation correctness and compatibility

- Fixed credential validation so local OpenAI-compatible AI services are not incorrectly required to provide a cloud API key.
- Enforced the selected target language across AI and web translation providers, including Spanish, French, Russian, Korean, and other non-Chinese/Japanese/English targets.
- Added one strict conversion retry when a provider returns text in an obviously incorrect language while preserving the translation-only output contract.
- Accepted single-field JSON translation responses from compatible local AI services.

### Connectivity and diagnostics

- Preferred IPv4 loopback for localhost-compatible AI endpoints to avoid slow Windows IPv6 fallback behavior.
- Clarified provider prewarm logging so transport reachability is not reported as successful inference.
- Reduced sensitive device, process, and audio inventory details in diagnostic logs.

## [1.3.9.1] - 2026-08-07

### Translation responsiveness

- Added no-key Microsoft Edge Web translation and improved Google Web compatibility while clearly identifying both as public web services without an availability SLA.
- Prepared manual and realtime translation clients in the background so startup and typed input do not wait for provider initialization.
- Replaced synthetic Edge translation POST warm-ups with bounded transport-only probes after real-world logs showed concurrent probes delaying requests for more than 40 seconds.

### Realtime and reverse translation

- Added provider-aware translation concurrency and permanently reserved one worker plus one queue slot for reverse translation when desktop listening is enabled.
- Kept local and low-power backends conservatively serialized while allowing cloud providers to use additional independent clients.

### AI rewrite safety

- Rebuilt transformation prompts and provider-independent output validation so rewrite modes transform only the supplied text and reject answers, explanations, or conversational continuations.
- Added the optimistic, gentle honor-student Japanese high-school persona and expanded regression coverage for rewrite output safety.

## [1.3.9] - 2026-07-30

### Startup and first-use performance

- Improved startup responsiveness by moving nonessential catalog, device, migration, cleanup, and background-image work out of the launch-critical path.
- Added safe background connection preparation for online translation, ASR, and TTS providers to reduce the delay before the first request without triggering inference or local-model downloads.
- Improved local ASR model loading and download integrity, provider reuse, and shutdown cleanup.

## [1.3.8.9] - 2026-07-24

### Qwen regions and local AI

- Added Qwen Japan workspace-endpoint configuration for translation, Qwen3-ASR, and Qwen TTS; Japan uses the URL and model enabled for the selected workspace.
- Fixed secure downloads from legitimate Qwen regional audio hosts and enabled native system-certificate trust across AI, ASR, and TTS provider connections.
- Made Local AI API keys optional and improved compatible localhost and private-LAN HTTP endpoints with direct, low-latency connections.

## [1.3.8.8] - 2026-07-22

### TTS and chatbox reliability

- Added a quick-window option for `Original Text Only (Read Translation)` that waits for translated TTS playback to finish, then sends the original text after a 0.2-second delay.
- Improved Qwen TTS recovery from temporary audio-address lookup failures while retaining secure URL validation and DNS pinning.
- Prevented punctuation-only OSC messages from reaching VRChat, while allowing the standalone reactions `!?` and `！？`.

## [1.3.8.7] - 2026-07-22

### Transform-only AI output safety

- Strengthened system prompts across ASR rewrite, typed-text rewrite, forward translation, and reverse translation so providers may use history only to resolve pronouns, omitted subjects, terminology, and ambiguity, never to answer the player or continue a conversation.
- Added provider-independent output validation, conversational-reply rejection, and bounded retry handling for GPT/OpenAI-compatible, Claude/Anthropic-compatible, Grok/xAI-compatible, DeepSeek, and Qwen paths.
- Added regression coverage for reply-like outputs, explanations, labels, quotations, unrelated additions, and structured-output failures while preserving valid rewritten and translated text.

### Output modes and TTS

- Added `Original Text Only (Read Translation)`, which displays or sends only the original text while reading the translated result through TTS.
- Applied the mode consistently to realtime, manual, reverse, chatbox, and speech-output dispatch paths with localized settings labels and focused regression coverage.

### Reverse-translation responsiveness

- Prioritized fresh reverse-translation work when busy scenes produce sustained desktop-audio activity, preventing ambient speech from monopolizing the realtime scheduler.
- Added bounded admission and stale-work retirement for reverse recognition and translation so nearby players' newer speech can progress without waiting behind obsolete noisy-scene work.
- Hardened reverse-pipeline lifecycle handling and tests for saturation, cancellation, restart, and shutdown behavior.

## [1.3.8.6] - 2026-07-21

### Provider compatibility and secure diagnostics

- Audited and hardened official and compatible-relay paths for GPT/OpenAI, Claude/Anthropic, and Grok/xAI, including custom Base URLs, custom headers, proxy settings, API-key validation, provider-specific request formats, streaming and non-streaming responses, and stable error classification.
- Preserved relay-specific custom model IDs exactly as entered instead of applying official-provider model migrations or rewrites.
- Reused bounded HTTP connection pools and separated queue, pool, connect, read, response-header, and wall-clock deadlines so one stalled provider request cannot indefinitely retain a pipeline worker.
- Removed raw relay paths, provider response prose, credentials, player text, and third-party transport tracebacks from runtime logs while retaining safe status, provider, category, and latency diagnostics.

### Latency, queues, ordering, and cancellation

- Added structured per-request latency reporting for local queue wait, connection-pool wait, measurable DNS/TCP/TLS and response-header stages, provider processing, first token or first audio, full response, parsing and post-processing, reorder wait, UI delivery, OSC/TTS wait, and total wall-clock time.
- Strengthened realtime, manual, reverse-translation, ASR-rewrite, and typed-text-rewrite paths with bounded queue admission, strict ordering, stale-work retirement, cancellation propagation, poisoned-client replacement, and deterministic recovery after timeouts.
- Made scheduler, output, OSC, and provider diagnostics safe under failure while keeping enough structured timing information to identify local backlog separately from upstream provider latency.

### Qwen API TTS and lifecycle reliability

- Audited the complete Qwen API TTS path from text admission, preprocessing, dictionary handling, and language detection through synthesis, audio download, decoding, conversion, buffering, playback, and virtual-audio-device delivery.
- Prevented stalled synthesis or DNS resolution from permanently consuming later TTS capacity; resolver, synthesis, playback, request-close, and engine-close work now remains tracked until its worker and resource ownership are released.
- Hardened rapid consecutive playback, repeated close calls, concurrent shutdown handoffs, queue saturation, partial-start failures, and deferred engine destruction so workers, queue slots, files, audio buffers, and device resources are reclaimed deterministically.
- Extended application shutdown quiescence to cover realtime pipelines, manual translation, ASR, OSC, application and settings-test TTS managers, provider cleanup threads, and pending resolver work before terminal cleanup is reported.

### Localization and regression coverage

- Localized new provider names, settings, credential validation, timeout, cancellation, recovery, and lifecycle messages across Chinese, English, Japanese, Russian, and Korean without exposing untranslated provider errors as UI fallbacks.
- Added focused compatibility, latency, security, queue, TTS, ordering, and shutdown regressions, plus static localization and release-surface validation for the production build.

### Release signing identity

- Established a project-specific Ed25519 v2 identity for updater manifests, installer metadata, and detached release checksums; only the public verification key and fingerprints are stored in the repository.
- Added support for loading the private seed from an ACL-protected file outside the repository while keeping it out of PyInstaller, Inno Setup, command lines, logs, and release artifacts.
- Retired the v1 signing identity instead of leaving an unavailable key authorized. Because the previous v1 private seed was unavailable for a signed overlap release, users on v1.3.8.5 or earlier must install v1.3.8.6 manually from GitHub, compare the v2 fingerprint through the project website, and then verify the published SHA-256 checksum and detached signature.

## [1.3.8.5] - 2026-07-16

### Safe visible update installation

- Reworked **Install Now** to verify the downloaded installer before touching the active runtime, then open the installer with its normal visible interface instead of using a detached helper or silent switches.
- Added a bounded update-install quiescence barrier that stops active sessions, cancels provider calls, drains realtime and manual-translation workers, releases audio/TTS/OSC resources, and waits for pipeline cleanup before launching the installer.
- Added explicit cancellation and bounded joining for manual translation workers so typed rewrites or translations cannot retain provider clients during installation.
- Confirmed the child installer has a valid process and remains running before Mio performs its final shutdown.
- Made preparation and launch failures recoverable: Mio remains open, the verified installer is retained for retry or deferred installation, and runtime services can be recreated when launch does not succeed.
- Added localized installation handoff, startup, shutdown-timeout, retry, and recovery messages across all supported UI languages.

## [1.3.8.4] - 2026-07-16

### Grok-compatible translation

- Added a dedicated Grok-compatible provider for official xAI access and OpenAI-compatible Grok relays, with editable model IDs, Base URLs, bounded timeouts, optional streaming, and provider-specific connection testing.
- Added validated custom relay headers with strict name/value limits, forbidden transport-header rejection, protected-secret failure handling, masked settings display, and no credential logging.
- Added streaming-response assembly with an automatic non-streaming retry when a compatible endpoint rejects streaming controls.
- Updated the signed provider catalog with the `grok-4.5` default while preserving relay-specific custom model identifiers exactly as entered.

### Pipeline latency and recovery

- Added per-source translation queue expiry so stale reverse-translation work is discarded without blocking or reordering newer realtime results.
- Isolated reverse Qwen ASR timeouts and provider state from microphone recognition, and added dedicated reverse-ASR and reverse-translation latency limits.
- Improved ordered-stage timing metrics for recognition, rewriting, translation, UI delivery, provider queues, connection reuse, and context/prompt preparation.
- Improved desktop-audio worker ownership, VAD prewarming, automatic capture restart, microphone segment timing, and cancellation cleanup.
- Reduced translation context to bounded inert reference data and strengthened prompt rules so prior text cannot be treated as current instructions or reproduced as output.

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
