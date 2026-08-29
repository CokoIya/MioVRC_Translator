# Mio RealTime Translator v1.4.0

## Changes

- Added direct Microsoft Edge speech recognition over WebSocket. The reverse-listen path can now use the existing desktop/WASAPI loopback capture without opening a browser page.
- Kept SenseVoice as an optional local ASR model and removed obsolete browser/live-ASR runtime paths from the active package, reducing first-use overhead for online configurations.
- Added Qwen cloud voice enrollment/cloning with local reference-audio validation and safe upload handling.
- Retained bounded realtime queues, VAD segmentation, provider lifecycle cleanup, and frozen-bundle payload verification.

## Upgrade

Users on v1.3.9.3 or earlier can install this release directly. Existing configuration and downloaded local models are preserved where their provider remains available.
